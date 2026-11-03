"""Read the native live bullet registry; never scan all objects in the combat loop."""
import math
import struct
from tactics_runtime import Reflection
from projectiles import incoming_time


class ProjectileReader:
    def __init__(self,runtime):
        self.r=runtime
        self.m=runtime.memory
        self.ref=Reflection(self.m)
        self.subsystems=[]

    def array(self,address,size,limit=512):
        h=self.m.read(address,16)
        if len(h)!=16:return []
        p,n,cap=struct.unpack('<Qii',h)
        if not 0<=n<=cap<=4096 or n>limit:return []
        data=self.m.read(p,n*size) if n else b''
        if len(data)!=n*size or self.m.read(address,16)!=h:return []
        return [data[i:i+size] for i in range(0,len(data),size)]

    def scalar(self,obj,field,default=None):
        f=self.ref.field(obj,field)
        raw=self.m.read(obj+f[0],4) if f and f[1]==4 else b''
        value=struct.unpack('<f',raw)[0] if len(raw)==4 else default
        return value if value is not None and math.isfinite(value) else default

    def vector(self,component):
        f=self.ref.field(component,'Velocity')
        raw=self.m.read(component+f[0],24) if f and f[1]==24 else b''
        if len(raw)!=24:return None
        v=struct.unpack('<3d',raw)
        return v if all(math.isfinite(x) and abs(x)<100000 for x in v) else None

    def sample(self,state):
        subsystem=next((s for s in self.subsystems if self.r.objects.valid_object(s)
                        and self.m.u64(s+0x20)==state.world),0)
        if not subsystem:return []
        player=self.r.position(state.player)
        if not player:return []
        movement=self.ref.pointer(state.player,'CharacterMovement')
        pv=self.vector(movement) if movement and self.r.objects.valid_object(movement) else None
        pv=pv or (0,0,0)
        root=self.m.u64(state.player+0x1b8)
        radius=self.scalar(root,'CapsuleRadius',42)+30
        half=max(radius,self.scalar(root,'CapsuleHalfHeight',96)+30)
        level=self.m.u64(state.player+0x20)
        settings=self.ref.pointer(level,'WorldSettings')
        gravity=self.scalar(settings,'WorldGravityZ',-980) if settings else -980
        threats=[];observed=[]
        for raw in self.array(subsystem+0x48,16):
            bullet=struct.unpack_from('<Q',raw)[0]
            if not self.r.objects.valid_object(bullet):continue
            f=self.ref.field(bullet,'BulletStatus')
            if not f or self.m.read(bullet+f[0],1)!=b'\x02':continue # EBulletStatus::Running
            move=self.ref.pointer(bullet,'SGameProjectileMovement')
            if not move or not self.r.objects.valid_object(move):continue
            if not self.m.u64(move+0xc0) or not self.m.u32(move+0x130)&0x20:continue
            owner=self.ref.pointer(bullet,'BulletOwnerAvatar')
            if owner==state.player or not self.r.objects.valid_object(owner):continue
            faction=self.ref.pointer(owner,'Faction Component')
            if not faction or not self.r.objects.valid_object(faction):continue
            if self.m.u64(faction+0xa8)!=owner:continue
            if self.ref.name(self.m.read(faction+0x134,8))!='Faction.Enemy':continue
            bullet_level=self.m.u64(bullet+0x20)
            if self.ref.pointer(bullet_level,'OwningWorld')!=state.world:continue
            position=self.r.position(bullet)
            velocity=self.vector(move)
            if not position or not velocity or math.dist(position,player)>7000:continue
            scale=self.scalar(move,'ProjectileGravityScale')
            if scale is None or not 0<=scale<=10:continue
            cls=self.m.u64(bullet+0x10)
            name=self.ref.name(self.m.read(cls+0x18,8))
            explosive=name=='BP_Bullet_FemaleThug_Throw_Molotov_C'
            eta=incoming_time(position,velocity,player,pv,gravity*scale,radius,half,explosive)
            if eta is None:continue
            index=self.m.u32(bullet+0xc);item=self.r.objects.item(index)
            serial=struct.unpack_from('<i',item,16)[0] if len(item)==24 else 0
            if serial<=0:continue
            threat=dict(id=f'projectile:{index}:{serial}',hit_time=state.game_time+eta,dodge=True,
                        attack_type='projectile',source=bullet,projectile=True,explosive=explosive)
            threats.append(threat)
            observed.append(dict(threat,position=position,velocity=velocity,distance=math.dist(position,player)/100,
                                 method='short_horizon_trajectory',class_name=name))
        state.diagnostics['projectiles']=observed
        return threats
