"""Weapon-specific preferences over observed, unlocked moves; costs always win."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class WeaponPolicy:
    label: str
    reserve: float=.30
    duration_penalty: float=1.0
    heavy_bonus: float=1.0
    max_chain: int=5
    audited_health: bool=False


POLICIES={
    'Weapon_Hun_Tang':WeaponPolicy('黑伤',.25,.65,1.5,7,True),
    'Weapon_Hun_Double':WeaponPolicy('黑伤双刃',.25,.85,.8,7,True),
    'Weapon_Hun_Long':WeaponPolicy('长刀',.30,1.25,1.9,5,True),
}


def policy_for(data):
    return POLICIES.get(data.get('weapon'),WeaponPolicy('通用适配'))


def finite_cost(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def health_cost(meta,policy):
    # Explicit unknown means unreadable/unsupported, not a free move.
    if 'health_cost' in meta:
        value=meta['health_cost']
        return value if finite_cost(value) else None
    return 0. if policy.audited_health else None


def affordable_health(entries,state,policy):
    costs=[health_cost(e,policy) for e in entries]
    total=sum(c for c in costs if c is not None)
    if total and state.hp-total<state.hp_max*.45:
        return False
    if any('health_cost' in e and c is None for e,c in zip(entries,costs)):
        return False
    if any(c is None for c in costs):
        # Unknown families may continue short ordinary attacks; spending attacks
        # need ample HP and cannot run long chains until their cost is resolved.
        return (all(not e.get('path','').endswith('B') for e in entries)
                or state.hp/state.hp_max>=.75 and len(entries)<=2)
    return True


def next_move_safe(meta,state):
    threats=[t['hit_time']-state.game_time for t in state.threats
             if math.isfinite(t.get('hit_time',float('nan'))) and t['hit_time']>=state.game_time-.05]
    if not threats:
        return True
    duration=meta.get('combo_open')
    if not finite_cost(duration):
        duration=.8
    # The combo opening is a conservative commitment estimate, not an assertion
    # of universal animation cancelability. Reevaluate on every follow-up.
    return min(threats)>max(.25,duration)+.16


def combat_art_decision(state,hurt=False,after_defense=False,heal_threshold=.4):
    from action_windows import can_start_offense
    data=state.tactics
    art=data.get('skills',{}).get('combat_art',{})
    policy=policy_for(data)
    charge,cost=art.get('charge'),art.get('cost')
    if not art:return False,'未识别当前武技'
    if not (type(charge) is int and type(cost) is int and 0<=charge<=100 and 1<=cost<=100):return False,'等待充能数据'
    if charge<cost:return False,f'充能不足 {charge}/{cost}'
    if not art.get('unlocked'):return False,'武技尚未解锁'
    if not art.get('cooldown_known'):return False,'武技冷却未就绪'
    if art.get('active'):return False,'武技进行中'
    if 'CharacterState.Space.Ground' not in (data.get('owned_tags') or []):return False,'等待落地'
    if not can_start_offense(data):return False,'等待武技衔接窗口'
    from move_runtime import tag_matches
    tags=data.get('owned_tags') or []
    if any(tag_matches(tags,t) for t in art.get('blocked_tags',[])):return False,'当前状态禁止武技'
    if not all(tag_matches(tags,t) for t in art.get('required_tags',[])):return False,'武技条件未满足'
    life=health_cost(art,policy)
    if life is None:return False,'武技生命代价待确认'
    if life and state.hp-life<state.hp_max*.55:return False,'保留生命余量'
    if state.hp/state.hp_max<max(.35,heal_threshold):return False,'优先恢复生命'
    if hurt:return False,'刚受伤，等待稳定'
    target=next((e for e in data.get('enemies',[]) if e['actor']==state.target),None)
    if not target:return False,'等待有效目标'
    if target['hp']/target['hp_max']<=.16:return False,'目标残血，保留充能'
    if not next_move_safe(dict(combo_open=1.2),state):return False,'等待来招空隙'
    nearby=sum(e['distance']<=7 for e in data.get('enemies',[]))
    broken=target.get('shachi_ratio') is not None and target['shachi_ratio']<=.12
    if nearby>=3 and state.hp/state.hp_max<.55:return False,'被围且血量偏低'
    if target['boss'] and not (broken or after_defense):return False,'等待 Boss 破绽'
    return True,'武技就绪'


def combat_art_ready(state,hurt=False,after_defense=False,heal_threshold=.4):
    return combat_art_decision(state,hurt,after_defense,heal_threshold)[0]
