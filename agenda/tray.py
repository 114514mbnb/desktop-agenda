"""托盘图标（纯 ctypes 对接 Win32 Shell_NotifyIcon，不加第三方依赖）。

用途：关掉面板后客户端常驻托盘，通知照收、到点提醒照弹。
菜单：呼出客户端窗口 / 快速录入群消息 / 打开面板 / 关闭面板 / 立即整合 / 退出应用。

**踩过的大坑（菜单是一片空白）**：菜单项原来是用 `AppendMenuW` 建的，
它**不会把字符串复制进菜单**，只记下你给的那个指针。ctypes 给 Python 字符串
临时转出来的 `wchar_t*` 在调用返回后就被回收了——于是：
  * 紧跟着调 `GetMenuStringW` 还能读回正确文字（那块内存还没被复用）→ 自检全绿；
  * 等真正弹出来画的时候，内存早被别的分配覆盖 → **菜单只剩一条分隔线，一个字都没有**。
用户截图里那个"白色空框"就是这个（ASCII 文案偶尔侥幸没被覆盖，中文一次都没活下来）。
改用 `InsertMenuItemW` + `MIIM_STRING`：这个 API 的文档写明**字符串会被复制**，
弹出来就正常了。`tools/` 下的临时探针就是这么一步步定位到的。

顺带修掉另一个坑：所有 user32/kernel32 函数都声明了 argtypes/restype。
原来 `DefWindowProcW(hwnd, msg, wparam, lparam)` 没声明类型，64 位的 lparam
被 ctypes 按 C int 转换 → `OverflowError: int too long to convert`，
窗口过程对所有它不处理的消息都在抛异常（被 ctypes 静默吞掉、返回垃圾值）。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
#: 气泡提示的图标（信息 / 警告）
NIIF_INFO, NIIF_WARNING = 0x01, 0x02
#: Explorer 重启（或崩溃后自动重启）会广播这条消息 —— 收到就得把图标重新挂上，
#: 否则通知区里那个图标永远消失（用户的原话："图标怎么没了？"）
WM_TASKBARCREATED = WM_USER + 21
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
MFT_SEPARATOR = 0x0800
MIIM_ID, MIIM_FTYPE, MIIM_STRING, MIIM_STATE = 0x0002, 0x0100, 0x0040, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001
IDI_APPLICATION = 32512

# 菜单项 id
ID_OPEN_CONSOLE = 1003
ID_QUICK_ENTRY = 1006
ID_OPEN_PANEL = 1001
ID_CLOSE_PANEL = 1002
ID_MERGE = 1004
ID_QUIT = 1005

#: 右键菜单的文案与顺序（id, 文字）；文字为 None 表示一条分隔线。
MENU_LABELS: tuple[tuple[int, str | None], ...] = (
    (ID_OPEN_CONSOLE, "呼出客户端窗口"),
    (ID_QUICK_ENTRY, "快速录入群消息…"),
    (ID_OPEN_PANEL, "打开桌面面板"),
    (ID_CLOSE_PANEL, "收起桌面面板"),
    (ID_MERGE, "立即整合群通知"),
    (0, None),
    (ID_QUIT, "退出应用"),
)

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("fMask", ctypes.c_uint),
        ("fType", ctypes.c_uint),
        ("fState", ctypes.c_uint),
        ("wID", ctypes.c_uint),
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP),
        ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", ctypes.c_size_t),
        ("dwTypeData", ctypes.c_wchar_p),
        ("cch", ctypes.c_uint),
        ("hbmpItem", wintypes.HBITMAP),
    ]


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _typed_user32():
    """把用到的 user32 函数签名写清楚。

    不声明的话 ctypes 会按 C int 转换 64 位的句柄/指针参数，
    轻则 `OverflowError: int too long to convert`，重则句柄被截断。
    """
    user32 = ctypes.windll.user32
    user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.InsertMenuItemW.argtypes = [wintypes.HMENU, ctypes.c_uint, wintypes.BOOL,
                                       ctypes.POINTER(MENUITEMINFOW)]
    user32.InsertMenuItemW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    user32.TrackPopupMenu.restype = wintypes.BOOL
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.restype = wintypes.HICON
    user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    return user32


def build_menu(user32=None):
    """建右键菜单，返回 HMENU（调用方负责 DestroyMenu）。

    用 `InsertMenuItemW` 而不是 `AppendMenuW`——见模块开头记的那个大坑：
    AppendMenu 不复制字符串，ctypes 的临时缓冲区一回收，菜单就成了空白框。
    """
    user32 = user32 or _typed_user32()
    menu = user32.CreatePopupMenu()
    for item_id, label in MENU_LABELS:
        info = MENUITEMINFOW()
        info.cbSize = ctypes.sizeof(MENUITEMINFOW)
        if label is None:
            info.fMask = MIIM_FTYPE
            info.fType = MFT_SEPARATOR
        else:
            info.fMask = MIIM_ID | MIIM_FTYPE | MIIM_STRING
            info.fType = MF_STRING
            info.wID = item_id
            info.dwTypeData = label          # InsertMenuItemW 会把它复制进菜单
            info.cch = len(label)
        user32.InsertMenuItemW(menu, user32.GetMenuItemCount(menu), True, ctypes.byref(info))
    return menu


class TrayIcon:
    """托盘图标；在独立线程里跑消息循环，回调抛回主线程执行。"""

    def __init__(self, title: str, on_action, icon_path: str | None = None):
        self.title = title
        self.on_action = on_action          # 回调：on_action(command_id)
        self.icon_path = icon_path
        self._thread = None
        self._hwnd = None
        self._nid = None
        self._wndproc = None
        self._class_atom = None
        self._running = False

    # -- 生命周期 --------------------------------------------------------
    def start(self) -> bool:
        global _LAST_ICON

        if sys.platform != "win32":
            return False
        if self._thread is not None:
            return True
        _LAST_ICON = self
        import threading
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        for _ in range(30):
            if self._hwnd:
                return True
            threading.Event().wait(0.05)
        return False

    def stop(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._running = False

    # -- 内部 ------------------------------------------------------------
    def _run(self) -> None:
        user32 = _typed_user32()
        kernel32 = ctypes.windll.kernel32
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"AgendaTrayWnd_{id(self)}"

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                if lparam == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                elif lparam == WM_LBUTTONDBLCLK:
                    # 双击托盘：把**控制台**叫出来。
                    # 以前是"打开面板"——但用户双击托盘通常是想找那个消失的窗口，
                    # 打开控制台才是他们要找的东西（面板本来就在屏幕上）。
                    self._dispatch(ID_OPEN_CONSOLE)
                return 0
            if msg == WM_COMMAND:
                self._dispatch(wparam & 0xFFFF)
                return 0
            if msg == WM_CLOSE:
                self._remove_icon(hwnd)
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_TASKBARCREATED:
                # Explorer 重启过：图标没了，得重新挂一个
                self._readd_icon()
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)
        wc = WNDCLASS()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinstance
        wc.hIcon = self._load_icon(user32)
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        wc.lpszClassName = class_name
        self._class_atom = user32.RegisterClassW(ctypes.byref(wc))
        if not self._class_atom:
            return

        self._hwnd = user32.CreateWindowExW(
            0, class_name, self.title, 0, 0, 0, 0, 0, None, None, hinstance, None,
        )
        if not self._hwnd:
            return

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = wc.hIcon
        nid.szTip = f"{self.title} · 双击呼出客户端窗口 · 右键有菜单"
        self._nid = nid
        if not ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            return

        self._running = True
        message = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def notify(self, title: str, message: str, *, warning: bool = False) -> bool:
        """弹一个托盘气泡（Win10/11 上显示成通知）。

        用途：用户点了图标/快捷方式但**看起来什么都没发生**时给个可见回执
        （比如"日程表已经在运行，它在屏幕右下角"）。没有这个回执，
        用户只会得出"点了没反应"的结论。
        """
        if not sys.platform == "win32" or self._nid is None:
            return False
        nid = self._nid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
        nid.szInfo = message[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        ok = bool(ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP      # 复位，免得下次改图标时又弹一遍
        return ok

    def _readd_icon(self) -> None:
        """Explorer 重启后把图标挂回来。"""
        if self._nid is None:
            return
        nid = self._nid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        except Exception:                                    # noqa: BLE001
            pass

    def _load_icon(self, user32):
        if self.icon_path:
            handle = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if handle:
                return handle
        return user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

    def _remove_icon(self, hwnd) -> None:
        if self._nid is not None:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None

    def _show_menu(self, hwnd) -> None:
        user32 = _typed_user32()
        menu = build_menu(user32)
        try:
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            # TrackPopupMenu 要求先把自己设成前台窗口，否则菜单会立刻被点掉
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, hwnd, None,
            )
        finally:
            user32.DestroyMenu(menu)
        if command:
            self._dispatch(command)

    def _dispatch(self, command: int) -> None:
        try:
            self.on_action(command)
        except Exception:
            pass


def command_ids() -> dict[str, int]:
    """给调用方用的菜单 id 常量。"""
    return {
        "open_panel": ID_OPEN_PANEL,
        "close_panel": ID_CLOSE_PANEL,
        "open_console": ID_OPEN_CONSOLE,
        "quick_entry": ID_QUICK_ENTRY,
        "merge": ID_MERGE,
        "quit": ID_QUIT,
    }


#: 最近创建的那个托盘图标；模块级 `balloon()` 转发到它（老调用点还在用）。
_LAST_ICON = None


def last_icon():
    return _LAST_ICON


def balloon(title: str, message: str, *, warning: bool = False) -> bool:
    """托盘气泡提醒（Windows 10/11 会显示为通知）。

    ⚠ 这里以前是**空壳**：`return True` 但什么都不做 —— 调用方以为提示发出去了，
    用户什么都没看到。现在转发给当前那个图标，没有图标就老实返回 False。
    """
    icon = _LAST_ICON
    if icon is None:
        return False
    return icon.notify(title, message, warning=warning)
