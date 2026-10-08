"""测试：多所学校的教务入口是否可达、走什么登录方式。

为什么要有这个工具：原来客户端里那条"用浏览器打开教务系统自动读课表"的路，
在学校目录里只登记了青岛科技大学。这个脚本用来**实测**其他学校，
把"能不能到登录页、会不会跳统一身份认证、是不是正方那套"写成可核对的事实，
而不是靠猜。

用法：
    runtime\\python.exe tools\\probe_schools.py            # 测内置的候选清单
    runtime\\python.exe tools\\probe_schools.py 网址1 网址2  # 测指定网址

它只做 GET 和重定向跟踪，**不登录、不提交任何账号密码**。
"""

from __future__ import annotations

import http.client
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 待测学校的候选入口。base 是校外访问网关，jw 是教务系统本尊（校内地址）。
CANDIDATES: dict[str, dict[str, list[str]]] = {
    "山东建筑大学": {
        "base": ["https://wvpn.sdjzu.edu.cn", "https://vpn.sdjzu.edu.cn", "https://ssl-vpn.sdjzu.edu.cn"],
        "jw": ["https://jwgl.sdjzu.edu.cn", "http://jwgl.sdjzu.edu.cn"],
    },
    "太原理工大学": {
        "base": ["https://wvpn.tyut.edu.cn", "https://vpn.tyut.edu.cn", "https://sslvpn.tyut.edu.cn",
                 "https://cas.tyut.edu.cn"],
        "jw": ["https://jwc.tyut.edu.cn"],
    },
    "山东科技大学": {
        "base": ["https://wvpn.sdust.edu.cn", "https://vpn.sdust.edu.cn", "https://ssl-vpn.sdust.edu.cn"],
        "jw": ["https://jwgl.sdust.edu.cn", "http://jwgl.sdust.edu.cn"],
    },
    "青岛大学": {
        "base": ["https://wvpn.qdu.edu.cn", "https://vpn.qdu.edu.cn", "https://ssl-vpn.qdu.edu.cn"],
        "jw": ["https://jwgl.qdu.edu.cn", "http://jwgl.qdu.edu.cn", "https://jwc.qdu.edu.cn"],
    },
}

#: 判定用的特征词
SIGNATURES = (
    ("统一身份认证", "统一身份认证（CAS）"),
    ("wengine", "WebVPN 网关（wengine）"),
    ("WebVPN", "WebVPN"),
    ("jwglxt", "正方教务 新版 /jwglxt/"),
    ("xtgl/login", "正方教务 老版 /xtgl/"),
    ("教务", "教务相关字样"),
    ("StrongSwan", "SSL VPN（非反代式，网页读不到课表）"),
    ("openvpn", "OpenVPN 客户端式"),
)

TIMEOUT = 8.0


@dataclass
class Probe:
    url: str
    status: int = 0
    final_url: str = ""
    title: str = ""
    hints: list[str] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0


def _title(html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html[:6000])
    return re.sub(r"\s+", " ", match.group(1)).strip()[:70] if match else ""


def fetch(url: str, *, max_redirects: int = 4) -> Probe:
    """只 GET，跟 3~4 跳重定向，读前 32KB。不登录、不提交表单。"""
    probe = Probe(url=url)
    started = time.time()
    current = url
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE          # 学校自签证书很常见
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            probe.error = "地址不合法"
            break
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            if parsed.scheme == "https":
                conn = http.client.HTTPSConnection(parsed.hostname, port, timeout=TIMEOUT, context=context)
            else:
                conn = http.client.HTTPConnection(parsed.hostname, port, timeout=TIMEOUT)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                # 有些网关按 Accept-Language 决定跳哪个认证页
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            response = conn.getresponse()
            body = response.read(32768)
            probe.status = response.status
            location = response.getheader("Location") or ""
            probe.final_url = current
            conn.close()
        except socket.gaierror as error:
            probe.error = f"域名解析不了（{error}）"
            break
        except (socket.timeout, TimeoutError):
            probe.error = "连接超时"
            break
        except ssl.SSLError as error:
            probe.error = f"TLS 握手失败（{error}）"
            break
        except (ConnectionRefusedError, OSError) as error:
            probe.error = f"{type(error).__name__}: {error}"
            break
        if response.status in (301, 302, 303, 307, 308) and location:
            current = _absolute(current, location)
            continue
        text = body.decode("utf-8", errors="replace")
        if "charset=gb" in text[:2000].lower():
            text = body.decode("gb18030", errors="replace")
        probe.title = _title(text)
        for needle, label in SIGNATURES:
            if needle.lower() in text.lower():
                probe.hints.append(label)
        break
    probe.elapsed = round(time.time() - started, 1)
    return probe


def _absolute(base: str, location: str) -> str:
    if location.startswith("http://") or location.startswith("https://"):
        return location
    parsed = urlparse(base)
    if location.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{location}"
    return f"{parsed.scheme}://{parsed.netloc}/{location}"


def main() -> int:
    targets: dict[str, dict[str, list[str]]] = CANDIDATES
    if len(sys.argv) > 1:
        targets = {"命令行给的网址": {"base": list(sys.argv[1:]), "jw": []}}

    reachable = 0
    total = 0
    for school, groups in targets.items():
        print(f"\n=== {school} ===")
        for kind, urls in groups.items():
            for url in urls:
                total += 1
                probe = fetch(url)
                if probe.status and not probe.error:
                    reachable += 1
                    flags = "、".join(probe.hints) or "（没认出特征词）"
                    print(f"  [{kind}] {url}")
                    print(f"        HTTP {probe.status}  {probe.elapsed}s  → {probe.final_url}")
                    print(f"        标题: {probe.title or '(空)'}")
                    print(f"        特征: {flags}")
                else:
                    print(f"  [{kind}] {url}")
                    print(f"        不可达：{probe.error or '未知错误'}")
    print(f"\n可达 {reachable}/{total} 个入口。")
    print("说明：只做了 GET 探测，没有登录、没有提交任何账号密码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
