"""主题系统的测试：调色板完整性、时段判定、照片配色、面板落地。

这个功能最容易出的两类问题，正好是下面两组用例盯着的：
  1. **少一个颜色键** → 某个控件构建时 KeyError 崩掉（而且只在某个模式下崩，
     手动测很容易漏）；
  2. **照片配色导致文字看不清** → 白底白字这种，功能"能用"但没法看。
     所以对比度是**算出来**的（WCAG），不是靠肉眼看截图。
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import backdrop, palettes  # noqa: E402

IS_WINDOWS = sys.platform == "win32"


class PaletteIntegrityTests(unittest.TestCase):
    def test_every_palette_has_the_same_keys(self):
        """所有配色必须键完全一致——少一个键就是在某个模式下崩。"""
        expected = set(palettes.CLASSIC)
        for name, palette in (("classic", palettes.CLASSIC), ("morning", palettes.MORNING),
                              ("day", palettes.DAY), ("dusk", palettes.DUSK),
                              ("night", palettes.NIGHT)):
            with self.subTest(palette=name):
                self.assertEqual(set(palette), expected,
                                 f"{name} 的键和经典深色不一致")

    def test_palette_for_every_mode(self):
        for mode in palettes.MODES:
            with self.subTest(mode=mode):
                palette = palettes.palette_for(mode)
                self.assertEqual(set(palette), set(palettes.CLASSIC))

    def test_unknown_mode_falls_back_to_classic(self):
        for value in ("", None, "乱七八糟", "CLASSIC "):
            with self.subTest(value=value):
                self.assertEqual(palettes.normalize_mode(value), "classic")
        self.assertEqual(palettes.palette_for("乱七八糟"), palettes.CLASSIC)

    def test_photo_mode_without_a_photo_falls_back(self):
        """照片丢了还得能用：没有照片配色时回退到经典深色。"""
        self.assertEqual(palettes.palette_for("photo", photo_palette=None), palettes.CLASSIC)


class ContrastTests(unittest.TestCase):
    """文字必须看得清：正文 ≥4.5（WCAG AA），次要文字 ≥3。"""

    def test_text_contrast_of_every_builtin_palette(self):
        for name in ("CLASSIC", "MORNING", "DAY", "DUSK", "NIGHT"):
            palette = getattr(palettes, name)
            with self.subTest(palette=name):
                self.assertGreaterEqual(
                    backdrop.contrast_ratio(palette["text"], palette["card"]), 4.5,
                    f"{name} 的正文对比度不够")
                self.assertGreaterEqual(
                    backdrop.contrast_ratio(palette["text_dim"], palette["card"]), 3.0,
                    f"{name} 的次要文字对比度不够")
                self.assertGreaterEqual(
                    backdrop.contrast_ratio(palette["text_faint"], palette["card"]), 3.0,
                    f"{name} 的辅助文字对比度不够")
                # 卡片和底色要能分出层次（不然卡片像浮不起来）
                self.assertNotEqual(palette["card"], palette["bg"])

    def test_time_slots(self):
        cases = {
            0: "night", 4: "night", 5: "morning", 8: "morning",
            9: "day", 12: "day", 16: "day",
            17: "dusk", 19: "dusk", 20: "night", 23: "night",
        }
        for hour, expected in cases.items():
            with self.subTest(hour=hour):
                moment = dt.datetime(2026, 9, 27, hour, 0)
                self.assertEqual(palettes.slot_for(moment), expected)

    def test_auto_mode_picks_the_slot_palette(self):
        morning = dt.datetime(2026, 9, 27, 7, 30)
        self.assertEqual(palettes.palette_for("auto", now=morning), palettes.MORNING)
        evening = dt.datetime(2026, 9, 27, 18, 0)
        self.assertEqual(palettes.palette_for("auto", now=evening), palettes.DUSK)


class ColorHelperTests(unittest.TestCase):
    def test_blend_endpoints_and_middle(self):
        self.assertEqual(backdrop.blend("#000000", "#FFFFFF", 0.0), "#000000")
        self.assertEqual(backdrop.blend("#000000", "#FFFFFF", 1.0), "#FFFFFF")
        self.assertEqual(backdrop.blend("#000000", "#FFFFFF", 0.5), "#808080")

    def test_luminance_and_contrast(self):
        self.assertAlmostEqual(backdrop.relative_luminance("#000000"), 0.0, places=3)
        self.assertAlmostEqual(backdrop.relative_luminance("#FFFFFF"), 1.0, places=3)
        self.assertAlmostEqual(backdrop.contrast_ratio("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(backdrop.contrast_ratio("#777777", "#777777"), 1.0, places=3)

    def test_readable_ink_flips_with_background(self):
        """这就是用户要的"白底黑字、黑底白字"。"""
        self.assertEqual(backdrop.readable_ink("#FFFFFF"), backdrop.INK_DARK)
        self.assertEqual(backdrop.readable_ink("#000000"), backdrop.INK_LIGHT)
        self.assertLess(backdrop.relative_luminance(backdrop.readable_ink("#F2F2F2")), 0.5)
        self.assertGreater(backdrop.relative_luminance(backdrop.readable_ink("#101010")), 0.5)


class PngEncodingTests(unittest.TestCase):
    """自己写的 PNG 编码器要真的能被解出来（不然照片根本显示不了）。"""

    def _picture(self, rgba_rows):
        pixels = bytearray()
        for row in rgba_rows:
            for r, g, b in row:
                pixels += bytes((b, g, r, 255))        # BGRA
        return backdrop.Picture(width=len(rgba_rows[0]), height=len(rgba_rows),
                                pixels=bytes(pixels), stride=len(rgba_rows[0]) * 4)

    def test_png_bytes_have_a_valid_header(self):
        picture = self._picture([[(255, 0, 0), (0, 255, 0)]])
        data = backdrop.png_bytes(picture)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", data[:32])
        self.assertTrue(data.endswith(b"IEND\xaeB`\x82"))

    def test_photoimage_round_trip(self):
        import tkinter as tk

        try:
            root = tk.Tk()
        except Exception as error:                # 无图形环境
            raise unittest.SkipTest(f"没有可用显示：{error}")
        root.withdraw()
        try:
            picture = self._picture([[(255, 0, 0), (0, 0, 255)]])
            image = backdrop.to_photoimage(root, picture)
            self.assertEqual((image.width(), image.height()), (2, 1))
            self.assertEqual(image.get(0, 0), (255, 0, 0))
            self.assertEqual(image.get(1, 0), (0, 0, 255))
        finally:
            root.destroy()


@unittest.skipUnless(IS_WINDOWS, "照片解码走 Windows 自带的 GDI+")
class PhotoPipelineTests(unittest.TestCase):
    """真照片走一遍：解码 → 出配色 → 文字自动深浅。"""

    def _make_photo(self, directory: Path, color: tuple[int, int, int]) -> Path:
        """用程序自己的 PNG 编码器造一张纯色图当"用户的照片"。"""
        picture = backdrop.Picture(
            width=64, height=64,
            pixels=bytes((color[2], color[1], color[0], 255)) * (64 * 64),
            stride=64 * 4)
        target = directory / "photo.png"
        self.assertTrue(backdrop.write_png(picture, target))
        return target

    def test_light_photo_gets_dark_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            source = self._make_photo(data, (245, 245, 245))       # 接近纯白
            ok, message = backdrop.prepare_background(source, data)
            self.assertTrue(ok, message)
            picture = backdrop.load_background(data, max_width=64)
            self.assertIsNotNone(picture)
            palette = backdrop.palette_from_image(picture)
            self.assertEqual(set(palette), set(palettes.CLASSIC))
            self.assertGreater(backdrop.relative_luminance(palette["card"]), 0.5,
                               "白照片应该得到亮底色")
            self.assertLess(backdrop.relative_luminance(palette["text"]), 0.5,
                            "亮底必须配深色字")
            self.assertGreaterEqual(
                backdrop.contrast_ratio(palette["text"], palette["card"]), 4.5)

    def test_dark_photo_gets_light_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            source = self._make_photo(data, (12, 14, 20))          # 接近纯黑
            ok, message = backdrop.prepare_background(source, data)
            self.assertTrue(ok, message)
            picture = backdrop.load_background(data, max_width=64)
            palette = backdrop.palette_from_image(picture)
            self.assertLess(backdrop.relative_luminance(palette["card"]), 0.5)
            self.assertGreater(backdrop.relative_luminance(palette["text"]), 0.5,
                               "暗底必须配浅色字")
            self.assertGreaterEqual(
                backdrop.contrast_ratio(palette["text"], palette["card"]), 4.5)

    def test_prepare_rejects_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            junk = data / "not-an-image.png"
            junk.write_bytes(b"this is definitely not an image")
            ok, message = backdrop.prepare_background(junk, data)
            self.assertFalse(ok)
            self.assertTrue(message)
            ok, message = backdrop.prepare_background(data / "missing.png", data)
            self.assertFalse(ok)

    def test_clear_removes_the_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            source = self._make_photo(data, (200, 120, 60))
            self.assertTrue(backdrop.prepare_background(source, data)[0])
            self.assertTrue(backdrop.has_background(data))
            backdrop.clear_background(data)
            self.assertFalse(backdrop.has_background(data))
            self.assertIsNone(backdrop.load_background(data, max_width=64))


class PanelThemeTests(unittest.TestCase):
    """面板侧：三种模式都能建起来，改配置能当场换掉配色。"""

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    def _save(self, data: Path, **fields):
        from agenda.client_config import ClientConfig

        config = ClientConfig.load(data)
        for key, value in fields.items():
            setattr(config, key, value)
        config.save(data)

    def test_panel_builds_in_every_mode(self):
        for mode in ("classic", "auto", "photo"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    data = Path(tmp)
                    self._save(data, theme_mode=mode)
                    panel = self._panel(data)
                    try:
                        panel.root.update()
                        self.assertEqual(panel._theme_state["mode"], mode
                                         if mode != "photo" else "classic",
                                         "照片模式没照片时应该退回经典深色")
                    finally:
                        panel.quit()

    def test_switching_mode_recolors_the_widgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._save(data, theme_mode="classic")
            panel = self._panel(data)
            try:
                panel.root.update()
                classic_bg = panel.clock_label.cget("bg")
                self._save(data, theme_mode="auto")
                panel._apply_theme()
                panel.root.update()
                auto_bg = panel.clock_label.cget("bg")
                self.assertNotEqual(classic_bg, auto_bg, "换主题后控件颜色没变")
                # 控件颜色必须等于当前调色板里的值（不能只是"变了"）
                from agenda import theme

                self.assertEqual(auto_bg, theme.COLORS["bg_soft"])
            finally:
                panel.quit()

    def test_auto_mode_slot_change_triggers_a_switch(self):
        """跨时段要自己换配色（模拟时间走过边界，不能等真的到点）。"""
        from agenda import palettes as palettes_mod

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._save(data, theme_mode="auto")
            panel = self._panel(data)
            real_slot = palettes_mod.slot_for
            try:
                panel.root.update()
                before = panel.clock_label.cget("bg")
                self.assertEqual(panel._theme_slot, real_slot())
                other = "day" if panel._theme_slot != "day" else "night"
                palettes_mod.slot_for = lambda moment=None: other
                panel._slot_tick()
                panel.root.update()
                after = panel.clock_label.cget("bg")
                self.assertNotEqual(before, after, "跨时段没有换配色")
                self.assertEqual(after, palettes_mod.TIME_PALETTES[other]["bg_soft"],
                                 "换出来的颜色不是那个时段的配色")
            finally:
                palettes_mod.slot_for = real_slot
                panel.quit()

    def test_photo_mode_puts_the_picture_on_the_header(self):
        if not IS_WINDOWS:
            self.skipTest("照片解码走 Windows 自带的 GDI+")
        from agenda import theme

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            picture = backdrop.Picture(width=32, height=32,
                                       pixels=bytes((40, 120, 200, 255)) * (32 * 32),
                                       stride=32 * 4)
            self.assertTrue(backdrop.write_png(picture, backdrop.background_path(data)))
            self._save(data, theme_mode="photo")
            panel = self._panel(data)
            try:
                panel.root.update()
                self.assertEqual(panel._theme_state["mode"], "photo")
                self.assertIsNotNone(panel._header_photo, "照片没有画到标题区上")
                self.assertGreater(len(panel.header_canvas.find_all()), 0,
                                   "标题区画布是空的")
                self.assertTrue(panel.header_canvas.winfo_ismapped(), "标题区画布没显示")
                # 照片模式的配色是从照片里算出来的，不是经典深色
                self.assertNotEqual(theme.COLORS["bg"], palettes.CLASSIC["bg"])
            finally:
                panel.quit()

    def test_theme_failure_never_breaks_the_panel(self):
        """照片坏了、配置写乱了，面板也必须能起来（宁可退回经典深色）。"""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._save(data, theme_mode="photo")          # 照片模式但没有照片
            panel = self._panel(data)
            try:
                panel.root.update()
                self.assertEqual(panel._theme_state["mode"], "classic")
                panel._apply_theme(force=True)
                panel.root.update()
            finally:
                panel.quit()


class PanelReadabilityTests(unittest.TestCase):
    """**实拍级**回归：面板上每一处可见文字，与它自己的底色都得看得清。

    这条测试是因为一个真 bug 才加的：`theme.tint()` 原来一律"往黑里混"（深色主题的假设），
    浅色主题下白卡片被压成中灰，配上深色字就成了"深底深字"——白天主题实拍时几乎看不清。
    逐个颜色写断言抓不到这种问题（卡片底色是运行时算的），所以这里直接遍历**真实控件树**：
    拿每个 Label 的 fg/bg 算 WCAG 对比度。
    """

    MIN_RATIO = 3.0        # 次要文字（时间、地点）也至少要 3:1

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    def _visible_text_contrast(self, panel) -> list[str]:
        import tkinter as tk

        problems: list[str] = []

        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)

        for widget in walk(panel.root):
            if not isinstance(widget, tk.Label):
                continue
            text = str(widget.cget("text") or "").strip()
            if not text:
                continue
            try:
                fg = str(widget.cget("fg"))
                bg = str(widget.cget("bg"))
            except tk.TclError:
                continue
            if not fg.startswith("#") or not bg.startswith("#"):
                continue
            ratio = backdrop.contrast_ratio(fg, bg)
            if ratio < self.MIN_RATIO:
                problems.append(f"「{text[:18]}」fg={fg} bg={bg} 对比度 {ratio:.2f}")
        return problems

    def _seed_content(self, data: Path) -> None:
        """造一门课 + 一条今天的通知，保证面板上真的有卡片文字可查。"""
        import json

        (data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07",
            "periods": [["08:00", "08:45"], ["08:55", "09:40"]],
            "courses": [{"name": "高等数学", "weekday": "周一", "period": "1-2",
                         "weeks": "1-18", "teacher": "陈立", "location": "中心校区 A-101"}],
        }, ensure_ascii=False), encoding="utf-8")
        today = dt.date.today().strftime("%Y-%m-%d")
        (data / "events.json").write_text(json.dumps({
            "schemaVersion": 1,
            "events": [{"id": "e1", "title": "班会：材料提交", "date": today,
                        "start": "12:00", "end": "13:00", "people": ["班长"],
                        "notes": "交到辅导员办公室", "group": "2026级群", "tentative": False}],
        }, ensure_ascii=False), encoding="utf-8")

    def test_text_is_readable_in_every_builtin_theme(self):
        """经典深色 + 四套时段配色：卡片、日期头、状态行的文字都要清楚。"""
        from agenda import theme

        for name in ("CLASSIC", "MORNING", "DAY", "DUSK", "NIGHT"):
            with self.subTest(palette=name):
                with tempfile.TemporaryDirectory() as tmp:
                    data = Path(tmp)
                    self._seed_content(data)
                    panel = self._panel(data)
                    palette = getattr(palettes, name)
                    try:
                        panel.root.update()
                        # 等价于"面板正好处于这个主题"：换配色 → 重新上色 → 重画卡片
                        theme.apply_palette(palette)
                        panel._theme_state = {"mode": name.lower(), "palette": palette,
                                              "picture": None, "note": ""}
                        panel._recolor_widgets()
                        panel.refresh()
                        panel.root.update()
                        problems = self._visible_text_contrast(panel)
                        self.assertEqual(problems, [],
                                         f"{name} 主题下有看不清的文字：{problems}")
                    finally:
                        theme.apply_palette(palettes.CLASSIC)
                        panel.quit()

    def test_card_background_follows_the_theme_direction(self):
        """浅色主题的卡片必须是亮色——这就是那个 bug 的直接断言。"""
        from agenda import theme

        try:
            theme.apply_palette(palettes.DAY)
            light_card = theme.tint(theme.COLORS["card"], "#2E6DD0", 0.20)
            self.assertGreater(backdrop.relative_luminance(light_card), 0.5,
                               "浅色主题下卡片被压暗了")
            theme.apply_palette(palettes.CLASSIC)
            dark_card = theme.tint(theme.COLORS["card"], "#4C8DF6", 0.20)
            self.assertLess(backdrop.relative_luminance(dark_card), 0.5,
                            "深色主题下卡片应该保持暗色")
        finally:
            theme.apply_palette(palettes.CLASSIC)


class ThemeApplyTests(unittest.TestCase):
    def test_apply_palette_rejects_incomplete_palettes(self):
        """少键要当场报错，而不是等到某个控件构建时 KeyError。"""
        from agenda import theme

        with self.assertRaises(ValueError):
            theme.apply_palette({"bg": "#000000"})

    def test_apply_palette_updates_in_place(self):
        """就地更新：所有已经拿着 COLORS 引用的地方都要看到新颜色。"""
        from agenda import theme

        reference = theme.COLORS
        try:
            theme.apply_palette(palettes.DAY)
            self.assertIs(theme.COLORS, reference)
            self.assertEqual(reference["bg"], palettes.DAY["bg"])
        finally:
            theme.apply_palette(palettes.CLASSIC)
        self.assertEqual(theme.COLORS["bg"], palettes.CLASSIC["bg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
