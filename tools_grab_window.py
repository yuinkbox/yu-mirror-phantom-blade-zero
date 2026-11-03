import ctypes
from ctypes import wintypes
from PIL import ImageGrab

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

hwnd = user32.FindWindowW(None, "Phantom Blade Zero  ")
if not hwnd:
    # fallback: any window whose title contains Phantom Blade Zero
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum(h, _):
        length = user32.GetWindowTextLengthW(h)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(h, buf, length + 1)
            if "Phantom Blade Zero" in buf.value:
                found.append((h, buf.value))
        return True

    user32.EnumWindows(enum, 0)
    print("windows:", found)
    if found:
        hwnd = found[0][0]

print("hwnd:", hwnd)
if hwnd:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    box = (pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom)
    print("box:", box, "size:", rect.right, rect.bottom)
    img = ImageGrab.grab(bbox=box, all_screens=True)
    img.thumbnail((1024, 640))
    img.save(r"C:\dev\pbz-window.png")
    print("saved")
