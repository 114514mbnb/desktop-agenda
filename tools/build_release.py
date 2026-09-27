"""打包：生成「便携版 zip」+「源码仓库目录」。

两个产物，用途不同：
  * 便携版（含 runtime/）：给朋友/给别的电脑用，解压双击就能跑，不需要装 Python。
    排除 __pycache__、浏览器缓存（那是几十 MB 垃圾）、以及**个人数据**。
  * 源码目录（不含 runtime/）：给 GitHub 用。runtime 是 60 MB 的二进制，
    不进仓库，仓库里写清楚怎么自己下载。

个人数据（events.json / timetable.json / client.json / calendar.json）**两个产物都不放**，
另外单独导出成 `我的数据备份/`，只有你自己知道在哪。

路径从脚本位置推导，需要换地方时用环境变量：
    AGENDA_RELEASE_DIR   产物目录（默认 <项目>/../dist）
输出：
    <产物目录>/桌面日程-便携版/、/desktop-agenda-源码/、/我的数据备份（别分享）/、/桌面日程-便携版.zip
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT = Path(os.environ.get("AGENDA_PROJECT")
               or Path(__file__).resolve().parent.parent).resolve()
TARGET = Path(os.environ.get("AGENDA_RELEASE_DIR")
              or PROJECT.parent / "dist").resolve()
PORTABLE = TARGET / "桌面日程-便携版"
SOURCE = TARGET / "desktop-agenda-源码"
PRIVATE = TARGET / "我的数据备份（别分享）"

#: 便携版要排除的东西（目录名）
EXCLUDE_DIRS = {"__pycache__", "browser-profile", ".git", "dist", "build", ".pytest_cache"}
#: 便携版要排除的文件（个人数据 + 日志 + 锁）
EXCLUDE_FILES = {
    "events.json", "state.json", "client.json", "panel.log",
    "client.lock", "panel.lock", "panel.stop", "client.show", "client.quit",
    "timetable.json", "timetable.json.bak",
}
#: 便携版还要额外排除的**个人数据备份**（zip 是拿去发给朋友的，连课表备份也不能带）。
#: ⚠ 这里必须**逐个点名**，不能用 "timetable." 这种前缀去匹配——
#: 那会把 `agenda/timetable.py` 这个核心模块也删掉，解压后整个程序起不来
#: （实测：`ModuleNotFoundError: No module named 'agenda.timetable'`，43 个测试报错）。
PORTABLE_EXTRA_EXCLUDE_EXACT = {
    "timetable.before-rrule-fix.json",
    "timetable.garbled-browser-import.json",
    "timetable.ics-full.json",
    "timetable.user-edited-backup.json",
    "schedule-from-pdf.json",
}
#: 源码仓库里额外不要的（大二进制 + 个人数据）
SOURCE_EXCLUDE_DIRS = EXCLUDE_DIRS | {"runtime"}
#: 便携版里**用不到**的运行时组件（按相对路径前缀匹配，只作用于 runtime/ 内部）。
#: python-build-standalone 会把整套开发/编辑器组件一起发出来，本项目一个都用不上：
#:   runtime/Lib/site-packages  pip（10.2 MB，实机确认里面只有 pip 和它的 dist-info）
#:   runtime/Lib/ensurepip      `python -m ensurepip` 专用
#:   runtime/Lib/idlelib        IDLE 编辑器
#:   runtime/Lib/turtledemo     海龟画图示例
#:   runtime/include、libs      编译 C 扩展用的头文件与导入库
#:   runtime/Scripts            空的 pip 脚本目录
#: 实测合计约 15.5 MB（解压后），zip 相应小 4～5 MB；运行时行为不受影响。
#: **注意别删** Lib/encodings、DLLs、tcl、Lib/unittest——前三个是解释器和 tkinter 的命脉，
#: unittest 是随包发布的 370 项测试要用的。
RUNTIME_TRIM_PREFIXES = (
    "runtime/Lib/site-packages",
    "runtime/Lib/ensurepip",
    "runtime/Lib/idlelib",
    "runtime/Lib/turtledemo",
    "runtime/include",
    "runtime/libs",
    "runtime/Scripts",
)
#: `data/` 目录**只允许**这几个文件进产物，其余一律不放，并且打印出来。
#: 为什么用白名单而不是继续往 EXCLUDE_FILES 里加名字：加名字永远慢一步 ——
#: 这一次就漏了 `data/calendar.json`（你的放假区间 + 调休安排，属于个人数据）
#: 进了要发给朋友的 zip。白名单让"以后新出现的数据文件"默认是安全的，
#: 而不是默认泄露。
DATA_ALLOW_FILES = {"首次启动说明.txt", "README.md", ".gitkeep", "agenda.ico"}

#: 个人数据备份要带的
PRIVATE_FILES = ["events.json", "timetable.json", "client.json", "state.json",
                 "calendar.json"]
#: 开发过程中随手截的图（screenshot-*.png）不进任何产物，只保留 README 里引用的这两张。
#: 踩过：整目录复制会把 `screenshot-qq-region.png` 这类**含真实群通知内容**的截图
#: 一起打进给朋友的 zip 里。
KEEP_SCREENSHOTS = {"screenshot-client.png", "screenshot-panel.png"}
SCREENSHOT_PREFIXES = ("screenshot-",)


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def measure_paths(prefixes: tuple[str, ...]) -> float:
    """量一下这些相对路径加起来有多大（MB），用来在打包日志里报告瘦身效果。"""
    total = 0
    for prefix in prefixes:
        target = PROJECT / prefix
        if target.exists():
            total += sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
    return total / 1024 / 1024


def force_rmtree(path: Path) -> None:
    """删目录，连只读文件一起删。

    为什么不能直接用 shutil.rmtree：从 zip 解出来的文件带只读位，
    rmtree 会 `PermissionError: [WinError 5] 拒绝访问` 然后半途而废，
    留下一半旧文件（真踩过，还导致源码目录没重建成功）。
    """
    import os
    import stat

    def on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onexc=on_error)


def copy_tree(src: Path, dst: Path, exclude_dirs: set[str], exclude_files: set[str],
              *, keep_data_files: set[str] = frozenset(),
              extra_prefixes: tuple[str, ...] = (),
              extra_exact: set[str] = frozenset(),
              skip_prefixes: tuple[str, ...] = (),
              skipped: list[str] | None = None) -> int:
    """复制目录。返回复制的文件数。

    `skip_prefixes` 是按**相对路径前缀**排除（例如 `runtime/Lib/idlelib`），
    比按目录名排除精确：不会误伤项目里同名的目录。
    """
    count = 0
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        posix = "/".join(rel.parts)
        if any(posix == prefix or posix.startswith(prefix + "/") for prefix in skip_prefixes):
            continue
        if item.is_dir():
            continue
        name = item.name
        # data/ 走白名单：除了明说允许的那几个，一律不进产物（个人数据默认安全）
        if rel.parts[0] == "data" and name not in DATA_ALLOW_FILES:
            if skipped is not None:
                skipped.append(str(rel))
            continue
        if name in exclude_files and name not in keep_data_files:
            continue
        if name in extra_exact:
            continue
        if any(name.startswith(prefix) for prefix in extra_prefixes) \
                and name not in keep_data_files:
            continue
        if name.endswith((".pyc", ".pyo")):
            continue
        if name in {"Thumbs.db", "desktop.ini"}:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def main() -> int:
    if not PROJECT.is_dir():
        print(f"项目目录不存在：{PROJECT}")
        return 1
    if TARGET.exists():
        log(f"清掉旧的 {TARGET}")
        force_rmtree(TARGET)
    TARGET.mkdir(parents=True, exist_ok=True)

    # ---- 便携版（整包，不含个人数据）----------------------------------
    log("打包便携版…")
    PORTABLE.mkdir(parents=True, exist_ok=True)
    trimmed = measure_paths(RUNTIME_TRIM_PREFIXES)
    log(f"运行时瘦身：跳过 {trimmed:.1f} MB（pip / IDLE / 头文件 / 示例等，运行时用不到）")
    skipped_private: list[str] = []
    files = copy_tree(PROJECT, PORTABLE, EXCLUDE_DIRS, EXCLUDE_FILES,
                      extra_exact=PORTABLE_EXTRA_EXCLUDE_EXACT,
                      extra_prefixes=SCREENSHOT_PREFIXES,
                      keep_data_files=KEEP_SCREENSHOTS,
                      skip_prefixes=RUNTIME_TRIM_PREFIXES,
                      skipped=skipped_private)
    # data 目录结构留着（空目录 + .gitkeep 说明），否则第一次启动要自己建
    for sub in ("inbox", "archive"):
        (PORTABLE / "data" / sub).mkdir(parents=True, exist_ok=True)
    (PORTABLE / "data" / "首次启动说明.txt").write_text(
        "这个目录是你的数据目录，程序会自动往里写：\n"
        "  events.json      通知日程\n"
        "  timetable.json   课程表\n"
        "  client.json      客户端设置\n"
        "  inbox/           把群消息 txt 丢这里，会被自动整合\n"
        "  archive/         整合过的原始文件\n\n"
        "备份就是把这个目录整个拷走。\n",
        encoding="utf-8")
    log(f"便携版：{files} 个文件")
    if skipped_private:
        log(f"data/ 里跳过了 {len(skipped_private)} 个文件（个人数据，不进包）："
            + "、".join(sorted(skipped_private)))

    # ---- 源码目录（给 GitHub）----------------------------------------
    log("生成源码目录…")
    SOURCE.mkdir(parents=True, exist_ok=True)
    skipped_source: list[str] = []
    source_files = copy_tree(PROJECT, SOURCE, SOURCE_EXCLUDE_DIRS, EXCLUDE_FILES,
                             extra_exact=PORTABLE_EXTRA_EXCLUDE_EXACT,
                             extra_prefixes=SCREENSHOT_PREFIXES,
                             keep_data_files=KEEP_SCREENSHOTS,
                             skipped=skipped_source)
    (SOURCE / "data" / "inbox").mkdir(parents=True, exist_ok=True)
    (SOURCE / "data" / "archive").mkdir(parents=True, exist_ok=True)
    for folder in ("data", "data/inbox", "data/archive"):
        (SOURCE / folder / ".gitkeep").write_text("", encoding="utf-8")
    log(f"源码：{source_files} 个文件")
    if skipped_source:
        log(f"data/ 里跳过了 {len(skipped_source)} 个文件："
            + "、".join(sorted(skipped_source)))

    # ---- 个人数据备份 -------------------------------------------------
    log("导出你的数据备份…")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for name in PRIVATE_FILES:
        source = PROJECT / "data" / name
        if source.exists():
            shutil.copy2(source, PRIVATE / name)
    # 通知的原始来源也一起留着，万一要重建
    for sub in ("inbox", "archive"):
        source = PROJECT / "data" / sub
        if source.is_dir() and any(source.iterdir()):
            shutil.copytree(source, PRIVATE / "data" / sub, dirs_exist_ok=True)
    (PRIVATE / "说明.txt").write_text(
        "这是你机器上的个人数据：通知日程、课程表、客户端设置。\n"
        "换电脑时把它们拷回新机器的 data\\ 目录即可恢复。\n"
        "**不要分享这个目录**（里面有你的群通知内容）。\n",
        encoding="utf-8")

    # ---- 打 zip ------------------------------------------------------
    log("压缩便携版…")
    started = time.time()
    archive = shutil.make_archive(str(PORTABLE), "zip", root_dir=PORTABLE.parent,
                                  base_dir=PORTABLE.name)
    size_mb = Path(archive).stat().st_size / 1024 / 1024
    log(f"zip：{archive}（{size_mb:.1f} MB，用了 {time.time() - started:.0f} 秒）")

    print()
    print("完成。目录内容：")
    for item in sorted(TARGET.iterdir()):
        if item.is_dir():
            total = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
            print(f"  {item.name}/  ({total / 1024 / 1024:.1f} MB)")
        else:
            print(f"  {item.name}  ({item.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
