"""一次完整跑批：扫描 inbox → 解析 → 合并入库 → 归档 → 清理过期。

这个模块是"每天自动整合"的落点：
  * 手动/半自动：把群通知粘进 inbox/，跑一次（或让面板每小时自动跑）
  * 定时：startup 里注册的计划任务每天跑一次
以后要接自动采集（读 QQ 窗口通知等），只需在 collect_* 里加一个来源。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date as Date, datetime
from pathlib import Path

from . import extract as extractor
from .models import RETENTION_DAYS, Event
from .store import EventStore

INBOX_DIRNAME = "inbox"
ARCHIVE_DIRNAME = "archive"
STATE_FILENAME = "state.json"

SUPPORTED_SUFFIXES = {".txt", ".md", ".log", ".text"}

#: 名字里带这些词的收件文件**自动跳过**，不解析。
#: 为什么要有这条：项目早期放了一份 `示例通知-可替换.txt` 到 inbox 做演示，
#: 结果它被真当成用户的通知抓进了面板——用户看到"开组会""讲座"会以为程序在编日程。
#: 宁可让一个真要用的文件因为名字带"示例"被跳过（改个名即可），也不要凭空冒出假日程。
DEMO_NAME_HINTS = ("示例", "样例", "例子", "示范", "demo", "sample", "example", "test")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_demo_name(filename: str) -> bool:
    """判断收件文件名是不是"示例/演示"性质（这类文件一律不解析）。"""
    lowered = filename.lower()
    return any(hint in lowered for hint in DEMO_NAME_HINTS)


@dataclass
class RunReport:
    """一次跑批的结果，同时用于命令行输出和面板状态行。"""

    processed_files: list[str] = field(default_factory=list)
    archived_files: list[str] = field(default_factory=list)
    candidates: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    pruned: int = 0
    skipped_messages: int = 0
    diagnostics: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    today_count: int = 0
    dry_run: bool = False

    def summary(self) -> str:
        parts = []
        if self.processed_files:
            parts.append(f"读取 {len(self.processed_files)} 个文件")
        if self.candidates:
            parts.append(f"识别 {self.candidates} 条")
        if self.added:
            parts.append(f"新增 {self.added}")
        if self.updated:
            parts.append(f"更新 {self.updated}")
        if self.unchanged:
            parts.append(f"重复 {self.unchanged}")
        if self.pruned:
            parts.append(f"清理 {self.pruned}")
        if not parts:
            parts.append("没有新通知")
        return "，".join(parts)


class Pipeline:
    """围绕一个数据目录的跑批器。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.inbox = self.data_dir / INBOX_DIRNAME
        self.archive = self.data_dir / ARCHIVE_DIRNAME
        self.store_path = self.data_dir / "events.json"
        self.state_path = self.data_dir / STATE_FILENAME

    # -- 目录 ---------------------------------------------------------------
    def ensure_layout(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.archive.mkdir(parents=True, exist_ok=True)

    def inbox_files(self) -> list[Path]:
        if not self.inbox.exists():
            return []
        files = [
            path for path in sorted(self.inbox.iterdir())
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
            and not is_demo_name(path.name)
        ]
        return files

    # -- 状态 ---------------------------------------------------------------
    def load_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_state(self, report: RunReport) -> None:
        state = {
            "lastRun": datetime.now().replace(microsecond=0).isoformat(sep=" "),
            "lastSummary": report.summary(),
            "lastFiles": report.processed_files,
            "totals": {
                "added": report.added,
                "updated": report.updated,
                "unchanged": report.unchanged,
            },
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- 主流程 -------------------------------------------------------------
    def run(self, *, dry_run: bool = False, today: Date | None = None) -> RunReport:
        self.ensure_layout()
        report = RunReport(dry_run=dry_run)
        files = self.inbox_files()

        store = EventStore.load(self.store_path) if self.store_path.exists() else EventStore(self.store_path)

        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                report.diagnostics.append(f"{path.name} 读取失败：{error}")
                continue
            if not text.strip():
                report.processed_files.append(path.name)
                continue

            outcome = extractor.extract(
                text,
                # 群名只认通知里的 [群名] 标注；文件名只是归档标签，不当来源群
                default_group=None,
                fallback_date=today or Date.today(),
                source_ref=path.name,
            )
            report.processed_files.append(path.name)
            report.candidates += len(outcome.candidates)
            report.skipped_messages += outcome.skipped
            report.diagnostics.extend(outcome.diagnostics)

            stats = store.merge(outcome.candidates)
            report.added += stats.added
            report.updated += stats.updated
            report.unchanged += stats.unchanged

        report.pruned = store.prune(today or Date.today(), RETENTION_DAYS)
        report.events = store.sorted_events()
        report.today_count = sum(
            1 for event in report.events
            if event.date == (today or Date.today()).strftime("%Y-%m-%d")
        )

        store.last_run = {
            "at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
            "summary": report.summary(),
        }

        if not dry_run:
            store.save()
            self.save_state(report)
            for path in files:
                self._archive(path)
                report.archived_files.append(path.name)
        return report

    def _archive(self, path: Path) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.archive / f"{stamp}-{path.name}"
        index = 2
        while target.exists():
            target = self.archive / f"{stamp}-{index}-{path.name}"
            index += 1
        try:
            shutil.move(str(path), str(target))
        except OSError:
            pass

    # -- 便捷入口 -----------------------------------------------------------
    def ingest_text(
        self,
        text: str,
        *,
        group: str | None = None,
        sender: str | None = None,
        source_label: str | None = None,
        today: Date | None = None,
        dry_run: bool = False,
    ) -> RunReport:
        """把一段文本直接并入（面板"快速录入"用），不经过 inbox 文件。"""
        store = EventStore.load(self.store_path) if self.store_path.exists() else EventStore(self.store_path)
        outcome = extractor.extract(
            text,
            default_group=group,
            default_sender=sender,
            fallback_date=today or Date.today(),
            source_ref=source_label or "手动录入",
        )
        report = RunReport(candidates=len(outcome.candidates), dry_run=dry_run)
        report.skipped_messages = outcome.skipped
        report.diagnostics.extend(outcome.diagnostics)
        stats = store.merge(outcome.candidates)
        report.added, report.updated, report.unchanged = stats.added, stats.updated, stats.unchanged
        report.pruned = store.prune(today or Date.today(), RETENTION_DAYS)
        report.events = store.sorted_events()
        report.today_count = sum(
            1 for event in report.events
            if event.date == (today or Date.today()).strftime("%Y-%m-%d")
        )
        if not dry_run:
            store.last_run = {
                "at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
                "summary": report.summary(),
            }
            store.save()
        return report

    def load_events(self) -> list[Event]:
        if not self.store_path.exists():
            return []
        return EventStore.load(self.store_path).sorted_events()

    def last_run_text(self) -> str | None:
        state = self.load_state()
        last = state.get("lastRun")
        summary = state.get("lastSummary")
        if not last:
            return None
        return f"上次整合 {last}" + (f"（{summary}）" if summary else "")
