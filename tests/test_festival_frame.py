"""节日边框：四条边都要看得见。

用户拿一张只有右边框缺失的截图问「右边框呢？」—— 根因是右侧那个 7px 的
**拖拽把手**（不透明的 Frame）贴在窗口最外层，把右边框整条盖掉了：节日时
左/上/下三边都是 6px 节日色，右边却是面板底色。修法是让把手按**当前边框宽度**
往内让开，并在节日开始/结束时重新让位。

这里用**逐像素**的方式量四条边的宽度和颜色 —— "看起来差不多"抓不到这种问题。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import theme  # noqa: E402

if sys.platform == "win32":
    from tools.capture_tutorial import Shot  # noqa: E402
else:
    Shot = None  # type: ignore[assignment]


def _run_length(pixels: list[str], target: str) -> int:
    """从头开始数，连续等于目标色的像素个数。"""
    count = 0
    for value in pixels:
        if value.upper() == target.upper():
            count += 1
        else:
            break
    return count


class FestivalBorderTests(unittest.TestCase):
    def setUp(self):
        if Shot is None:
            raise unittest.SkipTest("GDI 截图只在 Windows 上有")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _panel(self, width: int = 340):
        try:
            from agenda.panel import AgendaPanel

            panel = AgendaPanel(Path(self.tmp.name), width=width, pipeline_ms=0,
                                autostart_pipeline=False, hide_past=False,
                                window_mode="desktop", position=(70, 70))
        except Exception as error:                     # 无图形环境
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.addCleanup(panel.quit)
        panel.root.update()
        return panel

    def _settle(self, panel, rounds: int = 18) -> None:
        """把事件循环转够久：`_init_desktop_layer` 是 400ms 后跑的，不跑完 `_hwnd` 还是 0
        （那时截图工具抓到的是全黑，测试会以"四条边都是 #000000"的形式失败）。"""
        import time

        for _ in range(rounds):
            panel.root.update()
            time.sleep(0.04)
        panel.root.update()

    def _edges(self, panel) -> dict[str, list[str]]:
        """取窗口四条边从外往内的颜色序列。"""
        panel.root.update()
        shot = Shot.of_window(panel._hwnd)
        picture = shot.picture()
        width, height = picture.width, picture.height

        def pixel(x: int, y: int) -> str:
            offset = y * picture.stride + x * 4
            blue, green, red = (picture.pixels[offset], picture.pixels[offset + 1],
                                picture.pixels[offset + 2])
            return f"#{red:02X}{green:02X}{blue:02X}"

        mid_y, mid_x = height // 2, width // 2
        return {
            "left": [pixel(x, mid_y) for x in range(width)],
            "right": [pixel(x, mid_y) for x in range(width - 1, -1, -1)],
            "top": [pixel(mid_x, y) for y in range(height)],
            "bottom": [pixel(mid_x, y) for y in range(height - 1, -1, -1)],
        }

    def test_all_four_edges_show_the_festival_frame(self):
        """四条边都得是节日色、且宽度够。

        判据写成"四条边最外面那圈颜色一致、且都不是面板底色"：边框会呼吸变色
        （`_festival_frame_tick` 每帧混一次色），写死某个色值会随机失败。
        """
        panel = self._panel()
        panel._apply_festival_frame(SimpleNamespace(accent="#FF0000"))
        self._settle(panel)
        edges = self._edges(panel)
        outermost = {side: pixels[0] for side, pixels in edges.items()}
        self.assertEqual(len(set(outermost.values())), 1,
                         f"四条边颜色不一致：{outermost}")
        self.assertNotEqual(outermost["left"], theme.COLORS["bg"],
                            "边框没上色")
        for side, pixels in edges.items():
            with self.subTest(side=side):
                self.assertGreaterEqual(_run_length(pixels, pixels[0]),
                                        panel.FESTIVAL_FRAME_PAD - 1,
                                        f"{side} 边的节日边框太窄：{pixels[:10]}")

    def test_the_right_border_is_not_covered_by_the_resize_grip(self):
        """右边框曾经被 7px 的拖拽把手整条盖掉 —— 这条就是那时留下的。"""
        panel = self._panel()
        panel._apply_festival_frame(SimpleNamespace(accent="#00FF00"))
        self._settle(panel)
        grip = panel._resize_edge
        self.assertGreaterEqual(grip.winfo_x(), panel.FESTIVAL_FRAME_PAD,
                                "拖拽把手又压到右边框上了")
        pixels = self._edges(panel)["right"]
        self.assertNotEqual(pixels[0], theme.COLORS["bg"],
                            "右边框被盖住了（用户截图问的就是这个）")

    def test_the_grip_lets_the_border_through_again_after_the_festival(self):
        """节日结束、边框变回 1px 时，把手也要跟着回位。"""
        panel = self._panel()
        panel._apply_festival_frame(SimpleNamespace(accent="#FF0000"))
        self._settle(panel, rounds=6)
        during = panel._resize_edge.winfo_x()
        panel._apply_festival_frame(None)
        self._settle(panel, rounds=6)
        after = panel._resize_edge.winfo_x()
        self.assertGreater(after, during,
                           "节日结束后把手没有往外让回去（右边框会缺一大块）")
        self.assertEqual(panel.outer.cget("bg"), theme.COLORS["border"],
                         "节日结束后边框色没恢复")

    def test_the_grip_is_still_wide_enough_to_grab(self):
        panel = self._panel()
        panel._apply_festival_frame(SimpleNamespace(accent="#FF0000"))
        self._settle(panel, rounds=6)
        self.assertGreaterEqual(panel._resize_edge.winfo_width(), 4,
                                "把手被让得太窄，拖不动了")


if __name__ == "__main__":
    unittest.main()
