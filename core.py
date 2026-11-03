"""Image sensors and deterministic combat scheduling, independent of Windows."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import numpy as np
from PIL import Image


@dataclass
class Config:
    game_exe: str = r'E:\Steam\steamapps\common\PBZ\platform\steam\PBZ.exe'
    heal_key: str = 'R'
    guard_key: str = '鼠标右键'
    attack_key: str = '鼠标左键'
    heal_enabled: bool = True
    guard_enabled: bool = False
    attack_enabled: bool = False
    threshold: float = .40
    attack_period: float = 1.6
    attack_lock: float = .65
    heal_lock: float = 1.8
    heal_timeout: float = 4.0
    hp_rect: list | None = None
    anchor_rect: list | None = None
    color: dict | None = None
    calibration_aspect: float = 0

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text('utf-8'))
        obj = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        obj.validate()
        return obj

    def validate(self):
        if not .05 <= float(self.threshold) <= .9:
            raise ValueError('喝药阈值应为 5%–90%')
        if not .8 <= float(self.attack_period) <= 10:
            raise ValueError('攻击间隔应为 0.8–10 秒')
        if not .15 <= float(self.attack_lock) < float(self.attack_period):
            raise ValueError('攻击后等待应小于攻击间隔，且不少于 0.15 秒')
        if self.heal_key in (self.guard_key, self.attack_key) or self.guard_key == self.attack_key:
            raise ValueError('喝药、格挡、攻击需要使用不同按键')
        for rect in (self.hp_rect, self.anchor_rect):
            if rect is not None and (len(rect) != 4 or not all(0 <= float(v) <= 1 for v in rect)
                                     or rect[0] >= rect[2] or rect[1] >= rect[3]):
                raise ValueError('校准区域无效，请重新框选')

    def save(self, path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), 'utf-8')
        temp.replace(path)


def crop_rect(image, rect):
    w, h = image.size
    box = [round(rect[0]*w), round(rect[1]*h), round(rect[2]*w), round(rect[3]*h)]
    if box[2]-box[0] < 3 or box[3]-box[1] < 2:
        raise ValueError('框选区域太小')
    return image.crop(box)


def fit_color(bar):
    """Learn the fill from a full-health stripe, excluding dark/neutral backgrounds."""
    hsv = np.asarray(bar.convert('HSV'), dtype=float)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bright = v > 75
    chromatic = bright & (s > 65)
    if chromatic.mean() > .22:
        bins = np.floor(h[chromatic]/8).astype(int)
        hist = np.bincount(bins, minlength=32)
        peak = int(hist.argmax())*8+4
        distance = np.abs((h-peak+128) % 256-128)
        chosen = chromatic & (distance < 20)
        if chosen.mean() < .35:
            raise ValueError('血条颜色不够连续，请在满血时只框选血条内部的细横条')
        radians = h[chosen]*2*np.pi/256
        hue = float(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())*256/(2*np.pi) % 256)
        profile = dict(mode='color', hue=hue, sat_min=max(45., float(np.median(s[chosen]))*.4),
                       val_min=max(40., float(np.median(v[chosen]))*.28))
    else:
        chosen = bright & (s < 70)
        if chosen.mean() < .65:
            raise ValueError('无法识别填充色，请在满血且血条无遮挡时重新框选')
        profile = dict(mode='neutral', val_min=max(145., float(np.median(v[chosen]))*.78), sat_max=75.)
    ratio = read_health(bar, profile)
    if ratio is None or ratio < .88:
        raise ValueError('校准时血条应接近满血；请把区域精确框到整条血量填充槽')
    return profile


def read_health(bar, profile):
    hsv = np.asarray(bar.convert('HSV'), dtype=float)
    if hsv.ndim != 3 or hsv.shape[1] < 12:
        return None
    if profile['mode'] == 'color':
        d = np.abs((hsv[..., 0]-profile['hue']+128) % 256-128)
        pixels = (d < 19) & (hsv[..., 1] >= profile['sat_min']) & (hsv[..., 2] >= profile['val_min'])
    else:
        pixels = (hsv[..., 1] <= profile['sat_max']) & (hsv[..., 2] >= profile['val_min'])
    columns = pixels.mean(axis=0) >= .3
    width = len(columns)
    # Small specular gaps are tolerated; isolated particles beyond the fill are ignored.
    max_gap = max(2, round(width*.018))
    start = next((i for i, x in enumerate(columns[:max(3, round(width*.06))]) if x), None)
    if start is None:
        return 0.0
    last = start
    gap = 0
    for i in range(start, width):
        if columns[i]:
            gap = 0
            last = i
        else:
            gap += 1
            if gap > max_gap:
                break
    return float((last+1)/width)


def anchor_score(image, reference):
    a = np.asarray(image.convert('RGB').resize((96, 40)), dtype=float)
    b = np.asarray(reference.convert('RGB').resize((96, 40)), dtype=float)
    if b.std() < 5:
        return 0.0
    return max(0., 1-float(np.abs(a-b).mean())/75.)


@dataclass
class Observation:
    now: float
    foreground: bool
    hud: bool
    hp: float | None


class Scheduler:
    """All decisions are serialized. No worker or sensor can issue input itself."""
    def __init__(self, config):
        self.config = config
        self.armed = False
        self.guard_down = False
        self.low_frames = 0
        self.low_since = None
        self.pending_heal = None
        self.lock_until = 0.
        self.next_attack = 0.
        self.last_heal = -100.
        self.status = '已暂停'
        self.last_now = -1.

    def pause(self, reason='已暂停'):
        actions = [('up', self.config.guard_key)] if self.guard_down else []
        self.armed = False
        self.guard_down = False
        self.pending_heal = None
        self.low_frames = 0
        self.low_since = None
        self.status = reason
        return actions

    def arm(self, now):
        self.armed = True
        self.low_frames = 0
        self.low_since = None
        self.pending_heal = None
        self.next_attack = now+.8
        self.lock_until = now+.35
        self.last_now = now
        self.status = '运行中'

    def tick(self, obs):
        cfg = self.config
        if not self.armed:
            return []
        if obs.now < self.last_now or obs.now-self.last_now > .8:
            return self.pause('采样中断，已暂停；回到游戏后按 F8')
        self.last_now = obs.now
        if not obs.foreground:
            return self.pause('游戏失去焦点，已暂停')
        if not obs.hud or obs.hp is None or not np.isfinite(obs.hp) or not 0 <= obs.hp <= 1:
            return self.pause('血条或界面标识不可见，已暂停')
        if obs.hp < .015:
            return self.pause('血量为零或无法确认，已暂停')
        actions = []
        if self.pending_heal:
            at, before = self.pending_heal
            if obs.hp >= before+.06 or obs.hp >= .96:
                self.pending_heal = None
                self.status = '已检测到回血'
            elif obs.now-at >= cfg.heal_timeout:
                return self.pause('喝药后未检测到回血：请检查药槽、药量和按键')
        if obs.hp < cfg.threshold:
            self.low_frames += 1
            if self.low_since is None:
                self.low_since = obs.now
        else:
            self.low_frames = 0
            self.low_since = None
        if (cfg.heal_enabled and not self.pending_heal and self.low_frames >= 3
                and self.low_since is not None and obs.now-self.low_since >= .18
                and obs.now-self.last_heal > 4.5 and obs.now >= self.lock_until):
            if self.guard_down:
                actions.append(('up', cfg.guard_key))
                self.guard_down = False
            actions.append(('tap', cfg.heal_key))
            self.pending_heal = (obs.now, obs.hp)
            self.last_heal = obs.now
            self.lock_until = obs.now+cfg.heal_lock
            self.next_attack = obs.now+cfg.heal_timeout+.25
            self.status = '已发送喝药，等待回血'
            return actions
        if obs.now < self.lock_until:
            return actions
        if self.pending_heal:
            self.status = '等待回血确认'
            return actions
        if cfg.attack_enabled and obs.now >= self.next_attack:
            if self.guard_down:
                actions.append(('up', cfg.guard_key))
                self.guard_down = False
            actions.append(('tap', cfg.attack_key))
            self.lock_until = obs.now+cfg.attack_lock
            self.next_attack = obs.now+cfg.attack_period
            self.status = '轻攻击后等待'
            return actions
        if cfg.guard_enabled and not self.guard_down:
            actions.append(('down', cfg.guard_key))
            self.guard_down = True
            self.status = '基础格挡'
        else:
            self.status = '基础格挡' if self.guard_down else '监测血量'
        return actions
