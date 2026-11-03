"""Interruptible shadow-arm action; charge staging only when the arm exposes it."""
import math
from action_windows import can_start_offense
from move_runtime import tag_matches


def number(value):
    return type(value) in (float,int) and math.isfinite(value) and value>=0


def shielded(enemy):
    if not enemy or enemy.get('shield_present') is not True:return False
    current,maximum=enemy.get('shield'),enemy.get('shield_max')
    return not (number(current) and number(maximum) and maximum>0 and current==0)


def charge_arm(arm):
    times=arm.get('charge_times') or []
    return (len(times)==2 and all(number(v) and 0<v<4 for v in times)
            and number(arm.get('min_duration')))


def available(arm):
    ammo,cost=arm.get('ammo'),arm.get('cost')
    return arm.get('supported') and number(ammo) and number(cost) and cost>0 and ammo>=cost


def shadow_window(data,arm):
    if can_start_offense(data):return True
    if not data.get('phase','').endswith('.Combo'):return False
    tags=data.get('owned_tags') or []
    if tag_matches(tags,'CharacterState.Hitreact.Phase'):return False
    return any(all(tag_matches(tags,t) for t in rule['required'])
               and not any(tag_matches(tags,t) for t in rule['ignored'])
               for rule in arm.get('combo_bindings',[]))


class ShadowController:
    def __init__(self):
        self.pending=None;self.holding=False;self.next_try=0
        self.confirmed=0;self.broken=0;self.status=''

    def cancel(self):
        actions=[('up',self.pending['key'])] if self.holding and self.pending else []
        self.pending=None;self.holding=False
        return actions

    def release(self,now,reason):
        if not self.holding:return []
        self.holding=False;self.next_try=max(self.next_try,now+4)
        self.status=reason
        return [('up',self.pending['key'])]

    def observe(self,now,state,cfg):
        p=self.pending
        if not p:return []
        data=state.tactics;arm=data.get('shadow',{})
        target=next((e for e in data.get('enemies',[]) if e['actor']==state.target),None)
        same=(state.player==p['player'] and arm.get('config')==p['config']
              and arm.get('instance')==p['instance'])
        activation=arm.get('activation')
        if same and not p['confirmed'] and (arm.get('active') or type(activation) is int and activation>p['activation']):
            p['confirmed']=True;self.confirmed+=1
        if same and state.target==p['target'] and target and target.get('shield_present') is False and p['confirmed'] and not p['broken']:
            p['broken']=True;self.broken+=1
        actions=[]
        danger=any(-.07<=t['hit_time']-state.game_time<=.8 or
                   t.get('attack_type') in ('blue','red') and -.07<=t['hit_time']-state.game_time<=1.5
                   for t in state.threats)
        interrupted=(not same or not cfg.shadow_enabled or not cfg.attack_enabled or not cfg.adaptive_enabled
            or state.target!=p['target'] or not target or (p['charge'] and not shielded(target))
            or state.target_distance is None or state.target_distance>min(3.5,cfg.attack_distance)
            or state.hp<p['hp']-.5 or state.hp/state.hp_max<max(.25,cfg.threshold if cfg.heal_enabled and state.medicine else .25)
            or getattr(state,'execution_ready',False) or danger)
        if interrupted:actions+=self.release(now,'停止影之武装，优先应对当前战况')
        elif self.holding:
            stage=arm.get('charge_stage')
            if p['confirmed'] and type(stage) is int and stage>=p['stage']:
                actions+=self.release(now,'蓄力完成，释放破盾')
            elif now-p['at']>4 or state.game_time-p['game_time']>p['deadline']:
                actions+=self.release(now,'蓄力达到等待上限，释放按键')
            elif not p['confirmed'] and now-p['at']>.9:
                actions+=self.release(now,'未确认影之武装发动，稍后重试')
                self.next_try=max(self.next_try,now+8)
        if not self.holding and (not same or not arm.get('active') and (p['confirmed'] or now-p['at']>1.5)):
            self.pending=None
        return actions

    def request(self,now,state,cfg,hurt=False):
        decision,reason=self.decision(now,state,cfg,hurt)
        self.status=reason
        if decision is None:return None
        arm=state.tactics['shadow'];stage=decision['stage']
        self.pending=dict(at=now,game_time=state.game_time,player=state.player,target=state.target,
            config=arm['config'],instance=arm['instance'],activation=arm['activation'],key=cfg.shadow_key,
            hp=state.hp,stage=stage,deadline=decision['duration']+.5,charge=decision['charge'],
            confirmed=False,broken=False)
        self.holding=stage>0;self.next_try=now+4
        return ('down' if stage else 'tap',cfg.shadow_key)

    def decision(self,now,state,cfg,hurt=False):
        if not cfg.shadow_enabled:return None,'影之武装未开启'
        if self.pending:return None,'影之武装进行中'
        if now<self.next_try:return None,'等待影之武装重试间隔'
        data=state.tactics;arm=data.get('shadow',{})
        label=arm.get('label') or '影之武装'
        target=next((e for e in data.get('enemies',[]) if e['actor']==state.target),None)
        if not target:return None,'等待有效目标'
        charge=charge_arm(arm)
        # Charge arms exist to break shields; other arms are a plain heavy strike.
        if charge and not shielded(target):return None,'目标未持盾'
        if not available(arm):return None,label+'资源未就绪'
        tags=data.get('owned_tags') or []
        if (hurt or arm.get('active') or not arm.get('cooldown_known')
            or 'CharacterState.Space.Ground' not in tags
            or any(tag_matches(tags,t) for t in arm.get('blocked_tags',[]))
            or not all(tag_matches(tags,t) for t in arm.get('required_tags',[]))):return None,'等待角色恢复'
        if state.target_distance is None or state.target_distance>min(3.5,cfg.attack_distance):
            return None,'等待近身'+('破盾' if charge else '出招')
        if state.hp/state.hp_max<max(.25,cfg.threshold if cfg.heal_enabled and state.medicine else .25):
            return None,'先恢复生命再使用影之武装'
        if not shadow_window(data,arm):return None,'等待影之武装衔接窗口'
        if type(arm.get('activation')) is not int or not arm.get('instance'):return None,'等待武装实例'
        times=arm.get('charge_times',[])
        if not charge:
            duration=arm.get('min_duration')
            if not number(duration) or not 0<duration<5:duration=.8
            if any(-.07<=t['hit_time']-state.game_time<=duration+.8 for t in state.threats):
                return None,'来招接近，先防御再出招'
            return dict(stage=0,duration=duration,charge=False),label+' · 出招'
        nearby=sum(e['distance']<=5 for e in data.get('enemies',[]))
        maxstage=2 if nearby==1 and state.hp/state.hp_max>=.7 else 1 if state.hp/state.hp_max>=.6 else 0
        for stage in range(maxstage,-1,-1):
            duration=times[stage-1] if stage else arm.get('min_duration')
            if not number(duration) or duration<=0:continue
            if any(-.07<=t['hit_time']-state.game_time<=duration+.8 for t in state.threats):continue
            return dict(stage=stage,duration=duration,charge=True),label+'破盾 · '+('快速劈砍' if stage==0 else '蓄力'+str(stage)+'段')
        return None,'来招接近，先防御再破盾'
