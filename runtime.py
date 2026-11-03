"""Build-specific, read-only UE object access. No remote calls or process writes."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import struct
import time

k = C.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.CloseHandle.argtypes = [W.HANDLE]
k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k.CreateToolhelp32Snapshot.restype = W.HANDLE
k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]

BUILD_SHA256 = '7b4fcd286a350780faf0824d73aa150b21cbf1bccde883e6a96386cbe09973b4'
OBJECTS_RVA = 0xddb3c58
OBJECT_COUNT_RVA = 0xddb3c60
CLASS_SLOTS = {'telemetry': 0xe00c840, 'deflect': 0xdffa2e0,
               'player': 0xe012178, 'npc': 0xe011d08,
               'targeting': 0xdff62d0, 'backpack': 0xe019f88, 'execution': 0xe045b90,
               'duration_bullet': 0xdffa2f8, 'burst_bullet': 0xe054e70}

ATTACK_TYPES = {0: 'none', 1: 'normal', 2: 'blue', 3: 'red', 4: 'power', 5: 'hold'}


class ModuleEntry(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('th32ModuleID', W.DWORD), ('th32ProcessID', W.DWORD),
                ('GlblcntUsage', W.DWORD), ('ProccntUsage', W.DWORD), ('modBaseAddr', C.c_void_p),
                ('modBaseSize', W.DWORD), ('hModule', W.HMODULE), ('szModule', W.WCHAR*256),
                ('szExePath', W.WCHAR*260)]


class Memory:
    def __init__(self, pid, expected_path):
        self.handle = None
        self.pid = pid
        self.handle = k.OpenProcess(0x410, False, pid)
        if not self.handle:
            raise OSError('无法只读访问游戏进程：'+str(C.WinError(C.get_last_error())))
        self.started_at = None
        times = [C.c_ulonglong() for _ in range(4)]
        k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(C.c_ulonglong)]*4
        if k.GetProcessTimes(self.handle, *(C.byref(t) for t in times)):
            self.started_at = times[0].value/10_000_000-11_644_473_600
        snapshot = k.CreateToolhelp32Snapshot(0x18, pid)
        try:
            if snapshot == C.c_void_p(-1).value:
                raise C.WinError(C.get_last_error())
            entry = ModuleEntry()
            entry.dwSize = C.sizeof(entry)
            k.Module32FirstW.argtypes = [W.HANDLE, C.POINTER(ModuleEntry)]
            if not k.Module32FirstW(snapshot, C.byref(entry)):
                raise C.WinError(C.get_last_error())
            if Path(entry.szExePath).resolve() != Path(expected_path).resolve():
                raise ValueError('游戏进程路径不匹配')
            self.base = entry.modBaseAddr
            self.size = entry.modBaseSize
        except Exception:
            self.close()
            raise
        finally:
            if snapshot and snapshot != C.c_void_p(-1).value:
                k.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            k.CloseHandle(self.handle)
            self.handle = None

    def read(self, address, size):
        if size < 0 or size > 16*1024**2 or address < 0x10000 or address >= 0x7fffffffffff:
            return b''
        data = C.create_string_buffer(size)
        count = C.c_size_t()
        k.ReadProcessMemory(self.handle, C.c_void_p(address), data, size, C.byref(count))
        return data.raw[:count.value]

    def u64(self, address):
        raw = self.read(address, 8)
        return struct.unpack('<Q', raw)[0] if len(raw) == 8 else 0

    def u32(self, address):
        raw = self.read(address, 4)
        return struct.unpack('<I', raw)[0] if len(raw) == 4 else 0


@dataclass
class RuntimeState:
    valid: bool = False
    reason: str = '等待运行时数据'
    component: int = 0
    player: int = 0
    controller: int = 0
    world: int = 0
    game_time: float = 0
    hp: float = 0
    hp_max: float = 0
    shachi: float = 0
    shachi_max: float = 0
    deflect_component: int = 0
    defense_consistent: bool = True
    community_defense: bool = False
    medicine: int | None = None
    target: int = 0
    target_distance: float | None = None
    threats: list = field(default_factory=list)
    execution_ready: bool = False
    execution_target: int = 0
    diagnostics: dict = field(default_factory=dict)
    tactics: dict = field(default_factory=dict)
    input_blocked: bool = True


class ObjectReader:
    def __init__(self, memory):
        self.mem = memory
        self.base = memory.base
        self.table = 0
        self.count = 0
        self.refresh()

    def refresh(self):
        self.table = self.mem.u64(self.base+OBJECTS_RVA)
        self.count = self.mem.u32(self.base+OBJECT_COUNT_RVA)
        if self.table < 0x10000 or not 1000 < self.count < 4_000_000:
            raise ValueError('对象表尚未就绪或游戏构建不匹配')

    def item(self, index):
        if not 0 <= index < self.count:
            return b''
        chunk = self.mem.u64(self.table+(index//65536)*8)
        return self.mem.read(chunk+(index % 65536)*24, 24)

    def resolve(self, raw):
        if len(raw) != 8:
            return 0
        index, serial = struct.unpack('<ii', raw)
        if serial <= 0:
            return 0
        item = self.item(index)
        if len(item) != 24 or struct.unpack_from('<i', item, 16)[0] != serial:
            return 0
        flags = struct.unpack_from('<I', item, 4)[0]
        if flags & 0x10200000:
            return 0
        return struct.unpack_from('<Q', item, 8)[0]

    def valid_object(self, obj):
        data = self.mem.read(obj, 40)
        if len(data) != 40:
            return False
        vt, flags, index = struct.unpack_from('<QIi', data)
        if not self.base <= vt < self.base+self.mem.size or flags & (0x10|0x20|0x40000000):
            return False
        item = self.item(index)
        return len(item) == 24 and struct.unpack_from('<Q', item, 8)[0] == obj and not struct.unpack_from('<I', item, 4)[0] & 0x10200000

    def derives(self, cls, parent):
        for _ in range(32):
            if cls == parent:
                return True
            if not cls:
                break
            cls = self.mem.u64(cls+0x40)
        return False

    def objects(self, stop=lambda: False):
        for start in range(0, self.count, 65536):
            if stop():
                return
            chunk = self.mem.u64(self.table+(start//65536)*8)
            data = self.mem.read(chunk, min(65536, self.count-start)*24)
            for at in range(0, len(data)-23, 24):
                flags = struct.unpack_from('<I', data, at+4)[0]
                obj = struct.unpack_from('<Q', data, at+8)[0]
                if obj > 0x10000 and not flags & 0x10200000:
                    yield obj


class RuntimeReader:
    def __init__(self, pid, path):
        self.memory = Memory(pid, path)
        try:
            self.objects = ObjectReader(self.memory)
        except Exception:
            self.memory.close()
            raise
        self.classes = {name: self.memory.u64(self.memory.base+rva) for name, rva in CLASS_SLOTS.items()}
        self.component = 0
        self.deflects = []
        self.targeting = []
        self.executions = []
        self.backpack = 0
        self.last_discovery = 0
        self.npcs = []
        from projectile_runtime import ProjectileReader
        self.projectiles = ProjectileReader(self)

    def close(self):
        self.memory.close()

    def discover(self, stop=lambda: False):
        self.objects.refresh()
        candidates = []
        deflects = []
        targeting = []
        backpacks = []
        executions = []
        npcs = []
        bullet_subsystems = []
        class_cache = {}
        for obj in self.objects.objects(stop):
            data = self.memory.read(obj, 40)
            if len(data) != 40:
                continue
            flags = struct.unpack_from('<I', data, 8)[0]
            if flags & (0x10|0x20|0x40000000):
                continue
            cls = struct.unpack_from('<Q', data, 16)[0]
            if cls not in class_cache:
                class_cache[cls] = next((name for name in ('telemetry', 'deflect', 'targeting', 'backpack', 'execution', 'npc')
                                        if self.objects.derives(cls, self.classes[name])), None)
                if class_cache[cls] is None and self.projectiles.ref.name(self.memory.read(cls+0x18,8))=='BulletSubsystem':
                    class_cache[cls]='bullet_subsystem'
            kind = class_cache[cls]
            if kind == 'telemetry':
                state = self.read_component(obj)
                if state.valid:
                    candidates.append(obj)
            elif kind == 'deflect':
                deflects.append(obj)
            elif kind == 'targeting':
                targeting.append(obj)
            elif kind == 'backpack':
                backpacks.append(obj)
            elif kind == 'execution':
                executions.append(obj)
            elif kind == 'npc':
                npcs.append(obj)
            elif kind == 'bullet_subsystem':
                bullet_subsystems.append(obj)
        self.component = candidates[0] if len(candidates) == 1 else 0
        self.deflects = deflects
        self.targeting = targeting
        self.executions = executions
        self.backpack = backpacks[0] if len(backpacks) == 1 else 0
        self.last_discovery = time.monotonic()
        self.npcs = npcs
        self.projectiles.subsystems = bullet_subsystems
        return len(candidates)

    def read_component(self, obj):
        mem = self.memory
        data = mem.read(obj, 0x248)
        if len(data) != 0x248 or not self.objects.valid_object(obj):
            return RuntimeState(reason='角色组件失效')
        if not self.objects.derives(struct.unpack_from('<Q', data, 16)[0], self.classes['telemetry']):
            return RuntimeState(reason='角色组件类型变化')
        hp, max_hp, shachi, max_shachi = struct.unpack_from('<ffff', data, 0x1a0)
        if not all(math.isfinite(v) for v in (hp, max_hp, shachi, max_shachi)) or not 0 < max_hp <= 1e8 or not 0 <= hp <= max_hp*1.01 or not 0 < max_shachi <= 1e8:
            return RuntimeState(reason='角色属性无效')
        controller = struct.unpack_from('<Q', data, 0xe8)[0]
        player = self.objects.resolve(data[0xf0:0xf8])
        world = struct.unpack_from('<Q', data, 0xb0)[0]
        if (not player or not controller or not world or not self.objects.valid_object(player)
                or not self.objects.derives(mem.u64(player+0x10), self.classes['player'])):
            return RuntimeState(reason='玩家未附着或正在切换场景')
        raw_time = mem.read(world+0x848, 8)
        game_time = struct.unpack('<d', raw_time)[0] if len(raw_time) == 8 else float('nan')
        if not math.isfinite(game_time) or game_time < 0:
            return RuntimeState(reason='场景时间不可用')
        # AController native ignore counters (+341/+342) and reflected cursor bit (+554).
        controls=mem.read(controller+0x341,2)
        cursor=mem.read(controller+0x554,1)
        blocked=(len(controls)!=2 or len(cursor)!=1 or any(controls) or bool(cursor[0]&1))
        return RuntimeState(valid=True, reason='已读取角色属性', component=obj, player=player,
                            controller=controller, world=world, game_time=game_time,
                            hp=hp, hp_max=max_hp, shachi=shachi, shachi_max=max_shachi,input_blocked=blocked)

    def sample(self):
        self.objects.refresh()
        if not self.component:
            return RuntimeState(reason='尚未找到唯一玩家组件')
        state = self.read_component(self.component)
        if not state.valid:
            self.component = 0
            return state
        for component in self.deflects:
            if self.memory.u64(component+0xa8) == state.player and self.objects.valid_object(component):
                state.deflect_component = component
                break
        if state.deflect_component:
            entries = self.sparse_map(state.deflect_component+0x110, 0x60)
            if entries is None:
                state.defense_consistent = False
            observed = []
            for key, frame in entries or []:
                # FDeflectReceiveFrame, verified from property metadata and native selector.
                hit_time = struct.unpack_from('<d', frame, 0x30)[0]
                if not math.isfinite(hit_time):
                    continue
                if -.15 <= hit_time-state.game_time <= 1.5:
                    attack_type, source = self.attack_type(frame[:8])
                    threat = {'id': key, 'hit_time': hit_time, 'dodge': bool(frame[0x38]),
                              'attack_type': attack_type, 'source': source}
                    observed.append(dict(threat, hit=bool(frame[0x39]), paused=bool(frame[0x48])))
                    if not frame[0x39] and not frame[0x48] and hit_time-state.game_time >= -.08:
                        state.threats.append(threat)
            state.diagnostics['defense_frames'] = observed
            # FDeflectResultData is an 88-byte struct, NOT an array header.
            result = self.memory.read(state.deflect_component+0x1c8, 0x58)
            if len(result) == 0x58:
                state.diagnostics['dodge_result'] = {'raw':result.hex()}
                native=self.decode_defense_result(result)
                if native:state.diagnostics['native_defense_result']=native
        for component in self.targeting:
            if self.memory.u64(component+0xa8) != state.player or not self.objects.valid_object(component):
                continue
            targetable = self.memory.u64(component+0x1e8)
            if targetable and self.objects.valid_object(targetable):
                target = self.memory.u64(targetable+0x20)
                if self.objects.valid_object(target) and self.objects.derives(self.memory.u64(target+0x10), self.classes['npc']):
                    state.target = target
                    a, b = self.position(state.player), self.position(target)
                    if a and b:
                        state.target_distance = math.dist(a, b)/100.
            break
        state.medicine = self.medicine_count()
        state.threats.extend(self.projectiles.sample(state))
        for component in self.executions:
            if self.memory.u64(component+0xa8) != state.player or not self.objects.valid_object(component):
                continue
            entries = self.sparse_map(component+0x2b8, 0x68)
            if entries is not None:
                for key, point in entries:
                    socket = struct.unpack_from('<Q', point)[0]
                    if self.objects.valid_object(socket):
                        actor = self.memory.u64(socket+0xa8)
                        a,b = self.position(state.player),self.position(actor)
                        if actor != state.player and self.objects.valid_object(actor) and a and b and math.dist(a,b)<500:
                            state.execution_ready = True
                            state.execution_target = actor
                            break
            state.diagnostics['execution_component'] = component
            break
        return state

    def attack_type(self, source_weak):
        """Class-check before reading either action layout; unknown retains generic defense."""
        source = self.objects.resolve(source_weak)
        if not source or not self.objects.valid_object(source):
            return 'unknown', 0
        cls = self.memory.u64(source+0x10)
        for name, offset in (('duration_bullet', 0x100), ('burst_bullet', 0x108)):
            parent = self.classes.get(name, 0)
            if parent and self.objects.derives(cls, parent):
                raw = self.memory.read(source+offset, 1)
                return ATTACK_TYPES.get(raw[0], 'unknown') if raw else 'unknown', source
        # Other reflected native/Blueprint action classes can emit receive frames too.
        ref=self.projectiles.ref
        for field in ('DeflectBulletType','DeflectType'):
            f=ref.field(source,field)
            if f and f[1]==1:
                raw=self.memory.read(source+f[0],1)
                if raw:return ATTACK_TYPES.get(raw[0],'unknown'),source
        return 'unknown', source

    def decode_defense_result(self,raw):
        if len(raw)!=88:return None
        source=self.objects.resolve(raw[:8])
        defender=struct.unpack_from('<Q',raw,32)[0]
        hit_time=struct.unpack_from('<f',raw,68)[0]
        result=self.projectiles.ref.name(raw[56:64])
        if not source or not math.isfinite(hit_time) or not result.startswith('Deflect.Result.'):return None
        return dict(source=source,defender=defender,hit_time=hit_time,result=result)

    def position(self, actor):
        root = self.memory.u64(actor+0x1b8)
        data = self.memory.read(root+0x210, 24)
        if len(data) != 24:
            return None
        pos = struct.unpack('<3d', data)
        return pos if all(math.isfinite(v) and abs(v) < 1e9 for v in pos) else None

    def sparse_map(self, address, stride):
        """Read a TSparseArray snapshot, excluding vacant/recycled entries."""
        h = self.memory.read(address, 0x50)
        if len(h) != 0x50:
            return None
        ptr, num, cap = struct.unpack_from('<Qii', h)
        bits_num = struct.unpack_from('<i', h, 0x28)[0]
        free_count = struct.unpack_from('<i', h, 0x34)[0]
        if not (0 <= num <= cap <= 4096 and bits_num == num and 0 <= free_count <= num):
            return None
        if num == 0:
            return []
        bit_ptr = struct.unpack_from('<Q', h, 0x20)[0]
        bit_size = (bits_num+31)//32*4
        bits_data = self.memory.read(bit_ptr, bit_size) if bit_ptr else h[0x10:0x20][:bit_size]
        data = self.memory.read(ptr, num*stride)
        if len(data) != num*stride or len(bits_data) != bit_size:
            return None
        bits = int.from_bytes(bits_data, 'little') & ((1 << num)-1)
        if bits.bit_count() != num-free_count:
            return None
        # The container can grow on the game thread. Discard a torn snapshot.
        if self.memory.read(address, 0x38) != h[:0x38]:
            return None
        entries = []
        for index in range(num):
            if bits & (1 << index):
                entry = data[index*stride:(index+1)*stride]
                entries.append((struct.unpack_from('<i', entry)[0], entry[8:stride-8]))
        return entries

    def medicine_count(self):
        if not self.backpack or not self.objects.valid_object(self.backpack):
            return None
        entries = self.sparse_map(self.backpack+0x620, 24)
        if entries is None:
            return None
        # The selected medicine FName resides at +0x610 (verified against map key).
        key = self.memory.u32(self.backpack+0x610)
        for item_key, value in entries:
            if item_key == key:
                ptr = struct.unpack('<Q', value)[0]
                data = self.memory.read(ptr+0x50, 8)
                if len(data) != 8 or not self.objects.valid_object(ptr):
                    return None
                normal, extra = struct.unpack('<ii', data)
                if 0 <= normal <= 9999 and 0 <= extra <= 9999:
                    return normal+extra
        return 0


def verify_build(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest() == BUILD_SHA256
