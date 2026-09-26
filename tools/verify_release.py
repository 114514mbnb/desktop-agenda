"""验收：把便携版 zip 解到一个干净目录，跑一遍确认真的能用。

不能只看"zip 生成了"就算完——要证明：
  1. 解压出来能跑（自带 runtime 在别的目录也认）；
  2. 测试全过（说明源码没漏文件）；
  3. 客户端能起来、面板能起来、教程能打开；
  4. 里面**没有**个人数据（该忽略的真的忽略了）。

zip 路径用 `AGENDA_RELEASE_ZIP` 指定，默认 <项目>/../dist/桌面日程-便携版.zip。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ZIP = Path(os.environ.get("AGENDA_RELEASE_ZIP")
           or Path(__file__).resolve().parent.parent.parent / "dist" / "桌面日程-便携版.zip")
FORBIDDEN = ["events.json", "timetable.json", "client.json", "state.json",
             "calendar.json", "panel.log", "client.lock", "panel.lock"]
#: `data/` 里允许出现的文件（白名单）。其它任何文件都算"漏进去的个人数据"。
#: 为什么要有这一条：以前只按名字查那几个已知文件，结果 `calendar.json`
#: （放假区间 + 调休，是个人数据）一路躺进了要发给朋友的 zip 都没人发现。
DATA_ALLOW_FILES = {"首次启动说明.txt", "README.md", ".gitkeep", "agenda.ico"}


def run(command: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main() -> int:
    if not ZIP.is_file():
        print(f"找不到 zip：{ZIP}")
        return 1
    work = Path(tempfile.mkdtemp(prefix="agenda-verify-"))
    print(f"解压到 {work}")
    shutil.unpack_archive(str(ZIP), str(work))
    roots = [p for p in work.iterdir() if p.is_dir()]
    root = roots[0] if roots else work
    print(f"项目根：{root}")

    ok = True

    # 1) 结构完整
    print("\n[1] 关键文件在不在")
    for name in ("main.py", "README.md", "LICENSE", ".gitignore", "start-client.cmd",
                 "docs/教程.md", "runtime/python.exe", "agenda/panel.py",
                 "agenda/tutorial.py", "tests/test_tutorial.py"):
        exists = (root / name).exists()
        ok &= exists
        print(f"    {'✓' if exists else '✗'} {name}")

    # 2) 没有个人数据
    print("\n[2] 有没有漏进来的个人数据")
    leaked = []
    for pattern in FORBIDDEN:
        for found in root.rglob(pattern):
            leaked.append(str(found.relative_to(root)))
    for pattern in ("__pycache__", "browser-profile"):
        for found in root.rglob(pattern):
            leaked.append(str(found.relative_to(root)))
    # 白名单之外，`data/` 里不该有任何别的文件
    data_dir = root / "data"
    if data_dir.is_dir():
        for item in sorted(data_dir.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(data_dir)
            if rel.parts[0] in ("inbox", "archive"):
                continue                    # 空目录占位，本来就不带文件
            if item.name not in DATA_ALLOW_FILES:
                leaked.append(str(item.relative_to(root)))
    if leaked:
        ok = False
        for item in leaked[:10]:
            print(f"    ✗ {item}")
    else:
        print("    ✓ 干净（没有个人数据、缓存、浏览器 profile）")

    python = root / "runtime" / "python.exe"

    # 3) 测试
    print("\n[3] 跑测试（自带运行时 + 解压后的副本）")
    code, output = run([str(python), "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       root)
    summary = [line for line in output.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
    ok &= code == 0
    for line in summary:
        print(f"    {line}")
    if code != 0:
        print(output[-1500:])

    # 4) 命令行能跑
    print("\n[4] 命令行")
    for args, expect in ((["--help"], "--client"),
                         (["--status"], ""),
                         (["--check-timetable"], "")):
        code, output = run([str(python), "-B", "main.py", *args], root, timeout=60)
        good = (expect in output) if expect else (code in (0, 1))
        ok &= good
        first = (output.strip().splitlines() or [""])[0][:70]
        print(f"    {'✓' if good else '✗'} main.py {' '.join(args)} → {first}")

    # 5) 教程文件可达
    print("\n[5] 教程")
    code, output = run([str(python), "-B", "-c",
                        "import sys; sys.path.insert(0,'.');"
                        "from agenda.tutorial import tutorial_path;"
                        "p=tutorial_path(); print('OK' if p else 'MISSING', p)"], root, timeout=60)
    good = output.strip().startswith("OK")
    ok &= good
    print(f"    {'✓' if good else '✗'} {output.strip()[:90]}")

    # 6) 客户端 + 面板 真起一次（用独立数据目录，不碰任何现有数据）
    print("\n[6] 真起一次客户端 + 面板")
    data = work / "verify-data"
    data.mkdir(exist_ok=True)
    client = subprocess.Popen(
        [str(python), "-B", "main.py", "--data-dir", str(data), "--client", "--start-panel"],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 40
        panel_up = client_up = False
        while time.time() < deadline:
            time.sleep(1.5)
            lock = data / "panel.lock"
            cl = data / "client.lock"
            panel_up = lock.exists() and lock.read_text(encoding="utf-8").strip().isdigit()
            client_up = cl.exists() and cl.read_text(encoding="utf-8").strip().isdigit()
            if panel_up and client_up:
                break
        log = data / "panel.log"
        quiet = (not log.exists()) or log.stat().st_size == 0
        ok &= panel_up and client_up and quiet
        print(f"    {'✓' if client_up else '✗'} 客户端进程起来了（client.lock）")
        print(f"    {'✓' if panel_up else '✗'} 面板进程起来了（panel.lock）")
        print(f"    {'✓' if quiet else '✗'} panel.log {'空（无报错）' if quiet else '里有报错'}")
        if not quiet:
            print(log.read_text(encoding="utf-8", errors="replace")[-800:])
    finally:
        # 让它自己退（写停止标记），退不掉再杀
        try:
            (data / "panel.stop").write_text("verify", encoding="utf-8")
            time.sleep(2)
        except OSError:
            pass
        client.terminate()
        try:
            client.wait(timeout=10)
        except subprocess.TimeoutExpired:
            client.kill()
    # 收尾：杀掉这次验证拉起的 python（只杀数据目录指向 verify-data 的那些）
    try:
        listing = subprocess.run(
            ["wmic", "process", "where", "name='pythonw.exe'", "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, timeout=30).stdout
        for line in listing.splitlines():
            if "verify-data" in line:
                pid = line.strip().split()[-1]
                if pid.isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True, timeout=20)
                    print(f"    （清理验证进程 {pid}）")
    except Exception:
        pass

    print("\n" + ("=" * 60))
    print("验收结果：" + ("全部通过 ✅" if ok else "有失败项 ❌"))
    print(f"（临时目录 {work}，可手动删）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
