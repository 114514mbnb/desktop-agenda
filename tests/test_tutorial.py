"""使用教程的测试。

教程这东西最容易"写完就烂"——路径改了、章节改名了、渲染函数忘了处理某种语法，
用户点开就是一片空白或者一堆星号。所以这里钉住三件事：
  1. 教程文件真的在、章节结构还在（目录、13 个正文章节）；
  2. 渲染能把行内 Markdown 语法压掉（`**粗体**` 不能原样显示成星号）；
  3. 设置页里那个"查看使用教程"入口真的存在（用户能不能找到它）。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import tutorial  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class TutorialFileTests(unittest.TestCase):
    def test_tutorial_file_exists(self):
        path = tutorial.tutorial_path()
        self.assertIsNotNone(path, "找不到 docs/教程.md")
        self.assertTrue(path.is_file())

    def test_tutorial_covers_every_feature(self):
        """每个功能区都得在教程里有对应章节，不然用户查不到。

        注意：章节**编号**跟着结构走过一次（原来"待机模式"单列第 10 节，
        现在并进「设置项逐个说明」，后面的整体前移一位）。这里盯的是
        "每一块功能都有归宿"，不是编号本身。
        """
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        required = [
            "## 1. 它是干什么的", "## 2. 五分钟上手", "## 3. 桌面面板怎么用",
            "## 4. 把课表导进来", "## 5. 把群通知加进来（五种方式）", "## 6. 处理单条日程",
            "## 7. 上课时间与节数", "## 8. 假期、调休与节日彩蛋", "## 9. 设置项逐个说明",
            "## 10. 窗口找不到了怎么办", "## 11. 数据在哪", "## 12. 常见问题",
            "## 13. 命令行参数",
        ]
        for heading in required:
            with self.subTest(heading=heading):
                self.assertIn(heading, text, f"教程缺少章节：{heading}")
        # 待机模式并进设置页之后，别把它整个弄丢
        self.assertIn("待机模式", text)
        self.assertIn("智能隐身", text)
        self.assertIn("常驻待机", text)

    def test_tutorial_explains_holiday_makeup_and_eggs(self):
        """新加的三件事：假期压课 / 调休小字 / 节日彩蛋。"""
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        for phrase in ("按节日预填", "从放假通知识别", "调休X月X日日程",
                       "不排课", "群通知照", "彩蛋", "中秋节"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_tutorial_mentions_the_settings_entry(self):
        """教程里要告诉用户"设置里有教程"——否则他下次还是找不到。"""
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        self.assertIn("查看使用教程", text)

    def test_tutorial_mentions_right_click_actions(self):
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        for phrase in ("标记为已完成", "修改结束时间", "删除此条"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_tutorial_documents_the_new_capabilities(self):
        """新加的三样功能必须在教程里有位置，否则用户只能用猜的。

        盯的是"用户能不能自学"：每个功能至少要写清入口在哪、怎么用、有什么前提
        （例如热键需要面板在运行、照片会被转存、体检只检查不自动改）。
        """
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        for phrase in (
            "剪贴板全局热键", "检测并保存", "被别的程序占用",   # 热键
            "面板主题", "随时刻", "我的照片", "data\\theme",     # 主题
            "课表体检", "建议核对", "不会自动修改课表",           # 体检
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_tutorial_has_five_ways_to_add_notices(self):
        """通知录入口从四种变成五种（新增剪贴板热键），标题和目录要一起改。"""
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        self.assertIn("把群通知加进来（五种方式）", text)
        self.assertIn("方式五：收件箱", text)
        self.assertNotIn("把群通知加进来（四种方式）", text)

    def test_tutorial_uses_the_labels_that_actually_exist(self):
        """文档里写的按钮/菜单名必须和程序里的一模一样。

        界面文案改成书面文体那一次，改名的地方不少（清空全部通知 → 清空通知……），
        文档没跟着改的话，第三方用户会照着一个不存在的按钮找半天。
        """
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        for phrase in ("清空通知", "清空课程表", "删除所选", "最小化至托盘",
                       "打开客户端窗口", "呼出客户端窗口",
                       "暂无日程安排", "假期、调休与节日彩蛋",
                       "编辑此条通知…",
                       # 面板右下角那个小齿轮是设置入口，教程必须写清楚它叫什么
                       "日程表设置…", "⚙"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_tutorial_has_no_stale_labels(self):
        """已经改掉的旧名字不许再出现在文档里。"""
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        for phrase in ("清空全部通知", "标记完成（从日程里去掉）", "复制这条内容",
                       "编辑选中", "删除选中", "点一下这条横幅", "当前暂无相关日程",
                       "打开控制台", "最小化到托盘", "桌面模式下让面板不吃鼠标",
                       # 横幅上那行操作说明按用户要求删掉了，文档也不许再提
                       "点击横幅查看节日寄语",
                       # 彩蛋不再依赖假期名，这句老话必须消失
                       "名称需与节日一致", "名字要和节日对得上",
                       # 第 18 轮反馈：这两样从设置页删掉了，"（原方案）"也不许再提
                       "（原方案）", "自动整合 inbox 间隔"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)


class TutorialImageTests(unittest.TestCase):
    """教程配图：真的要贴进来，缺图要明说。

    用户原话：「用比较直白的方式说明，部分需要着重说明的配备有图片」。
    配图最容易烂的方式是"文件被挪走/改名"——正文里那行 `![](…)` 还在，
    但窗口里什么都没有，看着像坏了。所以两头都钉：文件在不在、窗口里贴没贴上。
    """

    def _references(self) -> list[tuple[str, str]]:
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        refs = []
        for line in text.splitlines():
            found = tutorial._IMAGE_RE.match(line.strip())
            if found:
                refs.append((found.group(1), found.group(2)))
        return refs

    def test_every_referenced_image_exists(self):
        base = tutorial.tutorial_path().parent
        refs = self._references()
        missing = [path for _caption, path in refs if not (base / path).is_file()]
        self.assertEqual(missing, [], f"教程引用了不存在的配图：{missing}")

    def test_there_are_enough_images_to_be_useful(self):
        """关键步骤都要有图，不是只放一张充数。"""
        self.assertGreaterEqual(len(self._references()), 10,
                                "教程里的配图太少了（关键操作都要配图）")

    def test_the_window_actually_embeds_them(self):
        import tkinter as tk

        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")

        window = tutorial.TutorialWindow()
        try:
            window.root.update()
            self.assertEqual(window._missing_images, 0, "有配图没贴上")
            self.assertEqual(len(window._images), len(self._references()),
                             "贴进来的图和教程里引用的数量对不上")
        finally:
            window.close()

    def test_a_missing_image_is_reported_instead_of_being_silent(self):
        import tkinter as tk

        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")

        window = tutorial.TutorialWindow()
        try:
            window.root.update()
            window._insert_image("images/这张图不存在.png", "说明")
            body = window.text.get("1.0", "end")
            self.assertIn("配图缺失", body, "找不到图却什么都不说，用户只会以为教程本来就没图")
        finally:
            window.close()


class MarkdownBlockTests(unittest.TestCase):
    """共用的 Markdown 解析：教程窗和浏览器版都吃这一份，别各写一遍。"""

    def test_recognises_the_things_the_tutorial_uses(self):
        from agenda import markdown_blocks

        text = (
            "# 大标题\n\n"
            "## 1. 一章\n\n"
            "### 1.1 小节\n\n"
            "一句话说明。\n\n"
            "| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n\n"
            "![说明](images/a.png)\n\n"
            "- 列表项\n\n"
            "> 引用一句\n\n"
            "```\ncode line\n```\n\n"
            "---\n"
        )
        kinds = [block.kind for block in markdown_blocks.iter_blocks(text)]
        self.assertIn("heading", kinds)
        self.assertIn("paragraph", kinds)
        self.assertIn("table_row", kinds)
        self.assertIn("image", kinds)
        self.assertIn("bullet", kinds)
        self.assertIn("quote", kinds)
        self.assertIn("code", kinds)
        self.assertIn("rule", kinds)
        # 表格分隔行不该被当成内容
        rows = [b.payload for b in markdown_blocks.iter_blocks(text) if b.kind == "table_row"]
        self.assertEqual(len(rows), 2, f"分隔行混进来了：{rows}")

    def test_inline_markup_is_stripped(self):
        from agenda import markdown_blocks

        self.assertEqual(markdown_blocks.plain("一份**从头到尾**的说明"), "一份从头到尾的说明")
        self.assertEqual(markdown_blocks.plain("跑 `main.py`"), "跑 main.py")
        self.assertEqual(markdown_blocks.plain("见 [第 4 节](#a)"), "见 第 4 节")

    def test_图片的路径与说明都取到了(self):
        from agenda import markdown_blocks

        blocks = [b for b in markdown_blocks.iter_blocks("![说明](images/a.png)")]
        self.assertEqual(blocks[0].payload, ("images/a.png", "说明"))


class TutorialHtmlTests(unittest.TestCase):
    """浏览器版教程：用户要"能放大图、能收藏"，所以这条链子得是通的。"""

    def test_the_page_is_built_with_cards_and_figures(self):
        from agenda import tutorial_html

        markdown_path = tutorial.tutorial_path()
        html = tutorial_html.build_html(markdown_path.read_text(encoding="utf-8"))
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('class="card"', html, "浏览器版没有卡片式分章")
        self.assertIn("<figure>", html, "配图没进 HTML")

    def test_images_are_referenced_relatively(self):
        """图片必须是**相对路径**：HTML 生成在教程旁边，整个 docs 拷走也能看。"""
        from agenda import tutorial_html

        markdown_path = tutorial.tutorial_path()
        target = tutorial_html.write_html(markdown_path)
        self.assertIsNotNone(target)
        self.assertEqual(target.parent, markdown_path.parent,
                         "HTML 没生成在教程旁边，图片会全部裂开")
        html = target.read_text(encoding="utf-8")
        self.assertNotIn('src="E:', html, "用了绝对路径，换台机器图就没了")
        base = markdown_path.parent
        for _caption, relative in self._image_refs():
            self.assertTrue((base / relative).is_file(), f"HTML 里引用的图不存在：{relative}")
            self.assertIn(f'src="{relative}"', html)

    def _image_refs(self) -> list[tuple[str, str]]:
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        return [(m.group(1), m.group(2))
                for m in (tutorial._IMAGE_RE.match(line.strip())
                          for line in text.splitlines()) if m]

    def test_a_missing_markdown_file_returns_none(self):
        from agenda import tutorial_html

        self.assertIsNone(tutorial_html.write_html(Path("不存在的目录/教程.md")))

    def test_the_cli_entry_exists(self):
        import main as cli

        args = cli.build_parser().parse_args(["--tutorial-web"])
        self.assertTrue(args.tutorial_web)


    def test_every_chapter_anchor_points_at_its_own_chapter(self):
        """目录跳转要跳到**各章自己的位置**，不能全挤在文末。

        真踩过：卡片标题栏那段先插入、后设 mark，又没锁 gravity，
        于是所有锚点被后续插入一路顶到文档结尾 —— 点目录等于跳到文末。
        """
        import tkinter as tk

        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")

        window = tutorial.TutorialWindow()
        try:
            window.root.update()
            lines = []
            for index in range(window.toc.size()):
                mark = window._anchors[window.toc.get(index)]
                lines.append(int(window.text.index(mark).split(".")[0]))
            self.assertEqual(lines, sorted(lines), f"锚点顺序乱了：{lines}")
            self.assertEqual(len(set(lines)), len(lines), f"有锚点重合：{lines}")
            end_line = int(window.text.index("end-1c").split(".")[0])
            self.assertLess(lines[-1], end_line, f"最后一个锚点跑到文末了：{lines}")
            self.assertGreater(lines[0], 1, "第一章的锚点跑到最顶上去了")
        finally:
            window.close()


    def test_every_internal_link_points_at_a_real_chapter(self):
        """文档里的 `](#…)` 必须都能跳到某一章。

        章节改名之后忘了改锚点，浏览器版和 GitHub 上就是一条死链 ——
        肉眼根本看不出来（点了没反应而已）。实测踩过一次：
        `#5-把群通知加进来` 在章节改成"（五种方式）"之后就失效了。
        """
        from agenda import markdown_blocks

        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        targets = set(re.findall(r"\]\(#([^)]+)\)", text))
        slugs = {markdown_blocks.slug(str(block.payload[1]))
                 for block in markdown_blocks.iter_blocks(text)
                 if block.kind == "heading" and block.payload[0] == 2}
        broken = sorted(targets - slugs)
        self.assertEqual(broken, [], f"这些锚点跳不到任何章节：{broken}")

    def test_the_html_keeps_those_anchors(self):
        """浏览器版的目录链接要真的能跳（章节 section 带上 id）。"""
        from agenda import tutorial_html

        markdown_path = tutorial.tutorial_path()
        target = tutorial_html.write_html(markdown_path)
        html = target.read_text(encoding="utf-8")
        for anchor in ("1-它是干什么的", "6-处理单条日程右键"):
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', html, f"HTML 里没有这个锚点：{anchor}")
                self.assertIn(f'href="#{anchor}"', html, f"HTML 里没有指向它的链接")


class MarkdownPlainTests(unittest.TestCase):
    def test_strips_emphasis_and_code(self):
        self.assertEqual(tutorial.plain_markdown("一份**从头到尾**的说明"), "一份从头到尾的说明")
        self.assertEqual(tutorial.plain_markdown("跑 `main.py --status`"), "跑 main.py --status")

    def test_keeps_link_text_drops_target(self):
        self.assertEqual(tutorial.plain_markdown("见 [第 4 节](#4-把课表导进来)"), "见 第 4 节")

    def test_plain_text_is_untouched(self):
        self.assertEqual(tutorial.plain_markdown("普通一行"), "普通一行")


class TutorialWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")

    def test_renders_headings_into_the_toc(self):
        window = tutorial.TutorialWindow()
        try:
            window.root.update()
            titles = [window.toc.get(i) for i in range(window.toc.size())]
            self.assertIn("1. 它是干什么的", titles)
            self.assertIn("6. 处理单条日程：右键", titles)
            # 三级标题不进目录（目录要只有章节，否则一屏全是子条目）
            self.assertNotIn("第一步：打开", titles)
            self.assertGreater(window.toc.size(), 10)
        finally:
            window.close()

    def test_search_finds_and_highlights(self):
        window = tutorial.TutorialWindow()
        try:
            window.root.update()
            window.query.set("标记为已完成")
            window.search()
            self.assertTrue(getattr(window, "_hits", []), "搜索没找到任何匹配")
            ranges = window.text.tag_ranges("find")
            self.assertTrue(ranges, "命中的文字没有被高亮")
            window.search_next()      # 跳到下一处，不该抛异常
        finally:
            window.close()

    def test_search_with_no_match_is_harmless(self):
        window = tutorial.TutorialWindow()
        try:
            window.root.update()
            window.query.set("这个词肯定不存在zzz")
            window.search()
            self.assertEqual(getattr(window, "_hits", []), [])
        finally:
            window.close()


class SettingsEntryTests(unittest.TestCase):
    def test_settings_tab_has_a_permanent_tutorial_button(self):
        """设置页里必须有一个常驻的教程入口（不是折叠起来的、不是菜单里的）。"""
        source = (ROOT / "agenda" / "control_window.py").read_text(encoding="utf-8")
        self.assertIn("查看使用教程", source)
        self.assertIn("def open_tutorial", source)
        # 入口建在 _build_settings_tab 里
        tab = source.split("def _build_settings_tab")[1].split("def save_settings")[0]
        self.assertIn("查看使用教程", tab, "教程入口不在设置页里")

    def test_cli_flag_exists(self):
        import main as cli

        args = cli.build_parser().parse_args(["--tutorial"])
        self.assertTrue(args.tutorial)


if __name__ == "__main__":
    unittest.main(verbosity=2)
