"""自绘滚动条 + 标题区背景跟随尺寸。

对应两个真实反馈：
  * 「一旦拉伸这个日程表，他这个颜色不会自动扩展」—— 渐变/照片是**按生成那一刻的
    宽度**画出来的一张图，拉宽后右边露出一条没画到的平色带；
  * 「为什么那个滚动条没有被边框覆盖？」—— 原来用的是 Windows 原生 `tk.Scrollbar`，
    它由系统绘制、**不吃颜色**（实测 bg 红/槽绿还是画成系统灰），深色主题上是一条
    突兀的亮灰条。现在改成 Canvas 自绘的细滑块，颜色跟主题走。
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from agenda import palettes, theme


class _PanelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        today = date.today()
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": (today - timedelta(days=7)).strftime("%Y-%m-%d"),
            "periods": [["08:00", "08:45"], ["08:55", "09:40"]],
            "courses": [],
        }, ensure_ascii=False), encoding="utf-8")
        (self.data / "events.json").write_text(json.dumps({
            "schemaVersion": 1,
            "events": [{"id": f"e{n}", "title": f"事情 {n}", "date": (
                today + timedelta(days=n // 6)).strftime("%Y-%m-%d"),
                "start": f"{8 + n % 10:02d}:00", "notes": "备注",
                "group": "群", "tentative": False} for n in range(24)],
        }, ensure_ascii=False), encoding="utf-8")

    def _panel(self, width: int = 340):
        try:
            from agenda.panel import AgendaPanel

            panel = AgendaPanel(self.data, width=width, pipeline_ms=0,
                                autostart_pipeline=False, hide_past=False,
                                window_mode="desktop", position=(80, 80))
        except Exception as error:                     # 无图形环境
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.addCleanup(panel.quit)
        panel.root.update()
        return panel

    def _settle(self, panel, rounds: int = 14) -> None:
        for _ in range(rounds):
            panel.root.update()
            time.sleep(0.04)
        panel.root.update()


class CustomScrollbarTests(_PanelCase):
    def test_it_is_our_own_canvas_not_the_native_scrollbar(self):
        import tkinter as tk

        panel = self._panel()
        self._settle(panel, rounds=6)
        self.assertIsInstance(panel.scrollbar, tk.Canvas)
        self.assertTrue(panel.scrollbar.winfo_ismapped(), "滚动条没挂上")
        self.assertGreaterEqual(panel.scrollbar.winfo_width(), 4, "滚动条太窄，看不见")

    def test_no_thumb_when_everything_fits(self):
        """内容全看得见时不画滑块（原生那会儿是一整条灰槽，白占地方）。"""
        panel = self._panel()
        panel._scroll_info = (0.0, 1.0)
        panel._draw_scrollbar()
        self.assertIsNone(panel.scrollbar_thumb)
        self.assertEqual(panel.scrollbar.find_withtag("thumb"), ())

    def test_a_thumb_appears_when_there_is_more_below(self):
        panel = self._panel()
        self._settle(panel, rounds=6)
        panel._scroll_info = (0.0, 0.4)
        panel._draw_scrollbar()
        self.assertIsNotNone(panel.scrollbar_thumb, "有内容溢出却没画滑块")
        box = panel.scrollbar.bbox("thumb")
        self.assertIsNotNone(box, "滑块画出来了但看不见")
        self.assertGreater(box[3] - box[1], 4, "滑块太薄")

    def test_the_thumb_follows_the_scroll_position(self):
        panel = self._panel()
        self._settle(panel, rounds=6)
        panel._scroll_info = (0.0, 0.4)
        panel._draw_scrollbar()
        top_first = panel.scrollbar.bbox("thumb")[1]
        panel._scroll_info = (0.6, 1.0)
        panel._draw_scrollbar()
        top_later = panel.scrollbar.bbox("thumb")[1]
        self.assertGreater(top_later, top_first, "滚到下面了滑块却没往下走")

    def test_dragging_the_thumb_scrolls_the_timeline(self):
        panel = self._panel()
        self._settle(panel, rounds=6)
        moves: list[float] = []
        panel.canvas.yview_moveto = moves.append          # 替身：只记比例
        panel._scroll_info = (0.0, 0.5)
        panel._scroll_drag_offset = 0
        panel._scrollbar_to(int(panel.scrollbar.winfo_height() * 0.5))
        self.assertEqual(len(moves), 1)
        self.assertAlmostEqual(moves[0], 0.5, delta=0.02)

    def test_it_follows_the_theme(self):
        panel = self._panel()
        self._settle(panel, rounds=6)
        theme.apply_palette(palettes.DAY)
        panel._theme_state = {"mode": "day", "palette": palettes.DAY, "picture": None,
                              "note": "", "gradient": None}
        panel._recolor_widgets()
        self.assertEqual(panel.scrollbar.cget("bg"), palettes.DAY["bg"],
                         "自绘滚动条的底色没跟着主题走")


class HeaderBackdropResizeTests(_PanelCase):
    def _auto_panel(self, width: int = 340):
        """造一个「随时刻」面板（标题区是渐变，最容易被看见"没铺满"）。"""
        import agenda.palettes as palettes_mod

        real = palettes_mod.slot_for
        palettes_mod.slot_for = lambda moment=None: "day"
        self.addCleanup(lambda: setattr(palettes_mod, "slot_for", real))
        # 配置里必须写明 theme_mode=auto：不写的话默认是经典深色，标题区根本不画渐变
        from agenda.client_config import ClientConfig

        config = ClientConfig.load(self.data)
        config.theme_mode = "auto"
        config.save(self.data)
        panel = self._panel(width=width)
        panel._apply_theme(force=True)
        self._settle(panel)
        return panel

    def test_the_gradient_covers_the_whole_header(self):
        panel = self._auto_panel()
        header_width = panel.header.winfo_width()
        photo = getattr(panel, "_header_photo", None)
        self.assertIsNotNone(photo, "随时刻模式没画标题区渐变")
        self.assertGreaterEqual(photo.width(), header_width - 2,
                                "渐变图比标题区窄，右边会露出一条平色带")

    def test_stretching_repaints_the_backdrop(self):
        """**用户报的"拉伸后颜色不自动扩展"就是这条。**"""
        panel = self._auto_panel()
        before = panel._header_photo.width()
        panel.width = 520
        panel.root.geometry(f"520x{panel.root.winfo_height()}")
        self._settle(panel)
        after = panel._header_photo.width()
        self.assertGreater(after, before, "拉宽之后渐变图没重画")
        self.assertGreaterEqual(after, panel.header.winfo_width() - 2,
                                "重画了但还是没铺满标题区")
        self.assertEqual(panel._header_backdrop_width, panel.header.winfo_width(),
                         "记下的宽度和标题区实际宽度对不上")

    def test_a_narrower_panel_repaints_too(self):
        panel = self._auto_panel(width=520)
        panel.width = 340
        panel.root.geometry(f"340x{panel.root.winfo_height()}")
        self._settle(panel)
        self.assertLessEqual(panel._header_photo.width(),
                             panel.header.winfo_width() + 2,
                             "变窄之后渐变图还是老宽度（会盖住右边框那一圈）")


if __name__ == "__main__":
    unittest.main()
