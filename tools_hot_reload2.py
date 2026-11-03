"""Focus the PBZ window and send Ctrl+R with explicit scan codes (UE4SS reload)."""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]


def send(vk, scan, up=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan, flags, 0, None))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def find_window(title_part="Phantom Blade Zero"):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_part in buf.value and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(enum, 0)
    return found


def main() -> int:
    windows = find_window()
    print("windows:", windows)
    if not windows:
        return 1
    hwnd = windows[0]
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    print("foreground:", user32.GetForegroundWindow() == hwnd)
    # LEFT CTRL scancode 0x1D, R scancode 0x13
    send(0x11, 0x1D)
    time.sleep(0.15)
    send(0x52, 0x13)
    time.sleep(0.18)
    send(0x52, 0x13, up=True)
    time.sleep(0.10)
    send(0x11, 0x1D, up=True)
    print("sent Ctrl+R (scan codes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
