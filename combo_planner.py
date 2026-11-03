"""Select real, affordable move paths; tactical constraints precede variety."""
from dataclasses import dataclass
from weapon_policy import policy_for, affordable_health, next_move_safe, combat_art_ready, finite_cost


@dataclass(frozen=True)
class ComboPlan:
    path: str
    label: str
    mode: str
    reason: str
    cost: float
    reserve: float
    skill: bool=False


def label(path):
    lights=path.count('A');heavies=path.count('B')
    return (f'{lights}段普攻' if lights else '')+(' → ' if lights and heavies else '')+(f'{heavies}段杀气' if heavies else '')


def choose_combo(state,recent=(),hurt=False):
    data=state.tactics
    target=next((e for e in data.get('enemies',[]) if e['actor']==state.target),None)
    if not target or not data.get('move_model_ready'):return None
    hp=state.hp/state.hp_max
    policy=policy_for(data)
    ratio=state.shachi/state.shachi_max if state.shachi_max>0 else 0
    target_hp=target['hp']/target['hp_max']
    nearby=sum(e['distance']<=7 for e in data.get('enemies',[]))
    broken=target.get('shachi_ratio') is not None and target['shachi_ratio']<=.12
    reserve=state.shachi_max*(max(.35,policy.reserve) if nearby>=3 or hp<.55 else policy.reserve)
    if broken and nearby<=1 and hp>=.7:reserve=state.shachi_max*.2
    mode,reason='单敌连段','按资源选择完整派生，减少连续重复'
    limit=policy.max_chain
    if hp<.4 or hurt:
        mode,reason,limit='保命','刚受伤或血量偏低，缩短出招并保留恢复机会',1
    elif ratio<.3:
        mode,reason,limit='回气','保留防御所需杀气，使用普通攻击',2
    elif target_hp<=.16:
        mode,reason,limit='收尾','目标残血，节省武技和长连段',2
    elif nearby>=3:
        mode,reason,limit='群战','多人近身，短派生后重新评估威胁',3
    elif nearby==2:
        mode,reason,limit='双敌','缩短连续出招，轮换起手',4
    elif target['boss'] and not broken:
        mode,reason,limit='Boss 试探','没有明确破绽，短连段压制后观察',2
    elif broken:
        mode,reason='破势爆发','目标杀气接近耗尽，把资源用于连段与武技'
    moves=data.get('moves',{})
    # Each weapon supplies its own reachable paths, including shorter or longer chains.
    base=[p for p in moves if 1<=len(p)<=8 and set(p)<={'A','B'}]
    candidates=[]
    for path in base:
        if len(path)>limit:continue
        if nearby>=3 and path.count('B')>1:continue
        entries=[moves.get(path[:i]) for i in range(1,len(path)+1)]
        if any(not e or not e.get('available') or not finite_cost(e.get('cost')) for e in entries):continue
        if not affordable_health(entries,state,policy):continue
        if not next_move_safe(entries[0],state):continue
        cost=sum(e['cost'] for e in entries)
        if cost and state.shachi-cost<reserve:continue
        if mode in ('保命','回气') and 'B' in path:continue
        duration=sum(e.get('combo_open',.7) for e in entries)
        score=2.*len(path)+path.count('B')*policy.heavy_bonus
        score-=duration*policy.duration_penalty*.25
        if nearby>=2 or target['boss'] and not broken:score-=duration*2.5
        if mode=='收尾':score=-duration
        if mode=='破势爆发':score+=path.count('B')*2
        if mode=='单敌连段' and len(path)>=3:score+=4
        # Older choices decay; safety filtering above can still require repeating a short attack.
        score-=sum(30/(i+1) for i,p in enumerate(reversed(recent[-5:])) if p==path)
        candidates.append((score,path,cost))
    if not candidates:return None
    _,path,cost=max(candidates,key=lambda x:(x[0],x[1]))
    skill=combat_art_ready(state,hurt)
    return ComboPlan(path,label(path),mode,reason,cost,reserve,bool(skill))
