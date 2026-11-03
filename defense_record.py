"""Bounded, local flight recording during ordinary play. Never drives input."""
from collections import deque
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class QuietHandler(RotatingFileHandler):
    def handleError(self, record):
        raise OSError('战斗记录暂不可写')


class DefenseRecorder:
    def __init__(self, path, write=None):
        self.path = Path(path)
        self.writer = write
        self.handler = None
        self.disabled = False
        self.history = deque(maxlen=64)
        self.seen = {}
        self.identity = None
        self.until = -1
        self.last_sample = -100
        self.sequence = 0
        self.last_written = -1

    def emit(self, value):
        if self.disabled:
            return
        try:
            if self.writer is not None:
                self.writer(value)
            else:
                if self.handler is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.handler = QuietHandler(self.path, maxBytes=2_000_000, backupCount=2,
                                                encoding='utf-8', delay=True)
                    self.handler.setFormatter(logging.Formatter('%(message)s'))
                message = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                self.handler.handle(logging.LogRecord('pbz.defense', logging.INFO, '', 0,
                                                      message, (), None))
        except (OSError, ValueError, TypeError):
            # Recording failure must not interrupt defense or hold an input down.
            self.disabled = True
            logging.warning('战斗记录暂不可用，辅助功能继续运行')

    @staticmethod
    def snapshot(state):
        diagnostics = getattr(state, 'diagnostics', {})
        return {'player': state.player, 'world': state.world, 'game_time': state.game_time,
                'hp': state.hp, 'hp_max': state.hp_max, 'medicine': state.medicine,
                'frames': [dict(t) for t in diagnostics.get('defense_frames', state.threats)],
                'projectiles':diagnostics.get('projectiles',[]),
                'native_result':diagnostics.get('native_defense_result',{}),
                'runtime_result': diagnostics.get('dodge_result', {})}

    def flush_history(self, now):
        for row in self.history:
            if row['sequence'] > self.last_written and now-row['monotonic'] <= .65:
                self.emit(row)
                self.last_written = row['sequence']

    def observe(self, now, state, armed):
        if self.disabled:
            return
        if not state.valid:
            self.history.clear()
            self.identity = None
            return
        identity = (state.player, state.world)
        if identity != self.identity:
            self.history.clear()
            self.seen.clear()
            self.until = -1
            self.last_sample = -100
            self.identity = identity
        snapshot = self.snapshot(state)
        self.seen = {k: at for k, at in self.seen.items() if now-at < 5}
        colored = [t for t in snapshot['frames']+snapshot['projectiles'] if t.get('attack_type') in ('blue', 'red','projectile')
                   and not t.get('hit', False) and not t.get('paused', False)]
        fresh = []
        for threat in colored:
            key = (threat['id'], threat.get('source', 0), 0 if threat.get('projectile') else threat['hit_time'])
            if key not in self.seen:
                fresh.append(threat)
                self.seen[key] = now
        if fresh:
            self.until = max(self.until, now+1.2)
            self.flush_history(now)
            self.emit({'event': 'warning', 'monotonic': now, 'armed': armed,
                       'state': snapshot, 'new_frames': fresh})
        if now-self.last_sample >= .019:
            self.sequence += 1
            row = {'event': 'sample', 'sequence': self.sequence, 'monotonic': now,
                   'armed': armed, 'state': snapshot}
            self.history.append(row)
            self.last_sample = now
            if now <= self.until:
                self.emit(row)
                self.last_written = row['sequence']

    def input(self, now, state, action, duration, sample_age, lead):
        if self.disabled:
            return
        self.flush_history(now)
        self.until = max(self.until, now+1.2)
        self.emit({'event': 'input', 'monotonic': now, 'action': list(action),
                   'duration_ms': round(duration*1000, 3),
                   'sample_age_ms': round(sample_age*1000, 3),
                   'configured_colored_lead_ms': round(lead*1000, 3),
                   'state': self.snapshot(state),
                   'perfect_outcome': 'unconfirmed'})

    def close(self):
        if self.handler is not None:
            self.handler.close()
