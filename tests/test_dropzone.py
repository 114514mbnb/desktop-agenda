"""拖放接收的测试。

真实的"从 QQ 拖到面板上"没法在单测里做（要么真人拖，要么造 OLE 数据对象），
所以这里测两件能确定的事：
  1. 接收窗口能建起来、消息泵能用（Windows 上）；
  2. `WM_DROPFILES` 里的 HDROP 能被正确拆成路径列表——用一个手工拼出来的
     DROPFILES 内存块喂进去，`DragQueryFileW` 是真解析（不是 mock）。
另外测落点分派：文本文件走通知解析、`.ics` 走课表识别。
"""

from __future__ import annotations

import ctypes
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import dropzone  # noqa: E402


def build_hdrop(paths: list[str]) -> tuple[ctypes.Array, int]:
    """手工拼一个 DROPFILES 结构 + 双 null 结尾的宽字符文件名列表。

    结构和 `shell32.DragQueryFileW` 期望的完全一致（20 字节头 + UTF-16 路径串），
    所以这是对真解析路径的测试，而不是打桩。
    """
    header_size = 20                      # DROPFILES: pFiles/fPoint/fNC/fWide 共 20 字节
    payload = "".join(f"{name}\0" for name in paths) + "\0"
    encoded = payload.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(header_size + len(encoded))
    # pFiles=20, pt=(0,0), fNC=0, fWide=1
    struct.pack_into("<IiiII", buffer, 0, header_size, 0, 0, 0, 1)
    buffer[header_size:header_size + len(encoded)] = encoded
    return buffer, ctypes.cast(buffer, ctypes.c_void_p).value


@unittest.skipUnless(sys.platform == "win32", "拖放是 Windows 特性")
class DropTargetTests(unittest.TestCase):
    def test_window_creation_and_pump(self):
        target = dropzone.DropTarget(lambda paths: None)
        try:
            self.assertTrue(target.create(), f"建不了接收窗口：{target.error}")
            self.assertTrue(target.hwnd)
            # 消息泵只要能安全跑就行（创建/销毁期间可能有零星系统消息）
            self.assertGreaterEqual(target.pump(), 0)
        finally:
            target.destroy()

    def test_pump_is_safe_without_a_window(self):
        target = dropzone.DropTarget(lambda paths: None)
        self.assertEqual(target.pump(), 0)
        target.destroy()

    def test_hdrop_parses_into_paths(self):
        """手工构造的 HDROP 要能被真的 DragQueryFileW 拆开。"""
        received: list[list[str]] = []
        target = dropzone.DropTarget(lambda paths: received.append(paths))
        try:
            if not target.create():
                self.skipTest(f"建不了接收窗口：{target.error}")
            wanted = [r"C:\tmp\群消息.txt", r"C:\tmp\课表.ics"]
            buffer, pointer = build_hdrop(wanted)
            target._handle_drop(pointer)                # 直接喂 HDROP
            del buffer
        finally:
            target.destroy()
        self.assertEqual(received, [wanted])

    def test_empty_hdrop_is_ignored(self):
        received: list[list[str]] = []
        target = dropzone.DropTarget(lambda paths: received.append(paths))
        try:
            if not target.create():
                self.skipTest(f"建不了接收窗口：{target.error}")
            buffer, pointer = build_hdrop([])
            target._handle_drop(pointer)
            del buffer
        finally:
            target.destroy()
        self.assertEqual(received, [])

    def test_read_text_file_handles_gbk(self):
        """QQ 导出的消息常是 GBK，读取不能变成乱码。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "群消息.txt"
            path.write_bytes("[实验室群]\n9月23日 15:00 在实验楼B302 开组会\n".encode("gbk"))
            text = dropzone.read_text_file(path)
        self.assertIn("实验室群", text)
        self.assertIn("实验楼B302", text)


class DropDispatchTests(unittest.TestCase):
    """落点分派：文本 → 通知解析，.ics → 课表识别（用真文件、真解析）。"""

    def test_text_file_is_ingested_as_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            from agenda.pipeline import Pipeline

            data = Path(tmp)
            pipeline = Pipeline(data)
            source = data / "群消息.txt"
            source.write_text(
                "[计科2301班群]\n通知：9月30日 14:00-15:30 在3号楼201开会，负责人：张伟老师\n",
                encoding="utf-8",
            )
            report = pipeline.ingest_text(
                dropzone.read_text_file(source), group=None, source_label="拖入",
            )
            self.assertEqual(report.added, 1)
            self.assertIn("开会", report.events[0].title)

    def test_ics_file_routes_to_course_import(self):
        from agenda.file_import import import_path

        ics = Path(__file__).resolve().parent / "fixtures" / "wakeup-sample.ics"
        outcome = import_path(ics)
        self.assertGreater(outcome.count, 0)
        self.assertTrue(outcome.term_start)


if __name__ == "__main__":
    unittest.main(verbosity=2)
