"""极简 Chrome DevTools Protocol 客户端（纯标准库）。

为什么自己写：项目坚持零第三方运行时依赖（开源出去别人 clone 就能跑），
而 CDP 需要的只是"HTTP 拿一下 WebSocket 地址 + WebSocket 发 JSON"，
用标准库 socket + 手写帧协议足够，不必引入 websocket-client。

用途：驱动一个真实的 Chrome 窗口去登录学校统一身份认证/WebVPN——
页面里的 RSA 加密、验证码、SSO 跳转全由浏览器自己处理，
程序不接触账号密码，只读取登录之后的页面内容。
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _http_json(port: int, path: str, *, timeout: float = 3.0):
    """直接走 http.client 读调试端口，绕开系统代理。

    为什么不用 urllib：本机装了 Watt Toolkit 之类会接管本地 HTTP 的加速工具时，
    urllib 尊重系统代理，请求会被劫持（实测随机端口返回 404），
    而 http.client 直连 127.0.0.1 不受影响。
    """
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read().decode("utf-8", "replace")
        if response.status != 200:
            raise CdpError(f"调试端口返回 {response.status}")
        return json.loads(body)
    finally:
        connection.close()


def _read_active_port(profile_dir: Path, timeout: float = 25.0) -> int | None:
    """读取 DevToolsActivePort 文件拿到 Chrome 真正监听的端口。

    直接用 --remote-debugging-port=<固定值> 有风险：本机若装了会占用本地端口的
    加速工具（Watt Toolkit 等），端口会被抢先占用。用 0 让 Chrome 自己挑。
    """
    marker = Path(profile_dir) / "DevToolsActivePort"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker.exists():
            try:
                first = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
                if first.isdigit():
                    return int(first)
            except (OSError, IndexError):
                pass
        time.sleep(0.2)
    return None


def _is_chrome_devtools(port: int) -> bool:
    try:
        payload = _http_json(port, "/json/version")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(payload, dict) and "Browser" in payload


class CdpError(RuntimeError):
    pass


def find_browser() -> str | None:
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def profile_in_use(profile_dir: Path) -> list[int]:
    """找出还在用这个 profile 目录的浏览器进程。

    Chrome 会独占 user-data-dir：上一个自动化窗口没关干净，下一次启动就会
    静默失败（既不写 DevToolsActivePort，也连不上调试端口）——实测踩过。
    """
    if os.name != "nt":
        return []
    try:
        import json as _json
        import subprocess as _subprocess

        completed = _subprocess.run(
            ["wmic", "process", "where", "name='chrome.exe' or name='msedge.exe'",
             "get", "ProcessId,CommandLine", "/format:json"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = _json.loads(completed.stdout or "[]")
    except Exception:  # noqa: BLE001
        return []
    needle = str(profile_dir).lower()
    pids: list[int] = []
    for item in payload if isinstance(payload, list) else []:
        command = str(item.get("CommandLine") or "").lower()
        if needle in command:
            try:
                pids.append(int(item.get("ProcessId")))
            except (TypeError, ValueError):
                continue
    return pids


def kill_processes(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, 9)
        except OSError:
            try:
                import subprocess as _subprocess

                _subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# WebSocket（RFC6455 客户端，够用即可：文本帧、掩码、分片重组）
# ---------------------------------------------------------------------------

class WebSocket:
    def __init__(self, url: str, *, timeout: float = 30.0):
        if not url.startswith("ws://"):
            raise CdpError(f"只支持 ws:// 直连（本地调试端口），收到：{url}")
        rest = url[len("ws://"):]
        host_port, _, path = rest.partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host or "127.0.0.1", int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        response = self._read_until(b"\r\n\r\n")
        if b"101" not in response.split(b"\r\n")[0]:
            raise CdpError(f"WebSocket 握手失败：{response.split(chr(13).encode())[0]!r}")
        self._buffer = b""

    def _read_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def send(self, payload: str) -> None:
        data = payload.encode("utf-8")
        header = bytearray([0x81])                     # FIN + text
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        while True:
            frame = self._read_frame()
            if frame is None:
                raise CdpError("WebSocket 连接已关闭")
            opcode, payload = frame
            if opcode == 0x8:
                raise CdpError("WebSocket 被对端关闭")
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", errors="replace")
            # ping/pong/continuation 都不关心，继续读

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise CdpError("WebSocket 读超时/断开")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _read_frame(self):
        header = self._read_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CDP 会话
# ---------------------------------------------------------------------------

@dataclass
class ChromeSession:
    """一次浏览器会话：启动 Chrome、连上调试端口、操作页面。"""

    profile_dir: Path
    browser_path: str | None = None
    port: int = 0
    process: subprocess.Popen | None = None
    ws: WebSocket | None = None
    target_id: str | None = None
    _message_id: int = 0
    events: list[dict] = field(default_factory=list)

    # -- 启动 / 关闭 -----------------------------------------------------
    def start(self, url: str = "about:blank", *, window_size: tuple[int, int] = (1180, 820)) -> None:
        browser = self.browser_path or find_browser()
        if not browser:
            raise CdpError("没找到 Chrome / Edge，请先安装其一，或在设置里指定浏览器路径")
        self.browser_path = browser
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        marker = self.profile_dir / "DevToolsActivePort"
        try:
            marker.unlink()
        except OSError:
            pass
        width, height = window_size
        # 注意：不要加 --new-window！它会让 Chrome 走"复用现有实例"的分支，
        # 结果调试端口被忽略（实测 DevToolsActivePort 不生成）。
        common = [
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,OptimizationHints",
            f"--window-size={width},{height}",
        ]
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        # 首选 0：让浏览器自己挑空闲端口，避免与本机端口占用类工具（Watt Toolkit 等）撞车
        self.process = subprocess.Popen(
            [browser, "--remote-debugging-port=0", *common, url], creationflags=creation,
        )
        port = _read_active_port(self.profile_dir, timeout=18)
        if port is not None and _is_chrome_devtools(port):
            self.port = port
            return

        # 退路：固定端口重试（少数环境下 0 不写 DevToolsActivePort）
        self._stop_process()
        for candidate in (47113, 47115, 47117, 47119, 47121):
            self.process = subprocess.Popen(
                [browser, f"--remote-debugging-port={candidate}", *common, url], creationflags=creation,
            )
            deadline = time.time() + 6
            while time.time() < deadline:
                if self.process.poll() is not None:
                    break
                if _is_chrome_devtools(candidate):
                    self.port = candidate
                    return
                time.sleep(0.3)
            self._stop_process()
        raise CdpError(
            "浏览器调试端口没有就绪。常见原因：\n"
            "  · 已经有 Chrome 在用这个 profile 目录（请先关掉自动化窗口）\n"
            "  · 本机安全软件 / 端口加速工具拦截了本地调试端口\n"
            "请关掉多余的 Chrome 窗口后重试；也可以改用左侧『粘贴课表网页』通道。"
        )

    def _stop_process(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
            except Exception:  # noqa: BLE001
                pass
            self.process = None

    def wait_for_devtools(self, timeout: float = 30.0) -> None:
        """兼容旧调用：端口已在 start() 里就绪。"""
        if self.port and _is_chrome_devtools(self.port):
            return
        port = _read_active_port(self.profile_dir, timeout=timeout)
        if port is None:
            raise CdpError("浏览器调试端口没有就绪（请关掉其他 Chrome 窗口后重试）")
        self.port = port

    def attach(self, *, timeout: float = 15.0) -> None:
        """连到第一个 page 目标。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            targets = self._list_targets()
            page = next((t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
            if page is not None:
                self.target_id = page.get("id")
                self.ws = WebSocket(page["webSocketDebuggerUrl"])
                self.call("Page.enable")
                self.call("Runtime.enable")
                return
            time.sleep(0.3)
        raise CdpError("连不上浏览器页面目标")

    def _list_targets(self) -> list[dict]:
        try:
            payload = _http_json(self.port, "/json/list")
            return payload if isinstance(payload, list) else []
        except Exception:  # noqa: BLE001
            return []

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
        if self.process is not None:
            try:
                self.call("Browser.close") if self.ws else None
            except Exception:
                pass
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None

    # -- CDP 调用 --------------------------------------------------------
    def call(self, method: str, params: dict | None = None, *, timeout: float = 30.0) -> dict:
        if self.ws is None:
            raise CdpError("尚未连接浏览器")
        self._message_id += 1
        message_id = self._message_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            payload = json.loads(self.ws.recv())
            if payload.get("id") == message_id:
                if "error" in payload:
                    raise CdpError(f"{method} 失败：{payload['error']}")
                return payload.get("result", {})
            if "method" in payload:
                self.events.append(payload)
        raise CdpError(f"{method} 超时（{timeout}s）")

    # -- 页面操作 --------------------------------------------------------
    def navigate(self, url: str, *, wait: float = 0.0) -> None:
        self.call("Page.navigate", {"url": url})
        self.wait_for_ready(wait=wait)

    def wait_for_ready(self, *, timeout: float = 25.0, wait: float = 0.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self.evaluate("document.readyState")
            except CdpError:
                time.sleep(0.3)
                continue
            if state == "complete":
                break
            time.sleep(0.25)
        if wait:
            time.sleep(wait)

    def evaluate(self, expression: str, *, timeout: float = 20.0):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, timeout=timeout)
        if result.get("exceptionDetails"):
            raise CdpError(str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    def current_url(self) -> str:
        return str(self.evaluate("location.href") or "")

    def frames(self) -> list[dict]:
        """列出页面里所有 frame（含 iframe）。

        为什么重要：正方等教务的课表页常把内容放在 iframe 里，
        只读主文档会看到"空壳页面"——于是检测不到课表、自动读取不触发。
        """
        try:
            tree = self.call("Page.getFrameTree")
        except CdpError:
            return []
        flat: list[dict] = []

        def walk(node: dict) -> None:
            frame = node.get("frame") or {}
            flat.append(frame)
            for child in node.get("childFrames") or []:
                walk(child)

        walk(tree.get("frameTree") or {})
        return flat

    def evaluate_all_frames(self, expression: str, *, timeout: float = 12.0) -> list[dict]:
        """在每个 frame 里跑同一段 JS，返回 [{frameId, url, value}]。

        跨域受限或已销毁的 frame 会静默跳过。
        """
        results: list[dict] = []
        for frame in self.frames():
            frame_id = frame.get("id")
            if not frame_id:
                continue
            try:
                world = self.call("Page.createIsolatedWorld", {"frameId": frame_id}, timeout=timeout)
            except CdpError:
                continue
            context_id = world.get("executionContextId")
            if not context_id:
                continue
            try:
                payload = self.call("Runtime.evaluate", {
                    "expression": expression,
                    "contextId": context_id,
                    "returnByValue": True,
                    "awaitPromise": True,
                }, timeout=timeout)
            except CdpError:
                continue
            results.append({
                "frameId": frame_id,
                "url": frame.get("url", ""),
                "value": payload.get("result", {}).get("value"),
            })
        return results

    def frame_reports(self) -> list[dict]:
        """逐 frame 体检：文本长度、表格数、命中哪些课表特征词、开头片段。"""
        script = """
        (() => {
          const text = document.body ? document.body.innerText : '';
          const tables = document.querySelectorAll('table').length;
          const hints = ['课程名称','上课地点','任课教师','节次','星期','周次','课表','教学班','周一','星期一'];
          const hit = hints.filter(h => text.includes(h));
          return { textLength: text.length, tables, hints: hit,
                   sample: text.replace(/\\s+/g, ' ').slice(0, 120) };
        })()
        """
        reports: list[dict] = []
        for item in self.evaluate_all_frames(script):
            value = item.get("value")
            if isinstance(value, dict):
                reports.append({**item, **value})
            else:
                reports.append({**item, "textLength": 0, "tables": 0, "hints": [], "sample": ""})
        return reports

    def tables_text_all_frames(self) -> str:
        """把所有 frame 里所有 table 抽成制表符文本（跨 iframe 合并）。"""
        script = """
        (() => {
          const rows = [];
          document.querySelectorAll('table').forEach(t => {
            t.querySelectorAll('tr').forEach(tr => {
              const cells = Array.from(tr.querySelectorAll('th,td'))
                .map(td => (td.innerText || '').replace(/\\s+/g, ' ').trim());
              if (cells.some(c => c)) rows.push(cells.join('\\t'));
            });
          });
          return rows.join('\\n');
        })()
        """
        chunks: list[str] = []
        for item in self.evaluate_all_frames(script):
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                chunks.append(value)
        return "\n".join(chunks)

    def html_all_frames(self) -> str:
        """把所有 frame 的 HTML 拼起来（主文档优先），统一走 HTML 解析。"""
        parts: list[str] = []
        try:
            parts.append(self.page_html())
        except CdpError:
            pass
        for item in self.evaluate_all_frames("document.documentElement.outerHTML"):
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                parts.append(value)
        return "\n<!-- frame -->\n".join(parts)

    def all_frames_text(self) -> str:
        chunks: list[str] = []
        try:
            chunks.append(self.page_text())
        except CdpError:
            pass
        for item in self.evaluate_all_frames("document.body ? document.body.innerText : ''"):
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                chunks.append(value)
        return "\n".join(chunks)

    def page_html(self) -> str:
        return str(self.evaluate("document.documentElement.outerHTML") or "")

    def page_text(self) -> str:
        return str(self.evaluate("document.body ? document.body.innerText : ''") or "")

    def hide_automation_banner(self) -> None:
        """关掉 Chrome 的"正在被自动化"提示条（尽量，失败无所谓）。"""
        try:
            self.evaluate(
                "(() => { const b=document.querySelector('#automation-banner'); if (b) b.remove(); return 1; })()"
            )
        except CdpError:
            pass


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
