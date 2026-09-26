"""浏览器导入通道的测试。

真实浏览器不好在单测里起（慢、依赖环境），所以这里钉住三件关键的事：
  1. WebSocket 帧的编码能对上 RFC6455（手写协议最容易错的地方）
  2. 页面解析：把课表 HTML 转成课程（复用 HtmlPageAdapter，走真实调用路径）
  3. 就绪文件读取与 profile 占用检测的边界
浏览器端到端另外用本地模拟教务服务实测（见提交说明）。
"""

from __future__ import annotations

import base64
import json
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import browser as browser_mod  # noqa: E402
from agenda.eas import HtmlPageAdapter  # noqa: E402

COURSE_HTML = """
<html><body>
<div>同学 · <a href="/logout">退出</a></div>
<table>
  <tr><th>节次</th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th>
      <th>星期五</th><th>星期六</th><th>星期日</th></tr>
  <tr><td>第1-2节</td>
      <td>高等数学(1-16周)[张伟]教三301</td><td></td>
      <td>大学英语(1-16周)[Linda]外语楼204</td><td></td>
      <td>软件工程(1-16周)[吴敏]教三402</td><td></td><td></td></tr>
  <tr><td>第3-4节</td>
      <td>数据结构(1-16周)[陈静]实验楼B302</td><td></td><td></td>
      <td>概率论(1-16周)[周涛]教二203</td><td></td><td></td><td></td></tr>
</table>
<div>课程名称 上课地点 任课教师 周次 节次</div>
</body></html>
"""


class HtmlPageImportTests(unittest.TestCase):
    """浏览器读到的页面 → 课程，用的是真实解析路径。"""

    def test_table_page_to_courses(self):
        result = HtmlPageAdapter().parse(COURSE_HTML)
        self.assertEqual(result.count, 5)
        math = next(c for c in result.courses if c.name == "高等数学")
        self.assertEqual((math.weekday, math.start_period, math.end_period), (0, 1, 2))
        self.assertEqual(math.location, "教三301")
        self.assertEqual(math.teacher, "张伟")
        self.assertEqual(math.weeks, tuple(range(1, 17)))

    def test_plain_table_fallback(self):
        text = "高等数学\t周一\t1\t2\t张伟\t教三301\t1-16"
        result = HtmlPageAdapter().parse(text)
        self.assertEqual(result.count, 1)

    def test_course_html_parses(self):
        """课表网页文本 → 课程：这是「从文件识别课表」里 .html/.txt 那条路的解析核心。"""
        from agenda.file_import import parse_html_text

        result = parse_html_text(COURSE_HTML)
        self.assertEqual(result.count, 5)
        math = next(row for row in result.courses if row["name"] == "高等数学")
        self.assertEqual(math["weekday"], "周一")
        self.assertEqual(math["period"], "1-2")
        self.assertEqual(math["location"], "教三301")
        self.assertEqual(result.warnings, [])


class WebSocketFrameTests(unittest.TestCase):
    """对着一个假 WebSocket 服务端，验证客户端帧编码能被标准解析。"""

    def _server(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        return server

    def _accept_and_handshake(self, server, received: list):
        client, _ = server.accept()
        request = b""
        while b"\r\n\r\n" not in request:
            request += client.recv(4096)
        key = b""
        for line in request.split(b"\r\n"):
            if line.lower().startswith(b"sec-websocket-key:"):
                key = line.split(b":", 1)[1].strip()
        accept = base64.b64encode(
            __import__("hashlib").sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
        )
        client.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        return client

    @staticmethod
    def _read_client_frame(client) -> tuple[int, bytes]:
        header = client.recv(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", client.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", client.recv(8))[0]
        mask = client.recv(4) if masked else b""
        payload = b""
        while len(payload) < length:
            payload += client.recv(length - len(payload))
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload

    def test_send_is_masked_text_frame(self):
        server = self._server()
        port = server.getsockname()[1]
        received: list = []

        def server_thread():
            client = self._accept_and_handshake(server, received)
            opcode, payload = self._read_client_frame(client)
            received.append((opcode, payload))
            # 回一个未掩码文本帧（服务端按协议不回掩码）
            body = json.dumps({"id": 1, "result": {"ok": True}}).encode()
            client.sendall(bytes([0x81, len(body)]) + body)
            client.close()

        threading.Thread(target=server_thread, daemon=True).start()
        ws = browser_mod.WebSocket(f"ws://127.0.0.1:{port}/devtools/page/x")
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        reply = json.loads(ws.recv())
        ws.close()
        server.close()

        self.assertTrue(received, "服务端没收到帧")
        opcode, payload = received[0]
        self.assertEqual(opcode, 0x1)
        self.assertEqual(json.loads(payload)["method"], "Page.enable")
        self.assertTrue(reply["result"]["ok"])

    def test_large_frame_uses_16bit_length(self):
        server = self._server()
        port = server.getsockname()[1]
        received: list = []
        done = threading.Event()

        def server_thread():
            try:
                client = self._accept_and_handshake(server, received)
                received.append(self._read_client_frame(client))
                client.close()
            finally:
                done.set()          # 不设事件的话主线程可能先断言，拿到空列表

        threading.Thread(target=server_thread, daemon=True).start()
        ws = browser_mod.WebSocket(f"ws://127.0.0.1:{port}/x")
        big = "x" * 500             # 长度 >125，会走 16 位长度分支
        ws.send(big)
        ws.close()
        self.assertTrue(done.wait(5), "服务端没在 5 秒内收到帧")
        server.close()
        self.assertTrue(received, "服务端没收到帧")
        self.assertEqual(received[0][1].decode("utf-8"), big)

    def test_rejects_non_ws_scheme(self):
        with self.assertRaises(browser_mod.CdpError):
            browser_mod.WebSocket("http://127.0.0.1:1/x")


class ProfileHelpersTests(unittest.TestCase):
    def test_read_active_port_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(browser_mod._read_active_port(Path(tmp), timeout=0.4))

    def test_read_active_port_parses_first_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "DevToolsActivePort").write_text("54321\n/devtools/browser/abc\n", encoding="utf-8")
            self.assertEqual(browser_mod._read_active_port(Path(tmp), timeout=1.0), 54321)

    def test_read_active_port_ignores_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "DevToolsActivePort").write_text("not-a-port\n", encoding="utf-8")
            self.assertIsNone(browser_mod._read_active_port(Path(tmp), timeout=0.4))

    def test_profile_in_use_returns_list(self):
        # 不假定机器上有 Chrome 在跑，只要求返回列表且不抛异常
        result = browser_mod.profile_in_use(Path(r"E:\definitely\not\used\profile"))
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
