"""Read equipped abilities and binding requirements, without a weapon allowlist."""
import math
import re
import struct
import time

WEAPONS={'Weapon_Hun_Tang':('Tang','黑伤'),'Weapon_Hun_Double':('Double','黑伤双刃')}
TANG_UPGRADES={'AAAAAA':'Combo_01','AAAAAB':'Combo_02','BBBB':'Combo_03'}


def weapon_family(weapon):
    return weapon.removeprefix('Weapon_Hun_') if weapon.startswith('Weapon_Hun_') else ''


def tag_matches(tags, query):
    return any(t==query or t.startswith(query+'.') for t in tags)


def move_path(name, ability_tags, family):
    if '_Air_' in name or any('.Air.' in t for t in ability_tags):return None,None
    match=re.fullmatch(r'Default__GA_Hun_'+re.escape(family)+r'_([AB]+)(?:_(Draw|Dash|DashDraw|Snap|New))?_C',name) if family else None
    if match:
        path,variant=match.groups()
        return path, None if variant=='New' else variant
    # Some weapons use Counter/CounterA class names for ordinary upgraded chains.
    for tag in ability_tags:
        tail=tag.removeprefix('Ability.Action.Hun.Light.')
        if tail!=tag and re.fullmatch(r'(?:\w+\.)?A+',tail):return tail.split('.')[-1],None
        match=re.fullmatch(r'Ability.Action.Hun.Heavy.Atk0([1-9])',tag)
        if match:return 'B'*int(match[1]),None
        match=re.fullmatch(r'Ability.Action.Hun.Heavy.ComboAtk_N([1-9])H([1-9])',tag)
        if match:return 'A'*int(match[1])+'B'*int(match[2]),None
        if tag=='Ability.Action.Hun.Light.A.Normal':return 'A',None
    return None,None


def binding_unlocked(ability_tags, rules, owned, previous_tags=None):
    """Only upgrade gates are static; combo/phase requirements are checked at input time."""
    relevant=[r for r in rules if r['trigger'] in ability_tags]
    if not relevant:return True
    if previous_tags is not None:
        reachable=[r for r in relevant if all(tag_matches(previous_tags,t) for t in r['required'] if t.startswith('Ability.Action.'))]
        if reachable:relevant=reachable
        elif any(any(t.startswith('CharacterState.WeaponUpgrade.') for t in r['required']) for r in relevant):return False
    return any(all(tag_matches(owned or [],t) for t in r['required'] if t.startswith('CharacterState.WeaponUpgrade.'))
               and not any(tag_matches(owned or [],t) for t in r['ignored'] if t.startswith('CharacterState.WeaponUpgrade.'))
               for r in relevant)


class MoveReader:
    def __init__(self,reader):
        self.reader=reader
        self.r,self.m,self.ref=reader.r,reader.m,reader.ref
        self.timing_cache={}
        self.weapon_cache={}
        self.model_generation={}
        self.equipment_identity=None

    def refresh_equipment(self,player,world,config,level,tags):
        upgrades=tuple(sorted(x for x in (tags or []) if x.startswith('CharacterState.WeaponUpgrade.')))
        identity=(player,world,config,level,upgrades)
        if identity!=self.equipment_identity:
            self.weapon_cache.clear();self.timing_cache.clear();self.model_generation.clear()
            self.equipment_identity=identity

    def weapon_model(self,config):
        if not config or not self.r.objects.valid_object(config):return None
        key=(config,self.ref.name(self.m.read(config+24,8)))
        if key in self.weapon_cache and time.monotonic()-self.model_generation.get(key,0)<2:
            return self.weapon_cache[key]
        abilities=set()
        for field in ('Equip Abilities','Owning Abilities'):
            f=self.ref.field(config,field)
            if not f:return None
            for raw in self.reader.array(config+f[0],8,256):abilities.add(struct.unpack('<Q',raw)[0])
        f=self.ref.field(config,'Weapon Combos')
        entries=self.r.sparse_map(config+f[0],24) if f else None
        if entries is None:return None
        rules=[]
        for _,value in entries:
            obj=struct.unpack('<Q',value)[0]
            if not self.r.objects.valid_object(obj):return None
            f=self.ref.field(obj,'Bindings')
            if not f:return None
            for block in self.reader.array(obj+f[0],152,64):
                for off in (24,40,56):
                    p,n,cap=struct.unpack_from('<Qii',block,off)
                    if not 0<=n<=cap<=128:return None
                    raw=self.m.read(p,n*96) if n else b''
                    if len(raw)!=n*96:return None
                    for at in range(0,len(raw),96):
                        row=raw[at:at+96]
                        def tags(offset):
                            p,n,c=struct.unpack_from('<Qii',row,offset)
                            if not 0<=n<=c<=128:return None
                            data=self.m.read(p,n*8) if n else b''
                            return [self.ref.name(data[i:i+8]) for i in range(0,len(data),8)] if len(data)==n*8 else None
                        required,ignored=tags(16),tags(48)
                        if required is None or ignored is None:return None
                        rules.append(dict(trigger=self.ref.name(row[80:88]),required=required,ignored=ignored,
                                          window={24:'actions',40:'combos',56:'interrupt'}[off]))
        result=dict(classes=abilities,rules=rules)
        if len(self.weapon_cache)>128:self.weapon_cache.clear()
        self.weapon_cache[key]=result
        self.model_generation[key]=time.monotonic()
        return result

    def number(self,obj,field):
        f=self.ref.field(obj,field)
        if not f or f[1] not in (4,8):return None
        raw=self.m.read(obj+f[0],f[1])
        if len(raw)!=f[1]:return None
        v=struct.unpack('<f' if f[1]==4 else '<d',raw)[0]
        return v if math.isfinite(v) else None

    def health_budget(self,obj,hp_max):
        """Read explicit cost properties; unfamiliar life-cost formulas stay unknown."""
        fields=self.ref.fields(self.m.u64(obj+16))
        suspicious=[k for k in fields if re.search(r'(?:health|hp|life|blood).*(?:cost|consum)|(?:cost|consum).*(?:health|hp|life|blood)',k,re.I)]
        if not suspicious:return {}
        # A property name alone proves neither its numeric type nor its units.
        # Until the formula is independently verified, suppress this move instead
        # of decoding an integer/ratio as an apparently tiny float cost.
        return dict(health_cost=None,health_cost_source='unverified_formula',health_cost_fields=suspicious)

    def owned_tags(self,player):
        asc=self.m.u64(player+0x750)
        if not self.r.objects.valid_object(asc):return None
        field=self.ref.field(asc,'GameplayTagCountContainer')
        if not field or field[1]!=240:return None
        base=asc+field[0]
        # Verified ExplicitTags TArray in the version-locked FGameplayTagCountContainer.
        if self.m.u64(base+0xe8)!=asc:return None
        h=self.m.read(base+0xc8,16)
        if len(h)!=16:return None
        p,n,cap=struct.unpack('<Qii',h)
        if not 0<=n<=cap<=1024:return None
        raw=self.m.read(p,n*8) if n else b''
        if len(raw)!=n*8 or self.m.read(base+0xc8,16)!=h:return None
        tags=[self.ref.name(raw[i:i+8]) for i in range(0,len(raw),8)]
        return tags if all(tags) else None

    def timing(self,instance):
        main=self.ref.pointer(instance,'Main')
        if not main or not self.r.objects.valid_object(main):return {}
        key=(main,self.m.u64(main+0x10))
        if key in self.timing_cache:return self.timing_cache[key]
        field=self.ref.field(main,'Actions')
        if not field:return {}
        inner=self.m.u64(field[2]+0x78)
        if self.m.u32(inner+0x34)!=64:return {}
        for raw in self.reader.array(main+field[0],64,128):
            action=struct.unpack_from('<Q',raw,16)[0]
            if not self.r.objects.valid_object(action):continue
            start=self.number(action,'StartDuration')
            act=self.number(action,'ActDuration')
            combo=self.number(action,'ComboDuration')
            if any(v is None or not 0<=v<=15 for v in (start,act,combo)):continue
            offset=struct.unpack_from('<f',raw)[0]
            if not math.isfinite(offset) or not 0<=offset<=10:continue
            result=dict(combo_open=offset+start+act,combo_close=offset+start+act+combo)
            if len(self.timing_cache)>512:self.timing_cache.clear()
            self.timing_cache[key]=result
            return result
        return {}

    def snapshot(self,player,weapon,shachi_max,config=0,hp_max=0):
        tags=self.owned_tags(player)
        result=dict(weapon_label=WEAPONS.get(weapon,('',weapon))[1],owned_tags=tags,
                    moves={},move_events={},active_moves=[],move_model_ready=False)
        family=weapon_family(weapon)
        model=self.weapon_model(config) if config else None
        if not family and model is None:return result
        asc=self.m.u64(player+0x750)
        if not self.r.objects.valid_object(asc):return result
        for raw in self.reader.array(asc+0x520,240,512):
            cdo=struct.unpack_from('<Q',raw,16)[0]
            name=self.ref.name(self.m.read(cdo+24,8))
            if raw[0x29]&4:continue
            if model is not None and self.m.u64(cdo+16) not in model['classes']:continue
            if model is None and not name.startswith('Default__GA_Hun_'+family+'_'):continue
            ability_tags=self.reader.tags(cdo+0xa8) if model is not None else []
            path,variant=move_path(name,ability_tags,family)
            if not path:continue
            instances=self.reader.pointer_array_bytes(raw[0x78:0x88])+self.reader.pointer_array_bytes(raw[0x88:0x98])
            instances=[p for p in instances if self.r.objects.valid_object(p) and self.m.u64(p+16)==self.m.u64(cdo+16)]
            if len(instances)!=1:continue
            ins=instances[0]
            count_raw=self.m.read(ins+0x4b8,4)
            if len(count_raw)!=4:continue
            count=struct.unpack('<I',count_raw)[0]
            meta=dict(path=path,name=name,instance=ins,count=count,active=bool(raw[0x28]),**self.timing(ins))
            meta.update(self.health_budget(ins,hp_max))
            if path.endswith('B'):
                cost=self.number(ins,'Shachi Cost')
                mul=self.number(ins,'Shachi Cost Mul')
                minimum=self.number(ins,'Shachi Cost Min')
                actual=self.number(ins,'Actual Shachi Consumed')
                # Conservative estimate corroborated by actual 82-point consumption for Tang.
                estimate=max(cost+mul*shachi_max,minimum) if all(v is not None and v>=0 for v in (cost,mul,minimum)) else None
                meta['cost']=max(estimate,actual or 0) if estimate is not None else None
            else:meta['cost']=0.
            # Native bindings are authoritative, including upgraded families.
            # The legacy Tang gate is only a fallback if bindings are unreadable.
            upgrade=TANG_UPGRADES.get(path) if family=='Tang' and model is None else None
            meta['available']=not upgrade or tags is not None and 'CharacterState.WeaponUpgrade.Tang.'+upgrade in tags
            if model is not None:
                meta['available'] &= binding_unlocked(ability_tags,model['rules'],tags)
                meta['ability_tags']=ability_tags
                meta['blocked_tags']=self.reader.tags(cdo+0x240)
                meta['required_tags']=self.reader.tags(cdo+0x220)
            elif weapon not in WEAPONS:
                # Missing binding data must not unlock unknown heavy/upgrade chains.
                meta['available']=path=='A'
            result['move_events'][name]=meta
            if meta['active']:result['active_moves'].append(name)
            if not variant:
                if path in result['moves']:
                    old=result['moves'][path]
                    if meta['available'] and not old['available']:result['moves'][path]=meta
                    elif meta['available'] and old['available']:old['available']=False
                else:result['moves'][path]=meta
        if model is not None:
            for path,meta in result['moves'].items():
                previous=result['moves'].get(path[:-1],{}).get('ability_tags',[])
                meta['available'] &= binding_unlocked(meta['ability_tags'],model['rules'],tags,previous)
        result['move_model_ready']=bool(result['moves'].get('A') and tags is not None)
        return result
