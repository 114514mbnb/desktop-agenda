"""把适配器抓到的课程落成 timetable.json，并回写每节课时间。

导入策略：写入文件后由客户端/面板重新读取，避免内存里两套课表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

from .base import Course, EasAdapter, EasError, FetchResult
from .zfsoft import guess_term
from ..timetable import DEFAULT_PERIODS


@dataclass
class ImportOutcome:
    """一次导入的结果，供客户端展示与用户确认。"""

    courses: list[Course] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = ""
    raw_html: str = ""
    timetable_path: Path | None = None

    @property
    def count(self) -> int:
        return len(self.courses)


def courses_to_timetable(
    courses: list[Course],
    *,
    term_start: Date | None = None,
    periods: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, object]:
    """转成 data/timetable.json 的结构（与手写课表同一格式）。"""
    payload: dict[str, object] = {
        "termStart": (term_start or Date.today()).strftime("%Y-%m-%d"),
        "periods": [[start, end] for start, end in (periods or DEFAULT_PERIODS)],
        "courses": [course.to_dict() for course in courses],
    }
    return payload


def save_timetable(data_dir: Path, payload: dict[str, object], *, backup: bool = True) -> Path:
    """写入 timetable.json；覆盖前先备份，导入错了能回退。"""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "timetable.json"
    if backup and target.exists():
        backup_path = data_dir / "timetable.json.bak"
        backup_path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def restore_backup(data_dir: Path) -> Path | None:
    """回退到导入前的课表。"""
    data_dir = Path(data_dir)
    target = data_dir / "timetable.json"
    backup = data_dir / "timetable.json.bak"
    if not backup.exists():
        return None
    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def run_eas_import(
    adapter: EasAdapter,
    *,
    username: str | None = None,
    password: str | None = None,
    log=print,
) -> ImportOutcome:
    """执行一次教务系统导入：登录 → 取课表 → 返回待确认结果（不落盘）。

    凭据只存在于本函数的调用栈与适配器内存中；函数返回后不再被引用。
    """
    outcome = ImportOutcome(source=adapter.describe())
    if username is not None and password is not None:
        log(f"正在登录 {adapter.login_url()} …")
        adapter.login(username, password)
    log("正在读取课表 …")
    result: FetchResult = adapter.fetch_courses()
    outcome.courses = result.courses
    outcome.warnings = list(result.warnings)
    outcome.raw_html = result.raw_html
    if result.source_url:
        outcome.source = result.source_url
    if not outcome.courses:
        raise EasError("没有解析到课程；请在客户端用「导出原始页面」把页面发给我，我来适配")
    return outcome


def guess_term_for(today: Date | None = None) -> tuple[str, str]:
    return guess_term(today)
