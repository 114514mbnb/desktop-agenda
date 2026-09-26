"""正方教务系统适配器（ZFSoft，覆盖高校最广的一套）。

覆盖两种常见部署：
  * 新版  /jwglxt/...（jwglxt 路径，课表接口返回 JSON 或 HTML 表格）
  * 老版  /xtgl/...  （login_slogin.html）

登录流程（标准流程，不依赖任何学校私有接口）：
  1. GET 登录页 → 取隐藏字段（lt / execution / _eventId 等）
  2. POST 账号密码（RSA 加密留了口子，但默认明文 POST，多数部署可用）
  3. 探测是否登录成功
  4. GET 课表页 → 优先解析 JSON，退回解析 HTML 表格
"""

from __future__ import annotations

import json as jsonlib
import re
from datetime import date as Date

from .base import (
    AdapterInfo,
    Course,
    EasAdapter,
    EasError,
    FetchResult,
    find_inputs,
    parse_tables,
    parse_weeks,
    strip_tags,
)

# 教务里课表页常见的路径（不同学校版本略有差异，按顺序试）
COURSE_PAGE_PATHS = (
    "/jwglxt/kbcx/xskbcx_cxXsKb.html",
    "/jwglxt/kbcx/xskbcx_cxXsKb.html?gnmkdm=N253508",
    "/xtgl/index_initMenu.html",
)
# 课表数据接口
COURSE_DATA_PATHS = (
    "/jwglxt/kbcx/xskbcx_cxXsKb.html?gnmkdm=N253508",
    "/jwglxt/kbcx/xskbcx_cxXsKb.html",
)

# 单元格内容：课程名(周次)[教师]地点 的各种变体
CELL_PATTERNS = (
    re.compile(r"(?P<name>[^()\[\]（\[\s]+?)\s*[（(](?P<weeks>[^）)]*)[）)]"
               r"\s*[\[【](?P<teacher>[^\]】]*)[\]】]\s*(?P<room>.*)"),
    re.compile(r"(?P<name>[^()\[\]（\[\s]+?)\s*[（(](?P<teacher>[^）)]*)[）)]"
               r"\s*[\[【](?P<weeks>[^\]】]*)[\]】]\s*(?P<room>.*)"),
    re.compile(r"(?P<name>.+?)\s*[（(](?P<weeks>[^）)]*)[）)]\s*(?P<room>.*)"),
)


class ZfsoftAdapter(EasAdapter):
    """正方教务（通用）。"""

    info = AdapterInfo(
        key="zfsoft",
        title="正方教务（通用）",
        homepage="",
        note="覆盖学校最多的一套教务；登录页形如 /jwglxt/xtgl/login_slogin.html",
    )

    # 正方登录页候选（新版在前）
    LOGIN_PATHS = (
        "/jwglxt/xtgl/login_slogin.html",
        "/xtgl/login_slogin.html",
    )

    def __init__(
        self,
        base_url: str | None = None,
        session=None,
        *,
        login_path: str | None = None,
        academic_year: str | None = None,
        term: str | None = None,
    ):
        super().__init__(base_url, session)
        self.login_path = login_path
        self.academic_year = academic_year
        self.term = term
        self._logged_in = False

    # -- 登录 ------------------------------------------------------------
    def login_url(self) -> str:
        if self.login_path:
            return self.absolute(self.login_path)
        return self.absolute(self.LOGIN_PATHS[0])

    def login(self, username: str, password: str) -> None:
        page_html = self.session.open(self.login_url())
        fields = find_inputs(page_html)
        payload = dict(fields)
        payload.update({
            "language": "zh_CN",
            "yhm": username,
            "mm": password,
            "mmZh": password,
        })
        # 去掉按钮类字段，避免提交错分支
        for key in list(payload):
            if key.lower() in {"submit", "login"}:
                payload.pop(key, None)
        self.session.open(self.login_url(), payload, referer=self.login_url())
        self._logged_in = self._probe_logged_in(page_html)
        if not self._logged_in:
            raise EasError(
                "登录失败：账号/密码不正确，或该教务需要验证码/统一身份认证。"
                "已停止重试（避免账号被锁）；若你们学校走统一身份认证（SSO），"
                "请在客户端的「教务导入」里改用『手动粘贴课表页』方式。"
            )

    def _probe_logged_in(self, login_html: str) -> bool:
        """判断是否登录成功：登录页特征消失 / 出现退出或主页特征。"""
        try:
            home = self.session.open(self.absolute("/jwglxt/xtgl/index_initMenu.html"))
        except EasError:
            return False
        markers = ("退出", "注销", "个人课表", "学生课表", "信息查询", "index_initMenu")
        if any(marker in home for marker in markers):
            return True
        # 还停在登录页 → 失败
        return "login_slogin" not in home and "yhm" not in home

    def logged_in(self) -> bool:
        return self._logged_in

    # -- 课表 ------------------------------------------------------------
    def fetch_courses(self) -> FetchResult:
        last_error: Exception | None = None
        for path in COURSE_DATA_PATHS:
            try:
                url = self.absolute(path)
                if self.academic_year and self.term:
                    url = self._with_term(url)
                html = self.session.open(url)
            except EasError as error:
                last_error = error
                continue
            result = self.parse_course_payload(html)
            if result.courses:
                result.source_url = url
                return result
        if last_error is not None:
            raise EasError(f"取课表失败：{last_error}")
        raise EasError("取到课表页但没有解析出任何课程，请把原始页面导出后反馈（客户端里有『导出原始页面』）")

    def _with_term(self, url: str) -> str:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}xnm={self.academic_year}&xqm={self.term}"

    def parse_course_payload(self, html: str) -> FetchResult:
        """课表页可能是 JSON（新版）、HTML 表格（常见），或 div 布局（部分版本）。"""
        text = html.strip()
        if text.startswith("{") or text.startswith("["):
            courses = self._parse_json_courses(text)
            if courses:
                return FetchResult(courses=courses, raw_html=html)
        courses = self._parse_html_courses(html)
        if courses:
            return FetchResult(courses=courses, raw_html=html)
        return FetchResult(courses=self._parse_div_courses(html), raw_html=html)

    def _parse_div_courses(self, html: str) -> list[Course]:
        """从 div 布局的课表里挖课程。

        不猜 DOM 结构，而是找"长得像课程格子"的文本块：
          去标签 → 一个格子的多行合成一条（`高等数学|(1-16周)[张伟]|教三301`）
          → 整格优先按 `课程名(周次)[教师]地点` 解析；整格不成再逐行试。
        定位不到星期/节次就跳过，避免造出假课程。
        """
        courses: list[Course] = []
        for raw_block in re.split(r"(?i)<(?:div|li|section)\b[^>]*>", html):
            plain = plain_text(raw_block)
            if not plain or len(plain) > 200:
                continue
            weekday = _weekday_from_text(plain)
            start, end = _periods_from_text(plain)
            # 闸门：必须是"课程格子"的样子——有星期，或带 (周次)/[教师] 结构。
            # 否则 "周次 1-16" 这类说明文字会被当成课程。
            looks_like_entry = bool(
                re.search(r"[（(][^）)]{0,20}(周|节)", plain) or re.search(r"[\[【][^\]】]{1,20}[\]】]", plain)
            )
            if weekday is None and not looks_like_entry:
                continue
            parts = [part.strip() for part in plain.split("|") if part.strip()]
            candidates = [plain, *parts]          # 整格优先，再退回逐行
            for chunk in candidates:
                course = _course_from_chunk(chunk, weekday or 0, start or 1, end or start or 1)
                if course is not None:
                    courses.append(course)
                    break
        return _dedupe(courses)

    def _parse_json_courses(self, text: str) -> list[Course]:
        try:
            payload = jsonlib.loads(text)
        except jsonlib.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            rows = payload.get("kbList") or payload.get("xskbList") or payload.get("rows") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            return []
        courses: list[Course] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("kcmc") or row.get("courseName") or "").strip()
            weekday_raw = str(row.get("xqj") or row.get("weekday") or "").strip()
            weekday = _weekday_from_text(weekday_raw)
            start, end = _periods_from_text(str(row.get("jcs") or row.get("jc") or ""))
            if not name or weekday is None or start is None:
                continue
            weeks = parse_weeks(str(row.get("zcd") or row.get("weeks") or ""))
            courses.append(Course(
                name=name,
                weekday=weekday,
                start_period=start,
                end_period=end or start,
                teacher=_clean(str(row.get("xm") or row.get("teacher") or "")) or None,
                location=_clean(str(row.get("cdmc") or row.get("room") or "")) or None,
                weeks=weeks,
            ))
        return _dedupe(courses)

    def _parse_html_courses(self, html: str) -> list[Course]:
        """老版课表：一个 <table>，行=节次，列=星期；单元格里含课程描述。"""
        courses: list[Course] = []
        for table in parse_tables(html):
            if len(table) < 3:
                continue
            weekday_map = _weekday_columns(table[0])
            if not weekday_map:
                continue
            for row in table[1:]:
                if not row:
                    continue
                periods = _periods_from_text(row[0])
                if periods == (None, None):
                    continue
                start, end = periods
                for column, weekday in weekday_map.items():
                    if column >= len(row):
                        continue
                    cell = row[column]
                    if not cell or cell in {"-", "—", "无"}:
                        continue
                    courses.extend(_courses_from_cell(cell, weekday, start, end))
        return _dedupe(courses)


# ---------------------------------------------------------------------------
# 解析工具（纯函数，便于单测）
# ---------------------------------------------------------------------------

_WEEKDAY_TEXTS = (
    ("星期一", 0), ("星期一", 0), ("周一", 0), ("礼拜一", 0), ("星期一", 0),
    ("星期二", 1), ("周二", 1), ("星期三", 2), ("周三", 2),
    ("星期四", 3), ("周四", 3), ("星期五", 4), ("周五", 4),
    ("星期六", 5), ("周六", 5), ("星期日", 6), ("星期天", 6), ("周日", 6), ("周天", 6),
)


def _weekday_from_text(text: str) -> int | None:
    value = (text or "").strip()
    if not value:
        return None
    if value.isdigit():
        number = int(value)
        if 1 <= number <= 7:
            return number - 1
    for token, index in _WEEKDAY_TEXTS:
        if token in value:
            return index
    match = re.search(r"(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", value)
    if match:
        token = match.group(1)
        table = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        return table.get(token, int(token) - 1 if token.isdigit() else None)
    return None


def _periods_from_text(text: str) -> tuple[int | None, int | None]:
    """从 "1-2节" / "第3,4节" / "5" 里取起止节次。

    合理性闸门：一天不可能有 30 节课，所以 1-16 这种（其实是"周次"）要拒掉，
    否则会把 "(1-16周)" 误读成节次、造出"第1-16节"的假课程。
    """
    value = (text or "").strip()
    if not value:
        return None, None
    match = re.search(r"第?\s*(\d{1,2})(?:\s*[-–~至,，、]\s*(\d{1,2}))?\s*节", value)
    if match is not None:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
    else:
        numbers = [int(n) for n in re.findall(r"\d{1,2}", value)]
        if not numbers:
            return None, None
        start, end = numbers[0], numbers[-1]
    if not (1 <= start <= 20) or not (1 <= end <= 20):
        return None, None
    return start, max(start, end)


def _weekday_columns(header: list[str]) -> dict[int, int]:
    """表头里找到"星期一…星期日"所在的列号。"""
    mapping: dict[int, int] = {}
    for index, cell in enumerate(header):
        weekday = _weekday_from_text(cell)
        if weekday is not None and weekday not in mapping.values():
            mapping[index] = weekday
    # 至少认出 5 天才认为这是课表
    if len(mapping) < 5:
        return {}
    return mapping


def _courses_from_cell(cell: str, weekday: int, start: int | None, end: int | None) -> list[Course]:
    """一个格子里可能塞了多门课（换行分隔）。"""
    if start is None:
        return []
    found: list[Course] = []
    for chunk in [part.strip() for part in cell.split("\n") if part.strip()]:
        course = _course_from_chunk(chunk, weekday, start, end)
        if course is not None:
            found.append(course)
    return found


def course_from_cell_lines(
    lines: list[str],
    weekday: int,
    start: int | None,
    end: int | None,
) -> Course | None:
    """把课表一个格子的多行文本解析成一门课。

    这是"浏览器注入脚本 + Python 解析"共用的入口：脚本只负责把格子里的
    原文本按行取回来，字段规则（课程名/周次/教师/地点）只在 Python 里写一份。

    兼容三种常见排布：
      课程名(1-16周)[张三]教三301      —— 一行写完
      课程名 / (1-16周)[张三] / 教三301 —— 多行分开
      课程名 / 张三 / 教三301 / 1-16周  —— 教师与周次独立成行
    """
    cleaned = [_clean(line) for line in lines]
    cleaned = [line for line in cleaned if line]
    if not cleaned:
        return None
    first = re.sub(r"[★☆*]+$", "", cleaned[0]).strip()
    # 逐行拆字段时，只拿"第一行"去套结构化正则：
    # 多行格子里把各行拼起来再套，会把上一行的地点当成下一行的说明（踩过：
    # 「张伟 / 1-16周 / 教三301」拼成一行后，正则把 教三301 吃成了带周次的说明）。
    single_line = len(cleaned) == 1 or bool(re.search(r"[（(][^）)]*(周|节)", cleaned[0]))
    if single_line:
        name = _clean_course_name(re.sub(r"[（(][^）)]*[）)].*$", "", first)) or _clean_course_name(first)
        joined = cleaned[0] if len(cleaned) == 1 else " ".join(cleaned)
        matched_text = joined
    else:
        name = _clean_course_name(first)
        joined = " ".join(cleaned)
        matched_text = cleaned[0]
    if not name or len(name) > 40:
        return None
    rest = joined.replace(name, " ", 1)

    weeks = parse_weeks(_first_week_token(joined))
    teacher = None
    room = None
    for pattern in CELL_PATTERNS:
        match = pattern.search(matched_text)
        if match is None:
            continue
        groups = match.groupdict()
        teacher = _clean(groups.get("teacher", "")) or None
        room = _clean(groups.get("room", "")) or None
        if not weeks:
            weeks = parse_weeks(groups.get("weeks", ""))
        break
    # 没括号时：剩下的行逐条归类，而不是"第一条给教师、其余全扔掉"。
    # 踩过：['军事理论', '秦政', '1-16周', 'E-302'] 会把教师写成地点。
    if teacher is None or room is None:
        extra_teacher, extra_room = _classify_lines([line for line in cleaned[1:] if line != name])
        teacher = teacher or extra_teacher
        room = room or extra_room
    # 地点里若混进了周次或括号说明，清理掉。
    # 注意：**不要**在这里做 name 的全量替换——"中心校区"里含"山"这种巧合会被误删。
    if room:
        room = re.sub(r"[（(][^）)]*[）)]", " ", room)
        room = re.sub(r"\d{1,2}\s*[-–~至,，]\s*\d{1,2}\s*周[^ ]*", " ", room)
        room = _clean(room).strip() or None
    if start is None and end is None:
        return None
    start = start or 1
    return Course(
        name=name,
        weekday=weekday,
        start_period=start,
        end_period=max(start, end or start),
        teacher=teacher,
        location=room,
        weeks=weeks,
    )


def _first_week_token(text: str) -> str:
    """从一段文本里捞出"周次"写法（`1-16周` / `(3周)` / `第5周`）。

    开头的 `(?<![A-Za-z0-9\\-–—])` 不是装饰：没有它，**教室号 + 姓周的老师**
    会被读成周次 —— `中心校区 D-107 周宏` 里 `107 周` 命中"107 周"，
    于是整行被当成"纯周次行"丢掉，教师和地点一起消失（清洗个人数据时改名成
    `周宏` 才炸出来：原来那批测试数据里恰好没有姓周的老师）。
    只挡 ASCII 字母/数字/连字符，是为了不影响 `第3周`（"第"不在这个字符类里）。
    """
    match = re.search(
        r"(?<![A-Za-z0-9\-–—])"
        r"\d{1,2}(?:\s*[-–~至,，]\s*\d{1,2})*\s*周(?:\s*[（(]\s*[单双]\s*[)）])?",
        text,
    )
    return match.group(0) if match else ""


#: 地点线索：楼/室/馆/厅/校区…、教室编号（A-330 / D-107 / B-201 / 教三301）、线上
_ROOM_HINT = re.compile(r"(楼|室|馆|厅|校区|场|区|中心|线上|网课|腾讯会议|钉钉|学习通|雨课堂|"
                        r"[A-Za-z\u4e00-\u9fa5]{1,3}\s*[-–—]\s*\d{1,4}|[A-Za-z]{1,2}\d{1,4}|"
                        r"[\u4e00-\u9fa5]{1,3}\d{2,4})")
#: 教师线索：2-4 个汉字（含少数民族姓名的点），或"张三 李四"这种并排
_TEACHER_HINT = re.compile(r"^[\u4e00-\u9fa5][\u4e00-\u9fa5·]{1,3}(?:[\s、,，/]+[\u4e00-\u9fa5][\u4e00-\u9fa5·]{1,3})*$")
#: 行首标签（"教师：张伟" / "地点: A-330"），只剥标签不剥内容
_LABEL_RE = re.compile(r"^(任课教师|授课教师|上课地点|上课教室|教师|老师|地点|教室)\s*[:：]\s*")


def _clean_course_name(raw: str) -> str:
    """洗掉课名上的标记与"紧跟其后的教务内部字段"。

    正方新版课表页一格里塞的是：``解析几何★ / 中心校区 B-103 李文明 / -D0001-01 应数1班;… / 考试 / 讲课:48``，
    innerText 把整格文字连起来之后，课名会变成
    ``解析几何★ 中心校区 B-103 李文明 -D0001-01 应数1班;应数2班;… 考试 讲课:48``。
    这里只保留**第一个字段**，并去掉 ★☆*○【调】这些标记。
    """
    text = _clean(raw).strip()
    text = re.sub(r"^[【\[](调|停|补|代|录)[】\]]\s*", "", text)      # 【调】/【停】
    text = re.sub(r"[★☆*○●※]+\s*", "", text)
    # 课名后面紧跟的教务内部字段：先按"两个以上空格"切，再按强分隔符切
    for separator in (r"\s{2,}", r"-?[A-Z]{1,3}\d{6,}", r"\s+-\s*", r"[;；]", r"\s+\d+\s*$"):
        head = re.split(separator, text, maxsplit=1)[0].strip()
        if head:
            text = head
    # 剩下的若是"课名 空格 说明"，只取第一段中文课名（说明里通常含数字/冒号/分号）
    match = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9+＋().、\-—/ ]{2,40}?)(?=\s+[^\s]*[:：;；]|\s+\d)", text)
    if match:
        text = match.group(1).strip()
    return text.strip()


#: 教务内部垃圾字段：课程代码 -D0001-01、班级名单 应数1班;应数2班、考核方式 考试/未安排、
#: 学时统计 讲课:48 48 48 3。内联文本里它们紧跟在地点/教师后面，必须切掉。
_NOISE_TAIL = re.compile(
    r"(\s*-?[A-Z]{1,3}\d{6,}\S*"          # 课程代码
    r"|\s*[^\s;；]*(?:;\s*[^\s;；]*)+"      # 分号串起来的班级名单
    r"|\s*(?:考试|考查|未安排|讲课|实验|上机|实践)\b[^\s]*"
    r"|\s*[\u4e00-\u9fa5]{2,4}\s*[:：]\s*\d+(?:\s*[,，]\s*[\u4e00-\u9fa5]{2,4}\s*[:：]\s*\d+)*"
    r"|\s*\d+(?:\.\d+)?(?:\s+\d+(?:\.\d+)?)*\s*$)"
)
_TRAILING_JUNK = re.compile(r"[\s\-–—,，、:：;；]+$")


#: 地点词的几种形态（**整词匹配**，不靠长正则去猜边界）
_SUFFIX_PLACE = re.compile(r"^[\u4e00-\u9fa5]{1,6}(?:校区|楼|室|馆|厅|场|中心)$")
#: 连字符教室号：A-330 / D-107 / A-101。
#: 注意 `[A-Za-z]{1,2}\d?` 这半边是必需的——只写 `[A-Za-z]{1,3}` 的话
#: "D-107" 会在 "D" 处就匹配掉（"2-107" 不匹配任何形态），地点只剩「中心校区」。
_DASH_ROOM = re.compile(r"^(?:[A-Za-z]{1,2}\d?|[\u4e00-\u9fa5]{1,3})[-–—]\d{1,4}$")
_ALNUM_ROOM = re.compile(r"^[A-Za-z]{1,2}\d{1,3}[A-Za-z]?$")            # B201 / E302 / D2
#: 汉字 + 字母 + 数字：B-103 / E-303 / 教三301 —— 注意长度上限，
#: 免得把课程代码 "D206020600" 那种"字母+六位数字"也当成教室
_CJK_ALNUM_ROOM = re.compile(r"^[\u4e00-\u9fa5]{1,3}[A-Za-z]{0,2}\d{2,4}$")
_ONLINE_PLACE = frozenset({"线上", "网课", "腾讯会议", "钉钉", "学习通", "雨课堂"})

#: 这些词出现在"教师"位置时是教务字段，不是人名
_NOT_A_TEACHER = frozenset({
    "考试", "考查", "考核", "未安排", "待定", "讲课", "实验", "上机", "实践", "理论",
    "必修", "选修", "限选", "任选", "公选", "通识", "专业课", "基础课", "无", "暂无",
})


def _is_place_token(token: str) -> bool:
    """这个空格分隔的词，像不像"地点"的一部分。"""
    token = token.strip(" ,，、")
    if not token:
        return False
    if token in _ONLINE_PLACE:
        return True
    return bool(
        _SUFFIX_PLACE.match(token) or _DASH_ROOM.match(token)
        or _ALNUM_ROOM.match(token) or _CJK_ALNUM_ROOM.match(token)
    )


def _split_room_tokens(raw: str) -> tuple[str | None, list[str]]:
    """把地点行切成 (地点, 剩下的词)。

    输入常是 ``中心校区 B-103 李文明 -D0001-01 应数1班;… 考试 讲课:48``，
    地点返回 ``中心校区 B-103``，剩下的词里含教师名（``李文明``）。
    剩下的词要交回去——**教师常常就写在地点后面**，只收地点会把教师丢掉（实机踩过）。
    """
    text = _clean(raw)
    text = re.sub(r"[（(][^）)]*[）)]", " ", text)
    text = re.sub(r"\d{1,2}\s*[-–~至,，]\s*\d{1,2}\s*周[^ ]*", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    tokens = [token for token in text.split(" ") if token.strip(" ,，、")]
    place: list[str] = []
    while tokens and _is_place_token(tokens[0]):
        place.append(tokens.pop(0).strip(" ,，、"))
    cleaned = _TRAILING_JUNK.sub("", " ".join(place)).strip()
    return (cleaned or None), tokens


def _clean_room(raw: str) -> str | None:
    """只要地点部分。"""
    return _split_room_tokens(raw)[0]


def _classify_lines(lines: list[str]) -> tuple[str | None, str | None]:
    """把"课程名之外的行"分成教师和地点。

    只在没有结构化括号信息时兜底。地点线索优先于教师线索——
    "A-330" 这种既短又不像姓名的，必须先判成地点，否则会被当成老师。
    """
    teachers: list[str] = []
    rooms: list[str] = []
    for raw in lines:
        line = _LABEL_RE.sub("", _clean(raw).strip(" :：-—")).strip()
        if not line:
            continue
        if _first_week_token(line) or re.fullmatch(r"[（(]?\s*\d{1,2}\s*[-–~,，、]\s*\d{1,2}\s*[)）]?", line):
            continue   # 纯周次行/节次行，不是教师也不是地点
        if _ROOM_HINT.search(line):
            cleaned, leftovers = _split_room_tokens(line)
            if cleaned:
                rooms.append(cleaned)
            # 教师常常紧跟在教室号后面（"中心校区 B-103 李文明"），从剩下的词里捞回来
            for token in leftovers:
                if _TEACHER_HINT.fullmatch(token) and token not in _NOT_A_TEACHER:
                    teachers.append(token)
        elif _TEACHER_HINT.fullmatch(line) and line not in _NOT_A_TEACHER:
            teachers.append(line)
    return ("、".join(dict.fromkeys(teachers)) or None), (" ".join(rooms) or None)


def dedupe_courses(courses: list[Course]) -> list[Course]:
    return _dedupe(courses)


def _course_from_chunk(chunk: str, weekday: int, start: int, end: int) -> Course | None:
    # 表格里一个 <td> 可能塞多门课（用 <br> 分隔，已被 _courses_from_cell 拆好）；
    # div 布局传来的分片用 | 连接同一个格子的多行，这里换成空格让正则能整体匹配
    text = chunk.replace("|", " ") if "|" in chunk else chunk
    text = re.sub(r"\s{2,}", " ", text).strip()
    if not text:
        return None
    for pattern in CELL_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        groups = match.groupdict()
        name = _clean(groups.get("name", ""))
        if not name or len(name) > 40:
            continue
        weeks = parse_weeks(groups.get("weeks", ""))
        teacher = _clean(groups.get("teacher", "")) or None
        room = _clean(groups.get("room", "")) or None
        return Course(
            name=name,
            weekday=weekday,
            start_period=start,
            end_period=end,
            teacher=teacher,
            location=room,
            weeks=weeks,
        )
    # 完全没有括号信息时，整段当课名（宁可粗糙也别丢课）
    name = _clean(text)
    if 1 < len(name) <= 40:
        return Course(name=name, weekday=weekday, start_period=start, end_period=end)
    return None


def _clean(value: str) -> str:
    text = strip_tags(str(value or ""))
    text = text.replace("&nbsp;", " ").strip()
    return re.sub(r"\s{2,}", " ", text)


def plain_text(fragment: str) -> str:
    """把一个 HTML 片段压成单行文本：去标签、按块切分、多行用 | 连接。

    div 布局的课表一个格子里常是 `<br>` 分隔的多行，必须合成一条再解析，
    否则会把"课程名"和"地点"当成两门课。
    """
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)
    text = re.sub(r"(?i)<br\s*/?>", "|", text)
    text = re.sub(r"(?i)</(?:div|li|p|tr|td|h\d)>", "|", text)
    text = re.sub(r"<[^>]*>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    parts = [part.strip() for part in text.split("|")]
    return "|".join(part for part in parts if part)


def _dedupe(courses: list[Course]) -> list[Course]:
    seen: set[tuple] = set()
    result: list[Course] = []
    for course in courses:
        key = (course.name, course.weekday, course.start_period, course.end_period,
               course.location, course.weeks)
        if key in seen:
            continue
        seen.add(key)
        result.append(course)
    return sorted(result, key=lambda c: (c.weekday, c.start_period, c.name))


def academic_year_of(when: Date) -> str:
    """按中国高校习惯把日期换算成学年，如 2026-09 → "2026"（2026-2027 学年）。"""
    return str(when.year if when.month >= 8 else when.year - 1)


def term_code_of(when: Date) -> str:
    """学期代码：3=秋季（第一学期），12=春季（第二学期），16=小学期。"""
    if when.month in (9, 10, 11, 12, 1):
        return "3"
    if when.month in (2, 3, 4, 5, 6, 7):
        return "12"
    return "16"


def guess_term(today: Date | None = None) -> tuple[str, str]:
    anchor = today or Date.today()
    return academic_year_of(anchor), term_code_of(anchor)
