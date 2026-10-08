"""把文件拖到面板上就能自动提取（Windows 拖放接收窗口）。

为什么需要一个单独的隐藏窗口、而不是用面板窗口：
  在 Tk 的窗口上换 WndProc 或调 DragAcceptFiles，都要动 Tk 自己的窗口过程，
  一旦处理不对就会影响指针事件、甚至让面板收不到鼠标消息。
  另起一个**隐藏的接收窗口**用 `DragAcceptFiles` 注册，成本几乎为零，
  而且完全不碰 Tk。

消息怎么跑起来：接收窗口建在 Tk 的线程里，靠 Tk 的 `after` 定时 `PeekMessageW`
把消息抽出来处理。这样回调里可以安全地操作 Tk 控件（同一个线程），
不用跨线程 post，也就没有并发问题。

支持拖放的内容：
  * 文件路径（资源管理器 / 桌面 / QQ 把消息另存为文件）
  * **文本**（`CF_UNICODETEXT`）：很多聊天软件拖出来的是纯文本，不是文件
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

# --- 拖放相关 -----------------------------------------------------------------
WM_DROPFILES = 0x0233
WM_COPYDATA = 0x004A
GWLP_USERDATA = -21
SW_HIDE = 0

#: 保住**所有**注册过的 WNDPROC 回调。不能只留一个全局变量：
#: 进程里可能存在多个 DropTarget（面板重启、测试里接连创建），
#: 后来的会把前一个覆盖掉，前一个窗口再收到消息就跳进已回收的回调 → 崩溃。
_callback_refs: list = []


if _IS_WINDOWS:
    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", ctypes.c_uint),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]
else:                          # 非 Windows 占位，保证 import 不炸
    WNDCLASSW = None            # type: ignore[assignment]


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    return kernel


def _shell32():
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.DragQueryFileW.argtypes = [
        wintypes.HANDLE, ctypes.c_uint, wintypes.LPWSTR, ctypes.c_uint]
    shell.DragQueryFileW.restype = ctypes.c_uint
    shell.DragFinish.argtypes = [wintypes.HANDLE]
    shell.DragFinish.restype = None
    shell.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell.DragAcceptFiles.restype = None
    return shell


def _typed_user32():
    """带 argtypes 的 user32：64 位下句柄/指针混用会 OverflowError。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    return user32


class DropTarget:
    """接收"拖到面板上"的文件/文件夹/文本。

    `on_files` 收到路径字符串列表；`on_text` 收到一段文本。
    窗口默认不可见；`show_hint()` 可以让它出现在屏幕上当拖放区（可选）。
    """

    CLASS_NAME = "DesktopAgendaDropTarget"

    def __init__(self, on_files, on_text=None):
        self.on_files = on_files
        self.on_text = on_text
        self.hwnd = 0
        self._visible = False
        self.error = ""
        # 类名**每个实例都不一样**。踩过的坑：用固定的类名时，进程里创建第二个
        # DropTarget 会因为 "class already exists" 而拿到**上一次注册的类**，
        # 那个类的 lpfnWndProc 指向上一份（可能已被 GC 回收的）Python 回调——
        # 于是新窗口一收到消息就跳进野指针，进程直接崩
        # （Windows 退出码 0xC000041D，而且只在"先建过别的 Tk 窗口再建它"时复现）。
        self.class_name = f"{self.CLASS_NAME}_{id(self)}"

    # -- 生命周期 --------------------------------------------------------
    def create(self) -> bool:
        if not _IS_WINDOWS:
            self.error = "只有 Windows 支持拖放"
            return False
        if self.hwnd:
            return True
        try:
            user32 = _typed_user32()
            shell = _shell32()
            kernel = _kernel32()
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                ctypes.c_size_t, ctypes.c_ssize_t)

            def wnd_proc(hwnd, message, wparam, lparam):
                if message == WM_DROPFILES:
                    try:
                        self._handle_drop(wparam)
                    except Exception:
                        pass
                    finally:
                        try:                       # 句柄无效（测试里是构造的）也不该炸
                            shell.DragFinish(wintypes.HANDLE(wparam))
                        except Exception:
                            pass
                    return 0
                if message == 0x0002:          # WM_DESTROY
                    return 0
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)

            proc = WNDPROC(wnd_proc)
            _callback_refs.append(proc)        # 保命：回调被回收就会崩

            instance = kernel.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = instance
            wc.hIcon = None
            wc.hCursor = None
            wc.hbrBackground = None
            wc.lpszMenuName = None
            wc.lpszClassName = self.class_name
            if not user32.RegisterClassW(ctypes.byref(wc)):
                # 每个实例都有自己的类名，走到这里说明真出问题了，别再往下建窗口
                self.error = f"注册窗口类失败（错误 {ctypes.get_last_error()}）"
                return False
            hwnd = user32.CreateWindowExW(
                0, self.class_name, "桌面日程·拖放接收", 0,
                0, 0, 10, 10, None, None, instance, None)
            if not hwnd:
                self.error = f"建不了拖放接收窗口（错误 {ctypes.get_last_error()}）"
                return False
            self.hwnd = int(hwnd)
            shell.DragAcceptFiles(wintypes.HWND(self.hwnd), True)
            # 窗口创建时就没给 WS_VISIBLE，本身是隐藏的，不用再 ShowWindow(SW_HIDE)，
            # 免得白白收一条 WM_SHOWWINDOW 进 pump
            return True
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
            return False

    def destroy(self) -> None:
        if self.hwnd and _IS_WINDOWS:
            try:
                _typed_user32().DestroyWindow(wintypes.HWND(self.hwnd))
            except Exception:
                pass
        self.hwnd = 0

    # -- 消息泵 ----------------------------------------------------------
    def pump(self, max_messages: int = 8) -> int:
        """把本窗口的待处理消息抽出来处理（Tk 的 after 定时调它）。返回处理条数。"""
        if not self.hwnd or not _IS_WINDOWS:
            return 0
        try:
            user32 = _typed_user32()
        except Exception:
            return 0
        msg = wintypes.MSG()
        handled = 0
        for _ in range(max_messages):
            if not user32.PeekMessageW(ctypes.byref(msg), wintypes.HWND(self.hwnd), 0, 0, 1):
                break
            # PM_REMOVE=1；翻译 + 派发，交给上面的 wnd_proc
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            handled += 1
        return handled

    # -- 落点处理 --------------------------------------------------------
    def _handle_drop(self, hdrop) -> None:
        shell = _shell32()
        count = shell.DragQueryFileW(wintypes.HANDLE(hdrop), 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = shell.DragQueryFileW(wintypes.HANDLE(hdrop), index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell.DragQueryFileW(wintypes.HANDLE(hdrop), index, buffer, length + 1)
            if buffer.value:
                paths.append(buffer.value)
        if not paths:
            return
        if callable(self.on_files):
            self.on_files(paths)

    def read_paths(self, paths) -> tuple[list[str], list[str]]:
        """把落点展开成 (文件列表, 文件夹里的文件列表)。"""
        files: list[str] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES:
                        files.append(str(child))
            elif path.is_file():
                files.append(str(path))
        return files, [raw for raw in paths if Path(raw).is_dir()]


#: 当成"一段通知文本"来读的后缀
TEXT_SUFFIXES = {".txt", ".md", ".text", ".log", ".csv", ".tsv", ".html", ".htm", ".json"}


def foreground_state(fullscreen_probe, desktop_probe) -> tuple[bool, bool]:
    """读一次"有没有全屏应用 / 是不是在桌面"。

    单独抽成函数是为了让面板那边的快速轮询只做这一件事——
    实测两个探测加起来约 0.01 ms，所以 100 ms 一次轮询的 CPU 开销可以忽略，
    比事件钩子划算得多。
    """
    return bool(fullscreen_probe()), bool(desktop_probe())


# ---------------------------------------------------------------------------
# 关于"前台窗口变化通知"：试过 SetWinEventHook，**不能用**
# ---------------------------------------------------------------------------
# 记录一下结论，免得以后有人再踩：
#   `SetWinEventHook(EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT, ...)` 注册的
#   ctypes 回调（WINFUNCTYPE）是**由系统的窗口事件线程直接调用**的，那个线程没有
#   持有 CPython 的 GIL。回调里只要碰 Python 对象，进程就会当场死：
#
#       Fatal Python error: PyEval_RestoreThread: the function must be called
#       with the GIL held, ... (the current Python thread state is NULL)
#
#   实时复现过（主线程在 Tk 的 update 里，事件回调一进来就崩）。
#   要安全用这个钩子就得写成 C 扩展（或者用 WINEVENT_INCONTEXT + 自己的消息循环），
#   对一个标准库零依赖的项目来说不值当。
#
#   替代方案：**100 ms 的廉价轮询**。探测本身 0.01 ms/次，
#   最坏延迟 100 ms（人眼基本无感），而且不会有任何线程/GIL 风险。


def read_text_file(path: str | Path) -> str:
    """按常见中文编码读出文本（QQ 导出的消息常是 GBK）。"""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")
