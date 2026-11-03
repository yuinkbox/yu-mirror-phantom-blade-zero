"""Read the equipped shadow arm (any Shaqi family), SpiritShadow and charge stages."""
import math
import struct
from combat_resources import attribute
from move_runtime import tag_matches

SHADOW_LABELS={'BrutalAxe':'巨凶斧'}


def shadow_family(weapon):
    """Weapon_BrutalAxe -> BrutalAxe; ability classes and event tags share it."""
    return weapon.removeprefix('Weapon_') if weapon.startswith('Weapon_') else ''


class ShadowReader:
    def __init__(self,reader):
        self.t=reader;self.r=reader.r;self.m=reader.m;self.ref=reader.ref
        self.cache={}

    def shield(self,actor):
        tags=self.t.move_reader.owned_tags(actor)
        # Live tags disappear after shield break; the enemy class never changes.
        has=None if tags is None else tag_matches(tags,'CharacterState.AbilityState.Shield.HasShield')
        return dict(shield_present=has,shield=attribute(self.t,actor,'Shield'),
                    shield_max=attribute(self.t,actor,'Shield_Max'))

    def integer(self,obj,key):
        f=self.ref.field(obj,key)
        raw=self.m.read(obj+f[0],4) if f and f[1]==4 else b''
        return struct.unpack('<i',raw)[0] if len(raw)==4 else None

    def timing(self,ins,family):
        main=self.ref.pointer(ins,'Main')
        key=(main,self.m.u64(main+16))
        if key in self.cache:return self.cache[key]
        if not self.r.objects.valid_object(main):return {}
        f=self.ref.field(main,'Actions')
        if not f or self.m.u32(self.m.u64(f[2]+0x78)+0x34)!=64:return {}
        gain=[];minimum=None
        for raw in self.t.array(main+f[0],64,128):
            at=struct.unpack_from('<f',raw)[0];obj=struct.unpack_from('<Q',raw,16)[0]
            if not math.isfinite(at) or not 0<at<5 or not self.r.objects.valid_object(obj):continue
            ev=self.ref.field(obj,'Event Tag')
            tag=self.ref.name(self.m.read(obj+ev[0],8)) if ev else ''
            if tag=='GameplayEvent.Ability.Action.'+family+'.GainCharge':gain.append(at)
            if tag=='GameplayEvent.Ability.Action.'+family+'.MinDuration':minimum=at
        gain=sorted(set(gain))
        if len(gain)!=2 or minimum is None or not minimum<gain[0]<gain[1]<4:return {}
        result=dict(charge_times=gain,min_duration=minimum)
        if len(self.cache)>64:self.cache.clear()
        self.cache[key]=result
        return result

    def sample(self,player):
        inv=self.t.weapon_inventory(player,'Weapon.Slot.Shachi')
        if not inv:return {}
        weapon=inv['weapon'];config=inv['config'];family=shadow_family(weapon)
        result=dict(weapon=weapon,family=family,config=config,index=inv['index'],supported=False,
                    label=SHADOW_LABELS.get(family,family or weapon),
                    ammo=attribute(self.t,player,'SpiritShadow'),ammo_max=attribute(self.t,player,'SpiritShadow_Max'))
        if not family:return result
        asc=self.m.u64(player+0x750)
        model=self.t.move_reader.weapon_model(config)
        if not model or not self.r.objects.valid_object(asc):return result
        prefix='Default__GA_'+family+'_'
        matches=[]
        for raw in self.t.array(asc+0x520,240,512):
            cdo=struct.unpack_from('<Q',raw,16)[0]
            name=self.ref.name(self.m.read(cdo+24,8))
            if raw[0x29]&4 or not name.startswith(prefix):continue
            if self.m.u64(cdo+16) not in model['classes']:continue
            instances=self.t.pointer_array_bytes(raw[0x78:0x88])+self.t.pointer_array_bytes(raw[0x88:0x98])
            instances=[p for p in instances if self.r.objects.valid_object(p) and self.m.u64(p+16)==self.m.u64(cdo+16)]
            if len(instances)!=1:continue
            ins=instances[0];f=self.ref.field(ins,'Cooldown Data')
            cd=self.m.read(ins+f[0],8) if f else b''
            timing=self.timing(ins,family)
            # Get Modified Strength Bullet Instant Cost: PowerWeapon accessory
            # selects Clamp(raw + 1, 0, 99); otherwise the raw cost is used.
            tags=self.t.move_reader.owned_tags(player)
            cost=self.t.move_reader.number(ins,'Bullet Instant Cost')
            if tags is None:cost=None
            elif cost is not None and tag_matches(tags,'CharacterState.Accessory.PowerWeapon'):
                cost=min(99,max(0,cost+1))
            matches.append(dict(prefer=name.endswith('_Normal_C'),
                instance=ins,active=bool(raw[0x28]),activation=self.integer(ins,'ActivationCount'),
                charge_stage=self.integer(ins,'Current Charge'),cost=cost,
                cooldown_known=cd==bytes(8) and self.m.read(ins+0x1b8,8)==bytes(8),
                required_tags=self.t.tags(cdo+0x220),blocked_tags=self.t.tags(cdo+0x240),
                combo_bindings=[r for r in model['rules'] if r.get('window')=='combos'
                    and r['trigger']=='Ability.Action.Hun.ShachiAbility.'+family+'.Main'],**timing))
        after=self.t.weapon_inventory(player,'Weapon.Slot.Shachi')
        if (config,inv['index'])!=(after.get('config'),after.get('index')):return {}
        preferred=[m for m in matches if m['prefer']] or matches
        if len(preferred)==1:
            chosen=dict(preferred[0]);chosen.pop('prefer')
            result.update(chosen)
            # Charge staging needs the full timing table; everything else uses a
            # single activation whose cost still has to be readable.
            result['supported']=bool(result.get('charge_times')) or (
                not result.get('charge_times') and result.get('cost') is not None)
        return result
