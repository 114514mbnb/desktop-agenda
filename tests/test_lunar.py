"""农历换算的回归测试（vendored 的 borax `lunardate`）。

这个库是**别人写的**，随包附带，所以必须有一套"写死的历史日期"当回归网：
升级版本、或者哪天有人手抖改了 `agenda/vendor/lunardate.py`，这里会立刻红。

表里的日期都是可公开查证的事实（春节/元宵/端午/中秋/清明），
2024–2035 那一段还跟本项目早期写死的日期表逐条对过。
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import date as Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import lunar  # noqa: E402
from agenda.festival import LAST_YEAR, FIRST_YEAR, festival_occurrences  # noqa: E402

#: 春节（正月初一）
SPRING = {
    1949: (1, 29), 1966: (1, 21), 1976: (1, 31), 1984: (2, 2), 1997: (2, 7),
    2000: (2, 5), 2008: (2, 7), 2020: (1, 25), 2024: (2, 10), 2025: (1, 29),
    2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3),
    2031: (1, 23), 2032: (2, 11), 2033: (1, 31), 2034: (2, 19), 2035: (2, 8),
}

#: 中秋节（八月十五）
MIDAUTUMN = {
    1978: (9, 17), 2000: (9, 12), 2010: (9, 22), 2020: (10, 1),
    2024: (9, 17), 2025: (10, 6), 2026: (9, 25), 2027: (9, 15), 2028: (10, 3),
    2029: (9, 22), 2030: (9, 12), 2031: (10, 1), 2032: (9, 19), 2033: (9, 8),
    2034: (9, 27), 2035: (9, 16),
}

#: 端午节（五月初五）
DRAGON = {
    2024: (6, 10), 2025: (5, 31), 2026: (6, 19), 2027: (6, 9), 2028: (5, 28),
    2029: (6, 16), 2030: (6, 5), 2031: (6, 24), 2032: (6, 12), 2033: (6, 1),
    2034: (6, 20), 2035: (6, 10),
}

#: 清明（节气）
QINGMING = {
    1900: (4, 5), 2000: (4, 4), 2024: (4, 4), 2025: (4, 4), 2026: (4, 5),
    2027: (4, 5), 2028: (4, 4), 2029: (4, 4), 2030: (4, 5), 2031: (4, 5),
    2032: (4, 4), 2033: (4, 4), 2034: (4, 5), 2035: (4, 5),
}

#: 元宵节（正月十五）
LANTERN = {2024: (2, 24), 2025: (2, 12), 2026: (3, 3), 2027: (2, 20)}


class VendoredLibraryTests(unittest.TestCase):
    def test_the_vendored_file_is_untouched(self):
        """登记在 vendor/__init__.py 里的校验值，防止有人就地改上游代码。

        要改就别在这儿改：升级版本 → 整份覆盖 → 更新校验值。
        """
        path = Path(lunar.__file__).resolve().parent / "vendor" / "lunardate.py"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(digest, "640e022f069117e3f5ab1dd3b3ebaefce7be1b4cedc099fe5e7c9a24a9f3d84d",
                         "vendored 的 lunardate.py 被改过了（升级请整份覆盖并更新本测试）")

    def test_license_file_is_shipped(self):
        path = Path(lunar.__file__).resolve().parent / "vendor" / "LICENSE-borax.txt"
        self.assertTrue(path.is_file(), "vendor 目录里必须带上游许可证")
        self.assertIn("MIT License", path.read_text(encoding="utf-8"))

    def test_imports_without_third_party_packages(self):
        """纯 Python 单文件：不该把 bom/依赖带进来。"""
        source = (Path(lunar.__file__).resolve().parent / "vendor" / "lunardate.py").read_text(
            encoding="utf-8")
        for line in source.splitlines():
            if line.startswith(("import ", "from ")):
                module = line.split()[1].split(".")[0]
                self.assertIn(module, {"datetime", "re", "warnings", "typing"}, line)


class LunarConversionTests(unittest.TestCase):
    def _check(self, table: dict, function) -> None:
        for year, (month, day) in sorted(table.items()):
            with self.subTest(year=year):
                self.assertEqual(function(year), Date(year, month, day))

    def test_spring_festival(self):
        self._check(SPRING, lunar.new_year)

    def test_midautumn(self):
        self._check(MIDAUTUMN, lunar.mid_autumn)

    def test_dragon_boat(self):
        self._check(DRAGON, lunar.dragon_boat)

    def test_lantern(self):
        self._check(LANTERN, lunar.lantern_festival)

    def test_qingming(self):
        self._check(QINGMING, lunar.qingming)

    def test_solar_to_lunar_round_trip(self):
        info = lunar.solar_to_lunar(Date(2026, 9, 25))
        self.assertIsNotNone(info)
        self.assertEqual((info.month, info.day, info.leap), (8, 15, False))
        self.assertEqual(info.text(), "八月十五")
        self.assertEqual(lunar.lunar_age_text(Date(2026, 2, 17)), "正月初一")

    def test_lunar_to_solar_matches_festival_helpers(self):
        self.assertEqual(lunar.lunar_to_solar(2026, 1, 1), lunar.new_year(2026))
        self.assertEqual(lunar.lunar_to_solar(2026, 8, 15), lunar.mid_autumn(2026))

    def test_impossible_lunar_dates_return_none(self):
        # 农历月最多 30 天；2026 年没有闰月
        self.assertIsNone(lunar.lunar_to_solar(2026, 1, 31))
        self.assertIsNone(lunar.lunar_to_solar(2026, 13, 1))

    def test_eventual_year_range_is_wide(self):
        """长期覆盖：至少两百年，够任何一届学生用到毕业很多年以后。"""
        self.assertLessEqual(FIRST_YEAR, 1900)
        self.assertGreaterEqual(LAST_YEAR, 2100)
        self.assertEqual(LAST_YEAR - FIRST_YEAR + 1, 201)

    def test_out_of_range_is_none_not_an_exception(self):
        for year in (1800, 1899, 2102, 2200):
            with self.subTest(year=year):
                self.assertIsNone(lunar.new_year(year))
                self.assertIsNone(lunar.mid_autumn(year))
                self.assertIsNone(lunar.qingming(year))
                self.assertIsNone(lunar.festival_date(year, "spring"))
        self.assertIsNone(lunar.solar_to_lunar(Date(1800, 1, 1)))
        self.assertIsNone(lunar.solar_to_lunar(Date(2200, 1, 1)))

    def test_every_supported_year_resolves(self):
        """1900–2100 每一年都要能把四个农历节日算出来（一个 None 都不许有）。"""
        missing = []
        for year in range(FIRST_YEAR, LAST_YEAR + 1):
            for key, function in (("spring", lunar.new_year), ("lantern", lunar.lantern_festival),
                                  ("dragon", lunar.dragon_boat), ("midautumn", lunar.mid_autumn),
                                  ("qingming", lunar.qingming)):
                if function(year) is None:
                    missing.append(f"{year}:{key}")
        self.assertEqual(missing, [], f"这些年份算不出来：{missing[:10]}")

    def test_festival_dates_are_inside_the_supported_solar_range(self):
        for year in (1900, 1901, 2099, 2100):
            for festival in festival_occurrences(year):
                with self.subTest(year=year, festival=festival.key):
                    self.assertIsNotNone(festival.day)
                    self.assertTrue(lunar.supported(festival.day))

    def test_boundary_year_drops_only_what_it_cannot_compute(self):
        """1900 年的元旦（1/1）在农历库的下界（1/31）之前，只有它会被丢掉。"""
        keys = {festival.key for festival in festival_occurrences(1900)}
        self.assertNotIn("newyear", keys)
        for key in ("spring", "lantern", "qingming", "labor", "dragon", "midautumn", "national"):
            self.assertIn(key, keys, f"1900 年丢了 {key}")

    def test_years_outside_the_range_have_no_festivals_at_all(self):
        for year in (1899, 2101, 2200):
            with self.subTest(year=year):
                self.assertEqual(festival_occurrences(year), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
