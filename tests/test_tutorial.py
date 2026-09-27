"""使用教程的测试。

教程这东西最容易"写完就烂"——路径改了、章节改名了、渲染函数忘了处理某种语法，
用户点开就是一片空白或者一堆星号。所以这里钉住三件事：
  1. 教程文件真的在、章节结构还在（目录、13 个正文章节）；
  2. 渲染能把行内 Markdown 语法压掉（`**粗体**` 不能原样显示成星号）；
  3. 设置页里那个"查看使用教程"入口真的存在（用户能不能找到它）。
"""

from __future__ import annotations

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
        """每个功能区都得在教程里有对应章节，不然用户查不到。"""
        text = tutorial.tutorial_path().read_text(encoding="utf-8")
        required = [
            "## 1. 它是干什么的", "## 2. 五分钟上手", "## 3. 桌面面板怎么用",
            "## 4. 把课表导进来", "## 5. 把群通知加进来", "## 6. 处理单条日程",
            "## 7. 上课时间与节数", "## 8. 假期、调休与节日彩蛋", "## 9. 设置项逐个说明",
            "## 10. 待机模式", "## 11. 窗口找不到了怎么办", "## 12. 数据在哪",
            "## 13. 常见问题", "## 14. 命令行参数",
        ]
        for heading in required:
            with self.subTest(heading=heading):
                self.assertIn(heading, text, f"教程缺少章节：{heading}")

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
