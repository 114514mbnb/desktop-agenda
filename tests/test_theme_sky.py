"""随时刻主题的渐变 + 换主题时**整棵控件树**都要跟着换色。

这一批用例对应三个真实反馈：
  * 「为什么会出现黑边？」—— 面板昨晚以「深夜」配色启动、早上 9 点自动切「白天」，
    右侧 7px 的拖拽把手和右下角 14×14 的缩放手柄还留着深夜的 `#0C1119`，
    贴在浅色面板上就是一根黑边（写死的重刷清单漏了它们）；
  * 「节日边框太细了」—— `FESTIVAL_FRAME_PAD` 从 3 加到 6；
  * 「主题颜色不合适，日出/日落做成渐变」—— 每套时段配色带一对 head_top/head_bottom，
    标题区画成竖向渐变。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from agenda import backdrop, palettes, theme


def _gradient_rows(picture):
    """取每一行的第一个像素颜色（用来判断是不是真的"上到下"渐变）。"""
    rows = []
    for y in range(picture.height):
        offset = y * picture.stride
        blue, green, red = picture.pixels[offset], picture.pixels[offset + 1], picture.pixels[offset + 2]
        rows.append((red, green, blue))
    return rows


class GradientPictureTests(unittest.TestCase):
    """`backdrop.gradient()`：竖直线性渐变，两端必须精确落到给定色。"""

    def test_the_ends_are_exact(self):
        picture = backdrop.gradient(8, 40, "#000000", "#FFFFFF")
        rows = _gradient_rows(picture)
        self.assertEqual(rows[0], (0, 0, 0), "第一行不是顶部颜色")
        self.assertEqual(rows[-1], (255, 255, 255), "最后一行不是底部颜色")

    def test_it_is_monotonic(self):
        rows = _gradient_rows(backdrop.gradient(4, 32, "#000000", "#FFFFFF"))
        reds = [row[0] for row in rows]
        self.assertEqual(reds, sorted(reds), "渐变不是单调的（中间出现了回头）")

    def test_the_size_is_respected(self):
        picture = backdrop.gradient(7, 5, "#123456", "#654321")
        self.assertEqual((picture.width, picture.height), (7, 5))
        self.assertEqual(len(picture.pixels), 7 * 5 * 4)

    def test_a_single_pixel_height_is_harmless(self):
        picture = backdrop.gradient(3, 1, "#102030", "#405060")
        self.assertEqual(picture.height, 1)

    def test_every_row_is_a_flat_line(self):
        """整行同色：逐行铺开，不是斜的。"""
        picture = backdrop.gradient(6, 4, "#000000", "#FFFFFF")
        for y in range(4):
            row = [picture.pixels[y * picture.stride + x * 4: y * picture.stride + x * 4 + 3]
                   for x in range(6)]
            self.assertEqual(len(set(bytes(item) for item in row)), 1,
                             f"第 {y} 行不是纯色")


class TimeOfDayPaletteTests(unittest.TestCase):
    """四套时段配色 + 渐变端点。"""

    def test_every_slot_has_a_palette_and_a_sky(self):
        for name in ("morning", "day", "dusk", "night"):
            with self.subTest(slot=name):
                palette = palettes.TIME_PALETTES[name]
                self.assertIn("head_top", palette)
                self.assertIn("head_bottom", palette)
                self.assertNotEqual(palette["head_top"].upper(), palette["head_bottom"].upper(),
                                    "上下端一样就等于没有渐变")

    def test_sky_gradient_only_for_the_auto_mode(self):
        self.assertIsNotNone(palettes.sky_gradient("auto"))
        for mode in ("classic", "photo", "乱七八糟"):
            with self.subTest(mode=mode):
                self.assertIsNone(palettes.sky_gradient(mode),
                                  "只有「随时刻」该画渐变（经典不变、照片铺照片）")

    def test_the_gradient_matches_the_slot_palette(self):
        import datetime as dt

        morning = dt.datetime(2026, 9, 27, 7, 0)
        top, bottom = palettes.sky_gradient("auto", now=morning)
        self.assertEqual((top, bottom), (palettes.MORNING["head_top"],
                                        palettes.MORNING["head_bottom"]))

    def test_white_text_on_the_accent_stays_readable(self):
        """强调色同时是课徽章的底（上面压白字），所以不能太亮。"""
        for name in ("CLASSIC", "MORNING", "DAY", "DUSK", "NIGHT"):
            palette = getattr(palettes, name)
            with self.subTest(palette=name):
                ratio = backdrop.contrast_ratio("#FFFFFF", palette["accent"])
                self.assertGreaterEqual(ratio, 3.0,
                                        f"{name} 的强调色太亮，白字压上去只有 {ratio:.2f}")

    def test_describe_mentions_the_sky(self):
        text = palettes.describe("auto")
        self.assertIn("渐变", text)


class ThemeRecolorTests(unittest.TestCase):
    """换主题 = 整棵控件树换色；漏一个就留一块旧颜色（用户看到的"黑边"）。"""

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

    def _panel(self, mode: str = "classic"):
        try:
            from agenda.panel import AgendaPanel

            panel = AgendaPanel(self.data, pipeline_ms=0, autostart_pipeline=False,
                                hide_past=False, window_mode="desktop")
        except Exception as error:                     # 无图形环境
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.addCleanup(panel.quit)
        panel.root.update()
        return panel

    def test_the_resize_grips_follow_the_palette(self):
        """**用户报的"黑边"就是这个**：拖拽把手/缩放手柄建的时候取了当时的底色。"""
        panel = self._panel()
        night = palettes.NIGHT
        day = palettes.DAY
        theme.apply_palette(night)
        panel._theme_state = {"mode": "night", "palette": night, "picture": None,
                              "note": "", "gradient": None}
        panel._recolor_widgets()
        self.assertEqual(panel._resize_edge.cget("bg"), night["bg"])
        # 换到白天（模拟早上 9 点随时刻切换）
        theme.apply_palette(day)
        panel._theme_state = {"mode": "day", "palette": day, "picture": None,
                              "note": "", "gradient": None}
        panel._recolor_widgets()
        self.assertEqual(panel._resize_edge.cget("bg"), day["bg"],
                         "右侧拖拽把手没跟着换色 —— 浅色面板上就是一根黑边")
        self.assertEqual(panel._resize_corner.cget("bg"), day["bg"],
                         "右下缩放手柄没跟着换色")
        self.assertNotEqual(panel._resize_edge.cget("bg"), night["bg"])

    def test_a_widget_outside_the_written_list_is_still_recolored(self):
        """按颜色值整体替换：没写进清单的控件也要跟着换（以后加控件不用再记着改清单）。"""
        import tkinter as tk

        panel = self._panel()
        theme.apply_palette(palettes.NIGHT)
        guest = tk.Frame(panel.root, bg=palettes.NIGHT["bg"])
        guest.place(x=0, y=0, width=2, height=2)
        panel.root.update()
        self.addCleanup(guest.destroy)
        theme.apply_palette(palettes.DAY)
        panel._recolor_widgets()
        self.assertEqual(guest.cget("bg"), palettes.DAY["bg"],
                         "写死清单之外的控件留下了旧底色")

    def test_the_scrollbar_is_drawn_by_us_not_by_the_system(self):
        """滚动条必须是**自绘**的。

        原来用 `tk.Scrollbar`，而 Windows 上它由系统绘制、**不吃颜色**（实测设成
        `bg=#FF0000, troughcolor=#00FF00`，画出来还是系统灰 `#F0F0F0`）：深色主题上
        就是一条突兀的亮灰条。现在换成 Canvas 自绘，颜色跟主题走、宽度也自己定。
        """
        from agenda import panel as panel_mod

        source = Path(panel_mod.__file__).read_text(encoding="utf-8")
        self.assertIn("_draw_scrollbar", source)
        self.assertIn("capstyle", source, "自绘的滑块没画成圆角胶囊")
        self.assertNotIn("tk.Scrollbar(", source,
                         "又用回原生 Scrollbar 了（系统画的，改了颜色也不生效）")


class CardCacheThemeTests(unittest.TestCase):
    """换主题时卡片缓存必须失效，否则旧卡片带着旧底色留下（深底深字看不清）。"""

    def test_the_cache_key_carries_the_palette(self):
        from agenda import panel as panel_mod

        class FakeCard:
            kind = "event"
            title = "班会"
            start = "12:00"
            end = "13:00"
            date = "2026-09-30"
            location = ""
            people = ()
            notes = ""
            badge = ""
            color = "#4C8DF6"
            tentative = False
            state = "future"
            countdown = None
            event_id = "e1"
            course_index = None

        theme.apply_palette(palettes.MORNING)
        morning = panel_mod.card_cache_key(FakeCard())
        theme.apply_palette(palettes.NIGHT)
        night = panel_mod.card_cache_key(FakeCard())
        self.assertNotEqual(morning, night,
                            "缓存键没带配色 —— 换主题后卡片不会重建（深底深字就是这么来的）")
        self.assertEqual(night, panel_mod.card_cache_key(FakeCard()))


class FestivalFrameTests(unittest.TestCase):
    def test_the_festival_border_is_thicker_than_the_normal_one(self):
        """用户：「这个节日边框太细了，改的粗一点」。"""
        from agenda.panel import AgendaPanel

        self.assertGreaterEqual(AgendaPanel.FESTIVAL_FRAME_PAD, 6,
                                "节日边框又变回细线了")
        self.assertGreater(AgendaPanel.FESTIVAL_FRAME_PAD, AgendaPanel.FRAME_PAD)


if __name__ == "__main__":
    unittest.main()
