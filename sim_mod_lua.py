"""Offline simulator for PBZSmartCombat's main.lua (UE4SS Lua mod).

Runs the real mod source against stubbed UE4SS/UEHelpers APIs so combat and
defense decisions can be checked without launching the game.  Development-only.
"""
from __future__ import annotations

import argparse
import math
import shutil
import time
from pathlib import Path

import lupa

ROOT = Path(__file__).resolve().parent
MOD_SOURCE = ROOT / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat' / 'Scripts' / 'main.lua'
WEAPON_SWITCH_SOURCE = ROOT / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat' / 'Scripts' / 'weapon_switch.lua'

# combo_open/combo_close of the shipped chain moves, mirroring MOVE_WINDOWS in
# main.lua.  The windowed game model below uses them to accept or drop an input.
MOVE_WINDOWS = {
    'A': (0.37, 0.77), 'AA': (0.62, 1.04), 'AAA': (0.72, 1.06),
    'AAAA': (0.63, 0.93), 'AAAAA': (0.66, 0.75),
    'B': (1.00, 1.45), 'BB': (1.04, 1.43), 'BBB': (0.74, 0.88),
    'AB': (1.37, 1.88), 'ABB': (1.19, 1.31), 'AAB': (1.43, 1.85),
    'AABB': (1.77, 1.95), 'AAAB': (1.31, 1.81), 'AAABB': (0.92, 1.07),
}
MOVE_TAGS = {
    'A': 'Ability.Action.Hun.Light.A', 'AA': 'Ability.Action.Hun.Light.AA',
    'AAA': 'Ability.Action.Hun.Light.AAA', 'AAAA': 'Ability.Action.Hun.Light.AAAA',
    'AAAAA': 'Ability.Action.Hun.Light.AAAAA',
    'B': 'Ability.Action.Hun.Heavy.Atk01', 'BB': 'Ability.Action.Hun.Heavy.Atk02',
    'BBB': 'Ability.Action.Hun.Heavy.Atk03', 'AB': 'Ability.Action.Hun.Heavy.ComboAtk_N1H1',
    'ABB': 'Ability.Action.Hun.Heavy.ComboAtk_N1H2', 'AAB': 'Ability.Action.Hun.Heavy.ComboAtk_N2H1',
    'AABB': 'Ability.Action.Hun.Heavy.ComboAtk_N2H2', 'AAAB': 'Ability.Action.Hun.Heavy.ComboAtk_N3H1',
    'AAABB': 'Ability.Action.Hun.Heavy.ComboAtk_N3H2',
}


class WindowedFight:
    """A game model that only chains an attack input inside the move's window.

    This is what the shipped Build does: combo_open/combo_close decide whether
    the next key is accepted, and the running move owns its Ability.Action.Hun.*
    tag.  The mod must therefore press inside the window and confirm the move.
    """

    def __init__(self, sim, actor=None, cost=82.0, publish_tags=True, publish_phase=False):
        self.sim = sim
        self.actor = actor if actor is not None else sim.actor
        self.cost = cost
        self.publish_tags = publish_tags
        self.publish_phase = publish_phase
        self.path = ''
        self.move_start = 0.0
        self.accepted = 0
        self.rejected = 0
        self.completed = 0
        self.presses = []
        self.spent = 0.0
        # Standing around with no move running is the "penalty stance" the
        # engine must avoid: track how long the character is idle.
        self.idle_seconds = 0.0
        self.longest_idle = 0.0
        self._idle_since = None
        self._last_ms = None
        if self.actor is not None:
            self.actor.AbilitySystemComponent = AbilitySystemStub(tags=[])

    # -- helpers -------------------------------------------------------
    def _window(self):
        return MOVE_WINDOWS.get(self.path)

    def _duration(self):
        window = self._window()
        return (window[1] + 0.12) if window else 0.6

    def _set_tag(self, now=None):
        if self.actor is None:
            return
        tags = self.actor.AbilitySystemComponent.tags
        tags.clear()
        if self.publish_tags:
            tag = MOVE_TAGS.get(self.path)
            if tag:
                tags.add(tag)
        if self.publish_phase and now is not None:
            window = self._window()
            if window and window[0] <= now - self.move_start <= window[1]:
                tags.add('CharacterState.Action.Phase.Combo')

    def _pay(self, key):
        if key != 'B' or self.actor is None:
            return
        values = self.actor.attributes._values
        value = values.get('Shachi')
        if value is not None and self.cost:
            values['Shachi'] = max(0.0, value - self.cost)
            self.spent += self.cost

    def press(self, key, now):
        """Handle one attack input; returns True when the game chained it."""
        if key not in ('A', 'B'):
            return False
        self.presses.append((key, now, self.path))
        if not self.path:
            if self.actor is not None:
                values = self.actor.attributes._values
                if key == 'B' and (values.get('Shachi') or 0) < self.cost:
                    self.rejected += 1
                    return False
            self.path = key
            self.move_start = now
            self.accepted += 1
            self._pay(key)
            self._set_tag(now)
            return True
        window = self._window()
        elapsed = now - self.move_start
        if window and not (window[0] <= elapsed <= window[1]):
            self.rejected += 1
            return False
        candidate = self.path + key
        if candidate not in MOVE_WINDOWS:
            self.rejected += 1
            return False
        if self.actor is not None:
            values = self.actor.attributes._values
            if key == 'B' and (values.get('Shachi') or 0) < self.cost:
                self.rejected += 1
                return False
        self.path = candidate
        self.move_start = now
        self.accepted += 1
        self._pay(key)
        self._set_tag(now)
        return True

    def tick(self, now):
        if self.path and now - self.move_start > self._duration():
            if len(self.path) >= 2:
                self.completed += 1
            self.path = ''
        if self.accepted:
            if self.path:
                if self._idle_since is not None:
                    self.longest_idle = max(self.longest_idle, now - self._idle_since)
                    self._idle_since = None
            else:
                if self._idle_since is None:
                    self._idle_since = now
                if self._last_ms is not None:
                    self.idle_seconds += now - self._last_ms
        self._last_ms = now
        self._set_tag(now)



class FName:
    def __init__(self, name: str):
        self._name = name

    def ToString(self) -> str:
        return self._name


class ClassObject:
    def __init__(self, name: str):
        self._name = name

    def IsValid(self) -> bool:
        return True

    def GetFName(self) -> FName:
        return FName(self._name)

    def GetFullName(self) -> str:
        return self._name


class Tag:
    def __init__(self, name: str):
        self._name = name
        self.TagName = FName(name)

    def ToString(self) -> str:
        return self._name


class TagNameOnly:
    def __init__(self, name: str):
        self.TagName = FName(name)


class FactionComponent:
    def __init__(self, owner, faction: str, tag_name_only: bool = False):
        self._owner = owner
        self.Faction = TagNameOnly(faction) if tag_name_only else Tag(faction)

    def IsValid(self) -> bool:
        return True

    def GetOwner(self):
        return self._owner


class AttrValue:
    def __init__(self, value):
        self.CurrentValue = value


class AttributeSet:
    def __init__(self, owner, values: dict):
        self._owner = owner
        self._values = dict(values)

    def IsValid(self) -> bool:
        return True

    def GetOuter(self):
        return self._owner

    def __getitem__(self, name: str):
        name = str(name)
        if name in self._values:
            return AttrValue(self._values[name])
        return getattr(self, name, None)


class AbilityObject:
    """Stands in for a GA_* class default object (Default__GA_Hun_Tang_A_C)."""

    def __init__(self, chain: str):
        self._chain = chain
        self._name = f'Default__GA_Hun_Tang_{chain}_C'

    def IsValid(self) -> bool:
        return True

    def GetFName(self) -> FName:
        return FName(self._name)

    def GetFullName(self) -> str:
        return self._name


class AbilitySpec:
    """FGameplayAbilitySpec: the mod reads ActiveCount to see the running move."""

    def __init__(self, chain: str):
        self.chain = chain
        self.Ability = AbilityObject(chain)
        self.ActiveCount = 0.0
        self.InputID = -1.0


class AbilityItems:
    def __init__(self, specs):
        self._specs = list(specs)

    def GetArrayNum(self) -> int:
        return len(self._specs)

    def Get(self, index: int):
        return self._specs[index]


class AbilityItemsForEachOnly:
    def __init__(self, specs):
        self._specs = list(specs)

    def GetArrayNum(self):
        return len(self._specs)

    def ForEach(self, callback):
        for index, spec in enumerate(self._specs, 1):
            if callback(index, FrameValue(spec)):
                break


class AbilitySystemStub:
    def __init__(self, specs=None, tags=None):
        specs = specs or []
        self.ActivatableAbilities = AbilityItems(specs)
        self.tags = set(tags or [])

    def IsValid(self) -> bool:
        return True

    def HasMatchingGameplayTag(self, tag):
        return tag['TagName'].ToString() in self.tags


class WeaponComponent:
    def __init__(self, owner):
        self.owner = owner
        self.index = 0
        self.cooldown = 0.0
        self.count = 2

    def IsValid(self) -> bool:
        return True

    def GetOuter(self):
        return self.owner

    def GetSlotWeapons(self, _slot):
        return self.count

    def GetCurrentChangeWeaponCooldown(self, _slot):
        return self.cooldown

    def GetCurrentWeaponIndex(self, _slot, _include_subslot):
        return self.index

    def IsInWeaponTransition(self):
        return False


class AbilityInputStub:
    def __init__(self, owner, sim):
        self.owner = owner
        self.sim = sim

    def IsValid(self) -> bool:
        return True

    def GetOuter(self):
        return self.owner

    def TryActivateExternalActionAbilityInterruptLowerByTag(self, tag, action, priority):
        self.sim.switch_calls.append((tag['TagName'].ToString(), action.GetFName().ToString(), priority))
        return True


class Actor:
    next_id = 1000

    def __init__(self, name: str, class_name: str, position=(0.0, 0.0, 0.0)):
        Actor.next_id += 1
        self._id = Actor.next_id
        self._name = name
        self._class = class_name
        self._position = position
        self._rotation_yaw = 0.0
        self._valid = True
        self.PlayerState = None
        self.CurrentWeapon = None
        self.AbilitySystemComponent = None
        self._lua = None
        self.attributes: AttributeSet | None = None

    def IsValid(self) -> bool:
        return self._valid

    def GetAddress(self) -> int:
        return self._id

    def GetFName(self) -> FName:
        return FName(self._name)

    def GetClass(self) -> ClassObject:
        return ClassObject(self._class)

    def K2_GetActorLocation(self):
        x, y, z = self._position
        if self._lua is not None:
            return self._lua.table_from({'X': x, 'Y': y, 'Z': z})
        return {'X': x, 'Y': y, 'Z': z}

    def K2_GetActorRotation(self):
        rotation = {'Yaw': self._rotation_yaw}
        return self._lua.table_from(rotation) if self._lua is not None else rotation

    def GetOuter(self):
        return None

    def GetFullName(self) -> str:
        return f'{self._class} {self._name}'


class BulletSource:
    """Stands in for the object that produced a deflect frame (arrow, grenade...)."""

    def __init__(self, class_name: str, attack_type=None):
        self._class = class_name
        if attack_type is not None:
            # mirrors the DeflectType / AttackType field the mod reads
            self.AttackType = attack_type

    def IsValid(self) -> bool:
        return True

    def GetClass(self) -> ClassObject:
        return ClassObject(self._class)

    def GetFName(self) -> FName:
        return FName(self._class)

    def __getitem__(self, key):
        return getattr(self, str(key), None)


class Frame:
    def __init__(self, hit: float, is_dodge=False, is_hit=False, source=None):
        self.HitGameTime = hit
        self.IsDodge = is_dodge
        self.IsHit = is_hit
        if source is not None:
            self.Bullet = source

    def __getitem__(self, key):
        # UE4SS structs are read by field name; mirror that for the simulator.
        return getattr(self, str(key), None)


class FrameValue:
    def __init__(self, frame):
        self._frame = frame

    def get(self):
        return self._frame


class HandleValue:
    def __init__(self, handle):
        self._handle = handle

    def get(self):
        return self._handle


class HookContext:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class TagValue:
    def __init__(self, name):
        self._name = name

    def get(self):
        return type('GameplayTagStub', (), {'TagName': FName(self._name)})()


class FrameMap:
    def __init__(self):
        self.frames: dict[int, Frame] = {}

    def ForEach(self, fn):
        for handle, frame in list(self.frames.items()):
            fn(HandleValue(handle), FrameValue(frame))


class DeflectComponent:
    next_id = 5000

    def __init__(self, owner):
        DeflectComponent.next_id += 1
        self._id = DeflectComponent.next_id
        self._owner = owner
        self.ReceiveFrames = FrameMap()

    def IsValid(self) -> bool:
        return True

    def GetOwner(self):
        return self._owner

    def GetAddress(self) -> int:
        return self._id


class TargetingComponent:
    def __init__(self, owner, target=None):
        self.owner = owner
        self.target = target
        self.requests = 0

    def IsValid(self):
        return True

    def GetOwner(self):
        return self.owner

    def HasLockTarget(self):
        return self.target is not None

    def GetLockTarget(self):
        return self.target

    def TryLockTarget(self, _duration):
        self.requests += 1


class Controller:
    def __init__(self, pawn):
        self.Pawn = pawn

    def IsValid(self) -> bool:
        return True


class World:
    pass


class GameplayStatistics:
    def __init__(self, sim):
        self._sim = sim

    def GetTimeSeconds(self, _world):
        return self._sim.game_time


class Subsystem:
    def __init__(self, sim):
        self._sim = sim

    def IsValid(self) -> bool:
        return True

    def InjectInputVectorForAction(self, action, value, modifiers, triggers):
        name = action.GetFName().ToString()
        sim = self._sim
        # keep the injected axis so tests can tell a press (1.0) from the
        # release (0.0) that 0.16.1 now emits after every tap
        try:
            axis_x = float(value['X'])
            axis_y = float(value['Y'])
        except Exception:
            axis_x, axis_y = 0.0, 0.0
        failure = sim.inject_failure
        if failure and failure in name:
            sim.injections.append((name, axis_x))
            sim.timed_injections.append((name, axis_x, sim.clock_value))
            raise RuntimeError(f'simulated inject failure for {name}')
        sim.injections.append((name, axis_x))
        sim.timed_injections.append((name, axis_x, sim.clock_value))
        # IA_MoveInput is a local 2D vector: X strafes and Y moves forward.
        if name.endswith('MoveInput') or name == 'IA_MoveInput':
            sim.move_vector = (axis_x, axis_y)
            sim.move_inputs.append((axis_x, axis_y))
        heal = sim.heal_on_inject.get(name)
        if axis_x > 0 and heal and sim.actor is not None and sim.actor.attributes is not None:
            values = sim.actor.attributes._values
            top = values.get('Health_Max', values.get('Health', 0.0))
            values['Health'] = min(top, values.get('Health', 0.0) + heal)
        if axis_x > 0 and name.startswith('IA_Attack') and sim.damage_on_attack:
            for owner in sim.extra_actors:
                if (owner.attributes is not None and owner.IsValid()
                        and ('Monster' in owner._class or 'Enemy' in owner._class)
                        and math.dist(owner._position, sim.actor._position) <= 210):
                    values = owner.attributes._values
                    values['Health'] = max(0.0, values.get('Health', 0.0) - 10.0)
                    break
        # only a real press advances the game's move chain; the release that
        # every tap emits afterwards must not count as a second attack
        if axis_x > 0:
            key = 'A' if 'AttackL' in name else 'B' if 'AttackH' in name else ''
            if sim.combo_game is not None and key:
                sim.combo_game.press(key, sim.clock_value)
            else:
                sim.advance_chain(name)
        return None


class Sim:
    def __init__(self, mod_dir: Path):
        self.mod_dir = mod_dir
        self.status_dir = mod_dir / 'Scripts'
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.clock_value = 100.0
        self.game_time = 500.0
        self.injections: list[tuple[str, float | None]] = []
        self.timed_injections: list[tuple[str, float | None, float]] = []
        self.inject_failure: str | None = None
        self.tick_fn = None
        self.actor = None
        self.actions = []
        self.change_weapon_action = Actor('IA_ChangeWeapon', 'InputAction')
        self.weapon_component = None
        self.weapon_input = None
        self.targeting = None
        self.switch_calls = []
        self.classes = []
        self.extra_actors = []
        self.faction_components = []
        self.find_calls: dict[str, int] = {}
        self.move_inputs: list[tuple[float, float]] = []
        self.heal_on_inject: dict[str, float] = {}
        self.damage_on_attack = True
        self.move_vector = (0.0, 0.0)
        self.defense_busy = False
        self.defense_ticks = 0
        self.controller_calls = 0
        self.deflect = None
        self.statistics = GameplayStatistics(self)
        self.world = World()
        self.subsystem = Subsystem(self)
        self.hooks = {}
        self.lua = None
        self.specs: list[AbilitySpec] = []
        self.chain = ''
        self.active_until = 0.0
        self.combo_game = None
        # Existing combat scenarios represent a player continuously holding
        # the combo key. Set None to stop publishing (transport-loss tests).
        self.assist_hold = True
        self.assist_focused = True
        self.assist_session = 'sim'
        self.assist_seq = 0

    # ---- ability chain model -----------------------------------------
    def install_combo(self, pawn, chains=('A', 'AA', 'AAA', 'AAAB', 'AAB', 'B')):
        """Give the pawn a GAS component whose GA_Hun_Tang_* specs light up."""
        self.specs = [AbilitySpec(chain) for chain in chains]
        pawn.AbilitySystemComponent = AbilitySystemStub(self.specs)
        return self.specs

    def advance_chain(self, action_name: str):
        if not self.specs:
            return
        key = 'A' if 'AttackL' in action_name else 'B' if 'AttackH' in action_name else None
        if key is None:
            return
        if len(self.chain) >= 5:
            self.chain = ''
        self.chain += key
        for spec in self.specs:
            spec.ActiveCount = 1.0 if spec.chain == self.chain else 0.0
        self.active_until = self.clock_value + 0.30

    # ---- UE4SS stubs -------------------------------------------------
    def clock(self):
        return self.clock_value

    def now_epoch(self):
        return int(time.time())

    def find_all(self, name):
        name = str(name)
        self.find_calls[name] = self.find_calls.get(name, 0) + 1
        if name == 'InputAction':
            return self._as_lua([a for a in self.actions if a.IsValid()])
        if name == 'Character':
            actors = ([self.actor] if self.actor and self.actor.IsValid() else [])
            return self._as_lua([a for a in actors + self.extra_actors
                                 if a.IsValid() and getattr(a, 'is_character', True)])
        if name == 'Actor':
            found = [self.actor] if self.actor and self.actor.IsValid() else []
            return self._as_lua(found + [a for a in self.extra_actors if a.IsValid()])
        if name == 'Class':
            return self._as_lua(self.classes)
        if name == 'BasicAttributeSet':
            actors = ([self.actor] if self.actor else []) + self.extra_actors
            return self._as_lua([a.attributes for a in actors if a.attributes is not None])
        if name == 'DeflectComponent':
            return self._as_lua([self.deflect] if self.deflect else [])
        if name == 'CharacterWeaponsComponent':
            return self._as_lua([self.weapon_component] if self.weapon_component else [])
        if name == 'AbilityInputComponent':
            return self._as_lua([self.weapon_input] if self.weapon_input else [])
        if name == 'TargetingComponent':
            return self._as_lua([self.targeting] if self.targeting else [])
        if name == 'FactionComponent':
            return self._as_lua([c for c in self.faction_components if c.IsValid()])
        return []

    def _as_lua(self, items):
        return self.lua.table_from(items) if self.lua is not None else items

    def find_first(self, name):
        return self.subsystem if str(name) == 'EnhancedInputLocalPlayerSubsystem' else None

    def register_loop(self, _frames, fn):
        self.tick_fn = fn

    def player_controller(self):
        self.controller_calls += 1
        return Controller(self.actor) if self.actor else None

    def note_defense_tick(self):
        self.defense_ticks += 1

    def world(self):
        return self.world

    # ---- scenario helpers -------------------------------------------
    def write_control(self, run=True, overrides=None, **settings):
        values = {
            'schema': '2', 'request_id': 'sim', 'run': '1' if run else '0',
            'lease_until': int(time.time()) + 3600,
            'heal_enabled': '1', 'attack_enabled': '1', 'guard_enabled': '1',
            'dodge_enabled': '1', 'execution_enabled': '1', 'skills_enabled': '1',
            'shadow_enabled': '1', 'switch_enabled': '1', 'lock_enabled': '1',
            'heal_threshold': '0.4', 'attack_distance': '7', 'attack_period': '0.35',
            'combo_preset': 'balanced', 'auto_items': '1', 'combo_macro': '0',
            'combat_mode': 'hold', 'combo_session': self.assist_session,
        }
        values.update({k: str(v) for k, v in settings.items()})
        if overrides:
            values.update(overrides)
        text = ''.join(f'{k}={v}\n' for k, v in values.items())
        (self.mod_dir / 'control.ini').write_text(text, encoding='utf-8')

    def write_assist(self, **overrides):
        self.assist_seq += 1
        values = dict(schema=1, session=self.assist_session, seq=self.assist_seq,
                      hold=int(bool(self.assist_hold)), focused=int(self.assist_focused),
                      stamp=self.now_epoch())
        values.update(overrides)
        (self.mod_dir / 'assist.ini').write_text(
            ''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')

    def read_status(self) -> dict:
        paths = [self.mod_dir / name for name in ('status-a.ini', 'status-b.ini')]
        paths = sorted((path for path in paths if path.is_file()),
                       key=lambda path: path.stat().st_mtime_ns)
        latest = {}
        for path in paths:
            data = {}
            for line in path.read_text(encoding='utf-8').splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    data[key.strip()] = value.strip()
            if data.get('heartbeat'):
                latest = data
        return latest

    def read_diag(self) -> dict:
        path = self.mod_dir / 'diag.txt'
        if not path.is_file():
            return {}
        data = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                data[key.strip()] = value.strip()
        return data

    def _walk(self, dt):
        """Apply the injected local movement vector using the pawn's yaw."""
        if self.actor is None:
            return
        axis_x, axis_y = self.move_vector
        magnitude = math.hypot(axis_x, axis_y)
        if magnitude <= 0.01:
            return
        target = None
        for component in self.faction_components:
            faction = component.Faction
            tag = getattr(faction, 'ToString', None)
            if not callable(tag):
                tag_name = getattr(faction, 'TagName', None)
                tag = getattr(tag_name, 'ToString', None)
            name = tag() if callable(tag) else str(faction)
            owner = component.GetOwner()
            owner_class = owner.GetClass().GetFName().ToString().lower() if owner is not None else ''
            if owner is not None and owner is not self.actor and (
                    'enemy' in name.lower() or 'monster' in owner_class):
                target = owner
                break
        if target is None:
            candidates = [actor for actor in self.extra_actors if actor.IsValid() and
                          any(token in actor.GetClass().GetFName().ToString().lower()
                              for token in ('monster', 'enemy'))]
            if candidates:
                target = min(candidates, key=lambda actor: math.dist(actor._position, self.actor._position))
        if target is None:
            return
        ax, ay, az = self.actor._position
        yaw = math.radians(self.actor._rotation_yaw)
        forward = (math.cos(yaw), math.sin(yaw))
        right = (-forward[1], forward[0])
        dx = (right[0] * axis_x + forward[0] * axis_y) / magnitude
        dy = (right[1] * axis_x + forward[1] * axis_y) / magnitude
        distance = math.dist((ax, ay), target._position[:2])
        if distance <= 190.0:
            return
        speed = min(320.0 * dt, distance - 190.0)
        self.actor._position = (ax + dx * speed, ay + dy * speed, az)

    def step(self, dt=1 / 60, count=1):
        for _ in range(count):
            self.clock_value += dt
            self.game_time += dt
            self._walk(dt)
            self.move_vector = (0.0, 0.0)  # InjectInput is a per-tick value
            if self.active_until and self.clock_value > self.active_until:
                for spec in self.specs:
                    spec.ActiveCount = 0.0
                self.active_until = 0.0
            if self.combo_game is not None:
                self.combo_game.tick(self.clock_value)
            if self.assist_hold is not None:
                self.write_assist()
            self.tick_fn()


def build(sim: Sim, source: str) -> None:
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    sim.lua = lua
    for actor in ([sim.actor] if sim.actor else []) + list(sim.extra_actors):
        actor._lua = lua
    def register_hook(path, callback):
        sim.hooks[str(path)] = callback
        return True
    lua.globals()['RegisterHook'] = register_hook
    lua.globals()['_SIM'] = sim
    lua.globals()['_WEAPON_SWITCH_SOURCE'] = WEAPON_SWITCH_SOURCE.read_text(encoding='utf-8')
    lua.globals()['_COMBO_INPUT_SOURCE'] = MOD_SOURCE.with_name('combo_input.lua').read_text(encoding='utf-8')
    lua.execute("""
        -- Scheduler tests isolate the defense engine, which has its own native-API
        -- tests in test_community_defense.py. Never silently replace the live file.
        dofile = function(path)
            if path:find('combo_input.lua', 1, true) then
                return assert(load(_COMBO_INPUT_SOURCE, 'combo_input.lua'))()
            end
            if path:find('community_defense.lua', 1, true) then
                return { new = function() return {
                    stop = function(self) end,
                    tick = function(self, pawn, time, options)
                        _SIM:note_defense_tick()
                        return {ready=true, busy=_SIM.defense_busy, action='defending'}
                    end,
                } end }
            end
            assert(path:find('weapon_switch.lua', 1, true))
            return assert(load(_WEAPON_SWITCH_SOURCE, 'weapon_switch.lua'))()
        end
        _REAL_OS = os
        os = {
            clock = function() return _SIM:clock() end,
            time = function() return _SIM:now_epoch() end,
        }
        FindAllOf = function(name) return _SIM:find_all(name) end
        FindFirstOf = function(name) return _SIM:find_first(name) end
        FName = function(name) return { TagName=name, ToString=function() return name end } end
        StaticFindObject = function(path)
            if path == '/SGameCombatSystem/Weapon/Input/IA_ChangeWeapon.IA_ChangeWeapon' then
                return _SIM.change_weapon_action
            end
            return nil
        end
        LoopInGameThreadAfterFrames = function(frames, fn) _SIM:register_loop(frames, fn) end
        _UEH = {
            GetPlayerController = function() return _SIM:player_controller() end,
            GetGameplayStatics = function() return _SIM.statistics end,
            GetWorld = function() return _SIM.world end,
        }
        package.preload['UEHelpers'] = function() return _UEH end
    """)
    lua.compile(source.encode('utf-8'), f'@{sim.status_dir.as_posix()}/main.lua')()
    assert sim.tick_fn is not None, 'LoopInGameThreadAfterFrames was not registered'


def scenario_attack(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 800.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 300.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (500, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 300.0, 'Health_Max': 300.0, 'Shield': 0.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy'),
                              FactionComponent(sim.actor, 'Faction.Player')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert status['running'] == '1', status
    assert int(status['attacks']) > 0, status
    assert any('IA_AttackL_Press' in name or 'IA_AttackH_Press' in name for name, _ in sim.injections)
    attacks = [name for name, value in sim.injections
               if name.startswith('IA_Attack') and value and value > 0]
    assert 'IA_AttackH_Press' in attacks, attacks  # the balanced plan ends on a heavy
    assert int(status['enemy_count']) >= 1, status
    return status


def scenario_weapon_switch_unavailable(mod_dir: Path) -> dict:
    """A low-Shachi fight reports missing switch components and keeps attacking."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 900.0, 'Health_Max': 1000.0, 'Shachi': 10.0, 'Shachi_Max': 100.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (100, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 300.0, 'Health_Max': 300.0, 'Shield': 0.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy'),
                              FactionComponent(sim.actor, 'Faction.Player')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 120)
    status = sim.read_status()
    assert status['weapon_switch_state'] == 'unavailable_weapons_component', status
    assert int(status['attacks']) > 0, status
    assert not any(name == 'IA_ChangeWeapon' for name, _ in sim.injections)
    return status


def scenario_weapon_switch_uses_native_combat_tag(mod_dir: Path) -> dict:
    """Native combat state permits a confirmed switch even when target discovery misses."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 900.0, 'Health_Max': 1000.0, 'Shachi': 10.0, 'Shachi_Max': 100.0,
    })
    sim.actor.AbilitySystemComponent = AbilitySystemStub(tags={'CharacterState.InCombat'})
    sim.weapon_component = WeaponComponent(sim.actor)
    sim.weapon_input = AbilityInputStub(sim.actor, sim)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    # Let the 0.5s status-file throttle elapse before asserting the snapshot.
    sim.step(1 / 60, 40)
    status = sim.read_status()
    assert status['running'] == '1', status
    assert status['weapon_switch_state'] == 'awaiting_confirmation', status
    assert int(status['weapon_switch_attempts']) == 1, status
    assert len(sim.switch_calls) == 1, sim.switch_calls
    assert status['enemy_count'] == '0', status
    assert status['attacks'] == '0', status
    return status


def scenario_faction_enemy(mod_dir: Path) -> dict:
    """Enemy whose class name has no Monster/Enemy token: only the faction tag marks it."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 900.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    boss = Actor('BP_HuaQianXIao_C_1', 'BP_HuaQianXIao_C', (400, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 500.0, 'Health_Max': 500.0, 'Shield': 0.0})
    villager = Actor('BP_Villager_C_1', 'BP_Villager_C', (300, 0, 0))
    villager.attributes = AttributeSet(villager, {'Health': 100.0, 'Health_Max': 100.0})
    sim.extra_actors = [boss, villager]
    sim.faction_components = [FactionComponent(boss, 'Faction.Enemy', tag_name_only=True),
                              FactionComponent(villager, 'Faction.Environment'),
                              FactionComponent(sim.actor, 'Faction.Player')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    diag = sim.read_diag()
    assert int(status['enemy_count']) == 1, status
    assert int(status['attacks']) > 0, status
    assert 'HuaQianXIao' in diag.get('enemy_list', ''), diag
    return status


def scenario_non_character_boss_manual_movement(mod_dir: Path) -> dict:
    """Use hostile Faction tags to find a non-Character boss without global scans."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'),
                   Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction'), Actor('IA_Deflect', 'InputAction'),
                   Actor('IA_Dodge', 'InputAction'), Actor('IA_DodgeHold', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 900.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    boss = Actor('BP_Monster_Horse_JingYe_C_1', 'BP_Monster_Horse_JingYe_C', (1400, 0, 0))
    boss.is_character = False
    boss.attributes = AttributeSet(boss, {'Health': 5000.0, 'Health_Max': 5000.0})
    fire = Actor('BP_CampFire_C_1', 'BP_CampFire_C', (300, 0, 0))
    sim.extra_actors = [boss, fire]
    sim.faction_components = [FactionComponent(fire, 'Faction.Environment'),
                              FactionComponent(boss, 'Faction.Enemy', tag_name_only=True)]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 600)
    status = sim.read_status()
    diag = sim.read_diag()
    assert status['target'] == 'BP_Monster_Horse_JingYe_C_1', status
    assert float(status['target_distance']) == 14.0, status
    assert int(status['attacks']) > 0, status
    assert int(diag.get('actor_fallback_scans', '0')) == 0, diag
    assert sim.find_calls.get('Actor', 0) == 0, sim.find_calls
    assert 'BP_Monster_Horse_JingYe_C' in diag.get('enemy_list', ''), diag
    assert not any(abs(x) + abs(y) > 0 for x, y in sim.move_inputs), sim.move_inputs
    return status


def scenario_missing_move_action(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    boss = Actor('BP_Monster_Horse_C_1', 'BP_Monster_Horse_C', (1200, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.extra_actors = [boss]
    sim.faction_components = [FactionComponent(boss, 'Faction.Enemy')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 120)
    status = sim.read_status()
    assert status['action'].startswith('combo_'), status
    assert int(status['attacks']) > 0, status
    return status


def scenario_boss_rejection_diagnostics(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    boss = Actor('BP_Monster_JingYe_C_1', 'BP_Monster_JingYe_C', (3200, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.extra_actors = [boss]
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 180)
    diag = sim.read_diag()
    assert 'BP_Monster_JingYe_C@32.0' in diag['enemy_list'], diag
    assert sim.read_status()['target'] == 'BP_Monster_JingYe_C_1', sim.read_status()
    boss._position = (150, 0, 0)
    sim.step(1 / 60, 180)
    assert sim.read_status()['target'] == 'BP_Monster_JingYe_C_1', sim.read_status()
    return sim.read_status()


def scenario_native_lock_target(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_LockTarget', 'InputAction')]
    boss = Actor('BP_Monster_JingYe_C_1', 'BP_Monster_JingYe_C', (150, 0, 0))
    boss.is_character = False
    boss.attributes = AttributeSet(boss, {'Health': 5000.0, 'Health_Max': 5000.0})
    sim.extra_actors = [boss]
    sim.actor.TrueLockTargetActor = boss
    sim.targeting = TargetingComponent(sim.actor, boss)
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 180)
    status, diag = sim.read_status(), sim.read_diag()
    assert status['target'] == 'BP_Monster_JingYe_C_1' and int(status['attacks']) > 0, status
    assert diag['native_lock_state'] == 'locked_enemy', diag
    assert not any(name == 'IA_LockTarget' and value > 0 for name, value in sim.injections), sim.injections
    assert sim.targeting.requests == 0
    return status


def scenario_native_lock_remains_manual(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction')]
    sim.targeting = TargetingComponent(sim.actor)
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 720)
    assert sim.targeting.requests == 0, sim.targeting.requests
    return sim.read_status()


def scenario_reacquire_while_defending(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_Dodge', 'IA_DodgeHold')]
    sim.deflect = DeflectComponent(sim.actor)
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 10)
    boss = Actor('BP_Monster_JingYe_C_1', 'BP_Monster_JingYe_C', (150, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 5000.0, 'Health_Max': 5000.0})
    boss._lua = sim.lua
    sim.extra_actors = [boss]
    for i in range(180):
        sim.deflect.ReceiveFrames.frames = {i: Frame(sim.game_time + 0.04, is_dodge=True)}
        sim.step(1 / 60)
    status = sim.read_status()
    assert status['target'] == 'BP_Monster_JingYe_C_1', status
    assert int(status['dodges']) > 0, status
    return status


def scenario_missing_action(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_SomethingElse', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (150, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 100.0, 'Health_Max': 100.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert status['action'] == 'attack_action_missing', status
    assert status['attacks'] == '0', status
    diag = sim.read_diag()
    assert 'IA_SomethingElse' in diag.get('actions', ''), diag
    return status


def scenario_heal(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_YaoHuLu', 'InputAction'), Actor('IA_UseBottomItem', 'InputAction'),
                   Actor('IA_AttackL_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 200.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    sim.heal_on_inject = {'IA_UseBottomItem': 120.0}
    started_at = sim.clock_value
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert int(status['heals']) >= 1, status
    assert status['heal_ok'] == '1', status
    assert status['heal_fail'] == '0', status
    assert any(name == 'IA_UseBottomItem' for name, _ in sim.injections), sim.injections
    heal_presses = [at for name, value, at in sim.timed_injections
                    if name == 'IA_UseBottomItem' and value > 0]
    assert heal_presses and heal_presses[0] - started_at >= 0.35, heal_presses
    return status


def scenario_combat_art_tap(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_CombatArt', 'InputAction'), Actor('IA_AttackL_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
        'UltimateChargeCount': 1.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (300, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 300.0, 'Health_Max': 300.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 60)
    status = sim.read_status()
    skill_inputs = [value for name, value in sim.injections if name == 'IA_CombatArt']
    assert status['skills'] == '1', status
    assert skill_inputs and skill_inputs[0] > 0 and 0.0 in skill_inputs[1:], skill_inputs
    return status


def scenario_combat_art_unknown_resource(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_CombatArt', 'InputAction'), Actor('IA_AttackL_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (150, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 300.0, 'Health_Max': 300.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 90)
    status = sim.read_status()
    assert status['skill_state'] == 'resource_unknown', status
    assert status['skill_charges'] == '', status
    assert not any(name == 'IA_CombatArt' and value and value > 0 for name, value in sim.injections)
    assert int(status['attacks']) > 0, status
    return status


def healing_fixture(mod_dir):
    sim = Sim(mod_dir)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_YaoHuLu', 'IA_UseTopItem', 'IA_UseBottomItem', 'IA_AttackL_Press', 'IA_AttackH_Press')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 200.0, 'Health_Max': 1000.0})
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (180, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 10000.0, 'Health_Max': 10000.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    return sim


def scenario_empty_medicine_resumes_attack(mod_dir):
    sim = healing_fixture(mod_dir)
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 660)
    status = sim.read_status()
    taps = [n for n, v in sim.injections if v and n in ('IA_YaoHuLu', 'IA_UseTopItem', 'IA_UseBottomItem')]
    assert taps[:2] == ['IA_UseBottomItem', 'IA_YaoHuLu'], taps
    timed_taps = [(name, at) for name, value, at in sim.timed_injections
                  if value > 0 and name in ('IA_YaoHuLu', 'IA_UseTopItem', 'IA_UseBottomItem')]
    assert timed_taps[1][1] - timed_taps[0][1] >= 1.9, timed_taps
    assert all(not (a[0] == b[0] and abs(a[1] - b[1]) < 1e-6)
               for a, b in zip(timed_taps, timed_taps[1:])), timed_taps
    assert 'IA_UseTopItem' not in taps, taps
    assert status['heal_fail'] == '1' and status['heal_ok'] == '0', status
    assert int(status['attacks']) > 0, status
    return status


def scenario_heal_does_not_consume_top_slot(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.heal_on_inject = {'IA_UseTopItem': 120.0}
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    taps = [n for n, v in sim.injections if v and n in
            ('IA_UseBottomItem', 'IA_UseTopItem', 'IA_YaoHuLu')]
    assert taps[:2] == ['IA_UseBottomItem', 'IA_YaoHuLu'], taps
    assert status['heal_ok'] == '0', status
    assert 'IA_UseTopItem' not in taps, taps
    return status


def scenario_support_medicine_boss_and_cooldown(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.actor.attributes._values['Health'] = 550.0
    sim.extra_actors[0]._class = 'BP_Monster_JingYe_C'
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 600)
    presses = [at for name, value, at in sim.timed_injections if name == 'IA_UseTopItem' and value > 0]
    assert len(presses) == 1, presses
    assert not any(name == 'IA_UseBottomItem' and value > 0 for name, value in sim.injections), sim.injections
    sim.step(1 / 60, 1500)
    presses = [at for name, value, at in sim.timed_injections if name == 'IA_UseTopItem' and value > 0]
    assert len(presses) >= 2 and presses[1] - presses[0] >= 15, presses
    assert presses[1] - presses[0] < 18, presses
    return sim.read_status()


def scenario_heal_feedback_during_defense(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.heal_on_inject = {'IA_UseBottomItem': 120.0}
    sim.deflect = DeflectComponent(sim.actor)
    sim.actions.extend([Actor('IA_Stance', 'InputAction'), Actor('IA_Deflect', 'InputAction')])
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 5)
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + 0.08)
    sim.actor.attributes._values['Health'] = 300.0
    sim.step(1 / 60, 8)
    assert not any(n.startswith('IA_Attack') and v for n, v in sim.injections), sim.injections
    sim.step(1 / 60, 30)
    status = sim.read_status()
    assert status['heal_ok'] == '1', status
    return status


def scenario_heal_wait_stops_combo(mod_dir):
    sim = healing_fixture(mod_dir)
    specs = sim.install_combo(sim.actor)
    specs[0].ActiveCount = 1.0
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 30)
    assert not any(n.startswith('IA_Attack') and v for n, v in sim.injections)
    assert not any(n == 'IA_UseBottomItem' and v for n, v in sim.injections)
    # A stale active-ability flag must not prevent medicine forever.
    sim.step(1 / 60, 90)
    assert any(n == 'IA_UseBottomItem' and v for n, v in sim.injections)
    return sim.read_status()


def scenario_pause_releases_combo(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.actions.append(Actor('IA_MoveInput', 'InputAction'))
    sim.actor.attributes._values['Health'] = 900.0
    sim.extra_actors[0]._position = (1000, 0, 0)
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.step(1 / 60, 2)
    assert any(n == 'IA_AttackL_Press' and v > 0 for n, v in sim.injections)
    sim.tick_fn()  # unchanged game clock = game menu
    assert any(n == 'IA_AttackL_Press' and v == 0 for n, v in sim.injections)
    assert sim.move_vector == (0.0, 0.0), sim.move_vector
    sim.write_control(run=False)
    sim.step(1 / 60, 40)
    assert sim.read_status()['running'] == '0'
    return sim.read_status()


def scenario_idle_and_disabled_defense(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.actor.attributes._values['Health'] = 900.0
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control(run=False)
    sim.step(1 / 60, 120)
    assert sim.defense_ticks == 0, sim.defense_ticks
    assert sim.controller_calls <= 5, sim.controller_calls
    assert sim.find_calls.get('InputAction', 0) == 0, sim.find_calls
    sim.write_control(guard_enabled=0, dodge_enabled=0)
    sim.step(1 / 60, 120)
    assert sim.defense_ticks == 0, sim.defense_ticks
    assert int(sim.read_status()['attacks']) > 0, sim.read_status()
    sim.write_control(guard_enabled=1, dodge_enabled=1)
    sim.step(1 / 60, 60)
    assert sim.find_calls.get('DeflectComponent', 0) > 0, sim.find_calls
    return sim.read_status()


def scenario_legacy_defense_release(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.actor.attributes._values['Health'] = 900.0
    sim.deflect = DeflectComponent(sim.actor)
    sim.actions.extend([Actor('IA_Stance', 'InputAction'), Actor('IA_Deflect', 'InputAction'),
                        Actor('IA_Dodge', 'InputAction'), Actor('IA_DodgeHold', 'InputAction')])
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + 0.08, is_dodge=True)
    sim.step(1 / 60, 20)
    taps = [(n, v) for n, v in sim.injections if n == 'IA_Dodge']
    assert any(v and v > 0 for _, v in taps), taps
    assert not sim.find_calls.get('PBZ_ProjectileBullet', 0), sim.find_calls
    sim.write_control(run=False)
    sim.step(1 / 60, 30)
    assert any(v == 0 for _, v in sim.injections if _ == 'IA_Dodge'), sim.injections
    return sim.read_status()


def scenario_legacy_parry_release(mod_dir):
    sim = healing_fixture(mod_dir)
    sim.actor.attributes._values['Health'] = 900.0
    sim.deflect = DeflectComponent(sim.actor)
    sim.actions.extend([Actor('IA_Stance', 'InputAction'), Actor('IA_Deflect', 'InputAction')])
    build(sim, MOD_SOURCE.read_text('utf-8'))
    sim.write_control()
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + 0.08, source=BulletSource('SweepTraceBullet'))
    sim.step(1 / 60, 60)
    presses = [(n, v) for n, v in sim.injections if n == 'IA_Deflect']
    assert any(v and v > 0 for _, v in presses), presses
    assert any(v == 0 for _, v in presses), presses
    assert int(sim.read_status()['attacks']) > 0, sim.read_status()
    return sim.read_status()


def scenario_attack_no_damage(mod_dir: Path) -> dict:
    """Injections land but the target never loses health: the mod must notice."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 900.0, 'Health_Max': 1000.0,
                                                    'Shachi': 400.0, 'Shachi_Max': 400.0})
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (400, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 300.0, 'Health_Max': 300.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.damage_on_attack = False
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert int(status['attacks']) > 0, status
    assert int(status['attack_no_damage']) > 0, status
    assert status['attack_ok'] == '0', status
    diag = sim.read_diag()
    # Unconfirmed damage must not slow requested combos or take over movement.
    reach = float(diag.get('reach_m', '7'))
    assert 2.1 <= reach <= 3.5, diag
    assert diag.get('plan_delays_ms') == '', diag
    assert float(diag.get('combo_period_ms', '0')) == 350, diag
    assert not any(abs(x) + abs(y) > 0 for x, y in sim.move_inputs)
    return status


def scenario_close_boss_keeps_combo_cadence(mod_dir: Path) -> dict:
    """A close boss with unchanged HP must receive window aligned combo presses.

    The chains only chain inside the game's own input window, so the gaps are
    the window opens of A/AA/AAA/AAAA, not a fixed period.
    """
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'),
                   Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 0.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    boss = Actor('BP_Boss_JingYe_C_1', 'BP_Boss_JingYe_C', (150, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 5000.0, 'Health_Max': 5000.0, 'Shield': 0.0})
    sim.extra_actors = [boss]
    sim.faction_components = [FactionComponent(boss, 'Faction.Enemy')]
    sim.damage_on_attack = False
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(attack_period=0.35)
    sim.step(1 / 60, 420)
    status, diag = sim.read_status(), sim.read_diag()
    attacks = [(name, at) for name, value, at in sim.timed_injections
               if name.startswith('IA_Attack') and value and value > 0]
    gaps = [b - a for (_, a), (_, b) in zip(attacks, attacks[1:])]
    assert status['target'] == 'BP_Boss_JingYe_C_1', status
    assert len(attacks) >= 9, attacks
    # A -> AA opens at 0.37 s, AA -> AAA at 0.62, AAA -> AAAA at 0.72,
    # AAAA -> AAAAA at 0.63; one frame of confirmation latency is expected.
    first = gaps[:4]
    assert all(0.30 <= gap <= 0.80 for gap in first), gaps
    assert first[0] < first[1] < first[2], gaps
    # 0 Sha-Chi keeps the run on the free light chain and never presses heavy.
    assert all(name == 'IA_AttackL_Press' for name, _ in attacks), attacks
    assert diag.get('combo_mode') == 'timed', diag
    assert diag.get('plan_delays_ms') == '', diag
    return status


def scenario_defense_handoff_resumes_combo(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'),
                   Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_MoveInput', 'InputAction'), Actor('IA_Deflect', 'InputAction'),
                   Actor('IA_Dodge', 'InputAction'), Actor('IA_DodgeHold', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 0.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    enemy = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (150, 0, 0))
    enemy.attributes = AttributeSet(enemy, {'Health': 5000.0, 'Health_Max': 5000.0})
    sim.extra_actors = [enemy]
    sim.faction_components = [FactionComponent(enemy, 'Faction.Enemy')]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 1)
    presses = [(name, value, at) for name, value, at in sim.timed_injections
               if name.startswith('IA_Attack') and value and value > 0]
    assert presses, sim.timed_injections

    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + 0.01, is_dodge=True)
    sim.step(1 / 60, 1)
    attack_events = [(name, value, at) for name, value, at in sim.timed_injections
                     if name.startswith('IA_Attack')]
    assert attack_events[-1][1] == 0, attack_events
    assert attack_events[-1][2] == sim.clock_value, attack_events

    sim.deflect.ReceiveFrames.frames.clear()
    before_resume = len([1 for name, value, _ in sim.timed_injections
                         if name.startswith('IA_Attack') and value and value > 0])
    sim.step(1 / 60, 4)
    holding = [1 for name, value, _ in sim.timed_injections if name == 'IA_DodgeHold' and value > 0]
    assert holding, sim.timed_injections
    # Defense owns the frames: no attack press may slip in while it holds input.
    assert len([1 for name, value, _ in sim.timed_injections
                if name.startswith('IA_Attack') and value and value > 0]) == before_resume, sim.timed_injections
    sim.step(1 / 60, 40)
    resumed = [(name, value, at) for name, value, at in sim.timed_injections
               if name.startswith('IA_Attack') and value and value > 0]
    assert len(resumed) > before_resume, sim.timed_injections
    # The handoff resumes the chain instead of freezing the controller.
    assert resumed[before_resume][0] in ('IA_AttackL_Press', 'IA_AttackH_Press'), resumed
    status = sim.read_status()
    assert status['combat_yields'] >= '1' and status['combat_resumes'] >= '1', status
    assert status['error'] == '', status
    return status


def scenario_inject_failure(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_MoveInput', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (300, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 100.0, 'Health_Max': 100.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.inject_failure = 'IA_Attack'
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert status['attacks'] == '0', status
    assert int(status['inj_fail']) > 0, status
    assert status['error'] == '', 'a thrown inject error must not pause the loop: ' + status['error']
    return status


def scenario_combo(mod_dir: Path) -> dict:
    """Ability driven combo: follow A,A,A,B by reading GA_Hun_Tang_* ActiveCount."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_Stance', 'InputAction'), Actor('IA_Deflect', 'InputAction'),
                   Actor('IA_Dodge', 'InputAction'), Actor('IA_DodgeHold', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0, 'Shield': 0.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.install_combo(sim.actor)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 300)
    status = sim.read_status()
    diag = sim.read_diag()
    attacks = [name for name, value in sim.injections
               if name.startswith('IA_Attack') and value and value > 0]
    assert len(attacks) >= 4, attacks
    first = ['L' if 'AttackL' in name else 'H' for name in attacks[:4]]
    assert first == ['L', 'L', 'L', 'H'], first
    assert int(diag.get('combo_press', '0')) >= 4, diag
    assert int(status['attacks']) >= 4, status
    return status


def scenario_combo_items_wrapper(mod_dir: Path) -> dict:
    """UE4SS may expose ActivatableAbilities as a struct with an Items array."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0, 'Shield': 0.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.install_combo(sim.actor)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    # Keep the native array wrapper, but put it behind the Lua table shape
    # returned by FGameplayAbilitySpecContainer on some UE4SS versions.
    native_items = sim.actor.AbilitySystemComponent.ActivatableAbilities
    for spec in sim.specs:
        spec.Ability = sim.lua.table_from({'Name': f'Default__GA_Hun_Tang_{spec.chain}_C'})
    sim.actor.AbilitySystemComponent.ActivatableAbilities = sim.lua.table_from({'Items': native_items})
    sim.write_control()
    sim.step(1 / 60, 300)
    status, diag = sim.read_status(), sim.read_diag()
    assert int(diag.get('spec_count', '0')) >= 4, diag
    assert diag.get('combo_mode') == 'ability', diag
    attacks = [name for name, value in sim.injections if name.startswith('IA_Attack') and value and value > 0]
    assert ['L' if 'AttackL' in name else 'H' for name in attacks[:4]] == ['L', 'L', 'L', 'H'], attacks
    return status


def scenario_combo_foreach_only(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    monster = Actor('BP_Monster_JingYe_C_1', 'BP_Monster_JingYe_C', (180, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.install_combo(sim.actor)
    sim.actor.AbilitySystemComponent.ActivatableAbilities = AbilityItemsForEachOnly(sim.specs)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 300)
    status, diag = sim.read_status(), sim.read_diag()
    assert int(diag['spec_count']) == len(sim.specs), diag
    assert diag['combo_mode'] == 'ability' and int(status['attacks']) >= 4, (status, diag)
    return status


def scenario_shield_tag_shadow_charge(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction'),
                   Actor('IA_ShachiAbility', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'SpiritShadow': 1.0, 'SpiritShadow_Max': 1.0})
    monster = Actor('BP_Boss_Rider_C_1', 'BP_Boss_Rider_C', (150, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0})
    tag = 'CharacterState.AbilityState.Shield.HasShield'
    monster.AbilitySystemComponent = AbilitySystemStub(tags=[tag])
    sim.extra_actors = [monster]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 45)
    events = [(v, t) for n, v, t in sim.timed_injections if n == 'IA_ShachiAbility']
    assert events and events[0][0] > 0, events
    assert not any(v == 0 for v, _ in events), events
    sim.step(1 / 60, 30)
    events = [(v, t) for n, v, t in sim.timed_injections if n == 'IA_ShachiAbility']
    release = next(t for v, t in events if v == 0)
    assert release - events[0][1] >= 0.8, events
    monster.AbilitySystemComponent.tags.clear()
    sim.step(1 / 60, 60)
    diag = sim.read_diag()
    assert int(diag['shield_breaks']) == 1, diag
    assert sim.read_status()['target'] == 'BP_Boss_Rider_C_1', sim.read_status()
    return sim.read_status()


def scenario_shadow_interrupt_release(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_ShachiAbility', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'SpiritShadow': 1.0, 'SpiritShadow_Max': 1.0})
    monster = Actor('BP_Boss_Rider_C_1', 'BP_Boss_Rider_C', (150, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0})
    monster.AbilitySystemComponent = AbilitySystemStub(tags=['CharacterState.AbilityState.Shield.HasShield'])
    sim.extra_actors = [monster]
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 20)
    assert any(n == 'IA_ShachiAbility' and v > 0 for n, v in sim.injections), sim.injections
    sim.write_control(run=False)
    sim.step(1 / 60, 40)
    events = [(v, t) for n, v, t in sim.timed_injections if n == 'IA_ShachiAbility']
    assert any(v == 0 for v, _ in events), events
    return sim.read_status()


def windowed_fixture(mod_dir: Path, game_kwargs=None, **settings):
    """Close-range fight against a game model that enforces the combo windows."""
    sim = Sim(mod_dir)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.damage_on_attack = False
    sim.combo_game = WindowedFight(sim, **(game_kwargs or {}))
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(**settings)
    return sim


def scenario_phase_combo(mod_dir: Path) -> dict:
    """A build that only publishes the action phase still gets chained hits.

    No move tag and no listed ability is readable here: the mod has only
    CharacterState.Action.Phase.Combo to tell it when a window is open.
    """
    sim = windowed_fixture(mod_dir, game_kwargs={'publish_tags': False, 'publish_phase': True})
    sim.step(1 / 60, 600)
    game, diag = sim.combo_game, sim.read_diag()
    assert diag.get('combo_mode') == 'phase', diag
    assert diag.get('phase_seen') == 'true', diag
    assert game.completed >= 2, (game.completed, game.presses)
    assert game.rejected <= 3, [(k, round(t - 100, 2), p) for k, t, p in game.presses]
    return sim.read_status()


def scenario_windowed_combo(mod_dir: Path) -> dict:
    """A game that drops out-of-window presses must still see AAAB twice.

    The old fixed 0.27 s cadence pressed outside combo_open on almost every
    step; the window aligned engine should chain on nearly every input.
    """
    sim = windowed_fixture(mod_dir)
    sim.step(1 / 60, 600)
    game, diag = sim.combo_game, sim.read_diag()
    assert len(game.presses) >= 8, game.presses
    assert game.path or game.completed >= 1, (game.path, game.completed)
    # Every accepted input landed inside its window and the full 4 step chain
    # was reached; at most a single dropped input is acceptable.
    assert game.rejected <= 1, [(k, round(t - 100, 2), p) for k, t, p in game.presses]
    assert game.completed >= 2, (game.completed, len(game.presses))
    assert diag.get('combo_mode') == 'tag', diag
    assert int(diag.get('combo_confirm', '0')) >= 6, diag
    assert int(diag.get('combo_reject', '0')) <= 1, diag
    # Heavy finisher is used, and the Sha-Chi reserve is still visible.
    assert any(key == 'B' for key, _, _ in game.presses), game.presses
    assert diag.get('chain_tag') in MOVE_TAGS.values() or diag.get('chain_tag') in ('none', ''), diag
    return sim.read_status()


def scenario_windowed_combo_low_shachi(mod_dir: Path) -> dict:
    """Below the reserve the engine keeps the light chain and spends no heavy."""
    sim = windowed_fixture(mod_dir)
    sim.actor.attributes._values['Shachi'] = 120.0
    sim.step(1 / 60, 420)
    game = sim.combo_game
    assert game.presses, game.presses
    assert all(key == 'A' for key, _, _ in game.presses), game.presses
    assert game.rejected == 0, game.presses
    status = sim.read_status()
    assert status['heavy_ok'] == '0', status
    return status


def scenario_windowed_combo_blocks_bad_cadence(mod_dir: Path) -> dict:
    """An over-fast setting recovers through bounded retries, not input spam."""
    sim = windowed_fixture(mod_dir)
    sim.write_control(combo_speed=0.8)
    sim.step(1 / 60, 600)
    game, diag = sim.combo_game, sim.read_diag()
    assert game.completed >= 2, (game.completed, game.presses)
    # Retries are bounded: at most one extra press per window, so the input
    # count stays close to the accepted count instead of becoming mashing.
    assert len(game.presses) <= game.accepted * 2 + 2, (len(game.presses), game.accepted)
    assert float(diag.get('combo_delay_ms', '0')) > 0, diag
    return sim.read_status()


def scenario_macro_hold_without_target(mod_dir: Path) -> dict:
    """A held key alone drives the string: no target, no chain tag, no distance."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'),
                   Actor('IA_AttackH_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 900.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    sim.assist_hold = True
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(combo_macro='1')
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert status['running'] == '1', status
    presses = [name for name, value in sim.injections
               if name.startswith('IA_Attack') and value and value > 0]
    assert len(presses) >= 4, presses
    assert status['action'].startswith('macro_'), status
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    base = ROOT / 'tmp-sim'
    results = {}
    scenarios = {
        'attack': scenario_attack,
        'weapon_switch_unavailable': scenario_weapon_switch_unavailable,
        'weapon_switch_native_combat_tag': scenario_weapon_switch_uses_native_combat_tag,
        'faction_enemy': scenario_faction_enemy,
        'non_character_boss_manual_movement': scenario_non_character_boss_manual_movement,
        'missing_move_action': scenario_missing_move_action,
        'boss_rejection_diagnostics': scenario_boss_rejection_diagnostics,
        'native_lock_target': scenario_native_lock_target,
        'native_lock_remains_manual': scenario_native_lock_remains_manual,
        'reacquire_while_defending': scenario_reacquire_while_defending,
        'missing_action': scenario_missing_action,
        'heal': scenario_heal,
        'combat_art_tap': scenario_combat_art_tap,
        'combat_art_unknown_resource': scenario_combat_art_unknown_resource,
        'empty_medicine_resumes_attack': scenario_empty_medicine_resumes_attack,
        'heal_does_not_consume_top_slot': scenario_heal_does_not_consume_top_slot,
        'support_medicine_boss_and_cooldown': scenario_support_medicine_boss_and_cooldown,
        'heal_feedback_during_defense': scenario_heal_feedback_during_defense,
        'heal_wait_stops_combo': scenario_heal_wait_stops_combo,
        'pause_releases_combo': scenario_pause_releases_combo,
        'idle_and_disabled_defense': scenario_idle_and_disabled_defense,
        'legacy_defense_release': scenario_legacy_defense_release,
        'legacy_parry_release': scenario_legacy_parry_release,
        'inject_failure': scenario_inject_failure,
        'attack_no_damage': scenario_attack_no_damage,
        'close_boss_combo_cadence': scenario_close_boss_keeps_combo_cadence,
        'defense_handoff_resumes_combo': scenario_defense_handoff_resumes_combo,
        'phase_combo': scenario_phase_combo,
        'windowed_combo': scenario_windowed_combo,
        'windowed_combo_low_shachi': scenario_windowed_combo_low_shachi,
        'windowed_combo_bad_cadence': scenario_windowed_combo_blocks_bad_cadence,
        'combo': scenario_combo,
        'combo_items_wrapper': scenario_combo_items_wrapper,
        'combo_foreach_only': scenario_combo_foreach_only,
        'macro_hold_without_target': scenario_macro_hold_without_target,
    }
    for name, run in scenarios.items():
        mod_dir = base / name
        if mod_dir.exists():
            shutil.rmtree(mod_dir)
        mod_dir.mkdir(parents=True)
        results[name] = run(mod_dir)
        print(f'{name}: OK  action={results[name].get("action")} '
              f'attacks={results[name].get("attacks")} blocks={results[name].get("blocks")} '
              f'heals={results[name].get("heals")} inj_ok={results[name].get("inj_ok")}')
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    print(f'simulated {len(results)} scenarios from {MOD_SOURCE.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())











