"""Behavior tests: health sensing, scene gating and conflicting combat actions."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from core import Config, Scheduler, Observation, fit_color, read_health, anchor_score


def bar(ratio, fill=(185, 24, 32), width=240):
    pixels = np.full((10, width, 3), 22, dtype=np.uint8)
    pixels[:, :round(width*ratio)] = fill
    return Image.fromarray(pixels)


class SensorTests(unittest.TestCase):
    def test_health_levels_and_empty_bar(self):
        profile = fit_color(bar(1))
        for ratio in (1., .73, .4, .15, 0):
            self.assertAlmostEqual(read_health(bar(ratio), profile), ratio, delta=.01)

    def test_warm_and_neutral_fill_colors(self):
        for color in ((210, 130, 35), (60, 170, 70), (225, 225, 225)):
            profile = fit_color(bar(1, color))
            self.assertAlmostEqual(read_health(bar(.35, color), profile), .35, delta=.01)

    def test_noise_beyond_fill_does_not_inflate_health(self):
        profile = fit_color(bar(1))
        pixels = np.asarray(bar(.25)).copy()
        pixels[:, 190:195] = (185, 24, 32)
        pixels[:, 25:27] = 22
        self.assertAlmostEqual(read_health(Image.fromarray(pixels), profile), .25, delta=.015)

    def test_low_health_cannot_be_used_as_full_calibration(self):
        with self.assertRaises(ValueError):
            fit_color(bar(.35))

    def test_anchor_accepts_same_hud_rejects_black_scene(self):
        rng = np.random.default_rng(3)
        image = Image.fromarray(rng.integers(40, 220, (40, 96, 3), dtype=np.uint8))
        self.assertAlmostEqual(anchor_score(image, image), 1.)
        self.assertLess(anchor_score(Image.new('RGB', image.size), image), .68)
        self.assertEqual(anchor_score(image, Image.new('RGB', image.size)), 0)


class SchedulerTests(unittest.TestCase):
    def create(self, **settings):
        cfg = Config(**settings)
        scheduler = Scheduler(cfg)
        scheduler.arm(10.)
        return scheduler

    def tick(self, s, now, hp=.8, hud=True, fg=True):
        return s.tick(Observation(now, fg, hud, hp))

    def test_one_heal_waits_for_confirmation_and_then_resumes(self):
        s = self.create()
        outputs = []
        for i in range(12):
            outputs += self.tick(s, 10+i*.1, .25)
        self.assertEqual(outputs, [('tap', 'R')])
        self.assertIsNotNone(s.pending_heal)
        self.tick(s, 11.2, .75)
        self.assertIsNone(s.pending_heal)
        self.assertTrue(s.armed)

    def test_no_response_stops_instead_of_drinking_repeatedly(self):
        s = self.create()
        outputs = []
        for i in range(55):
            outputs += self.tick(s, 10+i*.1, .2)
        self.assertEqual(outputs.count(('tap', 'R')), 1)
        self.assertFalse(s.armed)
        self.assertIn('未检测到回血', s.status)

    def test_single_frame_low_health_does_not_heal(self):
        s = self.create()
        self.assertEqual(self.tick(s, 10.4, .2), [])
        self.assertEqual(self.tick(s, 10.5, .8), [])
        self.assertIsNone(s.pending_heal)

    def test_healing_releases_guard_and_blocks_attack(self):
        s = self.create(guard_enabled=True, attack_enabled=True)
        self.assertEqual(self.tick(s, 10.4), [('down', '鼠标右键')])
        outputs = []
        for i in range(5):
            outputs += self.tick(s, 10.5+i*.1, .2)
        self.assertEqual(outputs, [('up', '鼠标右键'), ('tap', 'R')])
        for i in range(1, 10):
            self.assertEqual(self.tick(s, 10.9+i*.1, .2), [])

    def test_attack_releases_guard_and_waits_before_reholding(self):
        s = self.create(guard_enabled=True, attack_enabled=True)
        self.tick(s, 10.4)
        self.assertEqual(self.tick(s, 10.85), [('up', '鼠标右键'), ('tap', '鼠标左键')])
        self.assertEqual(self.tick(s, 11.0), [])
        self.assertEqual(self.tick(s, 11.55), [('down', '鼠标右键')])

    def test_loss_of_focus_releases_guard_and_never_auto_resumes(self):
        s = self.create(guard_enabled=True)
        self.tick(s, 10.4)
        self.assertEqual(self.tick(s, 10.5, fg=False), [('up', '鼠标右键')])
        self.assertEqual(self.tick(s, 10.6, fg=True), [])
        self.assertFalse(s.armed)

    def test_menu_death_invalid_sensor_and_stale_frames_stop(self):
        for kwargs in ({'hud': False}, {'hp': 0}, {'hp': None}, {'hp': float('nan')}, {'hp': 1.5}):
            s = self.create(guard_enabled=True)
            self.tick(s, 10.4)
            self.assertEqual(self.tick(s, 10.5, **kwargs), [('up', '鼠标右键')])
            self.assertFalse(s.armed)
        s = self.create(guard_enabled=True)
        self.tick(s, 10.4)
        self.assertEqual(self.tick(s, 11.5), [('up', '鼠标右键')])
        self.assertFalse(s.armed)

    def test_slow_low_samples_cannot_count_as_continuous_detection(self):
        s = self.create()
        self.tick(s, 10.3, .2)
        self.tick(s, 11.2, .2)
        self.assertFalse(s.armed)


class ConfigurationTests(unittest.TestCase):
    def test_settings_roundtrip_and_no_calibration_required_to_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            config = Config(threshold=.35, heal_key='T')
            config.save(path)
            self.assertEqual(Config.load(path), config)

    def test_invalid_timing_and_duplicate_actions_are_rejected(self):
        for config in (Config(threshold=1), Config(attack_lock=2), Config(heal_key='鼠标右键'),
                       Config(hp_rect=[.2, .1, .1, .2])):
            with self.assertRaises(ValueError):
                config.validate()


if __name__ == '__main__':
    unittest.main(verbosity=2)
