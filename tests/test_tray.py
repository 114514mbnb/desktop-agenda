"""托盘图标：右键菜单必须是**真的能看见字**的菜单。

用户原话：「当我点击右下角的状态栏想要右键关闭时却是空白……右键状态栏标识
可以退出应用，呼出客户端窗口，呼出快速录入群消息」。

那个"白色空框"的根因：菜单项原来用 `AppendMenuW` 建，它**不复制字符串**，
只记住你传进去的指针；ctypes 给 Python 字符串临时转出来的 `wchar_t*` 在调用
返回后就被回收了。刚建完调 `GetMenuStringW` 还能读回正确文字（那块内存还没被
复用），所以自检全绿；等真正弹出来画的时候内存已被覆盖 —— 菜单只剩一条分隔线。
改用 `InsertMenuItemW` + `MIIM_STRING`（文档写明会复制字符串）之后正常。

顺带钉住另一件事：user32 函数都要声明 argtypes，否则 64 位的 lparam 按 C int
转换会抛 `OverflowError: int too long to convert`，窗口过程对每条不处理的消息
都在抛异常（被 ctypes 静默吞掉）。
"""

from __future__ import annotations

import ctypes
import gc
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import tray  # noqa: E402

MF_BYPOSITION = 0x400


def read_labels(menu) -> list[str]:
    user32 = ctypes.windll.user32
    count = user32.GetMenuItemCount(menu)
    labels = []
    for index in range(count):
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetMenuStringW(menu, index, buffer, 128, MF_BYPOSITION)
        labels.append(buffer.value)
    return labels


@unittest.skipUnless(sys.platform == "win32", "托盘是 Win32 专属")
class TrayMenuTests(unittest.TestCase):
    def test_menu_labels_round_trip(self):
        menu = tray.build_menu()
        try:
            labels = read_labels(menu)
        finally:
            ctypes.windll.user32.DestroyMenu(menu)
        expected = [label for _id, label in tray.MENU_LABELS]
        self.assertEqual(len(labels), len(expected))
        for got, want in zip(labels, expected):
            if want is None:
                self.assertEqual(got, "", "分隔线不该有文字")
            else:
                self.assertEqual(got, want)

    def test_text_survives_memory_churn(self):
        """建完菜单之后再做一堆内存分配，文字还得在。

        这正是 AppendMenuW 版本的死法：它借的是临时缓冲区，一旦被复用/释放，
        菜单就画不出字。InsertMenuItemW 是复制，随便怎么折腾都还在。
        """
        menu = tray.build_menu()
        try:
            churn = []
            for index in range(20000):
                churn.append(f"garbage-{index}-{'x' * (index % 17)}")
                if index % 5000 == 0:
                    churn = churn[-100:]
            del churn
            gc.collect()
            labels = read_labels(menu)
        finally:
            ctypes.windll.user32.DestroyMenu(menu)
        for got, (_id, want) in zip(labels, tray.MENU_LABELS):
            if want is not None:
                self.assertEqual(got, want, "内存一折腾文字就没了 → 字符串没被复制进菜单")

    def test_menu_is_built_with_insertmenuitem(self):
        """源码级兜底：不许再**调用** AppendMenuW。

        单测碰不到"弹出来画"那一步，只有这个断言能把回归挡住。
        用 AST 找调用点——注释和文档字符串里提到它的名字不算
        （模块开头就专门写了一段解释这个坑，别把它误判成违规）。
        """
        import ast

        source = Path(tray.__file__).read_text(encoding="utf-8")
        called = {node.func.attr for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertNotIn("AppendMenuW", called,
                         "AppendMenuW 不复制字符串，会把菜单变成空白框")
        self.assertIn("InsertMenuItemW", called)

    def test_menu_has_the_three_requested_actions(self):
        """用户点名要的三项必须都在。"""
        labels = [label for _id, label in tray.MENU_LABELS if label]
        self.assertIn("呼出客户端窗口", labels)
        self.assertIn("快速录入群消息…", labels)
        self.assertIn("退出应用", labels)

    def test_command_ids_cover_every_menu_entry(self):
        ids = tray.command_ids()
        wanted = {item_id for item_id, label in tray.MENU_LABELS if label}
        self.assertEqual(wanted, set(ids.values()),
                         "菜单里的 id 和 command_ids 对不上，点了会没反应")
        self.assertIn("quick_entry", ids)

    def test_user32_signatures_are_declared(self):
        """DefWindowProcW 之类必须声明 argtypes。

        不声明的话 64 位 lparam 走 C int → OverflowError，
        窗口过程等于对大多数消息都在抛异常。
        """
        user32 = tray._typed_user32()
        for name in ("DefWindowProcW", "CreateWindowExW", "InsertMenuItemW",
                     "TrackPopupMenu", "DestroyMenu", "SetForegroundWindow"):
            function = getattr(user32, name)
            self.assertTrue(getattr(function, "argtypes", None),
                            f"{name} 没声明 argtypes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
