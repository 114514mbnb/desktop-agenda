"""探针 2：逐个字符检查——哪些会画成"豆腐块"（缺字形）。

面板横幅和彩蛋文案里如果出现豆腐块，看起来就是坏的，所以逐个过一遍。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agenda import theme  # noqa: E402

CANDIDATES = [
    ("emoji-old", "🌕 🌖 🌗 🌘 🌑 🌒 🌓 🌔 ☀ ☁ ☂ ❄ ⛄"),
    ("emoji-new", "🧧 🏮 🥮 🎆 🎇 🐉 🛶 🌿 💪 🎋 🍃 ✨ 🥟 🍚 🎑 🏮"),
    ("flags", "🇨🇳 ⚑ ⚐"),
    ("symbols", "★ ☆ ✦ ✧ ❋ ✺ ❁ ❀ ✿ ☾ ☽ ♥ ♪ ❄ ☃ ♣ ♦ ♠"),
]


def main() -> int:
    from PIL import ImageGrab

    root = tk.Tk()
    root.title("字符探针")
    root.configure(bg="#101722")
    root.geometry("900x420+200+160")
    root.attributes("-topmost", True)
    family = theme.pick_font_family(root)
    for name, text in CANDIDATES:
        for size in (9, 12, 20):
            tk.Label(root, text=f"[{name} {size}pt] {text}", bg="#101722", fg="#E8EDF7",
                     font=(family, size), anchor="w").pack(fill="x", padx=12, pady=2)
    root.lift()
    root.focus_force()
    root.update()
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    target = ROOT / "screenshot-font-probe2.png"
    ImageGrab.grab(bbox=(x - 4, y - 30, x + w + 4, y + h + 4), all_screens=True).save(target)
    print(f"saved {target} font={family}")
    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
