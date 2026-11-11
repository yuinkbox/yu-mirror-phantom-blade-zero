"""Physical hold gesture; never sends game inputs or runs combat on the desktop."""

COMBO_KEYS = {
    'mouse4': ('鼠标侧键 1', 0x05),
    'mouse5': ('鼠标侧键 2', 0x06),
    'f6': ('F6', 0x75),
    'f7': ('F7', 0x76),
}
DEFAULT_COMBO_KEY = 'mouse4'


def combo_key(value):
    return value if isinstance(value, str) and value in COMBO_KEYS else DEFAULT_COMBO_KEY


class HoldGesture:
    """Require a fresh press after start, changing keys, or leaving the game."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.armed = False
        self.key = None

    def sample(self, *, enabled, focused, key, down):
        key = combo_key(key)
        if key != self.key:
            self.armed = False
            self.key = key
        if not enabled or not focused:
            self.armed = False
            return False
        if not down:
            self.armed = True
            return False
        return self.armed
