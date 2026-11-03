"""Focus the PBZ window and send one virtual key (default ESC)."""
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
                found.append(hwnd)
        return True

    user32.EnumWindows(enum, 0)
    return found


def main() -> int:
    vk = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x1B
    windows = [hwnd for hwnd in find_window() if user32.IsWindowVisible(hwnd)]
    if not windows:
        print("no game window")
        return 1
    hwnd = windows[0]
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    print("foreground:", user32.GetForegroundWindow() == hwnd)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(vk, 0, 2, 0)
    print(f"sent vk=0x{vk:02X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
