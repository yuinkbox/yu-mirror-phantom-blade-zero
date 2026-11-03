"""Explainable combat policy using observed hostiles, resources and unlocked moves."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    mode: str
    reason: str
    sequence: tuple
    skill: bool = False


def choose_plan(state, cycle=0, after_defense=False):
    data = getattr(state,'tactics',{})
    target = next((e for e in data.get('enemies',[]) if e['actor']==state.target),None)
    if not target:
        return None
    close = [e for e in data['enemies'] if e['distance'] <= 7]
    hp = state.hp/state.hp_max
    resource = state.shachi/state.shachi_max if state.shachi_max > 0 else 0
    target_ratio = target['hp']/target['hp_max']
    if hp < .4:
        return Plan('保命','自身血量偏低，缩短出招并保留恢复机会',('light',))
    if resource < .25:
        return Plan('回气','煞气偏低，使用轻击并保留防御资源',('light','light'))
    if target_ratio <= .16:
        return Plan('收尾','目标残血，保留技能充能',('light','heavy') if resource >= .5 else ('light',))
    art = data.get('skills',{}).get('combat_art',{})
    charge, cost = art.get('charge'), art.get('cost')
    skill_ready = (art.get('unlocked') and art.get('cooldown_known') and not art.get('active')
                   and isinstance(charge,int) and isinstance(cost,int) and 1 <= cost <= 10
                   and cost <= charge <= 100 and not data.get('phase') and hp >= .55 and resource >= .45)
    depleted = target.get('shachi_ratio') is not None and target['shachi_ratio'] <= .1
    if target['boss']:
        opening = depleted or after_defense
        return Plan('Boss 反击' if opening else 'Boss 试探',
                    '敌方煞气耗尽，安排爆发' if depleted else '防御后观察到空隙，安排反击' if after_defense else '缩短连段，等待防御后的空隙',
                    ('light','light','light','heavy','heavy') if opening else ('light','heavy'),
                    bool(skill_ready and opening))
    if len(close) >= 3:
        return Plan('群战','7 米内有多名敌人，使用较短的轻重击派生',
                    (('light','heavy'),('light','light','heavy'))[cycle % 2],bool(skill_ready and hp >= .65))
    if len(close) == 2:
        return Plan('双敌','两名敌人接近，缩短连段并轮换派生',
                    (('light','heavy'),('light','light','heavy','heavy'))[cycle % 2],False)
    variants = (('light','light','heavy','heavy'),('light','light','light','heavy','heavy'),
                ('light','light','light','light','light','heavy'),('light','heavy','heavy'))
    # Longer Tang branches are used only when the corresponding ability is granted.
    abilities = data.get('abilities',[]) if data.get('weapon','Weapon_Hun_Tang')=='Weapon_Hun_Tang' else []
    required = ('_Tang_AABB_C','_Tang_AAABB_C','_Tang_AAAAAB_C','_Tang_ABB_C')
    options = [v for v,suffix in zip(variants,required) if any(a.endswith(suffix) for a in abilities)]
    return Plan('单敌连段','轮换已解锁派生，避免重复同一套动作',
                options[cycle % len(options)] if options else ('light','light','heavy'),
                bool(skill_ready and charge >= 3 and target_ratio >= .65))
