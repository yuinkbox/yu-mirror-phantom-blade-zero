"""Read-only probe: find how enemies are identified in the live game."""
import json
import struct
import time
from collections import Counter
from pathlib import Path

from runtime import RuntimeReader
from windows_io import find_game

GAME = r'E:\Steam\steamapps\common\PBZ\platform\steam\PBZ.exe'
OUT = Path(__file__).parent / 'evidence' / 'enemy-probe.json'


def main():
    window = find_game(GAME)
    if not window:
        print('game window unavailable')
        return
    r = RuntimeReader(window.pid(), GAME)
    try:
        deadline = time.monotonic() + 40
        r.discover(lambda: time.monotonic() > deadline)
        state = r.sample()
        print('player', hex(state.player or 0), 'valid', state.valid, 'world', hex(state.world or 0))
        ref, m = r.projectiles.ref, r.memory

        def fname(ptr):
            return ref.name(m.read(ptr + 0x18, 8))

        class_counts = Counter()
        faction_tags = Counter()
        faction_classes = Counter()
        samples = []
        actors = 0
        for obj in r.objects.objects(lambda: time.monotonic() > deadline):
            cls = m.u64(obj + 0x10)
            if not cls:
                continue
            cn = fname(cls)
            if not cn.startswith('BP_'):
                continue
            actors += 1
            class_counts[cn] += 1
            f = ref.pointer(obj, 'Faction Component')
            if not f or not r.objects.valid_object(f):
                continue
            if m.u64(f + 0xa8) != obj:
                continue
            tag = ref.name(m.read(f + 0x134, 8))
            faction_tags[tag or '<empty>'] += 1
            faction_classes[fname(m.u64(f + 0x10))] += 1
            if tag == 'Faction.Enemy' and len(samples) < 12:
                samples.append(dict(actor=hex(obj), actor_class=cn, actor_name=fname(obj),
                                    component=hex(f), component_class=fname(m.u64(f + 0x10))))
        data = dict(pid=window.pid(), actors=actors,
                    classes=class_counts.most_common(40),
                    faction_tags=faction_tags.most_common(),
                    faction_component_classes=faction_classes.most_common(),
                    enemy_samples=samples)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps({k: v for k, v in data.items() if k != 'classes'}, ensure_ascii=False, indent=1)[:2500])
        print('top classes:', class_counts.most_common(15))
    finally:
        r.close()


if __name__ == '__main__':
    main()
