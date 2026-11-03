"""Windows target validation and input used by the standalone desktop program."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import Path
import time

u = C.WinDLL('user32', use_last_error=True)
k = C.WinDLL('kernel32', use_last_error=True)
PTR = C.c_size_t
u.GetForegroundWindow.restype = W.HWND
u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
u.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
u.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
u.GetAsyncKeyState.argtypes = [C.c_int]
u.GetAsyncKeyState.restype = C.c_short
u.IsWindow.argtypes = [W.HWND]
u.IsWindowVisible.argtypes = [W.HWND]
u.IsIconic.argtypes = [W.HWND]
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.CloseHandle.argtypes = [W.HANDLE]
k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
u.MapVirtualKeyW.argtypes = [W.UINT, W.UINT]
u.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
u.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
u.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]


def dpi_aware():
    try:
        u.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
        u.SetProcessDpiAwarenessContext(C.c_void_p(-4))
    except (AttributeError, OSError):
        u.SetProcessDPIAware()


def process_path(hwnd):
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    handle = k.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ''
    try:
        buf = C.create_unicode_buffer(32768)
        length = W.DWORD(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(handle, 0, buf, C.byref(length)) else ''
    finally:
        k.CloseHandle(handle)


def normalized(path):
    return str(Path(path).resolve()).casefold()


@dataclass
class GameWindow:
    hwnd: int
    path: str

    def pid(self):
        value = W.DWORD()
        u.GetWindowThreadProcessId(self.hwnd, C.byref(value))
        return value.value

    def valid(self):
        return bool(u.IsWindow(self.hwnd)) and normalized(process_path(self.hwnd)) == normalized(self.path)

    def foreground(self):
        return self.valid() and u.GetForegroundWindow() == self.hwnd and not u.IsIconic(self.hwnd)

    def box(self):
        rect = W.RECT()
        if not u.GetClientRect(self.hwnd, C.byref(rect)):
            raise OSError('无法读取游戏窗口大小')
        point = W.POINT(0, 0)
        if not u.ClientToScreen(self.hwnd, C.byref(point)):
            raise OSError('无法定位游戏窗口')
        if rect.right < 320 or rect.bottom < 240:
            raise OSError('游戏窗口太小或已最小化')
        return (point.x, point.y, point.x+rect.right, point.y+rect.bottom)


def _find_windows(matches):
    by_pid={}
    for candidate in matches:
        by_pid.setdefault(candidate.pid(),[]).append(candidate)
    if len(by_pid)!=1:
        return None
    windows=next(iter(by_pid.values()))
    foreground=next((candidate for candidate in windows if candidate.foreground()),None)
    return foreground or windows[0]


def find_game(path):
    matches = []
    expected=normalized(path)
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    @callback_type
    def callback(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            found = process_path(hwnd)
            if found and normalized(found) == expected:
                try:
                    candidate = GameWindow(hwnd, found)
                    candidate.box()
                    matches.append(candidate)
                except OSError:
                    pass
        return True
    u.EnumWindows.argtypes = [callback_type, W.LPARAM]
    u.EnumWindows(callback, 0)
    return _find_windows(matches)


def find_game_by_name(name):
    matches = []
    expected=name.casefold()
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    @callback_type
    def callback(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            found=process_path(hwnd)
            if found and Path(found).name.casefold()==expected:
                try:
                    candidate=GameWindow(hwnd,found)
                    candidate.box()
                    matches.append(candidate)
                except OSError:
                    pass
        return True
    u.EnumWindows.argtypes=[callback_type,W.LPARAM]
    u.EnumWindows(callback,0)
    return _find_windows(matches)


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', PTR)]


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [('uMsg', W.DWORD), ('wParamL', W.WORD), ('wParamH', W.WORD)]


class INPUTUNION(C.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('type', W.DWORD), ('value', INPUTUNION)]


u.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
u.SendInput.restype = W.UINT
MOUSE = {'鼠标左键': (0x2, 0x4, 1), '鼠标右键': (0x8, 0x10, 2),
         '鼠标中键': (0x20, 0x40, 4)}
KEYS = {**{chr(v): v for v in range(65, 91)}, **{str(v): 48+v for v in range(10)},
        'Space': 0x20, 'Shift': 0xa0, 'Ctrl': 0xa2, 'Alt': 0xa4, 'Tab': 9}
CHOICES = list(MOUSE)+list(KEYS)


def is_down(vk):
    return bool(u.GetAsyncKeyState(vk) & 0x8000)


class Hotkeys:
    """Register on the worker thread so short presses cannot be missed by polling."""
    mapping = {1006: (0x75, 'capture'), 1008: (0x77, 'toggle'), 1010: (0x79, 'stop')}

    def __init__(self, mapping=None):
        self.mapping = mapping if mapping is not None else type(self).mapping
        self.registered = []
        try:
            for identity, (vk, _) in self.mapping.items():
                if not u.RegisterHotKey(None, identity, 0x4000, vk):
                    raise OSError(f'热键 F{vk-0x6f} 被占用，请关闭使用该热键的软件后重新启动辅助')
                self.registered.append(identity)
        except Exception:
            self.close()
            raise

    def poll(self):
        msg = W.MSG()
        result = []
        while u.PeekMessageW(C.byref(msg), None, 0x312, 0x312, 1):
            if int(msg.wParam) in self.mapping:
                result.append(self.mapping[int(msg.wParam)][1])
        return result

    def close(self):
        for identity in self.registered:
            u.UnregisterHotKey(None, identity)
        self.registered.clear()


class InputSink:
    def __init__(self):
        self.held = set()
        self.deadlines = {}

    def _event(self, key, down):
        item = INPUT()
        if key in MOUSE:
            item.type = 0
            item.mi.dwFlags = MOUSE[key][0 if down else 1]
        else:
            if key not in KEYS:
                raise ValueError('不支持的按键：'+str(key))
            item.type = 1
            item.ki.wScan = u.MapVirtualKeyW(KEYS[key], 0)
            item.ki.dwFlags = 0x8 | (0 if down else 0x2)
        if u.SendInput(1, C.byref(item), C.sizeof(INPUT)) != 1:
            raise OSError('按键发送失败；请检查辅助与游戏的运行权限是否一致')

    def pump(self,window,cancel=lambda:False):
        now=time.monotonic()
        suspended=cancel() or not window or not window.foreground()
        if suspended:
            self.release_all()
            return
        for key,deadline in list(self.deadlines.items()):
            if now>=deadline:
                self._event(key,False)
                self.held.discard(key)
                self.deadlines.pop(key,None)

    def dispatch(self, action, window, cancel=lambda: False, nonblocking=False):
        kind, key = action
        if kind == 'up':
            if key in self.held:
                self._event(key, False)
                self.held.discard(key)
                self.deadlines.pop(key,None)
            return
        if cancel() or not window.foreground():
            raise InputSuspended('输入前游戏暂不可操作')
        vk = MOUSE[key][2] if key in MOUSE else KEYS.get(key)
        if key not in self.held and vk and is_down(vk):
            raise OSError('请先松开手动操作的辅助按键，再按 F8 继续')
        # Track before injection, so exception recovery still releases a partial input.
        self.held.add(key)
        self._event(key, True)
        if kind == 'tap':
            if nonblocking:
                self.deadlines[key]=time.monotonic()+.055
                return
            try:
                deadline = time.monotonic()+.055
                while time.monotonic() < deadline and not cancel() and window.foreground():
                    time.sleep(.005)
            finally:
                self._event(key, False)
                self.held.discard(key)
                self.deadlines.pop(key,None)

    def release_all(self):
        errors = []
        for key in list(self.held):
            try:
                self._event(key, False)
                self.held.discard(key)
                self.deadlines.pop(key,None)
            except OSError as exc:
                errors.append(str(exc))
        if errors:
            raise OSError('; '.join(errors))

    def release_keys(self,keys):
        for key in keys:
            if key in self.held:
                self._event(key,False)
                self.held.discard(key)
                self.deadlines.pop(key,None)


class InputSuspended(OSError):
    pass
