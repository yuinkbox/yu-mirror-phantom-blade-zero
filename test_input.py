import unittest
from unittest.mock import patch
from windows_io import InputSink, Hotkeys


class Window:
    def __init__(self, foreground=True):
        self.focus = foreground

    def foreground(self):
        return self.focus


class InputTests(unittest.TestCase):
    def setUp(self):
        self.sink = InputSink()
        self.events = []
        self.event_patch = patch.object(self.sink, '_event', lambda key, down: self.events.append((key, down)))
        self.keys_patch = patch('windows_io.is_down', return_value=False)
        self.event_patch.start()
        self.keys_patch.start()
        self.addCleanup(self.event_patch.stop)
        self.addCleanup(self.keys_patch.stop)

    def test_background_window_never_receives_down(self):
        with self.assertRaises(OSError):
            self.sink.dispatch(('down', 'R'), Window(False))
        self.assertEqual(self.events, [])

    def test_stop_cancel_prevents_input(self):
        with self.assertRaises(OSError):
            self.sink.dispatch(('tap', 'R'), Window(), lambda: True)
        self.assertEqual(self.events, [])

    def test_release_is_allowed_after_focus_is_lost(self):
        self.sink.dispatch(('down', '鼠标右键'), Window())
        self.sink.dispatch(('up', '鼠标右键'), Window(False))
        self.assertEqual(self.events, [('鼠标右键', True), ('鼠标右键', False)])
        self.assertFalse(self.sink.held)

    def test_tap_releases_if_focus_changes_during_down(self):
        window = Window()
        def record(key, down):
            self.events.append((key, down))
            if down:
                window.focus = False
        with patch.object(self.sink, '_event', record):
            self.sink.dispatch(('tap', 'R'), window)
        self.assertEqual(self.events, [('R', True), ('R', False)])
        self.assertFalse(self.sink.held)

    def test_manual_held_action_is_not_overwritten(self):
        with patch('windows_io.is_down', return_value=True):
            with self.assertRaises(OSError):
                self.sink.dispatch(('down', '鼠标右键'), Window())
        self.assertEqual(self.events, [])

    def test_partial_injection_failure_remains_tracked_for_release(self):
        def fail(key, down):
            if down:
                raise OSError('synthetic injection failure')
            self.events.append((key, down))
        with patch.object(self.sink, '_event', fail):
            with self.assertRaises(OSError):
                self.sink.dispatch(('down', 'R'), Window())
            self.assertIn('R', self.sink.held)
            self.sink.release_all()
        self.assertEqual(self.events, [('R', False)])
        self.assertFalse(self.sink.held)


if __name__ == '__main__':
    unittest.main(verbosity=2)
