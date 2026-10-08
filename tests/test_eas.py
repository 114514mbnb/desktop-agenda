"""教务系统适配器与导入通道的测试。

重点覆盖"换一套教务/换一所学校还能不能work"以外的部分：
解析规则、列识别、错误提示、导出回环——这些是纯函数，可以放心钉死。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date as Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import eas  # noqa: E402
from agenda.eas import zfsoft  # noqa: E402
from agenda.eas.importer import courses_to_timetable, restore_backup, save_timetable  # noqa: E402


class WeekParsingTests(unittest.TestCase):
    def test_single_and_double_weeks(self):
        self.assertEqual(eas.parse_weeks("1-16周"), tuple(range(1, 17)))
        self.assertEqual(eas.parse_weeks("1-8周(单)"), (1, 3, 5, 7))
        self.assertEqual(eas.parse_weeks("2-8周(双)"), (2, 4, 6, 8))
        self.assertEqual(eas.parse_weeks("1-5,7-11单"), (1, 3, 5, 7, 9, 11))
        self.assertEqual(eas.parse_weeks("1-5、7-11单"), (1, 3, 5, 7, 9, 11))

    def test_every_week_and_empty(self):
        self.assertIsNone(eas.parse_weeks("每周"))
        self.assertIsNone(eas.parse_weeks(""))
        self.assertIsNone(eas.parse_weeks("无"))

    def test_format_round_trip(self):
        for text in ("1-16", "1-5、7-11单", "2-8双"):
            weeks = eas.parse_weeks(text)
            self.assertIsNotNone(weeks)
            self.assertEqual(eas.parse_weeks(eas.format_weeks(weeks)), weeks, text)


class FieldParsingTests(unittest.TestCase):
    def test_weekday_variants(self):
        self.assertEqual(zfsoft._weekday_from_text("星期一"), 0)
        self.assertEqual(zfsoft._weekday_from_text("周一"), 0)
        self.assertEqual(zfsoft._weekday_from_text("3"), 2)
        self.assertEqual(zfsoft._weekday_from_text("星期日"), 6)
        self.assertEqual(zfsoft._weekday_from_text("星期天"), 6)
        self.assertIsNone(zfsoft._weekday_from_text("星期八"))

    def test_periods(self):
        self.assertEqual(zfsoft._periods_from_text("1-2节"), (1, 2))
        self.assertEqual(zfsoft._periods_from_text("第3,4节"), (3, 4))
        self.assertEqual(zfsoft._periods_from_text("5"), (5, 5))
        self.assertEqual(zfsoft._periods_from_text(""), (None, None))

    def test_term_guess(self):
        self.assertEqual(eas.guess_term(Date(2026, 9, 21)), ("2026", "3"))
        self.assertEqual(eas.guess_term(Date(2027, 3, 1)), ("2026", "12"))


ZF_HTML = """
<html><body>
<table>
  <tr><th>节次</th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th><th>星期五</th><th>星期六</th><th>星期日</th></tr>
  <tr><td>第1-2节</td>
      <td>高等数学(1-16周)[张三]逸夫楼101</td>
      <td></td><td>大学英语(1-8周)[Linda]外语楼204<br>选修课(9-16周)[王五]教三301</td>
      <td></td><td></td><td></td><td></td></tr>
  <tr><td>第3-4节</td>
      <td></td><td></td><td></td>
      <td>有机化学(1-5周)[李四]逸夫楼108</td>
      <td></td><td></td><td></td></tr>
</table>
</body></html>
"""


class ZfsoftParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = eas.ZfsoftAdapter("https://jw.example.edu.cn")

    def test_html_table_parsing(self):
        result = self.adapter.parse_course_payload(ZF_HTML)
        names = sorted(course.name for course in result.courses)
        self.assertEqual(names, ["大学英语", "有机化学", "选修课", "高等数学"])
        math = next(c for c in result.courses if c.name == "高等数学")
        self.assertEqual((math.weekday, math.start_period, math.end_period), (0, 1, 2))
        self.assertEqual(math.teacher, "张三")
        self.assertEqual(math.location, "逸夫楼101")
        self.assertEqual(math.weeks, tuple(range(1, 17)))
        elective = next(c for c in result.courses if c.name == "选修课")
        self.assertEqual(elective.weekday, 2)
        self.assertEqual(elective.weeks, tuple(range(9, 17)))

    def test_json_payload_parsing(self):
        payload = """
        {"kbList":[
          {"kcmc":"数据结构","xqj":"2","jcs":"3-4","zcd":"1-16周","xm":"陈静","cdmc":"实验楼B302"},
          {"kcmc":"操作系统","xqj":"4","jcs":"9-10","zcd":"2-16周(单)","xm":"李娜","cdmc":"教三305"}
        ]}
        """
        result = self.adapter.parse_course_payload(payload)
        self.assertEqual(result.count, 2)
        structure = next(c for c in result.courses if c.name == "数据结构")
        self.assertEqual((structure.weekday, structure.start_period, structure.end_period), (1, 3, 4))
        operating = next(c for c in result.courses if c.name == "操作系统")
        self.assertEqual(operating.weeks, (3, 5, 7, 9, 11, 13, 15))

    def test_json_without_weeks_means_every_week(self):
        payload = '{"kbList":[{"kcmc":"体育","xqj":"3","jcs":"1-2"}]}'
        result = self.adapter.parse_course_payload(payload)
        self.assertIsNone(result.courses[0].weeks)

    def test_dedupe_identical_rows(self):
        payload = '{"kbList":[{"kcmc":"A","xqj":"1","jcs":"1-2"},{"kcmc":"A","xqj":"1","jcs":"1-2"}]}'
        self.assertEqual(self.adapter.parse_course_payload(payload).count, 1)

    def test_input_field_extraction(self):
        html = '<input name="lt" value="LT-123"><input name="execution" value="e1s1"><input name="yhm">'
        fields = eas.base.find_inputs(html)
        self.assertEqual(fields["lt"], "LT-123")
        self.assertEqual(fields["execution"], "e1s1")
        self.assertEqual(fields["yhm"], "")


class WakeUpCsvTests(unittest.TestCase):
    OFFICIAL = """课程名称,星期,开始节数,结束节数,老师,地点,周数
高等数学,1,1,2,张三,逸夫楼101,1-5、7-11单
大学计算机,5,1,2,李四,无,1-16
有机化学,4,3,4,无,逸夫楼108,1-5
"""

    def test_official_template(self):
        result = eas.WakeUpCsvAdapter().parse(self.OFFICIAL)
        self.assertEqual(result.count, 3)
        computer = next(c for c in result.courses if c.name == "大学计算机")
        self.assertIsNone(computer.location)      # 「无」→ 空
        self.assertEqual(computer.teacher, "李四")
        chemistry = next(c for c in result.courses if c.name == "有机化学")
        self.assertIsNone(chemistry.teacher)
        self.assertEqual(chemistry.location, "逸夫楼108")

    def test_wrong_header_is_rejected_with_hint(self):
        with self.assertRaises(eas.EasError) as ctx:
            eas.WakeUpCsvAdapter().parse("a,b,c\n1,2,3")
        self.assertIn("课程名称", str(ctx.exception))

    def test_all_rows_invalid_is_rejected(self):
        text = "课程名称,星期,开始节数,结束节数,老师,地点,周数\n高等数学,x,1,2,张三,无,1-16\n英语,y,1,2,无,无,1-16\n"
        with self.assertRaises(eas.EasError):
            eas.WakeUpCsvAdapter().parse(text)

    def test_partial_warning_collected(self):
        text = ("课程名称,星期,开始节数,结束节数,老师,地点,周数\n"
                "高等数学,1,1,2,张三,无,1-16\n"
                "坏行,x,1,2,无,无,1-16\n")
        result = eas.WakeUpCsvAdapter().parse(text)
        self.assertEqual(result.count, 1)
        self.assertTrue(any("星期" in w for w in result.warnings))


class PlainTableTests(unittest.TestCase):
    def test_tab_separated_without_header(self):
        text = "高等数学\t周一\t1\t2\t张三\t逸夫楼101\t1-16\n大学英语\t周三\t3\t4\tLinda\t外语楼204\t1-16"
        result = eas.PlainTableAdapter().parse(text)
        self.assertEqual(result.count, 2)

    def test_header_detection(self):
        text = "课程,星期,开始节,结束节,老师,地点,周数\n线性代数,周二,1,2,王强,教二108,1-16"
        result = eas.PlainTableAdapter().parse(text)
        self.assertEqual(result.courses[0].name, "线性代数")
        self.assertEqual(result.courses[0].weekday, 1)

    def test_empty_is_rejected(self):
        with self.assertRaises(eas.EasError):
            eas.PlainTableAdapter().parse("   \n  ")


class ImportWritingTests(unittest.TestCase):
    def test_courses_to_timetable_payload(self):
        courses = [eas.Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                              teacher="张三", location="逸夫楼101", weeks=(1, 3, 5))]
        payload = courses_to_timetable(courses, term_start=Date(2026, 9, 7))
        self.assertEqual(payload["termStart"], "2026-09-07")
        course = payload["courses"][0]
        self.assertEqual(course["period"], "1-2")
        self.assertEqual(course["weeks"], "1、3、5单")
        self.assertIn("periods", payload)

    def test_save_and_restore_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "timetable.json").write_text('{"courses":[{"name":"旧课"}]}', encoding="utf-8")
            save_timetable(root, {"courses": [{"name": "新课"}]})
            self.assertIn("新课", (root / "timetable.json").read_text(encoding="utf-8"))
            restored = restore_backup(root)
            self.assertIsNotNone(restored)
            self.assertIn("旧课", (root / "timetable.json").read_text(encoding="utf-8"))

    def test_restore_without_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(restore_backup(Path(tmp)))


class DivLayoutTests(unittest.TestCase):
    """div 布局课表（部分正方版本不用 table）。

    踩过的坑：早期实现把 HTML 标签/属性当课名，还把一格里的多行拆成多门课，
    于是"课程数"暴涨全是垃圾。这里把正确行为钉住。
    """

    DIV_HTML = """
    <html><body>
    <div class="kbcontent">高等数学<br>(1-16周)[张伟]<br>教三301</div>
    <div class="kbcontent">数据结构<br>(1-16周)[陈静]<br>实验楼B302</div>
    <div class="kbgrid">星期一 第1-2节 大学英语(1-16周)[Linda]外语楼204</div>
    </body></html>
    """

    def test_div_layout_parsed_cleanly(self):
        result = eas.HtmlPageAdapter().parse(self.DIV_HTML)
        names = sorted(course.name for course in result.courses)
        self.assertEqual(names, ["大学英语", "数据结构", "高等数学"])
        math = next(c for c in result.courses if c.name == "高等数学")
        self.assertEqual(math.teacher, "张伟")
        self.assertEqual(math.location, "教三301")
        self.assertEqual(math.weeks, tuple(range(1, 17)))
        english = next(c for c in result.courses if c.name == "大学英语")
        self.assertEqual((english.start_period, english.end_period), (1, 2))

    def test_no_html_tags_leak_into_names(self):
        result = eas.HtmlPageAdapter().parse(self.DIV_HTML)
        for course in result.courses:
            self.assertNotIn("<", course.name)
            self.assertNotIn("class=", course.name)

    def test_periods_parse_variants(self):
        self.assertEqual(zfsoft._periods_from_text("第3,4节"), (3, 4))
        self.assertEqual(zfsoft._periods_from_text("1-2节"), (1, 2))
        self.assertEqual(zfsoft._periods_from_text("5"), (5, 5))
        self.assertEqual(zfsoft._periods_from_text(""), (None, None))

    def test_periods_sanity_gate(self):
        """带"节"字的是节次；明显不是一节课的范围要拒绝。"""
        self.assertEqual(zfsoft._periods_from_text("第1-16节"), (1, 16))
        self.assertEqual(zfsoft._periods_from_text("(1-16周)"), (1, 16))
        self.assertEqual(zfsoft._periods_from_text("第28-30节"), (None, None))

    def test_week_only_block_is_not_turned_into_course(self):
        """只有周次、没有星期/节次的块不能变成课程（否则垃圾课会淹掉真课）。"""
        html = '<html><body><div>周次 1-16</div><div>教学周安排</div></body></html>'
        result = eas.HtmlPageAdapter().parse(html)
        self.assertEqual(result.count, 0)


class CellLineFieldTests(unittest.TestCase):
    """一个格子拆成多行时的字段归类（浏览器 DOM 提取直接喂这个入口）。"""

    def parse(self, lines):
        return zfsoft.course_from_cell_lines(lines, 0, 1, 2)

    def test_teacher_and_room_on_separate_lines(self):
        """踩过的坑：教师行占掉"第一个短中文"，地点行就没人管了。"""
        for lines, teacher, room in (
            (["军事理论", "秦政", "1-16周", "E-302"], "秦政", "E-302"),
            (["大学英语1", "苏晴", "1-16周", "A-202"], "苏晴", "A-202"),
            (["形势与政策", "郭雯雯等", "1-16周", "A-334"], "郭雯雯等", "A-334"),
            (["高等数学", "张伟", "1-16周", "教三301"], "张伟", "教三301"),
            (["解析几何", "李文明", "1-17周(单)", "中心校区 B-101"], "李文明", "中心校区 B-101"),
            (["大学生心理健康辅导", "吴晓敏", "1-16周", "A-520"], "吴晓敏", "A-520"),
        ):
            with self.subTest(lines=lines):
                course = self.parse(lines)
                self.assertIsNotNone(course)
                self.assertEqual(course.teacher, teacher)
                self.assertEqual(course.location, room)

    def test_labeled_lines_drop_the_label(self):
        course = self.parse(["高等数学", "任课教师：张伟", "上课地点：A-330", "1-16周"])
        self.assertEqual(course.teacher, "张伟")
        self.assertEqual(course.location, "A-330")

    def test_week_line_is_not_a_room(self):
        course = self.parse(["高等数学", "张伟", "1-16周"])
        self.assertEqual(course.location, None)
        self.assertEqual(course.weeks, tuple(range(1, 17)))

    def test_name_only_still_yields_a_course(self):
        """宁可字段空着，也不能因为识别不出地点就整门课丢掉。"""
        course = self.parse(["军事理论"])
        self.assertEqual(course.name, "军事理论")
        self.assertIsNone(course.teacher)
        self.assertIsNone(course.location)

    def test_dash_and_letter_room_numbers(self):
        for room in ("A-330", "D-107", "B-201", "教三301", "中心校区 A-330"):
            with self.subTest(room=room):
                course = self.parse(["数学分析1", "陈立", "1-16周", room])
                self.assertEqual(course.location, room)


class ZhengfangNoisyCellTests(unittest.TestCase):
    """正方新版课表页：一格里把课名、教师、教室、课程代码、班级名单、考核方式全糊在一起。

    实测原文形如::

        解析几何★ / 中心校区 B-103 李文明 / -D0001-01 应数1班;应数2班;应数3班 / 考试 / 讲课:48 48 48 3

    这些行绝不能进课表——用户看到 `解析几何★ 中心校区 B-103 李文明 -D0001-01 …` 只会以为程序坏了。
    """

    NOISY = [
        (["解析几何★", "中心校区 B-103 李文明", "-D0001-01 应数1班;应数2班", "考试", "讲课:48 48 48 3"],
         "解析几何", "李文明", "中心校区 B-103"),
        (["大学生心理健康辅导★", "中心校区 A-520 吴晓敏", "-D0002-11 应数3班", "未安排"],
         "大学生心理健康辅导", "吴晓敏", "中心校区 A-520"),
        (["【调】中国近现代史纲要★", "中心校区 E-303 何芳", "-D0003-15", "考试"],
         "中国近现代史纲要", "何芳", "中心校区 E-303"),
        (["C++程序设计实验*○", "中心校区 D-107 周宏", "-D0004-04", "考试"],
         "C++程序设计实验", "周宏", "中心校区 D-107"),
        (["数学分析1★", "中心校区 A-330 陈立", "-D0005-02", "考试"],
         "数学分析1", "陈立", "中心校区 A-330"),
    ]

    def test_teacher_surnamed_zhou_is_not_read_as_a_week(self):
        """姓周的老师和教室号撞在一起时，整行不能被当成"周次行"丢掉。

        踩过（清洗测试数据时改名成「周宏」才炸出来，原来那批数据里恰好没有姓周的老师）：
        `中心校区 D-107 周宏` 里 `107 周` 命中了"周次"的正则，于是这一行被判成
        "纯周次行"直接 continue —— 地点和教师**一起消失**，而单元格看着完全正常。
        """
        course = zfsoft.course_from_cell_lines(
            ["C++程序设计实验*○", "中心校区 D-107 周宏", "-D0004-04", "考试"], 0, 3, 4)
        self.assertIsNotNone(course)
        self.assertEqual(course.teacher, "周宏", "姓周的老师被当成周次吃掉了")
        self.assertEqual(course.location, "中心校区 D-107", "地点跟着一起丢了")

    def test_week_token_detection_still_works(self):
        """收紧之后，正常的周次写法必须照旧认得出来（别把孩子跟洗澡水一起倒掉）。"""
        for text, expected in (
            ("高等数学(1-16周)[张伟]教三301", "1-16周"),
            ("大学英语 第3周 上课", "3周"),
            ("1-16周", "1-16周"),
            ("(1-8周)", "1-8周"),
            ("3,5,7周", "3,5,7周"),
            ("英语(1-16周)(单)", "1-16周"),
            ("1-16周(单)", "1-16周(单)"),
        ):
            with self.subTest(text=text):
                self.assertEqual(zfsoft._first_week_token(text), expected)
        # 教室号里的数字不该被当成周次
        for text in ("中心校区 D-107 周宏", "中心校区 A-330 周敏", "B-201", "教三301"):
            with self.subTest(text=text):
                self.assertEqual(zfsoft._first_week_token(text), "",
                                 f"{text} 被误判成周次了")

    def test_name_teacher_room_survive_the_noise(self):
        for lines, name, teacher, room in self.NOISY:
            with self.subTest(name=name):
                course = zfsoft.course_from_cell_lines(lines, 0, 3, 4)
                self.assertIsNotNone(course)
                self.assertEqual(course.name, name)
                self.assertEqual(course.teacher, teacher)
                self.assertEqual(course.location, room)

    def test_course_code_never_becomes_a_room(self):
        """-D0001-01 是课程代码，不是教室；它绝不能被拼进地点。"""
        course = zfsoft.course_from_cell_lines(
            ["解析几何★", "中心校区 B-103 李文明", "-D0001-01 应数1班"], 0, 3, 4)
        self.assertNotIn("D2060", course.location or "")
        self.assertNotIn("应数1班", course.location or "")

    def test_classification_words_are_not_teachers(self):
        """考试/未安排/讲课 出现在教师位置时是教务字段，不能当人名。"""
        for noise in ("考试", "考查", "未安排", "讲课", "必修"):
            with self.subTest(noise=noise):
                course = zfsoft.course_from_cell_lines(
                    ["高等数学", noise, "1-16周", "教三301"], 0, 1, 2)
                self.assertEqual(course.teacher, None)
                self.assertEqual(course.location, "教三301")

    def test_marks_are_stripped_from_names(self):
        for raw, expected in (("解析几何★", "解析几何"), ("C++程序设计实验*○", "C++程序设计实验"),
                              ("【调】高等代数1★", "高等代数1"), ("【停】体育1", "体育1")):
            with self.subTest(raw=raw):
                self.assertEqual(zfsoft._clean_course_name(raw), expected)


class RegistryTests(unittest.TestCase):
    def test_registered_adapters(self):
        keys = [a.info.key for a in eas.available_adapters()]
        self.assertIn("zfsoft", keys)

    def test_search_qust(self):
        matches = eas.search_schools("青岛科技")
        self.assertEqual(matches[0].name, "青岛科技大学")
        self.assertEqual(matches[0].adapter, "zfsoft")

    def test_build_adapter_requires_url(self):
        with self.assertRaises(ValueError):
            eas.build_adapter(base_url="")

    def test_build_adapter_from_school(self):
        school = eas.search_schools("青岛")[0]
        adapter = eas.build_adapter(school=school)
        self.assertEqual(adapter.base_url, school.base_url)
        self.assertEqual(adapter.login_url(), school.base_url + "/jwglxt/xtgl/login_slogin.html")

    def test_unknown_adapter_key(self):
        with self.assertRaises(KeyError):
            eas.build_adapter(adapter_key="nope", base_url="https://x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
