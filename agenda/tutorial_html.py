"""把教程生成一份**能收藏、能放大图**的 HTML，用系统浏览器打开。

为什么要这个：应用里的教程窗再怎么做也是个 Tk 窗口 —— 图缩到 660 px 就看不清小字，
也没法收藏、没法用浏览器的"查找/放大/另存为 PDF"。用户的要求是
「或者干脆用浏览器打开（能放大图、能收藏）」，所以两条路都给：

  * 教程窗里点「在浏览器打开」→ 生成 `docs/教程.html` → 系统默认浏览器打开；
  * 命令行 `main.py --tutorial-web` 也一样。

样式是内联的一小段 CSS（不引外部资源，断网也能看），图片按**相对路径**引用，
所以把 `docs/` 整个拷走（或连同仓库）都能正常显示。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from . import markdown_blocks

#: 生成的 HTML 文件名（放在教程 md 旁边，图片的相对路径才成立）
HTML_NAME = "教程.html"

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px 80px;
  background: #0B0F16; color: #DCE6F5;
  font-family: "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif;
  line-height: 1.75; font-size: 15px;
}
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 6px; }
.lead { color: #9FB0C7; margin: 0 0 26px; }
.card {
  background: #151C28; border: 1px solid #26303F; border-radius: 12px;
  padding: 18px 22px 22px; margin: 0 0 22px;
}
.card > h2 {
  font-size: 19px; margin: 0 0 12px; padding-bottom: 10px;
  border-bottom: 1px solid #26303F; color: #7FB0FF;
}
h3 { font-size: 16px; margin: 22px 0 8px; color: #DCE6F5; }
p { margin: 10px 0; }
ul { margin: 8px 0 8px 4px; padding-left: 22px; }
li { margin: 5px 0; }
code {
  background: #0B0F16; border: 1px solid #26303F; border-radius: 5px;
  padding: 1px 5px; font-family: Consolas, monospace; font-size: 13px; color: #8BD5A0;
}
pre {
  background: #0B0F16; border: 1px solid #26303F; border-radius: 8px;
  padding: 12px 14px; overflow-x: auto;
}
pre code { border: 0; background: none; padding: 0; }
table { border-collapse: collapse; margin: 12px 0; width: 100%; font-size: 14px; }
th, td { border: 1px solid #26303F; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #1B2431; color: #DCE6F5; }
blockquote {
  margin: 12px 0; padding: 10px 14px; border-left: 3px solid #4C8DFF;
  background: #121926; color: #9FB0C7;
}
figure { margin: 18px 0; text-align: center; }
figure img {
  max-width: 100%; border: 1px solid #26303F; border-radius: 10px; cursor: zoom-in;
}
figure figcaption { color: #78899F; font-size: 13px; margin-top: 8px; }
hr { border: 0; border-top: 1px solid #26303F; margin: 4px 0 20px; }
.tip { color: #78899F; font-size: 13px; }
a { color: #7FB0FF; }
"""

#: 点图放大用到的一行脚本（点击图片切换"满宽显示"）
_SCRIPT = """
document.querySelectorAll('figure img').forEach(function (img) {
  img.addEventListener('click', function () {
    var big = img.style.maxWidth === 'none';
    img.style.maxWidth = big ? '' : 'none';
    img.style.width = big ? '' : 'auto';
    img.style.cursor = big ? 'zoom-in' : 'zoom-out';
  });
});
"""


#: 行内格式：`**粗体**`、`` `代码` ``、`[文字](链接)`
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def inline(text: str) -> str:
    """把行内 Markdown 变成 HTML（**粗体** / `代码` / [文字](链接)）。

    链接要留着：浏览器版的目录就是靠它跳章的（Tk 那边则必须压成纯文本）。
    """
    value = html.escape(text, quote=False)
    value = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">'
                  f'{_BOLD_RE.sub(r"<strong>\1</strong>", m.group(1))}</a>', value)
    value = _BOLD_RE.sub(r"<strong>\1</strong>", value)
    value = _CODE_RE.sub(r"<code>\1</code>", value)
    return value


def _figure(source: str, caption: str) -> str:
    alt = html.escape(caption or Path(source).stem)
    return (f'<figure><img src="{html.escape(source)}" alt="{alt}">'
            f'<figcaption>{html.escape(caption)}</figcaption></figure>')


def build_html(markdown_text: str, *, title: str = "桌面日程 · 使用教程") -> str:
    """把教程 Markdown 变成一页自带样式的 HTML（每章一张卡片）。"""
    body: list[str] = []
    open_card = False
    in_table = False
    in_code = False
    code_lines: list[str] = []
    in_list = False

    def close_all() -> None:
        nonlocal open_card, in_table, in_list, in_code
        if in_list:
            body.append("</ul>")
            in_list = False
        if in_table:
            body.append("</table>")
            in_table = False
        if open_card:
            body.append("</section>")
            open_card = False

    for block in markdown_blocks.iter_blocks(markdown_text):
        kind, payload = block.kind, block.payload
        if kind == "code":
            if not in_code:
                close_all()
                body.append("<pre><code>")
                in_code = True
            code_lines.append(html.escape(str(payload)))
            continue
        if in_code:
            body.append("\n".join(code_lines))
            body.append("</code></pre>")
            code_lines, in_code = [], False
        if kind == "heading":
            level, text = payload
            text = html.escape(str(text))
            if level == 1:
                close_all()
                body.append(f"<h1>{text}</h1>")
            elif level == 2:
                close_all()
                if text == "目录":
                    # 目录里的链接是真的能跳的（章节 section 都带了 id）
                    body.append('<section class="card" id="目录"><h2>目录</h2>'
                                '<p class="tip">点下面的章节名可直接跳到那一章；'
                                '图点一下能放大，整页可以收藏。</p></section>')
                    continue
                slug = markdown_blocks.slug(str(payload[1]))
                body.append(f'<section class="card" id="{slug}"><h2>{text}</h2>')
                open_card = True
            else:
                if in_list:
                    body.append("</ul>")
                    in_list = False
                body.append(f"<h3>{text}</h3>")
            continue
        if kind == "image":
            source, caption = payload
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(_figure(str(source), str(caption)))
            continue
        if kind == "rule":
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append("<hr>")
            continue
        if kind == "table_row":
            cells = markdown_blocks.table_cells(str(payload))
            if not in_table:
                body.append("<table><thead><tr>")
                body.extend(f"<th>{inline(cell)}</th>" for cell in cells)
                body.append("</tr></thead><tbody>")
                in_table = True
            else:
                body.append("<tr>")
                body.extend(f"<td>{inline(cell)}</td>" for cell in cells)
                body.append("</tr>")
            continue
        if in_table:
            body.append("</tbody></table>")
            in_table = False
        if kind == "bullet":
            text = inline(str(payload))
            # 表格里的 `1. xxx` 也算列表项，Tk 那边是一行文字，这里保持一行
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{text}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if kind == "quote":
            body.append(f"<blockquote>{inline(str(payload))}</blockquote>")
            continue
        # paragraph
        text = str(payload)
        if text.startswith("**一句话**"):
            body.append('<p class="lead">' + inline(text) + "</p>")
        else:
            body.append(f"<p>{inline(text)}</p>")
    if in_code:
        body.append("\n".join(code_lines))
        body.append("</code></pre>")
    close_all()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
{chr(10).join(body)}
</div>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def html_path(markdown_path: Path) -> Path:
    return Path(markdown_path).with_name(HTML_NAME)


def write_html(markdown_path: Path, *, title: str = "桌面日程 · 使用教程") -> Path | None:
    """生成 HTML，返回它写到了哪儿；读不到 Markdown 就返回 None。"""
    markdown_path = Path(markdown_path)
    try:
        text = markdown_path.read_text(encoding="utf-8")
    except OSError:
        return None
    target = html_path(markdown_path)
    target.write_text(build_html(text, title=title), encoding="utf-8")
    return target


def open_in_browser(markdown_path: Path) -> Path | None:
    """生成 HTML 并用系统默认浏览器打开它。"""
    import os
    import subprocess
    import sys

    target = write_html(markdown_path)
    if target is None:
        return None
    try:
        if sys.platform == "win32":
            os.startfile(str(target))                    # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception:                                    # noqa: BLE001
        return None
    return target
