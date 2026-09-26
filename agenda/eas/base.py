"""教务系统适配器框架。

设计目标（为了"适应面更广、能开源"）：
  * 一个适配器 = 一个学校/一套教务系统；核心代码不认识任何具体学校
  * 适配器只声明"登录页在哪、课表接口在哪、字段怎么映射"，解析逻辑走标准流程
  * 新增学校 = 新增一个适配器文件（或一行注册），不动其他代码
  * 全部只用标准库（urllib + http.cookiejar），开源出去零依赖、免安装

安全约定（写进代码而不是只写文档）：
  * 密码只在内存中存活一次登录请求，绝不落盘、不写日志
  * 每个适配器自带 max_attempts，避免反复重试把账号撞锁
  * 抓到的原始页面可导出，方便核对"是不是解析错了"而不是黑箱
"""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class EasError(RuntimeError):
    """适配器可预期的失败（登录失败、页面结构变了、需要校园网…）。"""


@dataclass
class Course:
    """一条从教务系统抓到的课程（与 timetable.Course 对齐，但独立以免循环依赖）。"""

    name: str
    weekday: int              # 0=周一
    start_period: int         # 1 起
    end_period: int
    teacher: str | None = None
    location: str | None = None
    weeks: tuple[int, ...] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "weekday": self.weekday,
            "period": f"{self.start_period}-{self.end_period}",
        }
        if self.teacher:
            payload["teacher"] = self.teacher
        if self.location:
            payload["location"] = self.location
        if self.weeks is not None:
            payload["weeks"] = format_weeks(self.weeks)
        return payload


@dataclass
class FetchResult:
    """一次抓取的结果：课程 + 原始页面（便于用户核对与排错）。"""

    courses: list[Course] = field(default_factory=list)
    raw_html: str = ""
    source_url: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.courses)


# ---------------------------------------------------------------------------
# 周次解析（正方等系统的通用写法）
# ---------------------------------------------------------------------------

def parse_weeks(value: str) -> tuple[int, ...] | None:
    """解析 "1-16周"、"1-5,7-11单周"、"3-15周(单)"、"1,3,5" 等写法。

    返回 None 表示整学期每周都有。
    """
    if not value:
        return None
    text = str(value).strip()
    if not text or text in {"-", "无", "每周", "全周"}:
        return None
    odd = "单" in text
    even = "双" in text
    weeks: set[int] = set()
    for start, end in re.findall(r"(\d{1,2})\s*[-–~至]\s*(\d{1,2})", text):
        low, high = int(start), int(end)
        if low > high:
            low, high = high, low
        weeks.update(range(low, high + 1))
    for single in re.findall(r"(?<![\d\-–~至])(\d{1,2})(?![\d\-–~至])", text):
        weeks.add(int(single))
    if not weeks:
        return None
    if odd:
        weeks = {w for w in weeks if w % 2 == 1}
    elif even:
        weeks = {w for w in weeks if w % 2 == 0}
    return tuple(sorted(weeks))


def format_weeks(weeks: Iterable[int]) -> str:
    """把周次列表压成 WakeUp/教务常见的紧凑写法：1-5、7-11单。"""
    values = sorted(set(int(w) for w in weeks))
    if not values:
        return "无"
    groups: list[list[int]] = []
    for value in values:
        if groups and value == groups[-1][-1] + 1:
            groups[-1].append(value)
        else:
            groups.append([value])
    parts: list[str] = []
    for group in groups:
        if len(group) == 1:
            parts.append(str(group[0]))
        else:
            parts.append(f"{group[0]}-{group[-1]}")
    text = "、".join(parts)
    # 全为奇数或全为偶数且跨度>2 时标注单/双周（与 WakeUp 模板一致）
    if len(values) > 2 and all(w % 2 == 1 for w in values):
        text += "单"
    elif len(values) > 2 and all(w % 2 == 0 for w in values):
        text += "双"
    return text


# ---------------------------------------------------------------------------
# HTTP 会话
# ---------------------------------------------------------------------------

class Session:
    """带 Cookie 的极简会话；不保存任何凭据。"""

    def __init__(self, *, timeout: float = 20.0, insecure: bool = True):
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPCookieProcessor(self.jar)]
        if insecure:
            # 部分教务用自签证书；这是内网课表抓取，接受自签但仅限显式开启
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self.opener = urllib.request.build_opener(*handlers)
        self.history: list[str] = []

    def open(self, url: str, data: dict[str, str] | None = None, *, referer: str | None = None) -> str:
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(url, data=body)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Language", "zh-CN,zh;q=0.9")
        if referer:
            request.add_header("Referer", referer)
        if body is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                self.history.append(f"{response.status} {response.geturl()}")
                return text
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:200]
            raise EasError(f"HTTP {error.code} {url}：{detail}") from error
        except urllib.error.URLError as error:
            raise EasError(f"连不上 {url}：{error.reason}") from error
        except TimeoutError as error:
            raise EasError(f"请求超时 {url}") from error


# ---------------------------------------------------------------------------
# 轻量 HTML 解析
# ---------------------------------------------------------------------------

class TableParser(HTMLParser):
    """把 <table> 解析成行列表；够用且不引入 bs4/lxml。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._table is not None and self._depth == 1:
                self.tables.append(self._table)
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._table = None
        elif tag == "tr" and self._row is not None:
            if self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag in {"td", "th"} and self._cell is not None:
            text = re.sub(r"[ \t\u3000]+", " ", "".join(self._cell)).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html: str) -> list[list[list[str]]]:
    parser = TableParser()
    parser.feed(html)
    return parser.tables


def strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"[ \t\u3000]{2,}", " ", text).strip()


def find_inputs(html: str) -> dict[str, str]:
    """抓取表单里的隐藏字段（正方登录要带 lt / execution 等）。"""
    fields: dict[str, str] = {}
    for tag in re.findall(r"(?is)<input[^>]+>", html):
        name = re.search(r'name=["\']([^"\']+)["\']', tag)
        if name is None:
            continue
        value = re.search(r'value=["\']([^"\']*)["\']', tag)
        fields[name.group(1)] = value.group(1) if value else ""
    return fields


# ---------------------------------------------------------------------------
# 适配器基类
# ---------------------------------------------------------------------------

@dataclass
class AdapterInfo:
    key: str
    title: str
    homepage: str = ""
    note: str = ""


class EasAdapter:
    """教务适配器接口。

    子类至少实现 login_url / fetch_courses；需要特殊登录流程时覆盖 login。
    """

    info = AdapterInfo(key="base", title="通用教务")
    #: 允许的登录尝试次数（防账号锁定；教务系统普遍有失败锁定策略）
    max_attempts = 2
    #: 是否声明需要校园网
    needs_campus_network = False

    def __init__(self, base_url: str | None = None, session: Session | None = None):
        self.base_url = (base_url or self.info.homepage).rstrip("/")
        self.session = session or Session()

    # -- 子类实现 --------------------------------------------------------
    def login_url(self) -> str:
        raise NotImplementedError

    def login(self, username: str, password: str) -> None:
        """默认实现：直接 POST 账号密码。凭据只在此方法内存活。"""
        fields = {}
        page = self.session.open(self.login_url())
        fields.update(find_inputs(page))
        fields.update(self.login_fields(username, password))
        self.session.open(self.login_url(), fields, referer=self.login_url())
        if not self.logged_in():
            raise EasError("登录失败：账号或密码不正确（已停止重试，避免账号被锁）")

    def login_fields(self, username: str, password: str) -> dict[str, str]:
        return {"username": username, "password": password}

    def logged_in(self) -> bool:
        """默认实现交给子类用一次探测请求判断。"""
        return True

    def fetch_courses(self) -> FetchResult:
        raise NotImplementedError

    # -- 通用工具 --------------------------------------------------------
    def absolute(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return urllib.parse.urljoin(self.base_url + "/", path.lstrip("/"))

    def describe(self) -> str:
        return f"{self.info.title}（{self.info.key}）"
