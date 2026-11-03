import struct
from types import SimpleNamespace
from test_runtime import FakeMemory
from projectile_runtime import ProjectileReader


def fixture():
    m=FakeMemory();m.u32=lambda p:int.from_bytes(m.read(p,4),'little')
    player=10000;bullet=20000;owner=30000;faction=40000;move=50000;system=60000;level=70000;world=80000;cls=90000
    def q(p,v):m.put(p,struct.pack('<Q',v))
    q(system+32,world);m.put(bullet+0x3c0,b'\x02');q(move+0xc0,1);m.put(move+0x130,struct.pack('<I',0x20))
    q(faction+0xa8,owner);m.put(faction+0x134,b'EnemyTag');q(bullet+32,level);q(bullet+16,cls)
    m.put(bullet+12,struct.pack('<I',7));m.put(cls+24,b'ClassTag')
    pointers={(bullet,'SGameProjectileMovement'):move,(bullet,'BulletOwnerAvatar'):owner,
              (owner,'Faction Component'):faction,(level,'OwningWorld'):world}
    ref=SimpleNamespace(pointer=lambda o,k:pointers.get((o,k),0),
                        field=lambda o,k:(0x3c0,1,0) if o==bullet and k=='BulletStatus' else None,
                        name=lambda raw:'Faction.Enemy' if raw==b'EnemyTag' else 'BP_Arrow_C')
    item=bytearray(24);struct.pack_into('<i',item,16,99)
    positions={player:(0,0,0),bullet:(1000,0,0)}
    objects=SimpleNamespace(valid_object=lambda o:o in (player,bullet,owner,faction,move,system),item=lambda _:item)
    r=SimpleNamespace(memory=m,objects=objects,position=lambda o:positions.get(o))
    p=ProjectileReader.__new__(ProjectileReader);p.r=r;p.m=m;p.ref=ref;p.subsystems=[system]
    p.array=lambda *a:[struct.pack('<QQ',bullet,123)]
    p.scalar=lambda o,k,default=None:0 if k=='ProjectileGravityScale' else default
    p.vector=lambda o:(-2000,0,0) if o==move else None
    s=SimpleNamespace(player=player,world=world,game_time=100,diagnostics={})
    return p,s,m,pointers,positions


def test_live_registry_adapter_generates_stable_projectile_identity():
    p,s,_,_,_=fixture();t=p.sample(s)
    assert len(t)==1 and t[0]['id']=='projectile:7:99' and t[0]['dodge']
    assert 100.4<t[0]['hit_time']<100.5
    assert s.diagnostics['projectiles'][0]['source']==20000


def test_own_projectile_is_never_treated_as_hostile():
    p,s,_,pointers,_=fixture();pointers[(20000,'BulletOwnerAvatar')]=s.player
    assert p.sample(s)==[]


def test_stopped_or_cross_world_bullet_is_excluded():
    p,s,m,_,_=fixture();m.put(20000+0x3c0,b'\x03')
    assert p.sample(s)==[]
    p,s,_,pointers,_=fixture();pointers[(70000,'OwningWorld')]=90001
    assert p.sample(s)==[]


def test_inactive_movement_is_not_a_flying_threat():
    p,s,m,_,_=fixture();m.put(50000+0xc0,bytes(8))
    assert p.sample(s)==[]


def test_projectile_data_that_misses_player_does_not_become_defense_input():
    p,s,_,_,pos=fixture();pos[20000]=(1000,500,0)
    assert p.sample(s)==[]
