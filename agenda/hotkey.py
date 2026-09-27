"""全局热键 + 剪贴板取文本（Windows）。

用户按一下自己设定的组合键，面板就把**剪贴板里的群通知**解析入库，不用再
"切窗口 → 粘贴 → 保存"。这是这个程序在录入环节能省掉的最后几步。

为什么用 `RegisterHotKey` 而不是键盘钩子（`SetWindowsHookEx`）：
  * 热键只向系统登记"我关心的这一个组合"，命中时系统发 `WM_HOTKEY` 给我们，
    我们的进程**看不到用户的任何其他按键**；
  * 键盘钩子会收到每一次击键，杀毒软件会拦、用户也会怀疑"这程序在偷看键盘"。
  功能上两者都做得到"在别的程序里按下也能触发"，但代价完全不同，所以选前者。

热键登记在**日程表面板**进程上（面板是常驻进程，且它本来就负责 inbox 自动整合）；
面板没在运行时热键不可用——这一条写在设置页的提示里，不藏着。
"""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

#: 默认热键：Ctrl+Alt+Q（Q = 群，且与常见软件冲突极小）
DEFAULT_HOTKEY = "Ctrl+Alt+Q"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
#: 按住不放时不要连发（否则会反复读同一个剪贴板入库）
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

ERROR_HOTKEY_ALREADY_REGISTERED = 1409


class HotkeyError(ValueError):
    """热键写法不合法（带中文原因，直接给界面显示）。"""


# ---------------------------------------------------------------------------
# 键名 → 虚拟键码
# ---------------------------------------------------------------------------

#: 修饰键的别名。写成小写比较，'ctrl' 'ctl' 这类缩写都收。
_MODIFIER_ALIASES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "ctl": MOD_CONTROL, "控制": MOD_CONTROL,
    "alt": MOD_ALT, "交替": MOD_ALT,
    "shift": MOD_SHIFT, "上档": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN, "徽标": MOD_WIN,
}

#: 单个键的虚拟键码表（不含字母数字，那两类用范围算）。
_VK_NAMED: dict[str, int] = {
    "space": 0x20, "空格": 0x20, "spacebar": 0x20,
    "enter": 0x0D, "return": 0x0D, "回车": 0x0D,
    "tab": 0x09, "制表": 0x09,
    "esc": 0x1B, "escape": 0x1B, "退出": 0x1B,
    "backspace": 0x08, "退格": 0x08,
    "insert": 0x2D, "ins": 0x2D, "插入": 0x2D,
    "delete": 0x2E, "del": 0x2E, "删除": 0x2E,
    "home": 0x24, "首页": 0x24,
    "end": 0x23, "末页": 0x23,
    "pageup": 0x21, "pgup": 0x21, "上翻页": 0x21,
    "pagedown": 0x22, "pgdn": 0x22, "下翻页": 0x22,
    "up": 0x26, "上": 0x26, "down": 0x28, "下": 0x28,
    "left": 0x25, "左": 0x25, "right": 0x27, "右": 0x27,
    # OEM 符号键（VK 与字符无关，这里按美式键盘对应）
    ";": 0xBA, "semicolon": 0xBA, "；": 0xBA,
    "=": 0xBB, "equal": 0xBB, "＝": 0xBB,
    ",": 0xBC, "comma": 0xBC, "，": 0xBC,
    "-": 0xBD, "minus": 0xBD, "－": 0xBD,
    ".": 0xBE, "period": 0xBE, "。": 0xBE,
    "/": 0xBF, "slash": 0xBF, "／": 0xBF,
    "`": 0xC0, "grave": 0xC0, "～": 0xC0,
    "[": 0xDB, "bracketleft": 0xDB, "【": 0xDB,
    "\\": 0xDC, "backslash": 0xDC, "、": 0xDC,
    "]": 0xDD, "bracketright": 0xDD, "】": 0xDD,
    "'": 0xDE, "quote": 0xDE, "’": 0xDE,
}

#: 虚拟键码 → 显示名（做规范化输出，保证同一个组合只有一种写法）
_DISPLAY_BY_VK: dict[int, str] = {
    0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x1B: "Esc", 0x08: "Backspace",
    0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End",
    0x21: "PageUp", 0x22: "PageDown",
    0x26: "Up", 0x28: "Down", 0x25: "Left", 0x27: "Right",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}

#: 这些组合是系统/外壳保留的，登记上去要么直接失败，要么行为诡异——提前劝退
_DISCOURAGED: dict[tuple[int, int], str] = {
    (MOD_ALT, 0x09): "Alt+Tab 是 Windows 切换窗口的快捷键",
    (MOD_ALT, 0x73): "Alt+F4 是 Windows 关闭窗口的快捷键",
    (MOD_CONTROL, 0x1B): "Ctrl+Esc 是 Windows 打开开始菜单的快捷键",
    (MOD_WIN, 0x4C): "Win+L 是 Windows 锁屏的快捷键（系统不允许被占用）",
    (MOD_WIN, 0x44): "Win+D 是 Windows 显示桌面的快捷键（本程序自己就要处理它）",
}

#: 「只有一个 Ctrl + 一个字母」里，这些字母在几乎所有软件里都是编辑快捷键。
#: 注册成全局热键的结果是天天打架（Ctrl+Z 想撤销却触发了录入），所以直接拒绝并给出建议。
_EDITING_LETTERS = {
    0x41: "全选", 0x43: "复制", 0x46: "查找", 0x4E: "新建", 0x50: "打印",
    0x53: "保存", 0x56: "粘贴", 0x57: "关闭窗口", 0x58: "剪切", 0x59: "重做",
    0x5A: "撤销",
}


@dataclass(frozen=True)
class Hotkey:
    """一个已经解析好的热键。`text` 是规范化写法，可直接存进配置。"""

    mods: int
    vk: int
    text: str

    @property
    def win32_mods(self) -> int:
        return self.mods | MOD_NOREPEAT


def _key_from_token(token: str) -> int:
    """把一个非修饰键的 token 变成虚拟键码，认不出来就抛 HotkeyError。"""
    low = token.strip().lower()
    if len(low) == 1 and "a" <= low <= "z":
        return 0x41 + (ord(low) - ord("a"))
    if len(low) == 1 and "0" <= low <= "9":
        return 0x30 + (ord(low) - ord("0"))
    if low.startswith("f") and low[1:].isdigit():
        number = int(low[1:])
        if 1 <= number <= 24:
            return 0x70 + (number - 1)
    if low in _VK_NAMED:
        return _VK_NAMED[low]
    raise HotkeyError(f"认不出这个键：{token.strip() or '（空）'}")


def _display_for_vk(vk: int) -> str:
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x70 + 1}"
    return _DISPLAY_BY_VK.get(vk, f"VK{vk:02X}")


def format_hotkey(mods: int, vk: int) -> str:
    """把人能读的写法拼出来，顺序固定为 Ctrl+Alt+Shift+Win+键。"""
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    parts.append(_display_for_vk(vk))
    return "+".join(parts)


def parse(text: str) -> Hotkey:
    """解析 "Ctrl+Alt+Q" 这类写法；不合法时抛 HotkeyError（消息可直接显示给用户）。"""
    raw = (text or "").strip()
    if not raw:
        raise HotkeyError("热键是空的")
    tokens = [token for token in raw.replace("＋", "+").replace("+", "+").split("+") if token.strip()]
    if not tokens:
        raise HotkeyError("热键是空的")

    mods = 0
    keys: list[str] = []
    for token in tokens:
        low = token.strip().lower()
        if low in _MODIFIER_ALIASES:
            mods |= _MODIFIER_ALIASES[low]
        else:
            keys.append(token)

    if not keys:
        raise HotkeyError("只写了修饰键，还得再加一个键（例如 Q）")
    if len(keys) > 1:
        raise HotkeyError("只能有一个主键，不能同时按两个（例如 Q 和 W）")

    vk = _key_from_token(keys[0])
    if not mods:
        # 不带修饰键的热键会吃掉全系统的一个按键，绝不能允许
        raise HotkeyError("至少要带一个修饰键（Ctrl / Alt / Shift / Win）")
    if not (mods & (MOD_CONTROL | MOD_ALT | MOD_WIN)):
        # 只按 Shift 的组合（例如 Shift+Z）看着无害，实际上会在**你正常打字时**被触发：
        # 每打一个大写 Z 都会命中一次。用户就是把热键设成了 Shift+Z，然后觉得"日程表
        # 用着用着就没了"（当时每次触发都会崩进程）。所以这类组合必须挡住。
        raise HotkeyError(
            "必须包含 Ctrl / Alt / Win 中的至少一个：只按 Shift 会和你平时打大写字母冲突"
            "（例如 Shift+Z，每打一个大写 Z 都会被触发）")
    if mods == MOD_CONTROL and vk in _EDITING_LETTERS:
        # Ctrl+Z 这种是"全宇宙通用"的编辑快捷键，注册成全局热键只会天天打架
        raise HotkeyError(
            f"Ctrl+{_display_for_vk(vk)} 是几乎所有软件里的「{_EDITING_LETTERS[vk]}」快捷键，"
            f"不适合做全局热键；建议再加一个修饰键（例如 Ctrl+Alt+{_display_for_vk(vk)}）")

    hint = _DISCOURAGED.get((mods, vk))
    if hint:
        raise HotkeyError(f"这个组合不建议使用：{hint}")
    return Hotkey(mods=mods, vk=vk, text=format_hotkey(mods, vk))


def describe(text: str) -> str:
    """给界面用：能解析就返回规范化写法，不能就返回空串。"""
    try:
        return parse(text).text
    except HotkeyError:
        return ""


# ---------------------------------------------------------------------------
# 冲突检测与登记
# ---------------------------------------------------------------------------

def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    return user32


#: 探测用的编号，跟真正登记时的编号错开，免得探测把正式登记顶掉
PROBE_ID = 0x4453

#: 当前线程真正登记着的热键：hwnd -> (id, Hotkey)
_REGISTERED: dict[int, tuple[int, Hotkey]] = {}


def _error_message(code: int) -> str:
    if code == ERROR_HOTKEY_ALREADY_REGISTERED:
        return "这个组合已经被别的程序占用了"
    if code == 0:
        return "系统没有给出原因"
    return f"系统拒绝了这次登记（错误码 {code}）"


def probe(hotkey: Hotkey) -> tuple[bool, str]:
    """试一下这个组合能不能用：能登记上就说明没被占用，随即立刻注销。

    为什么探测不需要窗口：`RegisterHotKey(NULL, ...)` 把热键挂到**调用线程的消息队列**上，
    对我们来说只是"试一试"，不需要真的收消息，所以探测可以完全不依赖 Tk 窗口。
    """
    if not _IS_WINDOWS:
        return True, ""
    user32 = _user32()
    try:
        ok = user32.RegisterHotKey(None, PROBE_ID, hotkey.win32_mods, hotkey.vk)
        if not ok:
            return False, _error_message(ctypes.get_last_error())
        user32.UnregisterHotKey(None, PROBE_ID)
        return True, ""
    except Exception as error:  # noqa: BLE001
        return False, f"探测失败：{type(error).__name__}: {error}"


def check(text: str, *, exclude_own: int | None = None) -> tuple[bool, str]:
    """界面上的"检测"按钮走这里：解析 + 冲突检测，返回 (能不能用, 说明文字)。

    `exclude_own` 传当前面板的 hwnd：如果这个组合正是**我们自己**已经登记的，
    应该报"可用"而不是"被占用"（用户重复保存同一个组合是常见操作）。
    """
    try:
        hotkey = parse(text)
    except HotkeyError as error:
        return False, str(error)
    # `exclude_own` 可能是 0（登记在"本线程消息队列"上的那种），所以判 None 不判真假
    own = _REGISTERED.get(exclude_own) if exclude_own is not None else None
    if own is not None and own[1].text == hotkey.text:
        return True, "当前正在使用这个组合"
    ok, message = probe(hotkey)
    return (True, "") if ok else (False, message)


def register(hwnd: int, hotkey: Hotkey, hotkey_id: int) -> tuple[bool, str]:
    """真正登记（`hwnd` 收 `WM_HOTKEY`）。失败时把系统原因翻成人话返回。"""
    if not _IS_WINDOWS:
        return False, "只有 Windows 支持全局热键"
    unregister(hwnd, hotkey_id)
    user32 = _user32()
    try:
        ok = user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, hotkey.win32_mods, hotkey.vk)
    except Exception as error:  # noqa: BLE001
        return False, f"登记失败：{type(error).__name__}: {error}"
    if not ok:
        return False, _error_message(ctypes.get_last_error())
    _REGISTERED[hwnd] = (hotkey_id, hotkey)
    return True, ""


def unregister(hwnd: int, hotkey_id: int) -> None:
    """注销。`hwnd` 传 0 表示"挂在本线程消息队列上"的那种登记（探测/测试会用到）。"""
    if _IS_WINDOWS:
        try:
            _user32().UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)
        except Exception:  # noqa: BLE001
            pass
    _REGISTERED.pop(hwnd, None)


def registered(hwnd: int) -> Hotkey | None:
    entry = _REGISTERED.get(hwnd)
    return entry[1] if entry else None


# ---------------------------------------------------------------------------
# 窗口钩子：把 WM_HOTKEY 交给回调（走 SetWindowSubclass，和唤醒钩子同一套路）
# ---------------------------------------------------------------------------

_HOTKEY_HOOKS: dict[int, tuple] = {}
SUBCLASS_ID = 0x44534148          # 'DSAH'


def hook_hotkey(hwnd: int, on_hotkey) -> bool:
    """让 `on_hotkey(hotkey_id)` 在热键被按下时执行。

    `WM_HOTKEY` 是**投递**给窗口的普通消息，会走 Tk 自己的消息循环，
    所以这里的回调跑在有 GIL 的线程上（对比：`SetWinEventHook` 的回调跑在
    系统线程上、没有 GIL，会把进程搞崩——那个坑记在 `agenda/dropzone.py`）。
    """
    if not _IS_WINDOWS or not hwnd or hwnd in _HOTKEY_HOOKS:
        return False
    try:
        from .winlayer import _typed_comctl32
        comctl = _typed_comctl32()
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
            ctypes.c_size_t, ctypes.c_size_t)

        def handler(window, message, wparam, lparam, _uid, _data):
            if message == WM_HOTKEY:
                try:
                    on_hotkey(int(wparam))
                except Exception:  # noqa: BLE001
                    pass
                return 0                     # 吞掉，不再往下传（别的程序也收不到）
            return comctl.DefSubclassProc(window, message, wparam, lparam)

        callback = WNDPROC(handler)
        ok = comctl.SetWindowSubclass(
            wintypes.HWND(hwnd), ctypes.cast(callback, ctypes.c_void_p), SUBCLASS_ID, 0)
        if not ok:
            return False
        _HOTKEY_HOOKS[hwnd] = (callback, comctl)
        return True
    except Exception:  # noqa: BLE001
        return False


def unhook_hotkey(hwnd: int) -> None:
    entry = _HOTKEY_HOOKS.pop(hwnd, None)
    if entry is None:
        return
    callback, comctl = entry
    try:
        comctl.RemoveWindowSubclass(wintypes.HWND(hwnd), ctypes.cast(callback, ctypes.c_void_p),
                                    SUBCLASS_ID)
    except Exception:  # noqa: BLE001
        pass


def hotkey_hooked(hwnd: int) -> bool:
    return hwnd in _HOTKEY_HOOKS


# ---------------------------------------------------------------------------
# 剪贴板
# ---------------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return kernel32


def read_clipboard_text(*, attempts: int = 5) -> str:
    """读剪贴板里的文本；没有文本或剪贴板被别的程序占着就返回空串。

    为什么要重试：Windows 的剪贴板是**独占**资源，别的程序（QQ、浏览器）在写入时
    会短暂持有它，`OpenClipboard` 会直接失败。实测有效做法是重试几次，
    每次之间歇 60 ms —— 比直接报"读取失败"体验好得多。
    """
    if not _IS_WINDOWS:
        return ""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32 = _kernel32()

    for attempt in range(max(1, attempts)):
        if not user32.OpenClipboard(None):
            time.sleep(0.06)
            continue
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        except Exception:  # noqa: BLE001
            return ""
        finally:
            try:
                user32.CloseClipboard()
            except Exception:  # noqa: BLE001
                pass
        if attempt < attempts - 1:
            time.sleep(0.06)
    return ""


def write_clipboard_text(text: str) -> bool:
    """把文本放进剪贴板（热键功能的测试要用；界面不调它）。"""
    if not _IS_WINDOWS:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = _kernel32()
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True                       # 交给系统了，不要再 GlobalFree
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 抓"选中的文字"：按一下热键就能识别，不用先手动复制
# ---------------------------------------------------------------------------

#: 这些窗口类里 Ctrl+C 不是"复制"而是别的东西（终端里是中断信号），绝不能往里发。
CONSOLE_CLASSES = (
    "consolewindowclass",               # cmd / 传统控制台
    "cascadia_hosting_window_class",    # Windows Terminal
    "pseudoconsolewindow",
    "mintty",                           # Git Bash
    "windows terminal",
)
#: Tk 窗口：本程序自己的面板/控制台都是这个类；往自己窗口发 Ctrl+C 没意义
_TK_CLASSES = ("tktoplevel", "tk")

VK_CONTROL = 0x11
VK_C = 0x43
VK_MENU = 0x12                          # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
KEYEVENTF_KEYUP = 0x0002

#: 会把合成的 Ctrl+C 污染成别的组合的修饰键。
#: **Ctrl 不在内**：Ctrl+C 本来就带 Ctrl，按着不影响；Alt / Shift / Win 按着就变味了
#: （Ctrl+Alt+Shift+C 在 Chromium 里根本不是复制）。
_POLLUTING_MODIFIERS = (VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)

#: 抓选区前等用户松开热键修饰键的时长；等不到就自己补 KEYUP。
MODIFIER_WAIT = 0.35
MODIFIER_POLL = 0.02
#: 合成的按键被前台程序漏掉时，隔多久再补一次。
RETRY_PAUSE = 0.12


def is_console_window(class_name: str) -> bool:
    return (class_name or "").strip().lower() in CONSOLE_CLASSES


def _async_key_down(vk: int) -> bool:
    """这个键此刻是不是**物理按下**着。"""
    if not _IS_WINDOWS:
        return False
    try:
        return bool(ctypes.WinDLL("user32", use_last_error=True).GetAsyncKeyState(vk) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


def _send_key(vk: int, up: bool) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


def _held_modifiers(key_state=None) -> list[int]:
    key_state = key_state or _async_key_down
    return [vk for vk in _POLLUTING_MODIFIERS if key_state(vk)]


def _release_modifiers(*, key_state=None, sleep=None, send=None, wait: float = MODIFIER_WAIT) -> list[int]:
    """把还按着的 Alt / Shift / Win 处理掉，别让它们把 Ctrl+C 污染成别的组合。

    先**等**用户自己松手（正常按一下热键，几十毫秒就松了，不用我们插手）；
    等不到才补 KEYUP。返回被强行松开的键，方便测试和排查。

    为什么必须做这件事：热键是 Alt+Shift+S 时，系统在**按下 S 的瞬间**就发 WM_HOTKEY，
    而面板要隔几十毫秒才去抓选区——那会儿用户往往还没松手。实测在 Chromium 里
    （QQ 就是这个引擎）按着 Alt+Shift 合成的 Ctrl+C **一次都不认**（6/6 失败），
    补一个 KEYUP 之后再发就 6/6 成功。表现就是用户说的"选中了按热键却录不进去"。
    """
    key_state = key_state or _async_key_down
    sleep = sleep or time.sleep
    send = send or _send_key

    waited = 0.0
    while waited < wait:
        if not _held_modifiers(key_state):
            return []
        sleep(MODIFIER_POLL)
        waited += MODIFIER_POLL
    forced = _held_modifiers(key_state)
    for vk in forced:
        send(vk, True)
    return forced


def _press_ctrl_c(*, key_state=None, sleep=None, send=None) -> None:
    """合成一次 Ctrl+C（交给前台程序自己把选区放进剪贴板）。

    先中和热键上可能还按着的修饰键，再发；顺序不能反（见 `_release_modifiers`）。
    """
    sleep = sleep or time.sleep
    send = send or _send_key
    _release_modifiers(key_state=key_state, sleep=sleep, send=send)
    send(VK_CONTROL, False)
    send(VK_C, False)
    sleep(0.03)
    send(VK_C, True)
    send(VK_CONTROL, True)


def capture_selection(*, press=None, read=None, write=None, sleep=None,
                      foreground_class=None, attempts: int = 8,
                      interval: float = 0.08, retries: int = 3) -> tuple[str, str]:
    """抓取前台程序里**选中的文字**，返回 (文本, 提示)。

    用户在 QQ 里选中一条通知，按一下热键就能录入——不必先按 Ctrl+C。
    做法：记住剪贴板原内容 → 合成 Ctrl+C → 等剪贴板变化 → 读出来 → **把原内容放回去**。
    复原这一步是必要的：否则用户自己剪贴板里的东西会被我们偷偷覆盖掉。

    两种情况直接放弃（返回空文本 + 说明）：
      * 前台是终端类窗口：那里 Ctrl+C 是中断信号，发过去可能打断用户正在跑的命令；
      * 剪贴板一直没变化：说明没有选中任何文字（那就让调用方退回"读剪贴板"）。

    `retries`：一次不成再补发一次。实测前台程序（Chromium）偶发漏掉第一次合成按键，
    单发成功率约 5/6——对用户就是"有时好用有时不好用"，所以默认再补两次。

    参数都可注入（press / read / write / sleep / foreground_class），
    这样单测能确定性地验证"先存 → 发键 → 轮询 → 复原"这套顺序，不必真的发按键。
    """
    press = press or _press_ctrl_c
    read = read or read_clipboard_text
    write = write or write_clipboard_text
    sleep = sleep or time.sleep
    foreground_class = foreground_class or _foreground_class

    try:
        class_name = foreground_class() or ""
    except Exception:  # noqa: BLE001
        class_name = ""
    if is_console_window(class_name):
        return "", "当前前台是终端窗口（Ctrl+C 在那里是中断信号），已跳过；请手动复制后再按热键"
    if class_name.strip().lower() in _TK_CLASSES:
        return "", "当前前台是本程序窗口，没有可抓取的选区"

    try:
        before = read()
    except Exception:  # noqa: BLE001
        before = ""

    captured = ""
    rounds = max(1, retries)
    for round_index in range(rounds):
        try:
            press()
        except Exception as error:  # noqa: BLE001
            return "", f"没能向前台程序发送复制指令：{error}"

        for _ in range(max(1, attempts)):
            sleep(interval)
            try:
                current = read()
            except Exception:  # noqa: BLE001
                current = ""
            if current and current != before:
                captured = current
                break
        if captured:
            break
        if round_index + 1 < rounds:
            sleep(RETRY_PAUSE)          # 歇一下再补一次，别把前台程序按懵

    if not captured:
        return "", "没有检测到选中的文字：先选中内容，或者直接复制到剪贴板再按热键"
    if before:
        # 把用户原来的剪贴板放回去（我们只是借用一下）
        try:
            write(before)
        except Exception:  # noqa: BLE001
            pass
    return captured, ""


def _foreground_class() -> str:
    """前台窗口的类名（抓选区前要判断"能不能往这里发 Ctrl+C"）。"""
    if not _IS_WINDOWS:
        return ""
    try:
        from . import winlayer
        return winlayer.foreground_class()
    except Exception:  # noqa: BLE001
        return ""
