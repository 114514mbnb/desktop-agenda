"""生成托盘/窗口图标（多尺寸 .ico）。

用法：python tools/make_icon.py [输出路径]
需要 Pillow（`pip install Pillow`，或随便找个装了 Pillow 的 Python 解释器跑）。
不生成也不影响使用：托盘会回落到系统默认图标。
"""

from __future__ import annotations

import sys
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int):
    from PIL import Image, ImageDraw

    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_ctx = ImageDraw.Draw(image)

    radius = int(56 * scale)
    draw_ctx.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius,
        fill=(76, 141, 246, 255),          # theme accent
    )
    # 日历主体
    margin = int(46 * scale)
    top = int(74 * scale)
    draw_ctx.rounded_rectangle(
        (margin, top, size - margin, size - int(40 * scale)),
        radius=int(18 * scale), fill=(255, 255, 255, 255),
    )
    # 顶部横条
    draw_ctx.rounded_rectangle(
        (margin, top, size - margin, top + int(34 * scale)),
        radius=int(14 * scale), fill=(233, 237, 245, 255),
    )
    # 三个小圆点代表日程项
    dot = max(3, int(11 * scale))
    y = top + int(66 * scale)
    for index, color in enumerate(((76, 141, 246), (242, 153, 74), (39, 174, 96))):
        x = margin + int(26 * scale) + index * int(46 * scale)
        draw_ctx.ellipse((x, y, x + dot, y + dot), fill=color + (255,))
    # 一条横线
    draw_ctx.rounded_rectangle(
        (margin + int(26 * scale), y + int(40 * scale),
         size - margin - int(26 * scale), y + int(40 * scale) + max(3, int(10 * scale))),
        radius=max(2, int(5 * scale)), fill=(167, 176, 192, 255),
    )
    return image


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parent.parent / "data" / "agenda.ico"
    )
    try:
        from PIL import Image
    except ImportError:
        print("需要 Pillow：请用带 Pillow 的 Python 运行（视觉工具包 venv 里有）")
        return 1
    frames = [draw(size) for size in SIZES]
    target.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(target, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"已生成 {target}（{len(SIZES)} 个尺寸）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
