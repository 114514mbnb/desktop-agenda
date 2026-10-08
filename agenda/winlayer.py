"""Win32 窗口行为：把面板"钉在桌面上"，而不是压在一切窗口之上。

为什么需要：面板常驻桌面右侧，如果设成 always-on-top，打游戏/看视频时就会挡住内容。
正确行为是像桌面小部件那样——在桌面这一层，被正常窗口盖住，切回桌面才看见。

本模块提供：
  * desktop_is_foreground() —— 前台窗口是不是桌面/任务栏
  * fullscreen_foreground() —— 前台是不是全屏应用（游戏、播放器）
  * send_to_bottom(hwnd)    —— 把窗口压到非置顶窗口的最底层
  * make_click_through(hwnd)—— 让窗口不吃鼠标事件（游戏时不影响操作）
  * attach_to_desktop(hwnd) —— 把窗口挂成桌面子窗口（可选，最彻底的桌面组件化）

全部失败都静默降级：拿不到句柄就什么都不做，不影响面板本身。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

HWND_BOTTOM = 1
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

DESKTOP_CLASSES = {
    "Progman",              # 桌面本体（点任务栏"显示桌面"后前台常常是它）
    "WorkerW",              # 承载桌面图标那层
    "Shell_TrayWnd",        # 主任务栏
    "Shell_SecondaryTrayWnd",
    "SHELLDLL_DefView",
}

_IS_WINDOWS = sys.platform == "win32"


def _user32():
    return ctypes.windll.user32 if _IS_WINDOWS else None


def foreground_class() -> str:
    """前台窗口的类名（非 Windows 或失败返回空串）。"""
    if not _IS_WINDOWS:
        return ""
    try:
        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value
    except Exception:  # noqa: BLE001
        return ""


def foreground_title() -> str:
    if not _IS_WINDOWS:
        return ""
    try:
        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:  # noqa: BLE001
        return ""


def desktop_is_foreground() -> bool:
    """前台是桌面或任务栏 → 说明用户正在看桌面。"""
    return foreground_class() in DESKTOP_CLASSES


def fullscreen_foreground() -> bool:
    """前台窗口是否是全屏应用（覆盖整块屏幕，通常是游戏/视频）。"""
    if not _IS_WINDOWS:
        return False
    try:
        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        if foreground_class() in DESKTOP_CLASSES:
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        # 允许几像素误差；同时排除"窗口位置为负"的多屏情况
        return width >= screen_w - 2 and height >= screen_h - 2 and rect.left <= 0 and rect.top <= 0
    except Exception:  # noqa: BLE001
        return False


def raise_to_top_of_normal(hwnd: int) -> bool:
    """浮到**普通窗口的最上层**——但仍然在置顶窗口之下。

    这是"桌面宠物"要的那个位置：显示桌面时你一眼能看到它，
    但它不是 always-on-top，所以切回别的窗口时会被正常盖住，不挡工作也不挡游戏。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().SetWindowPos(
            wintypes.HWND(hwnd), wintypes.HWND(HWND_TOP), 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        ))
    except Exception:  # noqa: BLE001
        return False


def is_visible(hwnd: int) -> bool:
    """窗口当前是否真的可见（被最小化/隐藏时为 False）。"""
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().IsWindowVisible(wintypes.HWND(hwnd)))
    except Exception:  # noqa: BLE001
        return False


def is_minimized(hwnd: int) -> bool:
    """窗口是不是被最小化了（IsIconic）。

    为什么盯着这个：任务栏右下角的"显示桌面"按钮会**把所有窗口最小化**，
    其中就包括面板。最小化之后再怎么 SetWindowPos 都不会让它重新出现，
    必须显式还原——用户看到的就是"我按了那个按钮，日程表就没了"。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().IsIconic(wintypes.HWND(hwnd)))
    except Exception:  # noqa: BLE001
        return False


SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5


def restore_window(hwnd: int, *, activate: bool = False) -> bool:
    """把最小化的窗口还原回来（默认不抢焦点）。"""
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        user32 = _user32()
        user32.ShowWindow(wintypes.HWND(hwnd),
                          SW_RESTORE if activate else SW_SHOWNOACTIVATE)
        return True
    except Exception:  # noqa: BLE001
        return False


def flash_window(hwnd: int, *, count: int = 3) -> bool:
    """让任务栏上那个按钮闪几下（不抢焦点，只提醒）。

    用途：用户点了托盘图标/快捷方式，我们把窗口放到最前时，如果窗口本来就
    在最前面（或它在别的虚拟桌面上），用户可能还是"没看出发生了什么"——
    任务栏闪一下是 Windows 上最标准的"喂，看这里"（用户点名要的：
    「点图标的时候要把控制台窗口带到最前面，并在任务栏闪烁提醒」）。
    """
    if not _IS_WINDOWS or not hwnd:
        return False

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD)]

    try:
        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = wintypes.HWND(hwnd)
        # FLASHW_ALL：任务栏按钮 + 标题栏都闪；FLASHW_TIMERNOFG：闪到用户来看它为止
        info.dwFlags = 0x00000003 | 0x0000000C
        info.uCount = max(1, count)
        info.dwTimeout = 0
        return bool(_user32().FlashWindowEx(ctypes.byref(info)))
    except Exception:  # noqa: BLE001
        return False


def bring_to_front(hwnd: int, *, flash: bool = True) -> bool:
    """把窗口放到最前并（可选）让任务栏按钮闪一下。

    对控制台这种**普通**窗口用的：`raise_to_top_of_normal` 只管层级，
    不保证它真的跑到最前面（用户切到别的程序时就还在后面）。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        user32 = _user32()
        handle = wintypes.HWND(hwnd)
        if is_minimized(hwnd):
            restore_window(hwnd, activate=True)
        user32.ShowWindow(handle, SW_SHOW)
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
        user32.SetWindowPos(handle, wintypes.HWND(HWND_TOP), 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE)
        if flash:
            flash_window(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False


def send_to_bottom(hwnd: int) -> bool:
    """压到非置顶窗口最底层（桌面之上、所有普通窗口之下）。"""
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().SetWindowPos(
            wintypes.HWND(hwnd), wintypes.HWND(HWND_BOTTOM), 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        ))
    except Exception:  # noqa: BLE001
        return False


def apply_click_through(hwnd: int) -> bool:
    """加上 WS_EX_TRANSPARENT | WS_EX_NOACTIVATE：不吃鼠标、不抢焦点。

    这样面板在游戏/工作时不会挡住点击，也不会因为你点面板而把游戏切到后台。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        user32 = _user32()
        style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
        # ctypes 默认按 32 位取；Win64 用 GetWindowLongPtrW 更稳
        getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = getter(wintypes.HWND(hwnd), GWL_EXSTYLE)
        setter(wintypes.HWND(hwnd), GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        return True
    except Exception:  # noqa: BLE001
        return False


def clear_click_through(hwnd: int) -> bool:
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        user32 = _user32()
        getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = getter(wintypes.HWND(hwnd), GWL_EXSTYLE)
        setter(wintypes.HWND(hwnd), GWL_EXSTYLE, style & ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE))
        return True
    except Exception:  # noqa: BLE001
        return False


def set_topmost(hwnd: int, topmost: bool) -> bool:
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().SetWindowPos(
            wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST if topmost else HWND_NOTOPMOST),
            0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        ))
    except Exception:  # noqa: BLE001
        return False


def find_desktop_worker() -> int:
    """找到承载桌面图标的那层 WorkerW（SetParent 用它，最像"桌面小部件"）。"""
    if not _IS_WINDOWS:
        return 0
    try:
        user32 = _user32()
        progman = user32.FindWindowW("Progman", None)
        if not progman:
            return 0
        # 让 Progman 生成 WorkerW
        result = ctypes.c_ulong()
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(result))
        worker = ctypes.c_ulong(0)

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            shell_view = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
            if shell_view:
                found = user32.FindWindowExW(0, hwnd, "WorkerW", None)
                if found:
                    worker.value = found
                    return False
            return True

        user32.EnumWindows(enum_proc, 0)
        return worker.value or progman
    except Exception:  # noqa: BLE001
        return 0


def attach_to_desktop(hwnd: int) -> bool:
    """把窗口挂成桌面子窗口：最彻底的"桌面组件"，但会影响窗口管理，谨慎使用。"""
    if not _IS_WINDOWS or not hwnd:
        return False
    parent = find_desktop_worker()
    if not parent:
        return False
    try:
        return bool(_user32().SetParent(wintypes.HWND(hwnd), wintypes.HWND(parent)))
    except Exception:  # noqa: BLE001
        return False


def detach_from_desktop(hwnd: int) -> bool:
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().SetParent(wintypes.HWND(hwnd), None))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 防止被"显示桌面"最小化
# ---------------------------------------------------------------------------

WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
SC_RESTORE = 0xF120
GWLP_WNDPROC = -4
SW_SHOWNOACTIVATE = 4

#: 被挡掉最小化的窗口（保留 WndProc 引用，否则 ctypes 会把它回收掉）
_BLOCKED: dict[int, tuple] = {}


def _typed_user32():
    """一份**带完整 argtypes** 的 user32 绑定。

    为什么不能用 `ctypes.windll.user32` 直接调这几个函数：ctypes 在没声明 argtypes 时
    按 C int（32 位）处理整数参数，而窗口过程/句柄在 64 位下是 64 位值，
    传进去会 `OverflowError: int too long to convert`——实测表现为窗口过程里每一跳都抛异常。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [
        ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    return user32


def _typed_comctl32():
    """带 argtypes 的 comctl32 绑定（窗口子类化要用它）。"""
    comctl = ctypes.WinDLL("comctl32", use_last_error=True)
    comctl.SetWindowSubclass.argtypes = [
        wintypes.HWND, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    comctl.SetWindowSubclass.restype = wintypes.BOOL
    comctl.DefSubclassProc.argtypes = [
        wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    comctl.DefSubclassProc.restype = ctypes.c_ssize_t
    comctl.RemoveWindowSubclass.argtypes = [
        wintypes.HWND, ctypes.c_void_p, ctypes.c_size_t]
    comctl.RemoveWindowSubclass.restype = wintypes.BOOL
    return comctl


def block_minimize(hwnd: int) -> bool:
    """让这个窗口**不可能被最小化**。

    为什么必须从消息层挡：任务栏右下角的"显示桌面"会把所有顶层窗口最小化。
    面板是 `overrideredirect + WS_EX_TOOLWINDOW`（不进 Alt+Tab、没有任务栏按钮），
    一旦被最小化就**没有任何入口能把它还原**——点任务栏找不到它，
    用户看到的就是"我按了那个按钮，日程表就没了"。
    轮询里再 ShowWindow 也救不回来（中间那段时间窗已经没了）。

    做法：用 **SetWindowSubclass**（不是 SetWindowLongPtr 换 WndProc）把 SC_MINIMIZE
    换成 SW_SHOWNOACTIVATE。子类化走 comctl32 的子类链，不会覆盖 Tk 自己存的过程指针，
    每个消息也都会继续往下传——换 WndProc 那种做法会破坏 Tk 的窗口数据处理。
    失败就静默降级（还有轮询兜底）。
    """
    if not _IS_WINDOWS or not hwnd or hwnd in _BLOCKED:
        return False
    try:
        user32 = _typed_user32()
        comctl = _typed_comctl32()
        SUBCLASS_ID = 0x44534147          # 'DSAG'，随便一个稳定值
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
            ctypes.c_size_t, ctypes.c_size_t)

        def handler(window, message, wparam, lparam, _uid, _data):
            if message == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MINIMIZE:
                # 换成"显示但不激活"：等于最小化无效，还不会抢焦点
                user32.ShowWindow(wintypes.HWND(window), SW_SHOWNOACTIVATE)
                return 0
            return comctl.DefSubclassProc(window, message, wparam, lparam)

        callback = WNDPROC(handler)
        ok = comctl.SetWindowSubclass(
            wintypes.HWND(hwnd), ctypes.cast(callback, ctypes.c_void_p),
            SUBCLASS_ID, 0)
        if not ok:
            return False
        _BLOCKED[hwnd] = (callback, comctl)   # 保留引用，否则回调会被回收
        return True
    except Exception:  # noqa: BLE001
        return False


def minimize_blocked(hwnd: int) -> bool:
    return hwnd in _BLOCKED


# ---------------------------------------------------------------------------
# 系统唤醒（解锁 / 显示器重新点亮 / 分辨率变化）→ 立刻回调，不用等轮询
# ---------------------------------------------------------------------------

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
WM_DISPLAYCHANGE = 0x007E
WM_WININICHANGE = 0x001A
WM_SETTINGCHANGE = 0x001A

#: 被挂了唤醒回调的窗口（保留回调引用，否则 ctypes 会回收掉）
_WAKE_HOOKS: dict[int, tuple] = {}


def hook_wake_events(hwnd: int, on_wake) -> bool:
    """解锁屏幕 / 显示器重新点亮 / 分辨率变化时**立刻**回调 `on_wake()`。

    为什么需要：`_fg_poll` 最快也要等一个轮询周期（现在 40 ms）——
    用户"切回主屏幕"时那点延迟是能感觉出来的。这几个事件系统会**主动发消息**到
    我们的窗口，走 `SetWindowSubclass` 在 Tk 自己的消息循环里回调（不会像
    `SetWinEventHook` 那样掉进"没有 GIL 的系统线程"里把进程搞崩——那个坑记在
    `agenda/dropzone.py`）。
    """
    if not _IS_WINDOWS or not hwnd or hwnd in _WAKE_HOOKS:
        return False
    try:
        comctl = _typed_comctl32()
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
            ctypes.c_size_t, ctypes.c_size_t)

        def handler(window, message, wparam, lparam, _uid, _data):
            try:
                if message == WM_WTSSESSION_CHANGE and int(wparam) == WTS_SESSION_UNLOCK:
                    on_wake("unlock")
                elif message == WM_POWERBROADCAST and int(wparam) == PBT_POWERSETTINGCHANGE:
                    on_wake("power")
                elif message in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
                    on_wake("display")
            except Exception:  # noqa: BLE001
                pass
            return comctl.DefSubclassProc(window, message, wparam, lparam)

        callback = WNDPROC(handler)
        # 子类 id 跟 block_minimize 用不同的值，两个钩子互不干扰（comctl32 维护的是链）
        ok = comctl.SetWindowSubclass(
            wintypes.HWND(hwnd), ctypes.cast(callback, ctypes.c_void_p),
            0x44534157, 0)          # 'DSAW'
        if not ok:
            return False
        _WAKE_HOOKS[hwnd] = (callback, comctl)
        # 会话通知要显式登记，否则收不到 WM_WTSSESSION_CHANGE（失败也无所谓）
        try:
            wtsapi = ctypes.WinDLL("wtsapi32", use_last_error=True)
            wtsapi.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, ctypes.c_uint]
            wtsapi.WTSRegisterSessionNotification(wintypes.HWND(hwnd), 0)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


def unhook_wake_events(hwnd: int) -> None:
    """注销注销时的钩子（主要是给测试用；进程退出时不做也无妨）。"""
    entry = _WAKE_HOOKS.pop(hwnd, None)
    if entry is None:
        return
    _callback, comctl = entry
    try:
        comctl.RemoveWindowSubclass(wintypes.HWND(hwnd), ctypes.cast(_callback, ctypes.c_void_p),
                                    0x44534157)
    except Exception:  # noqa: BLE001
        pass


def wake_hooked(hwnd: int) -> bool:
    return hwnd in _WAKE_HOOKS
