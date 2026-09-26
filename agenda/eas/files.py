"""文件型导入：WakeUp 官方 CSV 模板、Excel 粘贴、课表网页粘贴。

这三种都不需要联网、不需要账号，是"适应面最广"的兜底：
任何学校、任何教务，只要能把课表弄成文本，就能导进来。
"""

from __future__ import annotations

import csv
import io
import re

from .base import Course, EasAdapter, EasError, FetchResult, parse_weeks
from .zfsoft import _course_from_chunk, _dedupe, _periods_from_text, _weekday_from_text, _weekday_columns
from .base import parse_tables

# WakeUp 官方模板表头（7 列，顺序固定）
WAKEUP_HEADER = ("课程名称", "星期", "开始节数", "结束节数", "老师", "地点", "周数")
WAKEUP_ALIASES = {
    "name": ("课程名称", "课程名", "课程", "科目", "name", "course"),
    # 注意顺序：先长后短，"星期"要排在"周"前面
    "weekday": ("星期几", "星期", "周几", "weekday", "周"),
    # 合并写法（教务常见）：只有"节次"一列，值形如 第4-5节
    "period": ("节次", "上课节次", "节数", "period"),
    "start": ("开始节数", "开始节", "起始节数", "起始节", "start", "开始"),
    "end": ("结束节数", "结束节", "终止节数", "终止节", "end", "结束"),
    "teacher": ("任课教师", "教师", "老师", "teacher"),
    "location": ("上课地点", "地点", "教室", "location", "room"),
    "weeks": ("教学周", "周数", "周次", "weeks"),
}

PLACEHOLDER = {"", "无", "未知", "None", "none", "-", "—", "/"}

WEEK_TOKEN_RE = re.compile(
    r"\d{1,2}\s*[-–~至]\s*\d{1,2}\s*周(?:\s*[（(]\s*[单双]\s*[)）])?"
    r"|\d{1,2}\s*[-–~至]\s*\d{1,2}\s*[（(]\s*[单双]\s*[)）]"
)
#: 地点里真正的"地点标记"——没有这些就说明这一列装的是别的东西
PLACE_MARK_RE = re.compile(
    r"(校区|上课地点|教学楼|教室|楼|馆|厅|室|区|线上|腾讯会议|Zoom|钉钉|飞书|实验|机房|操场|体育)"
)


def _split_place_weeks(*cells: str) -> tuple[str, str]:
    """从若干列里分离出（地点, 周数文本）。

    教务导出的表格很脏：周次常被塞进"上课地点"列（`4-11周 中心校区 A-330`），
    而周数列反而是空的。这里按内容特征就地拆开——首行命中的周次片段归周数，
    其余内容若含真正的地点标记才算地点。
    """
    weeks_tokens: list[str] = []
    place_parts: list[str] = []
    for index, cell in enumerate(cells):
        text = (cell or "").strip()
        if not text or text.lower() in {item.lower() for item in PLACEHOLDER}:
            continue
        match = WEEK_TOKEN_RE.search(text)
        if match is not None and not weeks_tokens:
            weeks_tokens.append(match.group(0))
            text = (text[:match.start()] + " " + text[match.end():]).strip()
        if text and PLACE_MARK_RE.search(text):
            place_parts.append(text)
    weeks = weeks_tokens[0] if weeks_tokens else ""
    place = " ".join(dict.fromkeys(place_parts))
    # 去掉"校区:xx 上课地点: yy"里的标签，留下可读的地点
    place = re.sub(r"(?:校区|上课地点|上课教室)\s*[:：]\s*", "", place).strip()
    return place, weeks


def _cell(row: dict[str, str], field: str) -> str:
    for alias in WAKEUP_ALIASES[field]:
        for key, value in row.items():
            if key and key.strip().lower() == alias.lower():
                return (value or "").strip()
    return ""


def _optional(value: str) -> str | None:
    text = (value or "").strip()
    if text.lower() in {item.lower() for item in PLACEHOLDER}:
        return None
    return text or None


class WakeUpCsvAdapter(EasAdapter):
    """WakeUp 课程表官方 CSV 模板导入。

    模板规范（官方文档 import_from_csv）：
      表头固定 7 列：课程名称,星期,开始节数,结束节数,老师,地点,周数
      星期/开始节数/结束节数 必须是单个数字
      老师/地点未知要填「无」，不能留空
      周数支持 1-16、1-5、7-11单
    """

    info_hint = "WakeUp 官方 CSV 模板"

    def __init__(self, content: str = ""):
        self.content = content

    def parse(self, content: str | None = None) -> FetchResult:
        text = content if content is not None else self.content
        if not text or not text.strip():
            raise EasError("CSV 内容为空")
        # 官方模板从 WPS/Excel 另存为 CSV，可能是 GBK
        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                decoded = text.encode("utf-8", errors="ignore").decode("utf-8") if isinstance(text, bytes) else text
                break
            except (UnicodeDecodeError, AttributeError):
                continue
        else:  # pragma: no cover
            decoded = str(text)

        reader = csv.DictReader(io.StringIO(decoded))
        if reader.fieldnames is None:
            raise EasError("CSV 没有表头，无法识别（需要 WakeUp 模板的 7 列表头）")
        header = [name.strip().lstrip("\ufeff") for name in reader.fieldnames]
        missing = [field for field in ("课程名称",) if field not in header]
        if missing:
            raise EasError(
                f"CSV 表头不符合 WakeUp 模板，缺少 {missing}；"
                f"当前表头为 {header}。模板列顺序：{'、'.join(WAKEUP_HEADER)}"
            )

        courses: list[Course] = []
        warnings: list[str] = []
        for line_number, row in enumerate(reader, start=2):
            row = {(k or "").strip().lstrip("\ufeff"): (v or "") for k, v in row.items()}
            name = _cell(row, "name")
            if not name or name.lower() in {item.lower() for item in PLACEHOLDER}:
                if any((value or "").strip() for value in row.values()):
                    warnings.append(f"第 {line_number} 行：课程名称为空，已跳过")
                continue
            weekday = _weekday_from_text(_cell(row, "weekday"))
            if weekday is None:
                warnings.append(f"第 {line_number} 行：星期「{_cell(row, 'weekday')}」无法识别，已跳过")
                continue
            start_text = _cell(row, "start")
            end_text = _cell(row, "end")
            start = int(re.sub(r"\D", "", start_text) or 0) or None
            end = int(re.sub(r"\D", "", end_text) or 0) or None
            if start is None:
                warnings.append(f"第 {line_number} 行：开始节数为空，已跳过")
                continue
            weeks = parse_weeks(_cell(row, "weeks"))
            courses.append(Course(
                name=name,
                weekday=weekday,
                start_period=start,
                end_period=end or start,
                teacher=_optional(_cell(row, "teacher")),
                location=_optional(_cell(row, "location")),
                weeks=weeks,
            ))
        if not courses:
            raise EasError("CSV 里没有解析出任何课程，请检查是否按官方模板填写")
        return FetchResult(courses=_dedupe(courses), raw_html=decoded, warnings=warnings)


class PlainTableAdapter(EasAdapter):
    """直接从 Excel / WPS / 网页复制的表格文本。

    两种形态都认：
      * 制表符/多空格分隔的「课程 星期 开始节 结束节 老师 地点 周数」
      * WakeUp 模板同序（缺表头也认，按位置猜列）
    """

    def __init__(self, content: str = ""):
        self.content = content

    def parse(self, content: str | None = None) -> FetchResult:
        text = content if content is not None else self.content
        if not text or not text.strip():
            raise EasError("粘贴内容为空")
        rows = _split_rows(text)
        if not rows:
            raise EasError("没看出表格结构：请从 Excel 里连同表头一起复制")

        header_map = _detect_header(rows[0])
        body = rows[1:] if header_map else rows
        if not header_map:
            header_map = {index: field for index, field in enumerate(
                ("name", "weekday", "start", "end", "teacher", "location", "weeks")
            )}

        courses: list[Course] = []
        warnings: list[str] = []
        for line_number, row in enumerate(body, start=1):
            values = {field: (row[index] if index < len(row) else "") for index, field in header_map.items()}
            period_text = (values.get("period") or "").strip()
            weeks_text = (values.get("weeks") or "").strip()

            # 教务表格很脏：周次常被塞进"上课地点"列，而周数列是空的。
            # 按内容特征就地拆分（地点必须含真正的地点标记才算）。
            place_text, weeks_from_place = _split_place_weeks(
                values.get("location", ""), weeks_text, period_text,
            )
            if weeks_from_place and not weeks_text:
                weeks_text = weeks_from_place

            name = (values.get("name") or "").strip()
            if not name or name.lower() in {item.lower() for item in PLACEHOLDER}:
                continue

            # 星期优先取"星期"列；取不到就看别的列有没有夹带
            weekday = _weekday_from_text(values.get("weekday", "") or "")
            if weekday is None and period_text:
                weekday = _weekday_from_text(period_text)
            if weekday is None:
                weekday = _weekday_from_text(weeks_text)
            if weekday is None:
                warnings.append(f"第 {line_number} 行：{name} 没认出星期，已跳过")
                continue

            start: int | None = None
            end: int | None = None
            from_period = _periods_from_text(period_text) if period_text else (None, None)
            if from_period[0] is not None:
                start, end = from_period
            else:
                start_text = re.sub(r"\D", "", values.get("start", "") or "")
                end_text = re.sub(r"\D", "", values.get("end", "") or "")
                if start_text:
                    start = int(start_text)
                    end = int(end_text) if end_text else start
            if start is None:
                warnings.append(f"第 {line_number} 行：{name} 没有节次，已跳过")
                continue

            courses.append(Course(
                name=name,
                weekday=weekday,
                start_period=start,
                end_period=max(start, end or start),
                teacher=_optional(values.get("teacher", "")),
                location=place_text or None,
                weeks=parse_weeks(weeks_text),
            ))
        if not courses:
            raise EasError(
                "粘贴的表格里没有解析出课程。需要这些列（顺序不限）："
                "课程、星期、节次（或开始节/结束节）、地点、周数"
            )
        return FetchResult(courses=_dedupe(courses), raw_html=text, warnings=warnings)


class HtmlPageAdapter(EasAdapter):
    """手动粘贴课表网页内容（或保存的 HTML 文件）。

    给"统一身份认证/验证码导致无法自动登录"的学校兜底：
    用户在浏览器里登录，把课表页全选复制，粘进来即可。
    """

    def __init__(self, content: str = ""):
        self.content = content

    def parse(self, content: str | None = None) -> FetchResult:
        text = content if content is not None else self.content
        if not text or not text.strip():
            raise EasError("粘贴内容为空")
        warnings: list[str] = []
        courses: list[Course] = []
        if "<table" in text.lower() or "<td" in text.lower():
            for table in parse_tables(text):
                if len(table) < 3:
                    continue
                columns = _weekday_columns(table[0])
                if not columns:
                    continue
                for row in table[1:]:
                    if not row:
                        continue
                    periods = _periods_from_text(row[0])
                    if periods == (None, None):
                        continue
                    start, end = periods
                    for index, weekday in columns.items():
                        if index < len(row) and row[index].strip() not in {"", "-", "—"}:
                            for chunk in row[index].split("\n"):
                                course = _course_from_chunk(chunk, weekday, start, end or start)
                                if course is not None:
                                    courses.append(course)
            if not courses:
                warnings.append("页面里有表格，但没认出课表结构；已尝试按文本解析")
        if not courses:
            # div 布局的课表（部分正方版本不用 table）——复用正方适配器的挖法
            from .zfsoft import ZfsoftAdapter

            try:
                div_courses = ZfsoftAdapter("http://local")._parse_div_courses(text)
                if div_courses:
                    courses = div_courses
                    warnings.append("按 div 布局解析出课程")
            except Exception:  # noqa: BLE001
                pass
        if not courses:
            plain = PlainTableAdapter()
            try:
                fallback = plain.parse(text)
                courses = fallback.courses
                warnings.extend(fallback.warnings)
            except EasError as error:
                # 浏览器路径上"这个页面里没有课表"是正常状态，不该当成异常；
                # 返回空结果，由调用方决定提示什么
                warnings.append(f"没从页面里认出课表：{error}")
        return FetchResult(courses=_dedupe(courses), raw_html=text, warnings=warnings)


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

def _split_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if "\t" in line:
            cells = [cell.strip() for cell in line.split("\t")]
        elif "|" in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
        elif "," in line or "，" in line:
            cells = [cell.strip() for cell in re.split(r"[,，]", line)]
        else:
            cells = [cell.strip() for cell in re.split(r"\s{2,}", line)]
        cells = [cell for cell in cells if cell != ""]
        if cells:
            rows.append(cells)
    return rows


def _detect_header(row: list[str]) -> dict[int, str] | None:
    """识别表头 → 字段映射。

    两条规则（都是踩坑换来的）：
      1. 按别名长度优先分配：否则"周数"会被短别名"周"抢去当星期列；
      2. 只有当真正识别出的表头很少（<=2 列）时才按模板列序补齐，
         而且从**最后一个已识别列之后**开始补、跳过未映射列——
         否则教务表里"节数"这种额外的列会把地点/周数整体挤错位。
    """
    ordered = ("name", "weekday", "period", "start", "end", "teacher", "location", "weeks")
    candidates: list[tuple[int, int, str, int]] = []
    for index, cell in enumerate(row):
        value = (cell or "").strip()
        if not value:
            continue
        for priority, field in enumerate(ordered):
            for alias in WAKEUP_ALIASES[field]:
                if value == alias:
                    candidates.append((len(alias) + 100, index, field, priority))
                elif value.startswith(alias):
                    candidates.append((len(alias), index, field, priority))
    candidates.sort(key=lambda item: (-item[0], item[3]))
    mapping: dict[int, str] = {}
    for _, index, field, _priority in candidates:
        if index in mapping or field in mapping.values():
            continue
        mapping[index] = field
    if "name" not in mapping.values() or "weekday" not in mapping.values():
        return None
    if len(mapping) <= 2:
        order = ("period", "start", "end", "teacher", "location", "weeks")
        cursor = max(mapping) + 1
        for field in order:
            if field in mapping.values():
                continue
            while cursor in mapping:
                cursor += 1
            if cursor >= len(row):
                break
            mapping[cursor] = field
            cursor += 1
    return mapping
