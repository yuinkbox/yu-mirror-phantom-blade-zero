"""Verified Tang level-five stance + heavy enhancement."""
import struct
from move_runtime import tag_matches


def active_effects(reader,player):
    m,ref=reader.m,reader.ref;asc=m.u64(player+0x750)
    if not reader.r.objects.valid_object(asc):return None
    f=ref.field(asc,'ActiveGameplayEffects')
    if not f or f[1]!=768:return None
    struct_type=m.u64(f[2]+0x70);items=ref.fields(struct_type).get('GameplayEffects_Internal')
    if not items:return None
    inner=m.u64(items[2]+0x78)
    if m.u32(inner+0x34)!=872:return None
    address=asc+f[0]+items[0];header=m.read(address,16)
    if len(header)!=16:return None
    ptr,n,cap=struct.unpack('<Qii',header)
    if not 0<=n<=cap<=4096 or n>512:return None
    rows=reader.array(address,872,512)
    if len(rows)!=n:return None
    result=[]
    for raw in rows:
        if raw[732]:continue
        obj=struct.unpack_from('<Q',raw,24)[0]
        name=ref.name(m.read(obj+24,8))
        if not name:return None
        result.append(name)
    return result


def enhancement(reader,player,weapon):
    if weapon!='Weapon_Hun_Tang':return {}
    m,ref=reader.m,reader.ref;asc=m.u64(player+0x750)
    tags=reader.move_reader.owned_tags(player)
    if tags is None:return {}
    effects=active_effects(reader,player)
    for raw in reader.array(asc+0x520,240,512):
        cdo=struct.unpack_from('<Q',raw,16)[0]
        if raw[41]&4 or ref.name(m.read(cdo+24,8))!='Default__GA_Hun_Tang_Buff_C':continue
        ins=reader.pointer_array_bytes(raw[0x78:0x88])+reader.pointer_array_bytes(raw[0x88:0x98])
        ins=[p for p in ins if reader.r.objects.valid_object(p) and m.u64(p+16)==m.u64(cdo+16)]
        if len(ins)!=1:return {}
        p=ins[0];count=m.read(p+0x4b8,4);f=ref.field(p,'Cooldown Data')
        return dict(name='黑伤强化',unlocked=tag_matches(tags,'CharacterState.WeaponUpgrade.Tang.CanBuff'),
            instance=p,activation=struct.unpack('<I',count)[0] if len(count)==4 else None,active=bool(raw[40]),
            buff_active=None if effects is None else any(e.startswith('Default__GE_Weapon_Tang_Buff_Lv') for e in effects),
            cooldown_known=bool(f and m.read(p+f[0],8)==bytes(8) and m.read(p+0x1b8,8)==bytes(8)),
            required_tags=reader.tags(cdo+0x220),blocked_tags=reader.tags(cdo+0x240),
            **reader.move_reader.timing(p))
    return {}
