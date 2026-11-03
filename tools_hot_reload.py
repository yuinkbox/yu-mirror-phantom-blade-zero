"""Focus the Phantom Blade Zero window and send Ctrl+R (UE4SS hot reload)."""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32


def find_window(title_part="Phantom Blade Zero"):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_part in buf.value:
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(enum, 0)
    return found


def main():
    windows = find_window()
    print("windows:", windows)
    if not windows:
        return 1
    shown = []
    for hwnd, title in windows:
        if user32.IsWindowVisible(hwnd):
            shown.append((hwnd, title))
    if not shown:
        return 1
    hwnd, title = shown[0]
    print("focusing:", hwnd, repr(title))
    user32.ShowWindow(hwnd, 9)
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception as exc:  # pragma: no cover - best effort
        print("SetForegroundWindow failed:", exc)
    time.sleep(0.6)
    fore = user32.GetForegroundWindow()
    print("foreground is target:", fore == hwnd, fore)

    vk_control, vk_r = 0x11, 0x52
    user32.keybd_event(vk_control, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk_r, 0, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(vk_r, 0, 2, 0)
    time.sleep(0.03)
    user32.keybd_event(vk_control, 0, 2, 0)
    print("sent Ctrl+R")
    return 0


if __name__ == "__main__":
    sys.exit(main())
