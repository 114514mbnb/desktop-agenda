"""教务系统导入（可扩展适配器框架）。

对外只暴露这几件事：
  * search_schools / build_adapter  —— 找学校、造适配器
  * WakeUpCsvAdapter               —— WakeUp 官方 7 列 CSV 模板
  * PlainTableAdapter              —— 直接从 Excel/WPS 粘贴的表格文本
  * HtmlPageAdapter                —— 手动粘贴课表网页（SSO 学校的兜底）
  * courses_to_timetable           —— 统一转成 timetable.json 结构
"""

from .base import Course, EasAdapter, EasError, FetchResult, parse_weeks, format_weeks
from .zfsoft import ZfsoftAdapter, guess_term
from .registry import (
    SCHOOLS,
    School,
    available_adapters,
    build_adapter,
    get_adapter,
    register,
    search_schools,
)
from .files import HtmlPageAdapter, PlainTableAdapter, WakeUpCsvAdapter
from .importer import ImportOutcome, courses_to_timetable, run_eas_import

# 注册内置适配器（导入本包即完成注册）
register(ZfsoftAdapter)

__all__ = [
    "Course", "EasAdapter", "EasError", "FetchResult", "parse_weeks", "format_weeks",
    "ZfsoftAdapter", "guess_term", "SCHOOLS", "School", "build_adapter", "get_adapter",
    "register", "search_schools", "available_adapters",
    "WakeUpCsvAdapter", "PlainTableAdapter", "HtmlPageAdapter",
    "ImportOutcome", "courses_to_timetable", "run_eas_import",
]
