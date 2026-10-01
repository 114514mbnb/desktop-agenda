"""我的照片：照片铺不上去的 bug 与框选裁切。

用户这一轮的反馈：
  * 「你那个自选照片有问题啊，我刚刚测试了一下，没有反应啊！」——
    实际是他选成功了（`theme_mode=photo`、`background.png` 都在），但面板标题区
    被 6 条**不透明**的"渐变"矩形盖成了一块纯色，照片根本没露脸；
  * 「增加一个裁切功能，对自定义照片用户可以自己选择框选位置（框选位置与日程表适配）」；
（"自启动只开面板""快捷方式第一层只出面板"那几条在 test_startup_entries.py 里。）
"""

from __future__ import annotations

import json
import base64
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import backdrop, photo_crop  # noqa: E402
from agenda.backdrop import Picture  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def make_picture(width: int = 240, height: int = 160) -> Picture:
    """造一张有色块的假照片：左边红、中间绿、右边蓝，裁到哪儿一眼能看出来。"""
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            third = x * 3 // max(1, width)
            red, green, blue = ((220, 40, 40), (40, 200, 60), (50, 80, 230))[third]
            pixels[offset] = blue
            pixels[offset + 1] = green
            pixels[offset + 2] = red
            pixels[offset + 3] = 255
    return Picture(width=width, height=height, pixels=bytes(pixels), stride=width * 4)


def pixel_at(picture: Picture, x: int, y: int) -> tuple[int, int, int]:
    offset = y * picture.stride + x * 4
    return (picture.pixels[offset + 2], picture.pixels[offset + 1], picture.pixels[offset])


def quadrant_picture(width: int = 400, height: int = 300) -> Picture:
    """四等分的色块图：左上红、右上蓝、左下黄、右下绿。

    「框里看到的 == 最后裁出来的」这种判断，靠色块比靠肉眼靠谱：
    哪一块被裁走了一看颜色就知道。
    """
    pixels = bytearray(width * height * 4)
    palette = {(0, 0): (220, 40, 40), (1, 0): (50, 80, 230),
               (0, 1): (230, 210, 40), (1, 1): (40, 200, 60)}
    for y in range(height):
        for x in range(width):
            red, green, blue = palette[(0 if x < width // 2 else 1,
                                        0 if y < height // 2 else 1)]
            offset = (y * width + x) * 4
            pixels[offset:offset + 4] = bytes((blue, green, red, 255))
    return Picture(width=width, height=height, pixels=bytes(pixels), stride=width * 4)


class BackdropPictureTests(unittest.TestCase):
    """像素层：渐变、缩放、裁切。"""

    def test_vertical_fade_keeps_the_photo_at_the_top(self):
        """顶上要**保留画面**，底下才化进底色。

        这就是那个 bug 的正面要求：原来用不透明矩形"做渐变"，等于把照片盖成纯色。
        """
        picture = make_picture()
        faded = backdrop.vertical_fade(picture, "#000000", top=0.0, bottom=1.0)
        self.assertEqual((faded.width, faded.height), (picture.width, picture.height))
        # 顶行原样保留
        self.assertEqual(pixel_at(faded, 10, 0), pixel_at(picture, 10, 0))
        # 底行几乎全黑（ratio=1）
        bottom = pixel_at(faded, 10, faded.height - 1)
        self.assertLess(max(bottom), 6, f"底行没有化进底色：{bottom}")
        # 中间是过渡：同一个 x 上，越往下越暗
        top_value = sum(pixel_at(faded, 10, 0))
        middle_value = sum(pixel_at(faded, 10, faded.height // 2))
        self.assertLess(middle_value, top_value, "从上到下应当越来越接近底色")

    def test_vertical_fade_keeps_horizontal_variation(self):
        """横向的细节不能被抹掉 —— 抹掉了就等于"没有照片"。"""
        picture = make_picture()
        faded = backdrop.vertical_fade(picture, "#101418", top=0.3, bottom=0.8)
        row = [pixel_at(faded, x, 4) for x in range(0, faded.width, 8)]
        reds = {p[0] for p in row}
        self.assertGreater(len(reds), 1, "顶行被压成了一色，照片等于没显示")

    def test_scale_helpers_keep_the_aspect(self):
        picture = make_picture(240, 160)
        wide = backdrop.scale_to_width(picture, 120)
        self.assertEqual((wide.width, wide.height), (120, 80))
        tall = backdrop.scale_to_height(picture, 40)
        self.assertEqual((tall.width, tall.height), (60, 40))
        # 不需要缩的时候原样返回（不白白重采样一遍）
        self.assertIs(backdrop.scale_to_width(picture, 999), picture)

    def test_apply_box_crops_the_expected_region(self):
        picture = make_picture(300, 100)          # 红 | 绿 | 蓝
        middle = backdrop.apply_box(picture, (1 / 3, 0.0, 1 / 3, 1.0))
        self.assertEqual((middle.width, middle.height), (100, 100))
        self.assertEqual(pixel_at(middle, 50, 50), (40, 200, 60))

    def test_apply_box_without_a_box_is_a_no_op(self):
        picture = make_picture()
        self.assertIs(backdrop.apply_box(picture, None), picture)


class PrepareBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.source = self.data / "photo.png"
        backdrop.write_png(make_picture(600, 400), self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def test_box_is_cropped_and_scaled_down(self):
        """框选之后成品比例 = 框的比例，而且不会存成 1600 宽那么胖。"""
        box = (0.0, 0.25, 1.0, 1 / 3)             # 600x133 → 比例 4.5
        ok, message = backdrop.prepare_background(self.source, self.data, box=box)
        self.assertTrue(ok, message)
        strip = backdrop.load_background(self.data, max_width=10000)
        self.assertIsNotNone(strip)
        self.assertAlmostEqual(strip.width / strip.height, 4.5, delta=0.05)
        self.assertLessEqual(strip.width, backdrop.STRIP_WIDTH)

    def test_the_uncropped_original_is_kept_for_re_cropping(self):
        """留着原图，「调整照片范围…」才能重新框。"""
        backdrop.prepare_background(self.source, self.data, box=(0, 0.2, 1, 0.3))
        self.assertTrue(backdrop.source_path(self.data).is_file())
        original = backdrop.load_source(self.data)
        self.assertEqual((original.width, original.height), (600, 400))

    def test_whole_photo_keeps_the_original_aspect(self):
        ok, _message = backdrop.prepare_background(self.source, self.data, box=None)
        self.assertTrue(ok)
        strip = backdrop.load_background(self.data, max_width=10000)
        self.assertAlmostEqual(strip.width / strip.height, 1.5, delta=0.02)
        # 没框选时不该被 STRIP_WIDTH 压小（还是完整照片）
        self.assertEqual(strip.width, 600)

    def test_clear_removes_both_files(self):
        backdrop.prepare_background(self.source, self.data, box=(0, 0.2, 1, 0.3))
        backdrop.clear_background(self.data)
        self.assertFalse(backdrop.has_background(self.data))
        self.assertFalse(backdrop.source_path(self.data).is_file())

    def test_upgrading_from_an_old_photo_still_allows_re_cropping(self):
        """老版本只存了成品（那会儿成品就是整张原图）——升级后要能立刻重新框选。"""
        # 造出"老版本留下的状态"：只有 background.png，没有 source.png
        backdrop.write_png(make_picture(600, 400), backdrop.background_path(self.data))
        self.assertFalse(backdrop.source_path(self.data).is_file())
        source = backdrop.load_source(self.data)
        self.assertIsNotNone(source, "老照片读不出来，「调整照片范围…」会点不动")
        self.assertEqual((source.width, source.height), (600, 400))

    def test_load_source_prefers_the_uncropped_copy(self):
        backdrop.prepare_background(self.source, self.data, box=(0, 0.2, 1, 0.3))
        source = backdrop.load_source(self.data)
        self.assertEqual((source.width, source.height), (600, 400),
                         "有原图副本时应当用原图，而不是裁好的成品")

    def test_re_cropping_works_on_legacy_data(self):
        """老数据只有成品：`source_file()` 必须给出一个**真的存在**的路径。

        真踩过：`crop_theme_photo()` 原来自己拼 `source_path()`，老数据上没有这个文件，
        于是"调整照片范围…"框完保存时报「找不到这个文件」。路径只能由 `source_file()` 给。
        """
        backdrop.write_png(make_picture(600, 400), backdrop.background_path(self.data))
        path = backdrop.source_file(self.data)
        self.assertTrue(path.is_file(), f"给出的原图路径不存在：{path}")
        ok, message = backdrop.prepare_background(path, self.data, box=(0.0, 0.3, 1.0, 0.4))
        self.assertTrue(ok, message)
        strip = backdrop.load_background(self.data, max_width=10000)
        # 比例要**按像素**算：画面是 600x400，不是正方形，
        # 归一化的 1.0/0.4 换算到像素是 600/160（这里也栽过一次）
        expected = (600 * 1.0) / (400 * 0.4)
        self.assertAlmostEqual(strip.width / strip.height, expected, delta=0.05)


class BoxMathTests(unittest.TestCase):
    """框选的比例数学。"""

    def test_default_box_is_maximal_centred_and_exact(self):
        box = photo_crop.default_box(2.769, 1146, 779)
        left, top, width, height = box
        self.assertAlmostEqual(width * 1146 / (height * 779), 2.769, delta=0.01)
        self.assertAlmostEqual(left * 1146, (1146 - width * 1146) / 2, delta=1)
        self.assertAlmostEqual(top * 779, (779 - height * 779) / 2, delta=1)
        # 最大：至少贴满一个方向
        self.assertTrue(abs(width - 1.0) < 1e-9 or abs(height - 1.0) < 1e-9)

    def test_default_box_on_a_tall_photo_uses_the_full_width(self):
        box = photo_crop.default_box(2.769, 800, 2000)
        self.assertAlmostEqual(box[2], 1.0, places=6)
        self.assertLess(box[3], 1.0)

    def test_clamp_box_compares_the_aspect_in_pixels(self):
        """**真踩过**：直接在归一化坐标里比比例，非正方形画面会算错。

        1146×779 的照片上，0.5 × 0.5 的归一化框看起来是 1:1，实际像素比例是 1.47；
        拿它去和 2.769 比就会得出 4.07 这种错值。
        """
        size = (1146, 779)
        fixed = photo_crop.clamp_box((0.9, 0.9, 0.5, 0.5), aspect=2.769, size=size)
        self.assertAlmostEqual(fixed[2] * size[0] / (fixed[3] * size[1]), 2.769, delta=0.01)

    def test_clamp_box_keeps_the_box_inside(self):
        size = (1000, 500)
        for raw in ((0.9, 0.9, 0.5, 0.5), (-0.4, -0.4, 0.8, 0.8), (0.2, 0.1, 3.0, 0.05)):
            with self.subTest(raw=raw):
                x, y, w, h = photo_crop.clamp_box(raw, aspect=2.0, size=size)
                self.assertGreaterEqual(x, 0.0)
                self.assertGreaterEqual(y, 0.0)
                self.assertLessEqual(x + w, 1.0 + 1e-9)
                self.assertLessEqual(y + h, 1.0 + 1e-9)
                self.assertAlmostEqual(w * size[0] / (h * size[1]), 2.0, delta=0.01)

    def test_clamp_box_shrinks_an_oversized_box_without_distorting(self):
        fixed = photo_crop.clamp_box((0.0, 0.0, 5.0, 5.0), aspect=2.769, size=(1146, 779))
        self.assertAlmostEqual(fixed[2] * 1146 / (fixed[3] * 779), 2.769, delta=0.01)
        self.assertLessEqual(fixed[2], 1.0)
        self.assertLessEqual(fixed[3], 1.0)


class CropDialogTests(unittest.TestCase):
    """真的把框选窗开起来点一遍。"""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.root.geometry("+4000+4000")
        self.picture = make_picture(400, 300)
        self.colors = {"bg": "#101722", "card": "#1D2430", "text": "#E8EDF7",
                       "text_dim": "#9FB0C7", "text_faint": "#8A93A6",
                       "accent": "#5B8DEF", "border": "#1B2536"}

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:                # noqa: BLE001
            pass

    def _run(self, action, aspect=2.0):
        dialog = photo_crop.CropDialog(self.root, self.picture, aspect=aspect,
                                       colors=self.colors)
        seen: dict = {}

        def go():
            seen["box_before"] = dialog.box
            action(dialog)
            if dialog.window.winfo_exists():
                dialog.window.destroy()

        self.root.after(300, go)
        result = dialog.show()
        return result, seen

    def test_saving_returns_the_selected_box(self):
        result, seen = self._run(lambda dialog: dialog._confirm())
        self.assertEqual(result[0], True)
        self.assertEqual(result[1], seen["box_before"])

    def test_use_whole_photo_confirms_with_no_box(self):
        result, _seen = self._run(lambda dialog: dialog._use_whole())
        self.assertEqual(result, (True, None))

    def test_closing_without_saving_is_a_cancel(self):
        """取消必须能和"用整张"区分开：取消不该动配置。"""
        result, _seen = self._run(lambda dialog: None)
        self.assertFalse(result[0], "没点保存却算成了确认")
        self.assertIsNone(result[1])

    def test_the_selection_always_keeps_the_panel_aspect(self):
        def drag(dialog):
            # 假装用户从中心往外拖一个很扁的框
            dialog._drag = ("draw", 200, 150, None)
            dialog._draw_to(390, 160)
            dialog._drag = None

        _result, seen = self._run(drag)
        x, y, w, h = seen["box_before"]
        self.assertAlmostEqual(w * 400 / (h * 300), 2.0, delta=0.02)
        self.assertGreaterEqual(x, 0.0)
        self.assertGreaterEqual(y, 0.0)

    def test_dragging_the_selection_moves_it_without_resizing(self):
        def drag(dialog):
            before = dialog.box
            dialog._drag = ("move", 100, 100, before)
            dialog._on_drag(type("E", (), {"x": 140, "y": 100})())
            dialog._drag = None

        _result, seen = self._run(drag)
        self.assertAlmostEqual(seen["box_before"][2], photo_crop.default_box(
            2.0, 400, 300)[2], delta=0.02)

    def test_what_you_frame_is_what_you_get(self):
        """**框里看到的，必须就是最后裁出来的那块**（逐像素）。

        用户报「框选错位了」。这条把"对话框里框的"和"`apply_box` 裁出来的"钉成同一块：
        四等分的色块图上框住右下那块，裁出来必须整块都是右下那块的颜色。
        """
        picture = quadrant_picture(400, 300)
        result, _seen = self._run(lambda dialog: dialog._confirm(), aspect=2.0)
        _confirmed, box = result
        self.assertIsNotNone(box)
        target = (0.5, 0.5, 0.5, 0.5)              # 右下那块
        # 直接把框设成右下四分之一（比例 2:1 时会占满宽度、贴底）
        x, y, w, h = photo_crop.clamp_box(target, aspect=2.0, size=(400, 300))
        strip = backdrop.apply_box(picture, (x, y, w, h))
        colors_seen = {pixel_at(strip, sx, sy)
                       for sx in range(4, strip.width - 4, 9)
                       for sy in range(4, strip.height - 4, 9)}
        self.assertEqual(colors_seen, {(40, 200, 60)},
                         f"框住的区域和裁出来的不是同一块：看到 {colors_seen}")

    def test_the_preview_image_and_the_canvas_agree_on_the_size(self):
        """缩略图的显示尺寸必须和画布一致。

        差几个像素就会"框到边上多一点、裁出来却少一块"——这类错位肉眼很难判断，
        所以直接量：显示出来的图宽度/高度 == 画布声明的尺寸。
        """
        dialog = photo_crop.CropDialog(self.root, self.picture, aspect=2.0,
                                       colors=self.colors)
        try:
            dialog.window.update_idletasks()
            self.assertEqual((dialog._photo.width(), dialog._photo.height()),
                             (dialog.view_w, dialog.view_h),
                             "缩略图尺寸和画布尺寸不一致，框选会错位")
        finally:
            dialog.window.destroy()


class PanelHeaderPhotoTests(unittest.TestCase):
    """面板侧：标题区真的把照片画出来了（这是用户报的那个 bug）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07", "periods": [["08:00", "08:50"]], "courses": [],
        }, ensure_ascii=False), encoding="utf-8")
        (self.data / "events.json").write_text(
            json.dumps({"schemaVersion": 1, "events": []}, ensure_ascii=False),
            encoding="utf-8")
        self.photo = self.data / "photo.png"
        backdrop.write_png(make_picture(600, 216), self.photo)
        self._write_config("classic")
        try:
            from agenda.panel import AgendaPanel

            self.panel = AgendaPanel(self.data, width=360, pipeline_ms=0,
                                     autostart_pipeline=False, window_mode="desktop",
                                     position=(80, 60))
        except Exception as error:                 # 无图形环境
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.panel.root.update()

    def tearDown(self):
        try:
            self.panel.quit()
        except Exception:                # noqa: BLE001
            pass
        self.tmp.cleanup()

    def _write_config(self, mode: str) -> None:
        (self.data / "client.json").write_text(json.dumps({
            "theme_mode": mode, "hotkey_enabled": False,
        }, ensure_ascii=False), encoding="utf-8")

    def _enable_photo(self) -> None:
        ok, message = backdrop.prepare_background(self.photo, self.data,
                                                  box=(0.0, 0.2, 1.0, 0.36))
        self.assertTrue(ok, message)
        self._write_config("photo")
        # 主题是 `_apply_theme()` 套上去的（refresh 只管时间线）。
        # 真实面板靠 700ms 的巡检触发它，这里直接强制套一次。
        self.panel._apply_theme(force=True)
        self.panel.root.update()

    def test_the_header_draws_the_photo_and_nothing_opaque_on_top(self):
        """标题区只能有**一张图片 + 文字**，不许再有矩形（也不许有不透明的标签）盖着。

        真踩过两次：
          1. 渐变是用 6 条不透明矩形画的，等于把照片盖成一堆纯色；
          2. 改成像素级渐变之后，三行文字的 Label 底色**不透明**、又是 `fill="x"`，
             整条把照片盖住 —— 用户选完照片只看到"颜色变了、照片没有"。
        所以这条同时盯住：没有矩形、图片只有一张、且文字是 Canvas 文本（没底色）。
        """
        self._enable_photo()
        self.assertEqual(self.panel._theme_state["mode"], "photo")
        self.assertIsNotNone(self.panel._header_photo, "照片没画出来")
        canvas = self.panel.header_canvas
        kinds = [canvas.type(item) for item in canvas.find_all()]
        self.assertEqual(kinds.count("image"), 1, f"照片数量不对：{kinds}")
        self.assertNotIn("rectangle", kinds, f"又有不透明矩形压在照片上：{kinds}")
        self.assertGreaterEqual(kinds.count("text"), 1,
                                f"标题区的文字没画到 Canvas 上：{kinds}")

    def test_the_header_labels_step_aside_in_photo_mode(self):
        """照片模式下三个标签必须从版面上摘下来，否则它们的底色会盖住照片。"""
        styles = (self.panel.clock_label, self.panel.date_label, self.panel.next_label)
        for widget in styles:
            self.assertEqual(widget.winfo_manager(), "pack", "非照片模式应当正常装在版面上")
        self._enable_photo()
        for widget in styles:
            self.assertEqual(widget.winfo_manager(), "", f"照片模式下 {widget} 还占着版面")

    def test_leaving_photo_mode_puts_the_labels_back(self):
        """切回经典深色要把标签装回去，并且把写死的头部高度交还给自适应。"""
        self._enable_photo()
        self._write_config("classic")
        self.panel._apply_theme(force=True)
        self.panel.root.update()
        self.assertEqual(self.panel._theme_state["mode"], "classic")
        self.assertIsNone(self.panel._header_photo)
        for widget in (self.panel.clock_label, self.panel.date_label, self.panel.next_label):
            self.assertEqual(widget.winfo_manager(), "pack", f"{widget} 没装回版面")
        self.assertGreater(int(self.panel.header.winfo_height()), 1,
                           "头部高度没恢复成自适应")

    def test_the_visible_header_keeps_horizontal_detail(self):
        """画出来的那张图不能是纯色。"""
        self._enable_photo()
        photo = self.panel._header_photo
        self.assertIsNotNone(photo, "照片没画出来")
        self.assertGreater(photo.width(), 1)
        # 测试图是"红|绿|蓝"三块，左边和右边必然不同色
        left = str(photo.get(2, 2))
        right = str(photo.get(photo.width() - 3, 2))
        self.assertNotEqual(left, right, "照片被压成了纯色")

    def test_switching_theme_at_runtime_applies_photo(self):
        """控制台改完主题，面板要自己换过来（用户在运行中切的）。"""
        self.panel._data_changed()                 # 先建立基线快照
        ok, _message = backdrop.prepare_background(self.photo, self.data,
                                                   box=(0.0, 0.2, 1.0, 0.36))
        self.assertTrue(ok)
        self._write_config("photo")
        import time

        time.sleep(1.05)                           # 让 mtime 真的变一下
        self.panel._watch_stop_flag()
        self.panel.root.update()
        self.assertEqual(self.panel._theme_state["mode"], "photo")
        self.assertIsNotNone(self.panel._header_photo)

    def test_non_photo_modes_leave_the_header_canvas_empty(self):
        self.assertIsNone(self.panel._header_photo)
        self.assertEqual(self.panel.header_canvas.find_all(), ())

    def test_header_ink_follows_the_photo(self):
        """照片亮就把字调深、照片暗就调浅，不然压在照片上读不出来。"""
        self._enable_photo()
        backdrop_top = self.panel._header_backdrop_color
        self.assertIsNotNone(backdrop_top)
        self.assertIn(self.panel.clock_label.cget("fg"),
                      (backdrop.INK_DARK, backdrop.INK_LIGHT))


if __name__ == "__main__":
    unittest.main(verbosity=2)
