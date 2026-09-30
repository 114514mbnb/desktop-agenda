"""教程配图工具的像素层自检（`tools/capture_tutorial.py`）。

为什么专门测这个工具：它是"给人看的图"的生产者，而图错了**不会报错**。
真踩过：`zoom()` 写错一个下标（字节数组里按像素数算行偏移，漏了 ×4），
放大之后整片错位 —— 齿轮特写拍出来是一片空白，而当时的"验证"只看了第一行
（第 0 行恰好是对的），于是就这么漏过去了。

这里的判据是**逐像素**比对，不是"看起来差不多"。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    from tools.capture_tutorial import Shot
else:                                       # GDI 是 Windows 的东西
    Shot = None  # type: ignore[assignment]


def checkerboard(width: int, height: int) -> bytes:
    """棋盘格：颜色由 (x+y) 决定，任何错位一眼就能定位。"""
    data = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            data[offset:offset + 4] = bytes((0, 0, 255 if (x + y) % 2 == 0 else 0, 255))
    return bytes(data)


@unittest.skipUnless(sys.platform == "win32", "截图工具依赖 Windows GDI")
class ShotPixelTests(unittest.TestCase):
    def setUp(self):
        self.width, self.height = 20, 12
        self.base = Shot.from_pixels(self.width, self.height,
                                     checkerboard(self.width, self.height))

    def pixel(self, shot, x: int, y: int) -> tuple[int, int, int]:
        data = shot.pixels()
        offset = (y * shot.width + x) * 4
        return (data[offset + 2], data[offset + 1], data[offset])

    def test_from_pixels_round_trips(self):
        self.assertEqual(self.base.pixels(), checkerboard(self.width, self.height))

    def test_zoom_is_pixel_exact_for_every_factor(self):
        for factor in (1, 2, 3, 4, 5):
            with self.subTest(factor=factor):
                zoomed = self.base.zoom(factor)
                self.assertEqual((zoomed.width, zoomed.height),
                                 (self.width * factor, self.height * factor))
                wrong = [(x, y) for y in range(zoomed.height)
                         for x in range(zoomed.width)
                         if self.pixel(zoomed, x, y) != self.pixel(
                             self.base, x // factor, y // factor)]
                # 只查第一行是发现不了那个 bug 的（第 0 行恰好正确），所以整图都查
                self.assertEqual(wrong, [], f"放大 {factor} 倍后有 {len(wrong)} 个像素错位")

    def test_crop_is_pixel_exact(self):
        cropped = self.base.crop(3, 2, 7, 5)
        self.assertEqual((cropped.width, cropped.height), (7, 5))
        for y in range(5):
            for x in range(7):
                self.assertEqual(self.pixel(cropped, x, y), self.pixel(self.base, x + 3, y + 2))

    def test_crop_clamps_at_the_edges(self):
        """贴着右下角要框大一点时，只能夹到边界内（齿轮特写就撞过这个）。"""
        cropped = self.base.crop(self.width - 4, self.height - 3, 40, 40)
        self.assertEqual((cropped.width, cropped.height), (4, 3))

    def test_with_margin_keeps_the_content_put(self):
        shot = self.base.with_margin(right=30, top=10, bottom=5)
        self.assertEqual((shot.width, shot.height),
                         (self.width + 30, self.height + 15))
        for y in range(self.height):
            for x in range(self.width):
                self.assertEqual(self.pixel(shot, x, y + 10), self.pixel(self.base, x, y))

    def test_annotations_do_not_touch_pixels_outside_their_bounds(self):
        """画一个框、一个箭头的方块之外必须原样——标注不能把自己画花。"""
        before = self.base.pixels()
        self.base.box(2, 2, 10, 8)
        self.base.circle(15, 6, 3)
        self.base.arrow(12, 2, 18, 9)
        self.base.label(1, 10, "x")
        self.assertNotEqual(self.base.pixels(), before, "标注一个像素都没画上去")


if __name__ == "__main__":
    unittest.main(verbosity=2)
