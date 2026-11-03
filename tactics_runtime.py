"""Bounded, read-only tactical sampling on its own thread for the verified build."""
import math
import logging
import re
import struct
import threading
import time
from runtime import RuntimeReader
from move_runtime import MoveReader, weapon_family
from combat_resources import ultimate_resource
from shadow_runtime import ShadowReader
from upgrade_runtime import enhancement


class Reflection:
    def __init__(self, memory):
        self.m = memory
        self.names = {}
        self.props = {}

    def name(self, raw):
        if len(raw) != 8:
            return ''
        index, number = struct.unpack('<II', raw)
        if index >> 16 > 8192:
            return ''
        if index not in self.names:
            block = self.m.u64(self.m.base+0xdd11ac0+0x10+(index >> 16)*8)
            entry = block+(index & 65535)*2
            h = self.m.read(entry, 2)
            if len(h) != 2:
                return ''
            header = int.from_bytes(h, 'little')
            length = header >> 6
            if not 0 < length <= 256:
                return ''
            raw_name = self.m.read(entry+2, length*(2 if header & 1 else 1))
            try:
                value = raw_name.decode('utf-16le' if header & 1 else 'utf-8')
            except UnicodeError:
                return ''
            if not value or any(ord(c) < 32 for c in value):
                return ''
            if len(self.names) < 20000:
                self.names[index] = value
            else:
                return value
        return self.names[index]+('_'+str(number-1) if number else '')

    def fields(self, cls):
        if cls in self.props:
            return self.props[cls]
        original = cls
        result = {}
        seen = set()
        for _ in range(24):
            if not cls:
                break
            p = self.m.u64(cls+0x50)
            for _ in range(512):
                if not p or p in seen:
                    break
                seen.add(p)
                h = self.m.read(p, 0x80)
                if len(h) != 0x80:
                    break
                n = self.name(h[0x20:0x28])
                off, size = struct.unpack_from('<I', h, 0x44)[0], struct.unpack_from('<I', h, 0x34)[0]
                if n and off < 0x10000:
                    result.setdefault(n, (off, size, p))
                p = struct.unpack_from('<Q', h, 0x10)[0]
            cls = self.m.u64(cls+0x40)
        self.props[original] = result
        return result

    def field(self, obj, key):
        return self.fields(self.m.u64(obj+0x10)).get(key)

    def pointer(self, obj, key):
        f = self.field(obj, key)
        return self.m.u64(obj+f[0]) if f else 0


def boss_from_tags(tags):
    """Use the game's explicit classification, never a guessed HP threshold."""
    return any(t == 'CharacterType.AI.Boss' or t in {
        'CharacterType.Difficulty.MiniBoss', 'CharacterType.Difficulty.NormalBoss',
        'CharacterType.Difficulty.GreatBoss', 'CharacterType.Difficulty.UltimateBoss'} for t in tags)


class TacticalReader:
    def __init__(self, runtime):
        self.r = runtime
        self.m = runtime.memory
        self.ref = Reflection(self.m)
        self.npcs = set()
        self.class_cache = {}
        self.scan = None
        self.new_npcs = set()
        self.next_scan = 0
        self.identity = None
        self.move_reader = MoveReader(self)
        self.shadow_reader = ShadowReader(self)

    def array(self, addr, size, limit=256):
        h = self.m.read(addr, 16)
        if len(h) != 16:
            return []
        p, n, cap = struct.unpack('<Qii', h)
        if not 0 <= n <= cap <= 4096 or n > limit:
            return []
        data = self.m.read(p, n*size) if n else b''
        return [data[i:i+size] for i in range(0, len(data), size)] if len(data)==n*size and self.m.read(addr,16) == h else []

    def tags(self, address):
        return [self.ref.name(v) for v in self.array(address, 8)]

    def scan_slice(self, now):
        if self.scan is None:
            if now < self.next_scan:
                return
            self.r.objects.refresh()
            self.scan = iter(self.r.objects.objects())
            self.new_npcs = set()
        for _ in range(512):
            obj = next(self.scan, None)
            if obj is None:
                self.npcs = self.new_npcs
                self.scan = None
                self.next_scan = now+2
                return
            h = self.m.read(obj, 40)
            if len(h) != 40 or struct.unpack_from('<I',h,8)[0] & 0x40000030:
                continue
            cls = struct.unpack_from('<Q',h,16)[0]
            if cls not in self.class_cache:
                self.class_cache[cls] = self.r.objects.derives(cls,self.r.classes['npc'])
            if self.class_cache[cls]:
                self.new_npcs.add(obj)
                self.npcs.add(obj)

    def actor_world(self, actor):
        level = self.m.u64(actor+0x20)
        return self.ref.pointer(level,'OwningWorld') if self.r.objects.valid_object(level) else 0

    def enemy(self, actor, position, world=0):
        if not self.r.objects.valid_object(actor):
            return None
        if world and self.actor_world(actor) != world:
            return None
        faction = self.ref.pointer(actor, 'Faction Component')
        if not faction or not self.r.objects.valid_object(faction) or self.m.u64(faction+0xa8) != actor:
            return None
        if self.ref.name(self.m.read(faction+0x134,8)) != 'Faction.Enemy':
            return None
        attr = self.m.u64(actor+0x758)
        if not attr or not self.r.objects.valid_object(attr) or self.m.u64(attr+0x20) != actor:
            return None
        h = self.m.read(attr+0x64,20)
        if len(h) != 20:
            return None
        hp, maximum = struct.unpack_from('<f',h)[0], struct.unpack_from('<f',h,16)[0]
        p = self.r.position(actor)
        if not p or not all(math.isfinite(v) for v in (hp,maximum)) or not 0 < hp <= maximum*1.01:
            return None
        distance = math.dist(position,p)/100
        if distance > 30 or abs(position[2]-p[2]) > 500:
            return None
        config = self.ref.pointer(actor,'Actor Config')
        tags = []
        if config and self.r.objects.valid_object(config):
            f = self.ref.field(config,'Difficulty Tag')
            if f:
                tags.append(self.ref.name(self.m.read(config+f[0],8)))
            f = self.ref.field(config,'InitialTags')
            if f:
                tags.extend(self.tags(config+f[0]))
        sha = self.m.read(attr+0x644,36)
        sr = None
        if len(sha)==36:
            current, maximum_sha = struct.unpack_from('<f',sha)[0],struct.unpack_from('<f',sha,32)[0]
            if math.isfinite(current) and math.isfinite(maximum_sha) and 0 <= current <= maximum_sha and maximum_sha > 0:
                sr = current/maximum_sha
        return dict(actor=actor,hp=hp,hp_max=maximum,distance=distance,height_delta=p[2]-position[2],
                    boss=boss_from_tags(tags),tags=tags,shachi_ratio=sr)

    def skills(self, player, weapon=None):
        asc = self.m.u64(player+0x750)
        if not self.r.objects.valid_object(asc):
            return {}, [], 'unknown', {}
        names = []
        art = None
        moves = {}
        weapon = self.current_weapon(player) if weapon is None else weapon
        active = []
        for raw in self.array(asc+0x520,240,512):
            ability = struct.unpack_from('<Q',raw,0x10)[0]
            # Ability pointers are class default objects, deliberately not valid_object().
            name = self.ref.name(self.m.read(ability+0x18,8))
            if not name.startswith('Default__GA_'):
                continue
            if raw[0x29] & 0x04:
                continue
            names.append(name)
            if raw[0x28] and 'GA_Hun_' in name:
                active.append(name)
            cost = self.ref.field(ability,'Ultimate Charge Cost')
            if cost and weapon_family(weapon) and name == 'Default__GA_Hun_'+weapon_family(weapon)+'_C_C':
                instances = self.pointer_array_bytes(raw[0x78:0x88])+self.pointer_array_bytes(raw[0x88:0x98])
                instance = next((p for p in instances if self.r.objects.valid_object(p)
                                 and self.m.u64(p+0x10)==self.m.u64(ability+0x10)),0)
                cd = self.ref.field(ability,'Cooldown Data')
                duration = self.m.read(instance+cd[0],8) if cd and instance else b''
                # Read actual instance values; an unreadable cooldown stays unknown.
                effect = self.m.read(instance+0x1b8,8) if instance else b''
                zero_cd = len(duration)==8 and struct.unpack('<d',duration)[0]==0 and effect==bytes(8)
                value_raw = self.m.read(instance+cost[0],4) if instance else b''
                value = struct.unpack('<I',value_raw)[0] if len(value_raw)==4 else None
                count_raw = self.m.read(instance+0x4b8,4) if instance else b''
                activation = struct.unpack('<I',count_raw)[0] if len(count_raw)==4 else None
                art = dict(cost=value,unlocked=True,cooldown_known=zero_cd,active=bool(raw[0x28]),
                           activation=activation,instance=instance,
                           required_tags=self.tags(ability+0x220),blocked_tags=self.tags(ability+0x240))
                hp_raw=self.m.read(self.m.u64(player+0x758)+0x74,4)
                hp_max=struct.unpack('<f',hp_raw)[0] if len(hp_raw)==4 else 0
                if instance:art.update(self.move_reader.health_budget(instance,hp_max))
            if weapon=='Weapon_Hun_Tang' and name.startswith('Default__GA_Hun_Tang_'):
                suffix=name[len('Default__GA_Hun_Tang_'):-2]
                if re.fullmatch(r'[AB]+(?:_Draw|_Dash|_DashDraw|_Snap)?',suffix):
                    instances=self.pointer_array_bytes(raw[0x78:0x88])+self.pointer_array_bytes(raw[0x88:0x98])
                    values=[]
                    for ins in instances:
                        if self.r.objects.valid_object(ins):
                            count_raw=self.m.read(ins+0x4b8,4)
                            if len(count_raw)==4:values.append(struct.unpack('<I',count_raw)[0])
                    if values:moves[name]=sum(values)
        result = {'combat_art':dict(art,**ultimate_resource(self,player))} if art else {}
        inp = self.ref.pointer(player,'Ability Input Component')
        phase = 'unknown'
        if inp and self.r.objects.valid_object(inp) and self.m.u64(inp+0xa8)==player:
            in_phase=self.m.read(inp+0x1a0,1)
            if in_phase==b'\x00':phase='busy' if active else ''
            elif in_phase==b'\x01':phase=self.ref.name(self.m.read(inp+0x1a4,8)) or 'busy'
        return result, names, phase, moves

    def pointer_array_bytes(self, raw):
        if len(raw)!=16:return []
        p,n,cap=struct.unpack('<Qii',raw)
        if not 0 <= n <= cap <= 64:return []
        data=self.m.read(p,n*8) if n else b''
        return list(struct.unpack('<'+'Q'*n,data)) if len(data)==n*8 else []

    def current_weapon(self, player):
        return self.weapon_inventory(player).get('weapon','')

    def weapon_level(self,weapon):
        backpack=self.r.backpack
        if not backpack or not self.r.objects.valid_object(backpack):return None
        f=self.ref.field(backpack,'MainWeaponInfos')
        if not f:return None
        for key,value in self.r.sparse_map(backpack+f[0],24) or []:
            if self.ref.name(struct.pack('<Q',key))!=weapon_family(weapon):continue
            info=struct.unpack('<Q',value)[0]
            if not self.r.objects.valid_object(info) or self.m.u64(info+32)!=backpack:return None
            f=self.ref.field(info,'WeaponLevel');raw=self.m.read(info+f[0],4) if f and f[1]==4 else b''
            level=struct.unpack('<i',raw)[0] if len(raw)==4 else 0
            return level if 1<=level<=100 else None
        return None

    def weapon_inventory(self, player, slot='Weapon.Slot.Main'):
        component = self.ref.pointer(player,'Character Weapons Component')
        if (not component or not self.r.objects.valid_object(component)
                or self.m.u64(component+0xa8)!=player):return {}
        for key,value in self.r.sparse_map(component+0xf8,48) or []:
            if self.ref.name(struct.pack('<II',key & 0xffffffff,0))!=slot:
                continue
            if len(value)<20:
                return {}
            p,n,cap=struct.unpack_from('<Qii',value)
            # UCharacterWeaponsComponent::GetCurrentWeaponIndex reads slot +0x10.
            index=struct.unpack_from('<i',value,16)[0]
            if not 0 <= index < n <= cap <= 32:
                return {}
            config = self.m.u64(p+index*288)
            if not self.r.objects.valid_object(config):return {}
            slots=[]
            for i in range(n):
                candidate=self.m.u64(p+i*288)
                if self.r.objects.valid_object(candidate):
                    cooldown=self.move_reader.number(candidate,'Change Weapon Cooldown')
                    slots.append(dict(index=i,config=candidate,name=self.ref.name(self.m.read(candidate+24,8)),cooldown=cooldown))
            return dict(weapon=self.ref.name(self.m.read(config+24,8)),index=index,config=config,slots=slots)
        return {}

    def sample(self, player, world, target, backpack):
        began=time.monotonic()
        self.r.objects.refresh()
        if not self.r.objects.valid_object(player) or self.actor_world(player)!=world:
            return {}
        if self.identity is not None and self.identity!=(player,world):
            self.npcs.clear()
            self.new_npcs.clear()
            self.scan=None
            self.next_scan=0
        self.identity=(player,world)
        p = self.r.position(player)
        if not p:
            return {}
        self.r.backpack = backpack
        enemies = []
        for actor in self.npcs | ({target} if target else set()):
            e = self.enemy(actor,p,world)
            if e:
                if actor==target:e.update(self.shadow_reader.shield(actor))
                enemies.append(e)
        inventory=self.weapon_inventory(player)
        weapon=inventory.get('weapon','')
        level=self.weapon_level(weapon)
        self.move_reader.refresh_equipment(player,world,inventory.get('config'),level,self.move_reader.owned_tags(player))
        skills, abilities, phase, moves = self.skills(player,weapon)
        shadow=self.shadow_reader.sample(player)
        upgrade=enhancement(self,player,weapon)
        maximum_raw=self.m.read(self.m.u64(player+0x758)+0x664,4)
        maximum=struct.unpack('<f',maximum_raw)[0] if len(maximum_raw)==4 else 0
        hp_raw=self.m.read(self.m.u64(player+0x758)+0x74,4)
        hp_max=struct.unpack('<f',hp_raw)[0] if len(hp_raw)==4 else 0
        model=(self.move_reader.snapshot(player,weapon,maximum,inventory.get('config',0),hp_max) if math.isfinite(maximum) and maximum>0
               else dict(moves={},move_events={},move_model_ready=False,owned_tags=None))
        after=self.weapon_inventory(player)
        if (after.get('index'),after.get('config'))!=(inventory.get('index'),inventory.get('config')):return {}
        return dict(player=player,world=world,at=began,enemies=enemies,skills=skills,
                    abilities=abilities,phase=phase,scan_complete=self.scan is None,weapon=weapon,
                    weapon_inventory=inventory,weapon_level=level,shadow=shadow,enhancement=upgrade,move_counts=moves,**model)


class TacticalMonitor(threading.Thread):
    def __init__(self, pid, path, npcs=()):
        super().__init__(daemon=True)
        self.pid, self.path = pid,path
        self.seed = npcs
        self.request = None
        self.latest = {}
        self.stop_event = threading.Event()

    def run(self):
        runtime = None
        try:
            runtime = RuntimeReader(self.pid,self.path)
            reader = TacticalReader(runtime)
            reader.npcs.update(self.seed)
            next_sample = 0
            while not self.stop_event.is_set():
                now = time.monotonic()
                try:
                    if self.request and now >= next_sample:
                        self.latest = reader.sample(*self.request)
                        next_sample = now+(.04 if self.latest.get('phase') else .1)
                    reader.scan_slice(now)
                except (OSError,ValueError,struct.error):
                    self.latest = {}
                self.stop_event.wait(.005)
        except Exception:
            self.latest={}
            logging.exception('战况读取线程退出')
        finally:
            if runtime:
                runtime.close()
