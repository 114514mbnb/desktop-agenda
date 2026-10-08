"""适配器注册表与学校目录。

新增一所学校：在 SCHOOLS 里加一条，或让用户直接在客户端填"教务网址"。
新增一套系统类型：加一个 EasAdapter 子类，然后 @register 注册，核心代码不用动。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import AdapterInfo, EasAdapter
from .zfsoft import ZfsoftAdapter

_REGISTRY: dict[str, type[EasAdapter]] = {}


def register(adapter_cls: type[EasAdapter]) -> type[EasAdapter]:
    _REGISTRY[adapter_cls.info.key] = adapter_cls
    return adapter_cls


def available_adapters() -> list[type[EasAdapter]]:
    return list(_REGISTRY.values())


def get_adapter(key: str) -> type[EasAdapter]:
    try:
        return _REGISTRY[key]
    except KeyError as error:
        raise KeyError(f"未知的教务适配器：{key}（可用：{', '.join(_REGISTRY)}）") from error


@dataclass
class School:
    """一所学校的教务入口配置。"""

    name: str
    adapter: str
    base_url: str
    aliases: tuple[str, ...] = ()
    login_path: str | None = None
    course_url: str = ""          # 登录后直接可达的课表页（WebVPN 包装地址也放这里）
    note: str = ""
    needs_campus_network: bool = False

    def matches(self, keyword: str) -> bool:
        needle = (keyword or "").strip().lower()
        if not needle:
            return False
        haystack = [self.name.lower(), *(alias.lower() for alias in self.aliases)]
        return any(needle in item for item in haystack)


# ---------------------------------------------------------------------------
# 学校目录
# ---------------------------------------------------------------------------
# 说明：
#   * base_url  —— 浏览器打开哪个地址去登录
#   * course_url —— 登录成功后课表页的确切地址（粘贴通道/排错用；WebVPN 包装地址照收）
# 入口不确定的学校不写死，让用户用「自定义网址」——与其猜错，不如让用户填一次然后记住。

# 青岛科技大学：教务藏在 WebVPN 反代后面，登录走 CAS 统一身份认证，
# 课表页是正方 /jwglxt/kbcx/ 那套；未登录访问会跳登录页，登录后直接可达。
QUST_COURSE_URL = (
    "https://wvpn.qust.edu.cn/http/"
    "77726476706e69737468656265737421fae046903f2426416b1b9de29d51367bb4e3"
    "/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"
)

SCHOOLS: list[School] = [
    School(
        name="青岛科技大学",
        adapter="zfsoft",
        base_url="https://wvpn.qust.edu.cn",
        aliases=("qust", "青科大", "青岛科技"),
        course_url=QUST_COURSE_URL,
        note="统一身份认证 + WebVPN 网关，课表页是正方 /jwglxt/kbcx/；"
             "请用「浏览器登录导入」，直连账号密码那条走不通",
    ),
]


def search_schools(keyword: str) -> list[School]:
    return [school for school in SCHOOLS if school.matches(keyword)]


def build_adapter(
    *,
    school: School | None = None,
    adapter_key: str | None = None,
    base_url: str | None = None,
    login_path: str | None = None,
    academic_year: str | None = None,
    term: str | None = None,
) -> EasAdapter:
    """按学校或显式参数构造适配器实例。"""
    key = adapter_key or (school.adapter if school else "zfsoft")
    adapter_cls = get_adapter(key)
    url = base_url or (school.base_url if school else "")
    if not url:
        raise ValueError("必须提供教务系统网址（base_url）")
    path = login_path or (school.login_path if school else None)
    if issubclass(adapter_cls, ZfsoftAdapter):
        return adapter_cls(url, login_path=path, academic_year=academic_year, term=term)
    return adapter_cls(url)
