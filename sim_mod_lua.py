"""Offline simulator for PBZSmartCombat's main.lua (UE4SS Lua mod).

Runs the real mod source against stubbed UE4SS/UEHelpers APIs so combat and
defense decisions can be checked without launching the game.  Development-only.
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import lupa

ROOT = Path(__file__).resolve().parent
MOD_SOURCE = ROOT / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat' / 'Scripts' / 'main.lua'


class FName:
    def __init__(self, name: str):
        self._name = name

    def ToString(self) -> str:
        return self._name


class ClassObject:
    def __init__(self, name: str):
        self._name = name

    def GetFName(self) -> FName:
        return FName(self._name)


class Tag:
    def __init__(self, name: str):
        self._name = name
        self.TagName = FName(name)

    def ToString(self) -> str:
        return self._name


class FactionComponent:
    def __init__(self, owner, faction: str):
        self._owner = owner
        self.Faction = Tag(faction)

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


class AbilitySystemStub:
    def __init__(self, specs):
        self.ActivatableAbilities = AbilityItems(specs)

    def IsValid(self) -> bool:
        return True


class Actor:
    next_id = 1000

    def __init__(self, name: str, class_name: str, position=(0.0, 0.0, 0.0)):
        Actor.next_id += 1
        self._id = Actor.next_id
        self._name = name
        self._class = class_name
        self._position = position
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

    def GetOuter(self):
        return None

    def GetFullName(self) -> str:
        return f'{self._class} {self._name}'


class Frame:
    def __init__(self, hit: float, is_dodge=False, is_hit=False):
        self.HitGameTime = hit
        self.IsDodge = is_dodge
        self.IsHit = is_hit


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


class FrameMap:
    def __init__(self):
        self.frames: dict[int, Frame] = {}

    def ForEach(self, fn):
        for handle, frame in list(self.frames.items()):
            fn(HandleValue(handle), FrameValue(frame))


class DeflectComponent:
    def __init__(self, owner):
        self._owner = owner
        self.ReceiveFrames = FrameMap()

    def IsValid(self) -> bool:
        return True

    def GetOwner(self):
        return self._owner


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
        failure = sim.inject_failure
        if failure and failure in name:
            sim.injections.append((name, None))
            raise RuntimeError(f'simulated inject failure for {name}')
        sim.injections.append((name, None))
        heal = sim.heal_on_inject.get(name)
        if heal and sim.actor is not None and sim.actor.attributes is not None:
            values = sim.actor.attributes._values
            top = values.get('Health_Max', values.get('Health', 0.0))
            values['Health'] = min(top, values.get('Health', 0.0) + heal)
        if name.startswith('IA_Attack') and sim.damage_on_attack:
            for enemy in sim.faction_components:
                owner = enemy.GetOwner()
                if owner is not None and owner.attributes is not None:
                    values = owner.attributes._values
                    values['Health'] = max(0.0, values.get('Health', 0.0) - 10.0)
                    break
        sim.advance_chain(name)
        return None


class Sim:
    def __init__(self, mod_dir: Path):
        self.mod_dir = mod_dir
        self.status_dir = mod_dir / 'Scripts'
        self.clock_value = 100.0
        self.game_time = 500.0
        self.injections: list[tuple[str, float | None]] = []
        self.inject_failure: str | None = None
        self.tick_fn = None
        self.actor = None
        self.actions = []
        self.classes = []
        self.extra_actors = []
        self.faction_components = []
        self.heal_on_inject: dict[str, float] = {}
        self.damage_on_attack = True
        self.deflect = None
        self.statistics = GameplayStatistics(self)
        self.world = World()
        self.subsystem = Subsystem(self)
        self.lua = None
        self.specs: list[AbilitySpec] = []
        self.chain = ''
        self.active_until = 0.0

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
        if name == 'InputAction':
            return self._as_lua([a for a in self.actions if a.IsValid()])
        if name == 'Actor':
            found = [self.actor] if self.actor and self.actor.IsValid() else []
            return self._as_lua(found + [a for a in self.extra_actors if a.IsValid()])
        if name == 'Class':
            return self._as_lua(self.classes)
        if name == 'BasicAttributeSet':
            actors = [self.actor] if self.actor else []
            return self._as_lua([a.attributes for a in actors if a.attributes is not None])
        if name == 'DeflectComponent':
            return self._as_lua([self.deflect] if self.deflect else [])
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
        return Controller(self.actor) if self.actor else None

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
            'heal_threshold': '0.4', 'attack_distance': '7', 'attack_period': '0.46',
            'combo_preset': 'balanced',
        }
        values.update({k: str(v) for k, v in settings.items()})
        if overrides:
            values.update(overrides)
        text = ''.join(f'{k}={v}\n' for k, v in values.items())
        (self.mod_dir / 'control.ini').write_text(text, encoding='utf-8')

    def read_status(self) -> dict:
        merged = {}
        for name in ('status-a.ini', 'status-b.ini'):
            path = self.mod_dir / name
            if not path.is_file():
                continue
            data = {}
            for line in path.read_text(encoding='utf-8').splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    data[key.strip()] = value.strip()
            if data.get('heartbeat'):
                merged = data
        return merged

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

    def step(self, dt=1 / 60, count=1):
        for _ in range(count):
            self.clock_value += dt
            self.game_time += dt
            if self.active_until and self.clock_value > self.active_until:
                for spec in self.specs:
                    spec.ActiveCount = 0.0
                self.active_until = 0.0
            self.tick_fn()


def build(sim: Sim, source: str) -> None:
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    sim.lua = lua
    for actor in ([sim.actor] if sim.actor else []) + list(sim.extra_actors):
        actor._lua = lua
    lua.globals()['_SIM'] = sim
    lua.execute("""
        _REAL_OS = os
        os = {
            clock = function() return _SIM:clock() end,
            time = function() return _SIM:now_epoch() end,
        }
        FindAllOf = function(name) return _SIM:find_all(name) end
        FindFirstOf = function(name) return _SIM:find_first(name) end
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
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction')]
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
    attacks = [name for name, _ in sim.injections if name.startswith('IA_Attack')]
    assert 'IA_AttackH_Press' in attacks, attacks  # the balanced plan ends on a heavy
    assert int(status['enemy_count']) >= 1, status
    return status


def scenario_faction_enemy(mod_dir: Path) -> dict:
    """Enemy whose class name has no Monster/Enemy token: only the faction tag marks it."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 900.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    boss = Actor('BP_HuaQianXIao_C_1', 'BP_Monster_HuaQianXIao_C', (400, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 500.0, 'Health_Max': 500.0, 'Shield': 0.0})
    villager = Actor('BP_Villager_C_1', 'BP_Villager_C', (300, 0, 0))
    villager.attributes = AttributeSet(villager, {'Health': 100.0, 'Health_Max': 100.0})
    sim.extra_actors = [boss, villager]
    sim.faction_components = [FactionComponent(boss, 'Faction.Enemy'),
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


def scenario_missing_action(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_SomethingElse', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (300, 0, 0))
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
    sim.actions = [Actor('IA_YaoHuLu', 'InputAction'), Actor('IA_AttackL_Press', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 200.0, 'Health_Max': 1000.0})
    sim.deflect = DeflectComponent(sim.actor)
    sim.heal_on_inject = {'IA_YaoHuLu': 120.0}
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 240)
    status = sim.read_status()
    assert int(status['heals']) == 1, status
    assert status['heal_ok'] == '1', status
    assert status['heal_fail'] == '0', status
    assert any(name == 'IA_YaoHuLu' for name, _ in sim.injections), sim.injections
    return status


def scenario_attack_no_damage(mod_dir: Path) -> dict:
    """Injections land but the target never loses health: the mod must notice."""
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction'), Actor('IA_AttackH_Press', 'InputAction')]
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
    return status


def scenario_defense(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_Stance', 'InputAction'), Actor('IA_Deflect', 'InputAction'),
                   Actor('IA_Dodge', 'InputAction'), Actor('IA_DodgeHold', 'InputAction')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0,
                                                    'Shachi': 400.0, 'Shachi_Max': 400.0})
    sim.deflect = DeflectComponent(sim.actor)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 2)
    sim.deflect.ReceiveFrames.frames[7] = Frame(sim.game_time + 0.05)
    sim.step(1 / 60, 180)
    status = sim.read_status()
    names = [name for name, _ in sim.injections]
    assert int(status['blocks']) >= 1, status
    assert 'IA_Deflect' in names and 'IA_Stance' in names, names
    return status


def scenario_inject_failure(mod_dir: Path) -> dict:
    sim = Sim(mod_dir)
    sim.actions = [Actor('IA_AttackL_Press', 'InputAction')]
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
    attacks = [name for name, _ in sim.injections if name.startswith('IA_Attack')]
    assert len(attacks) >= 4, attacks
    first = ['L' if 'AttackL' in name else 'H' for name in attacks[:4]]
    assert first == ['L', 'L', 'L', 'H'], first
    assert int(diag.get('combo_press', '0')) >= 4, diag
    assert int(status['attacks']) >= 4, status
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    base = ROOT / 'tmp-sim'
    results = {}
    scenarios = {
        'attack': scenario_attack,
        'faction_enemy': scenario_faction_enemy,
        'missing_action': scenario_missing_action,
        'heal': scenario_heal,
        'defense': scenario_defense,
        'inject_failure': scenario_inject_failure,
        'attack_no_damage': scenario_attack_no_damage,
        'combo': scenario_combo,
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











