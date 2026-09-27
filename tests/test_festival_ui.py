"""节日彩蛋横幅 + 控制台「假期」页的界面级测试。

跑在真 Tk 上（没有显示环境就跳过），验的是"用户真的能看到"：
横幅出没出现、点一下彩蛋展不展开、控制台两张表填没填上。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date as Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.date_entry import DateEntry  # noqa: E402
from agenda.festival_banner import FestivalBanner  # noqa: E402
from agenda.holidays import Calendar, Holiday, Makeup  # noqa: E402

CALENDAR = {
    "holidays": [{"name": "国庆节", "start": "2026-10-01", "end": "2026-10-08"}],
    "makeups": [{"date": "2026-10-10", "source": "2026-10-07"}],
}


def seed(tmp: str) -> Path:
    data = Path(tmp)
    (data / "timetable.json").write_text(json.dumps({
        "termStart": "2026-09-05",
        "periods": [["08:00", "09:50"], ["10:10", "12:00"]],
        "courses": [{"name": "数学分析1", "weekday": "周三", "period": "1-2",
                     "location": "A-330", "teacher": "陈立", "weeks": "1-16"}],
    }, ensure_ascii=False), encoding="utf-8")
    (data / CALENDAR_FILE).write_text(json.dumps(CALENDAR, ensure_ascii=False), encoding="utf-8")
    return data


CALENDAR_FILE = "calendar.json"


class FestivalBannerTests(unittest.TestCase):
    def setUp(self):
        from agenda.panel import AgendaPanel

        self.tmp = tempfile.TemporaryDirectory()
        self.data = seed(self.tmp.name)
        try:
            self.panel = AgendaPanel(self.data, pipeline_ms=0, autostart_pipeline=False,
                                     window_mode="topmost", position=(80, 60))
        except Exception as error:
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.panel.root.update()

    def tearDown(self):
        try:
            self.panel.quit()          # 走真退出路径：顺带验一遍定时器都取消干净了
        except Exception:
            try:
                self.panel.root.destroy()
            except Exception:
                pass
        self.tmp.cleanup()

    def _go_to(self, day: Date):
        self.panel.today_override = day
        self.panel.refresh()
        self.panel.root.update()

    def test_banner_appears_inside_the_holiday(self):
        self._go_to(Date(2026, 10, 2))
        banner = self.panel._festival_banner
        self.assertIsNotNone(banner, "国庆假期里应该有节日横幅")
        self.assertEqual(banner.festival.name, "国庆节")
        self.assertIn("国庆节", banner.text_label.cget("text"))
        self.assertIn("★", banner.text_label.cget("text"))

    def test_banner_appears_on_the_exact_festival_day(self):
        self._go_to(Date(2026, 9, 25))       # 中秋当天，没配假期
        banner = self.panel._festival_banner
        self.assertIsNotNone(banner)
        self.assertEqual(banner.festival.name, "中秋节")

    def test_banner_is_gone_on_a_normal_day(self):
        self._go_to(Date(2026, 11, 15))        # 离任何节日都远
        self.assertIsNone(self.panel._festival_banner, "平常日子不该挂着节日横幅")

    def test_banner_shows_up_inside_the_window(self):
        """范围扩大后：节前 3 天就有横幅，并且写明"还有几天"。"""
        self._go_to(Date(2026, 9, 23))
        banner = self.panel._festival_banner
        self.assertIsNotNone(banner, "节前 3 天就该进入节日状态")
        self.assertEqual(banner.festival.name, "中秋节")
        self.assertIn("还有 2 天", banner.text_label.cget("text"))

    def test_banner_is_removed_when_the_festival_is_over(self):
        """踩过的坑：只比 key 的话，"横幅还在但今天没节日"会被当成没变化，
        国庆的横幅一路挂到调休那天。现在收尾期（假后 3 天）保留，之后必须撤掉。"""
        self._go_to(Date(2026, 10, 2))
        self.assertIsNotNone(self.panel._festival_banner)
        self._go_to(Date(2026, 10, 10))       # 还在收尾期里 → 保留
        self.assertIsNotNone(self.panel._festival_banner, "收尾期内应保留节日状态")
        self._go_to(Date(2026, 11, 15))        # 早就过去了 → 撤掉
        self.assertIsNone(self.panel._festival_banner, "节日过完了横幅必须撤掉")

    def test_border_takes_the_festival_color(self):
        """用户要求：日程表的边框要和节日主题吻合。

        边框是**动画**的：在节日主色和面板底色之间来回混（"呼吸"）。
        所以这里不能断言某个瞬间的具体颜色——那取决于采样时动画跑到哪一帧，
        是个竞态（真踩过：同一条用例时过时不过，因为 `update()` 一次正好可能
        把第一帧跑掉，也可能没跑）。改成断言与相位无关的性质：
          * 进节日后边框不再是普通边框色，且边框加粗；
          * 连续采样能看到**颜色在变**（= 真的在呼吸），且始终不回退成普通边框色；
          * 离开节日后恢复成普通边框色与细边框。
        """
        import time

        def sample_border(times: int = 14, gap: float = 0.03) -> list[str]:
            seen: list[str] = []
            for _ in range(times):
                self.panel.root.update()
                seen.append(self.panel.outer.cget("bg").lower())
                time.sleep(gap)
            return seen

        self._go_to(Date(2026, 11, 15))        # 离节日远：普通细边框
        normal = self.panel.outer.cget("bg").lower()
        self.assertEqual(self.panel.shell.pack_info()["padx"], self.panel.FRAME_PAD)

        self._go_to(Date(2026, 9, 25))
        self.assertEqual(self.panel.shell.pack_info()["padx"], self.panel.FESTIVAL_FRAME_PAD,
                         "节日期间边框应加粗")
        samples = sample_border()
        self.assertTrue(all(color != normal for color in samples),
                        f"节日期间边框不该退回普通边框色：{sorted(set(samples))}")
        self.assertGreater(len(set(samples)), 1,
                           f"边框颜色没有在「呼吸」（采样到的都是 {samples[0]}）")

        self._go_to(Date(2026, 11, 15))
        self.assertEqual(self.panel.outer.cget("bg").lower(), normal)
        self.assertEqual(self.panel.shell.pack_info()["padx"], self.panel.FRAME_PAD)

    def test_post_holiday_countdown_strip(self):
        """假期结束后三天：面板上有一条按天数递减的收心倒计时。"""
        self._go_to(Date(2026, 10, 9))         # 演示日历里国庆是 10/1–10/8
        strip = getattr(self.panel, "_countdown_strip", None)
        self.assertIsNotNone(strip, "收尾期应该显示倒计时")
        text = self.panel._countdown_label.cget("text")
        self.assertIn("收心倒计时", text)
        self.assertIn("3 天", text)
        self._go_to(Date(2026, 10, 11))
        self.assertIn("1 天", self.panel._countdown_label.cget("text"))
        self._go_to(Date(2026, 11, 15))
        self.assertIsNone(getattr(self.panel, "_countdown_strip", None),
                          "收尾期过了要把倒计时收掉")

    def test_clicking_reveals_the_egg_and_collapses_again(self):
        self._go_to(Date(2026, 9, 25))
        banner = self.panel._festival_banner
        self.assertFalse(banner.egg_visible)
        banner.toggle_egg()
        self.panel.root.update()
        self.assertTrue(banner.egg_visible)
        self.assertTrue(banner.egg_label.winfo_ismapped(), "彩蛋文字没显示出来")
        self.assertIn("婵娟", banner.egg_label.cget("text"))
        banner.toggle_egg()
        self.panel.root.update()
        self.assertFalse(banner.egg_visible)
        self.assertFalse(banner.egg_label.winfo_ismapped())

    def test_the_banner_has_no_operating_instructions(self):
        """横幅上不该挂「点击横幅查看节日寄语」这类操作说明。

        用户原话：「把图二的那行字删掉，同时类似的解释文字都删掉，太掉价了」。
        横幅整块可点就够了。
        """
        self._go_to(Date(2026, 9, 25))
        banner = self.panel._festival_banner
        texts = [child.cget("text") for child in banner.winfo_children()
                 if hasattr(child, "cget") and "text" in child.keys()]
        for text in texts:
            self.assertNotIn("点击横幅", text, f"横幅上还留着操作说明：{text}")
            self.assertNotIn("收起", text, f"横幅上还留着操作说明：{text}")
        self.assertFalse(hasattr(banner, "tip_label"), "说明标签应当整个删掉")

    def test_banner_has_an_animation_job_and_stops_cleanly(self):
        self._go_to(Date(2026, 9, 25))
        banner = self.panel._festival_banner
        self.assertIsNotNone(banner._job, "装饰动画没在跑")
        banner.stop()
        self.assertIsNone(banner._job)

    def test_panel_header_shows_the_year(self):
        self._go_to(Date(2026, 9, 25))
        text = self.panel.date_label.cget("text")
        self.assertIn("2026年9月25日", text)
        self.assertIn("周五", text)

    def test_makeup_small_text_next_to_the_date(self):
        """用户点名要的那行小字：日期旁边的「调休X月X日日程」。"""
        self._go_to(Date(2026, 10, 10))
        section = self.panel.timeline.sections[0]
        self.assertEqual(section.makeup_label, "调休10月7日日程")
        head = self.panel._day_heads[0]
        labels = [child.cget("text") for child in head.winfo_children()
                  if hasattr(child, "cget") and "text" in child.keys()]
        self.assertTrue(any(text == "调休10月7日日程" for text in labels),
                        f"日期旁边没画出那行小字：{labels}")

    def test_day_heads_do_not_pile_up(self):
        """日期头每次重排都新建 —— 旧的必须销毁，否则一天下来攒上万个控件。"""
        for offset in range(4):
            self._go_to(Date(2026, 10, 2 + offset))
            if not self.panel.timeline.total_cards:
                # 整周都没有安排：只画一句空状态提示，不建任何日期头
                self.assertEqual(self.panel._day_heads, [],
                                 "空状态下还留着日期头引用")
                continue
            # 只给"有安排的日子 + 今天"建日期头（空着的未来日子不占版面）
            visible = [section for section in self.panel.timeline.sections
                       if section.cards or section.is_today]
            self.assertEqual(len(self.panel._day_heads), len(visible))
        heads = [child for child in self.panel.inner.winfo_children()
                 if child.winfo_class() == "Frame"]
        # 缓存复用的卡片 + 当前这一轮的日期头，不该是历史累积量
        self.assertLessEqual(len(heads), 40, f"inner 下面挂了 {len(heads)} 个控件，日期头没清干净")

    def test_holiday_today_shows_a_notice_but_no_course(self):
        self._go_to(Date(2026, 10, 2))
        section = self.panel.timeline.sections[0]
        self.assertEqual(section.course_count, 0)
        self.assertIn("放假", self.panel.status_label.cget("text"))


class HolidayTabTests(unittest.TestCase):
    """控制台「假期」页：两张表能读能写。"""

    def setUp(self):
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        self.mod = control_window_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.data = seed(self.tmp.name)
        config = ClientConfig.load(self.data)
        config.panel_visible = False

        def save(payload):
            (self.data / "timetable.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        from types import SimpleNamespace
        controller = SimpleNamespace(
            data_dir=self.data, config=config,
            load_timetable_payload=lambda: json.loads(
                (self.data / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=save,
            restart_panel=lambda: None,
            refresh_events=lambda: [],
            panel_running=lambda: False,
            panel_pid=lambda: None,
            save=lambda: None,
        )
        try:
            self.window = ControlWindow(controller)
        except Exception as error:
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.root = self.window.root
        self.root.geometry("+4000+4000")

    def tearDown(self):
        try:
            self.window.stop_tick()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_holiday_dialog_uses_the_masked_date_widget(self):
        """新增假期窗里的日期必须是"短横线删不掉"的控件，并且原样写回 ISO 日期。"""
        import tkinter as tk

        window = self.window
        seen: dict = {}

        def fake_wait(dialog):
            # 替掉模态等待：检查控件 → 填值 → 点保存
            entries = []

            def walk(widget):
                for child in widget.winfo_children():
                    entries.append(child)
                    walk(child)

            walk(dialog)
            dates = [child for child in entries if isinstance(child, DateEntry)]
            seen["dates"] = dates
            for widget in entries:
                if isinstance(widget, tk.Entry) and widget.get() == "":
                    widget.insert(0, "寒假")
                    break
            if dates:
                dates[0].set("2027-01-20")
                dates[1].set("2027-02-20")
            for widget in entries:
                if isinstance(widget, tk.Button) and widget.cget("text") == "保存":
                    widget.invoke()
                    break

        real_wait = window.root.wait_window
        window.root.wait_window = fake_wait
        try:
            window.add_holiday_dialog()
        finally:
            window.root.wait_window = real_wait
        self.assertEqual(len(seen.get("dates", [])), 2, "开始/结束都应该是日期控件")
        stored = {item.name: item for item in window._calendar().holidays}
        self.assertIn("寒假", stored)
        self.assertEqual(str(stored["寒假"].start), "2027-01-20")
        self.assertEqual(str(stored["寒假"].end), "2027-02-20")

    def test_holiday_dialog_rejects_an_incomplete_date(self):
        """日期只填了一半时不许静默当成"没填"，要提示并保持窗口。"""
        import tkinter as tk

        window = self.window
        warned: list[str] = []
        real_warn = self.mod.messagebox.showwarning
        self.mod.messagebox.showwarning = lambda title, message, **kw: warned.append(message)

        def fake_wait(dialog):
            entries = []

            def walk(widget):
                for child in widget.winfo_children():
                    entries.append(child)
                    walk(child)

            walk(dialog)
            dates = [child for child in entries if isinstance(child, DateEntry)]
            dates[0].set("2027-01-20")
            dates[1].set("2027-02")          # 只填到月
            for widget in entries:
                if isinstance(widget, tk.Button) and widget.cget("text") == "保存":
                    widget.invoke()
                    break

        real_wait = window.root.wait_window
        window.root.wait_window = fake_wait
        try:
            before = len(window._calendar().holidays)
            window.add_holiday_dialog()
            after = len(window._calendar().holidays)
        finally:
            window.root.wait_window = real_wait
            self.mod.messagebox.showwarning = real_warn
        self.assertEqual(warned, ["请把开始日期与结束日期的年、月、日都填写完整。"])
        self.assertEqual(after, before, "日期不完整时不该写入")


    def _rows(self, tree):
        return [tree.item(item, "values") for item in tree.get_children()]

    def test_tab_exists(self):
        titles = [self.window.notebook.tab(tab, "text")
                  for tab in self.window.notebook.tabs()]
        self.assertIn("假期", titles)

    def test_trees_are_filled_from_calendar_json(self):
        self.window.refresh_holidays()
        self.root.update()
        holidays = self._rows(self.window.holiday_tree)
        makeups = self._rows(self.window.makeup_tree)
        self.assertEqual(holidays[0][0], "国庆节")
        self.assertEqual(holidays[0][1], "2026-10-01")
        self.assertEqual(holidays[0][2], "2026-10-08")
        self.assertEqual(holidays[0][3], "8 天")
        self.assertIn(holidays[0][4], ("未开始", "进行中", "收尾中", "已结束"),
                      f"状态列取值不对：{holidays[0][4]}")
        self.assertEqual(makeups[0][0], "2026-10-10")
        self.assertEqual(makeups[0][2], "2026-10-07")
        self.assertEqual(makeups[0][4], "调休10月7日日程")

    def test_holiday_status_column_reflects_today(self):
        """状态列：未开始 / 进行中 / 收尾中 / 已结束 —— "假期自动结束"要看得见。"""
        import datetime as dt

        from agenda.holidays import Holiday

        today = dt.date.today()
        calendar = self.window._calendar()
        calendar.holidays = [
            Holiday("进行中", today - dt.timedelta(days=1), today + dt.timedelta(days=2)),
            Holiday("收尾中", today - dt.timedelta(days=5), today - dt.timedelta(days=2)),
            Holiday("未开始", today + dt.timedelta(days=3), today + dt.timedelta(days=5)),
            Holiday("已结束", today - dt.timedelta(days=40), today - dt.timedelta(days=35)),
        ]
        self.window._write_calendar(calendar)
        self.root.update()
        status = {row[0]: row[4] for row in self._rows(self.window.holiday_tree)}
        self.assertEqual(status["进行中"], "进行中")
        self.assertEqual(status["收尾中"], "收尾中")
        self.assertEqual(status["未开始"], "未开始")
        self.assertEqual(status["已结束"], "已结束")

    def test_prune_holidays_removes_only_finished(self):
        """清理已结束：只删真正过期的，收尾期内的和未开始的都要留着。"""
        import datetime as dt

        from agenda import control_window as control_window_mod
        from agenda.holidays import Holiday

        today = dt.date.today()
        keep_wrap = Holiday("收尾中", today - dt.timedelta(days=5), today - dt.timedelta(days=2))
        keep_future = Holiday("未开始", today + dt.timedelta(days=3), today + dt.timedelta(days=5))
        drop = Holiday("已结束", today - dt.timedelta(days=40), today - dt.timedelta(days=35))
        calendar = self.window._calendar()
        calendar.holidays = [keep_wrap, keep_future, drop]
        self.window._write_calendar(calendar)

        real = control_window_mod.messagebox.askyesno
        control_window_mod.messagebox.askyesno = lambda *a, **kw: True
        try:
            self.window.prune_holidays()
        finally:
            control_window_mod.messagebox.askyesno = real
        self.root.update()
        names = [row[0] for row in self._rows(self.window.holiday_tree)]
        self.assertNotIn("已结束", names)
        self.assertIn("收尾中", names)
        self.assertIn("未开始", names)

    def test_writing_calendar_updates_the_trees(self):
        calendar = self.window._calendar()
        calendar.add_holiday(Holiday("元旦", Date(2027, 1, 1), Date(2027, 1, 3)))
        self.window._write_calendar(calendar)
        self.root.update()
        names = [row[0] for row in self._rows(self.window.holiday_tree)]
        self.assertIn("元旦", names)
        # 也要真的落盘（面板读的是同一个文件）
        again = Calendar.from_payload(json.loads(
            (self.data / CALENDAR_FILE).read_text(encoding="utf-8")))
        self.assertIsNotNone(again.holiday_on(Date(2027, 1, 2)))

    def test_index_helpers_parse_user_input(self):
        self.assertEqual(self.mod._parse_user_date("2026-10-01", 2026), Date(2026, 10, 1))
        self.assertEqual(self.mod._parse_user_date("10月1日", 2026), Date(2026, 10, 1))
        self.assertEqual(self.mod._parse_date_list("10月10日、10月11日", 2026),
                         [Date(2026, 10, 10), Date(2026, 10, 11)])
        self.assertEqual(self.mod._parse_date_list("10月7日-10月8日", 2026),
                         [Date(2026, 10, 7), Date(2026, 10, 8)])
        self.assertEqual(self.mod._parse_date_list("不是日期", 2026), [])

    def test_agenda_tab_wraps_the_long_hint_instead_of_clipping(self):
        """长文本要出现在**能折行的标签**里，不能塞进单元格被列宽硬切。

        用户截图里的问题是「中秋国庆连放假期中（至 10月7日，还剩 1」被切了一半。
        """
        window = self.window
        window.refresh_agenda()
        self.root.update()
        rows = self._rows(window.agenda_tree)
        self.assertTrue(rows or window.agenda_hint.cget("text"),
                        "日程表既没行也没有说明文字")
        hint = window.agenda_hint.cget("text")
        self.assertTrue(hint, "空状态说明显示在提示标签上")
        self.assertGreater(int(window.agenda_hint.cget("wraplength") or 0), 200,
                           "提示标签必须设了折行宽度")
        # 表格里的单元格文字一律不含被硬切的长句
        for row in rows:
            for cell in row:
                self.assertNotIn("还剩 1（", str(cell))

    def test_agenda_cells_wrap_to_two_lines(self):
        """长标题在单元格里折成两行（而不是被切掉）。"""
        from agenda.control_window import _fit_cell

        long_title = "思想动态调研：请各班班长收齐后提交，逾期不候，务必转告全班同学"
        fitted = _fit_cell(long_title, 260, ("Microsoft YaHei UI", 9), max_lines=2)
        self.assertIn("\n", fitted, "放不下的文字应该折行")
        self.assertEqual(len(fitted.split("\n")), 2)
        short = _fit_cell("交材料", 260, ("Microsoft YaHei UI", 9), max_lines=2)
        self.assertEqual(short, "交材料", "放得下就别动它")


if __name__ == "__main__":
    unittest.main(verbosity=2)
