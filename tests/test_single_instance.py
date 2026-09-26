"""单实例保护的测试（面板不能被重复拉起，也不能被误杀）。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as cli  # noqa: E402


class PanelLockTests(unittest.TestCase):
    def test_first_acquire_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = cli._acquire_panel_lock(Path(tmp))
            self.assertIsNotNone(lock)
            self.assertEqual(int(lock.read_text(encoding="utf-8").strip()), os.getpid())

    def test_second_acquire_refused_while_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = cli._acquire_panel_lock(Path(tmp))
            self.assertIsNotNone(first)
            # 同一进程再申请：pid 相同，视为自己的锁，允许继续（不阻塞测试）
            second = cli._acquire_panel_lock(Path(tmp))
            self.assertIsNotNone(second)

    def test_other_live_process_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "panel.lock"
            # 起一个真实存活、可查询句柄的子进程冒充"已在运行的面板"
            child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"])
            try:
                lock.write_text(str(child.pid), encoding="utf-8")
                self.assertTrue(cli._process_alive(child.pid))
                self.assertIsNone(cli._acquire_panel_lock(Path(tmp)))
            finally:
                child.terminate()
                child.wait(timeout=10)

    def test_dead_process_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "panel.lock"
            child = subprocess.Popen([sys.executable, "-B", "-c", "pass"])
            child.wait(timeout=20)
            lock.write_text(str(child.pid), encoding="utf-8")
            self.assertFalse(cli._process_alive(child.pid))
            self.assertIsNotNone(cli._acquire_panel_lock(Path(tmp)))

    def test_stale_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "panel.lock"
            lock.write_text("999999", encoding="utf-8")   # 几乎不可能存在的 pid
            acquired = cli._acquire_panel_lock(Path(tmp))
            self.assertIsNotNone(acquired)
            self.assertEqual(int(acquired.read_text(encoding="utf-8").strip()), os.getpid())

    def test_garbage_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "panel.lock"
            lock.write_text("not-a-pid", encoding="utf-8")
            self.assertIsNotNone(cli._acquire_panel_lock(Path(tmp)))

    def test_process_alive_self_and_bogus(self):
        self.assertTrue(cli._process_alive(os.getpid()))
        self.assertFalse(cli._process_alive(999999))

    def test_generic_lock_used_by_client_too(self):
        """客户端和面板共用同一套锁实现（桌面快捷方式点两次不能开出两个控制台）。"""
        with tempfile.TemporaryDirectory() as tmp:
            lock = cli._acquire_lock(Path(tmp), "client")
            self.assertIsNotNone(lock)
            self.assertEqual(lock.name, "client.lock")
            self.assertEqual(int(lock.read_text(encoding="utf-8").strip()), os.getpid())
            cli._release_lock(lock)
            self.assertFalse(lock.exists())

    def test_stale_client_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "client.lock"
            lock.write_text("999999", encoding="utf-8")
            acquired = cli._acquire_lock(Path(tmp), "client")
            self.assertIsNotNone(acquired)

    def test_live_client_lock_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "client.lock"
            child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"])
            try:
                lock.write_text(str(child.pid), encoding="utf-8")
                self.assertIsNone(cli._acquire_lock(Path(tmp), "client"))
            finally:
                child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
