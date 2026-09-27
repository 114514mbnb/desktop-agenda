"""命令行入口。

常用：
  python main.py                     启动桌面面板（默认）
  python main.py --once              只整合一次 inbox 并打印结果（适合计划任务）
  python main.py --paste             打开快速录入窗口
  python main.py --tutorial          打开使用教程窗口
  python main.py --add-text "…"      直接并入一段文本（也支持 --add-text @文件）
  python main.py --status            打印当前一周日程
  python main.py --check-timetable   检查课程表读到没有
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as Date, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from agenda.holidays import load_calendar  # noqa: E402
from agenda.pipeline import Pipeline, project_root  # noqa: E402
from agenda.timeline import build_timeline, day_heading, footer_summary  # noqa: E402
from agenda.timetable import load_timetable  # noqa: E402


def _default_data_dir() -> Path:
    import os
    override = os.environ.get("AGENDA_DATA_DIR")
    if override:
        return Path(override)
    return project_root() / "data"


def _read_add_text(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8", errors="replace")
    return value


def _process_alive(pid: int) -> bool:
    """纯探测进程是否存活。

    不能用 os.kill(pid, 0)：Windows 上它会调用 TerminateProcess，
    真的把已经在跑的第二个面板杀掉（这个坑差点踩进去）。
    """
    if sys.platform != "win32":
        import os
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


def _acquire_lock(data_dir: Path, name: str) -> Path | None:
    """通用单实例锁：data/<name>.lock 存 pid，占用者还活着就拒绝启动。"""
    import os

    lock = Path(data_dir) / f"{name}.lock"
    if lock.exists():
        try:
            # strip("\ufeff") 是必要的：PowerShell 的 Set-Content -Encoding utf8
            # 会写 BOM，int("\\ufeff123") 直接抛错 → 会被误判成陈旧锁
            pid = int(lock.read_text(encoding="utf-8-sig").strip().strip("\ufeff") or "0")
        except (ValueError, OSError):
            pid = 0
        if pid > 0 and pid != os.getpid() and _process_alive(pid):
            return None
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        return None
    return lock


def _release_lock(lock: Path | None) -> None:
    if lock is None:
        return
    try:
        lock.unlink()
    except OSError:
        pass


def _acquire_panel_lock(data_dir: Path) -> Path | None:
    """面板单实例：开机自启 + 手动双击可能同时拉起两个面板。"""
    return _acquire_lock(data_dir, "panel")


def cmd_run(args: argparse.Namespace) -> int:
    from agenda.panel import AgendaPanel

    lock = _acquire_panel_lock(args.data_dir)
    if lock is None:
        print("面板已在运行（data/panel.lock），不再重复启动。")
        return 0
    position = None
    if args.panel_x is not None and args.panel_y is not None:
        position = (args.panel_x, args.panel_y)
    # 桌面宠物模式与鼠标穿透都以 data/client.json 为准（设置页改完无需重启客户端
    # 就能生效），显式给了 --no-pet-mode / --no-click-through 才覆盖。
    #
    # 这里曾经是 `click_through=not args.no_click_through`：`--no-click-through`
    # 默认 False，于是**手工运行 `python main.py`（教程第 14 节就这么教的）拿到的
    # 是一个点不动的面板** —— 界面里的默认值明明是"不穿透"，命令行却反过来，
    # 用户只会再次得出"这不能编辑"的结论。现在两边共用一个来源。
    pet_mode = True
    click_through = False
    try:
        from agenda.client_config import ClientConfig
        client_config = ClientConfig.load(args.data_dir)
        pet_mode = bool(client_config.pet_mode)
        click_through = bool(client_config.click_through)
    except Exception:
        pass
    if args.no_pet_mode:
        pet_mode = False
    if args.no_click_through:
        click_through = False
    try:
        panel = AgendaPanel(
            args.data_dir,
            width=args.width,
            refresh_ms=max(5000, args.refresh * 1000),
            # 支持小数分钟：0.25 就是 15 秒一次（排障与实测用）
            pipeline_ms=int(max(0.0, args.pipeline_minutes) * 60_000),
            topmost=args.window_mode == "topmost",
            position=position,
            window_mode=args.window_mode,
            click_through=click_through,
            desktop_only=not args.always_visible,
            pet_mode=pet_mode,
        )
        panel.run()
    finally:
        _release_lock(lock)
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    pipeline = Pipeline(args.data_dir)
    report = pipeline.run(dry_run=args.dry_run)
    print(report.summary())
    if args.verbose and report.diagnostics:
        for line in report.diagnostics:
            print("  -", line)
    for event in report.events:
        if event.date == Date.today().strftime("%Y-%m-%d"):
            print(f"  今天 {event.time_label()} {event.title}"
                  + (f" @ {event.location}" if event.location else ""))
    return 0


def cmd_add_text(args: argparse.Namespace) -> int:
    pipeline = Pipeline(args.data_dir)
    text = _read_add_text(args.add_text)
    report = pipeline.ingest_text(text, group=args.group, source_label="命令行录入", dry_run=args.dry_run)
    print(report.summary())
    for event in report.events:
        print(f"  {event.date} {event.time_label()} {event.title}"
              + (f" @ {event.location}" if event.location else "")
              + (f" / {'、'.join(event.people)}" if event.people else ""))
    return 0


def cmd_stdin(args: argparse.Namespace) -> int:
    text = sys.stdin.read()
    pipeline = Pipeline(args.data_dir)
    report = pipeline.ingest_text(text, group=args.group, source_label="粘贴录入", dry_run=args.dry_run)
    print(report.summary())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    pipeline = Pipeline(args.data_dir)
    table = load_timetable(args.data_dir)
    calendar = load_calendar(args.data_dir)
    timeline = build_timeline(pipeline.load_events(), table, last_run_text=pipeline.last_run_text(),
                              calendar=calendar)
    print(f"今天 {timeline.today} {timeline.week_label}".rstrip())
    if timeline.festival is not None:
        print(f"节日：{timeline.festival.emoji} {timeline.festival.name} · {timeline.festival.greeting}")
    if timeline.holiday is not None:
        print(f"放假中：{timeline.holiday.label()}")
    if timeline.makeup is not None:
        print(f"调休：{timeline.makeup.text()}" + (f"（{timeline.makeup.date}）"))
    if timeline.next_item is not None:
        item = timeline.next_item
        print(f"下一项：{item.card.title} {item.card.start or ''} {item.text()}")
    for section in timeline.sections:
        heading = day_heading(section)
        if section.makeup_label:
            heading = f"{heading}　{section.makeup_label}"
        print(f"{heading}  （{section.count} 项）")
        for card in section.cards:
            meta = " · ".join(part for part in (card.location, card.people) if part)
            print(f"   {card.start or '全天':>5}  {card.title}"
                  + (f"  [{meta}]" if meta else "")
                  + (f"  ({card.badge})" if card.badge else ""))
    print(footer_summary(timeline))
    return 0


def cmd_check_timetable(args: argparse.Namespace) -> int:
    table = load_timetable(args.data_dir)
    if table is None:
        print(f"未找到课程表。请将 timetable.json / timetable.csv / timetable.txt 放入 {args.data_dir}")
        return 1
    print(f"已读取 {table.source}：{len(table)} 门课"
          + (f"，第 1 周周一 = {table.term_start}" if table.term_start else ""))
    for course in sorted(table.courses, key=lambda c: (c.weekday, c.start_period)):
        weeks = "每周" if course.weeks is None else f"第{','.join(map(str, course.weeks))}周"
        start, end = course.times(table.periods)
        print(f"  周{'一二三四五六日'[course.weekday]} {course.period_label()} "
              f"{start or '--:--'}-{end or '--:--'} {course.name}"
              + (f" @ {course.location}" if course.location else "")
              + (f" / {course.teacher}" if course.teacher else "")
              + f"  ({weeks})")
    return 0


def cmd_paste(args: argparse.Namespace) -> int:
    from agenda.panel import AgendaPanel

    panel = AgendaPanel(args.data_dir, refresh_ms=60_000, pipeline_ms=0, autostart_pipeline=False)
    panel.root.after(200, panel.quick_entry)
    panel.run()
    return 0


def cmd_client(args: argparse.Namespace) -> int:
    """启动客户端控制台（可选同时拉起桌面面板）。"""
    from agenda.client_app import AppController
    from agenda.control_window import ControlWindow

    # 客户端也要单实例：桌面快捷方式 / 开机自启 / 手点可能同时拉起两个控制台
    client_lock = _acquire_lock(args.data_dir, "client")
    if client_lock is None:
        # 已经在跑：**不要再安静退出**——把已经开着的那个窗口叫回来。
        # 控制台可能被"最小化到托盘"了，用户再点一次图标却什么都没发生，
        # 看到的就是"客户端怎么打不开了？"（真踩过）。
        controller = AppController(args.data_dir)
        if controller.request_show():
            print("客户端已在运行，已请它把窗口显示出来。")
        else:
            print("客户端已在运行（未能获取其进程号，请从托盘图标打开）。")
        return 0

    controller = AppController(args.data_dir)
    if args.hide_panel:
        controller.config.panel_visible = False
    if args.start_panel and not controller.panel_running():
        controller.start_panel()
    if args.merge_now:
        try:
            controller.run_inbox()
        except Exception as error:
            print(f"inbox 整合失败：{error}")

    window = ControlWindow(controller)
    window.refresh_all()

    # 托盘：关掉控制台后仍能收通知、弹提醒
    try:
        from agenda import tray
        ids = tray.command_ids()

        def on_action(command: int) -> None:
            if command == ids["open_panel"]:
                controller.start_panel()
            elif command == ids["close_panel"]:
                controller.stop_panel()
            elif command == ids["open_console"]:
                controller.start_panel() if controller.config.panel_visible else None
                window.root.after(0, window.show)
            elif command == ids["quick_entry"]:
                # 托盘右键 →「快速录入群消息…」：把控制台叫出来，切到「通知」页，
                # 光标直接落在输入框里，粘上就能存。
                controller.start_panel() if controller.config.panel_visible else None
                window.root.after(0, window.quick_entry)
            elif command == ids["merge"]:
                controller.run_inbox()
            elif command == ids["quit"]:
                # 统一走 ControlWindow.quit_app：停面板 → 停定时器 → 销毁主窗。
                # after(0, …) 是必须的：托盘回调跑在托盘自己的线程上，要回到 Tk 线程执行。
                window.root.after(0, window.quit_app)

        icon_path = args.data_dir / "agenda.ico"
        tray_icon = tray.TrayIcon(
            "桌面日程", on_action, icon_path=str(icon_path) if icon_path.exists() else None,
        )
        if tray_icon.start():
            window._tray = tray_icon  # 保持引用，别被 GC
    except Exception as error:
        print(f"托盘不可用（不影响使用）：{error}")

    try:
        window.root.mainloop()
    except SystemExit:
        pass
    finally:
        _release_lock(client_lock)
    return 0


def cmd_list_schools(args: argparse.Namespace) -> int:
    from agenda import eas
    print("已注册的教务适配器：")
    for adapter in eas.available_adapters():
        print(f"  · {adapter.info.key:10} {adapter.info.title}")
        if adapter.info.note:
            print(f"      {adapter.info.note}")
    print()
    print(f"已登记学校（{len(eas.SCHOOLS)} 所）：")
    for school in eas.SCHOOLS:
        print(f"  · {school.name}  →  {school.adapter}  {school.base_url}")
    print()
    print("找不到自己的学校？可先运行 tools/probe_schools.py 查看该校入口形态；")
    print("也可将网址反馈给开发者，以加入 agenda/eas/registry.py 的 SCHOOLS 目录。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desktop-agenda", description="QQ 群通知 + 课程表 → 桌面日程面板")
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir(), help="数据目录（默认 <项目>/data）")
    sub = parser.add_argument_group("运行模式")
    sub.add_argument("--once", action="store_true", help="只整合 inbox 一次后退出")
    sub.add_argument("--client", action="store_true", help="启动客户端控制台（推荐入口）")
    sub.add_argument("--paste", action="store_true", help="打开快速录入窗口")
    sub.add_argument("--tutorial", action="store_true", help="打开使用教程窗口")
    sub.add_argument("--add-text", metavar="TEXT", help="直接并入一段文本；@文件 表示读取文件")
    sub.add_argument("--stdin", action="store_true", help="从标准输入读入文本并入日程")
    sub.add_argument("--status", action="store_true", help="打印当前时间线")
    sub.add_argument("--check-timetable", action="store_true", help="检查课程表读取情况")
    sub.add_argument("--list-schools", action="store_true", help="列出已知教务适配与学校")
    group = parser.add_argument_group("面板参数")
    group.add_argument("--width", type=int, default=360, help="面板宽度（像素）")
    group.add_argument("--refresh", type=int, default=30, help="界面刷新间隔（秒）")
    group.add_argument("--pipeline-minutes", type=float, default=15.0,
                       help="自动整合间隔（分钟，可小数；0=关闭）")
    group.add_argument("--window-mode", choices=("desktop", "topmost"), default="desktop",
                       help="desktop=钉在桌面底层（默认，不挡游戏）；topmost=总在最前")
    group.add_argument("--no-click-through", action="store_true",
                       help="强制关闭鼠标穿透（默认本来就是关的；开着会点不动面板）")
    group.add_argument("--always-visible", action="store_true",
                       help="全屏应用时也保持显示（默认隐身）")
    group.add_argument("--no-pet-mode", action="store_true",
                       help="关掉桌面宠物模式（默认：显示桌面时面板浮到普通窗口之上）")
    group.add_argument("--no-topmost", action="store_true", help="等同 --window-mode desktop")
    group.add_argument("--panel-x", type=int, help="面板 X 坐标")
    group.add_argument("--panel-y", type=int, help="面板 Y 坐标")
    client_group = parser.add_argument_group("客户端参数")
    client_group.add_argument("--hide-panel", action="store_true", help="客户端启动时不显示面板")
    client_group.add_argument("--start-panel", action="store_true", help="客户端启动时同时拉起面板")
    client_group.add_argument("--merge-now", action="store_true", help="客户端启动时先整合一次 inbox")
    parser.add_argument("--group", help="这批通知的来源群名")
    parser.add_argument("--dry-run", action="store_true", help="只解析不写入")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出更多诊断")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.data_dir = Path(args.data_dir)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.no_topmost:
        args.window_mode = "desktop"

    if args.once:
        return cmd_once(args)
    if args.client:
        return cmd_client(args)
    if args.list_schools:
        return cmd_list_schools(args)
    if args.tutorial:
        from agenda.tutorial import show_tutorial
        show_tutorial()
        return 0
    if args.add_text:
        return cmd_add_text(args)
    if args.stdin:
        return cmd_stdin(args)
    if args.status:
        return cmd_status(args)
    if args.check_timetable:
        return cmd_check_timetable(args)
    if args.paste:
        return cmd_paste(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
