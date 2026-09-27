"""测试包（让 unittest discover 能导入）+ 一道进程级剪贴板安全网。

## 为什么要有这道网

`tools/verify_release.py` 会在**用户自己的机器上**跑全套测试。只要有一个用例
往系统剪贴板里写了文本却没还原，用户的剪贴板就被覆盖了——而且后果不止是"剪贴板脏了"：

真踩过：有个用例把「【提醒】1.只用剪贴板：明天之内提交材料。」留在了用户剪贴板里；
用户随后按一下热键，面板把这句**测试文本**当成真通知录进了他的真实数据，
界面里凭空多出一条莫名其妙的日程（他截图来问"这条识别的不对"）。

只靠"每个用例自觉继承 `ClipboardSafeTestCase`"挡不住新增用例——已经漏过两次
（`PanelSelectionModeTests` 和 `DateEntryTests` 都忘了继承）。所以这里改成进程级兜底：
第一次写剪贴板之前先存下**用户的原内容**，测试进程退出时无条件放回去。
用例级的 `ClipboardSafeTestCase` 仍然保留：它还原得更早、更精确，
而且能覆盖"写完还要读回来断言"的中间状态。
"""

from __future__ import annotations

import atexit
import sys
import unittest


class ClipboardSafeTestCase(unittest.TestCase):
    """凡是要写**系统剪贴板**的用例，必须还原现场。

    开始前存下原内容，结束后放回去（原来没有文本就写空串）。
    新写的用例**优先继承这个类**，不要自己写一遍存取逻辑。
    """

    def setUp(self) -> None:
        from agenda import hotkey

        self._saved_clipboard = hotkey.read_clipboard_text() if sys.platform == "win32" else ""

    def tearDown(self) -> None:
        if sys.platform != "win32":
            return
        from agenda import hotkey

        try:
            hotkey.write_clipboard_text(self._saved_clipboard)
        except Exception:            # noqa: BLE001
            pass


#: 用户进测试之前剪贴板里的内容。只记一次——之后不管谁来写，都还原到**最初**这个值。
_original: list[str] = []
_restored = False


def _remember() -> None:
    """第一次有人要写剪贴板时，先把用户的原内容存下来。"""
    if _original:
        return
    try:
        from agenda import hotkey

        _original.append(hotkey.read_clipboard_text())
    except Exception:                # noqa: BLE001
        _original.append("")
    atexit.register(_restore)


def _restore() -> None:
    global _restored
    if _restored:
        return
    _restored = True
    try:
        from agenda import hotkey

        hotkey.write_clipboard_text(_original[0] if _original else "")
    except Exception:                # noqa: BLE001
        pass


def _install_clipboard_guard() -> None:
    """把两条写剪贴板的路径都套上"先存后还原"。

    两条都要套，因为它们互不相干：`agenda.hotkey.write_clipboard_text` 是程序自己用的
    Win32 那条，`tkinter` 的 `clipboard_clear/clipboard_append` 是控件那条
    （`DateEntryTests` 走的就是后者，只套前者抓不到）。
    """
    try:
        from agenda import hotkey
    except Exception:                # noqa: BLE001
        return

    original_write = hotkey.write_clipboard_text

    def guarded_write(text: str) -> bool:
        _remember()
        return original_write(text)

    guarded_write.tests_clipboard_guard = True          # type: ignore[attr-defined]
    hotkey.write_clipboard_text = guarded_write        # type: ignore[assignment]

    try:
        import tkinter
    except Exception:                # noqa: BLE001
        return

    for name in ("clipboard_clear", "clipboard_append"):
        original = getattr(tkinter.Misc, name, None)
        if original is None:
            continue
        # 用默认参数把 original 绑进闭包，避免循环变量在闭包里被覆盖
        setattr(tkinter.Misc, name, _guard_tk(original))


def _guard_tk(original):
    def guarded(self, *args, **kwargs):
        _remember()
        return original(self, *args, **kwargs)

    guarded.tests_clipboard_guard = True                # type: ignore[attr-defined]
    guarded.__name__ = getattr(original, "__name__", "guarded")
    guarded.__doc__ = original.__doc__
    return guarded


if __name__ == "tests":
    _install_clipboard_guard()
