"""Confirm each actual move and buffer the next input near its native combo window."""
from collections import deque
from combo_planner import choose_combo
from move_runtime import tag_matches
from weapon_policy import policy_for, affordable_health, next_move_safe, finite_cost
from action_windows import can_start_offense


class ComboController:
    def __init__(self):
        self.history=deque(maxlen=8)
        self.confirmed=0
        self.completed=0
        self.repairs=0
        self.identity=None
        self.plan=None
        self.step=0
        self.pending=None
        self.last=None
        self.wait_until=0
        self.hp=None
        self.hurt_until=0
        self.status='等待招式状态'
        self.strategy='等待战况'

    def interrupt(self):
        self.plan=None
        self.step=0
        self.pending=None
        self.last=None

    def observe(self,now,state):
        data=state.tactics
        identity=(state.player,state.world,state.target,data.get('weapon'))
        if identity!=self.identity:
            self.interrupt()
            self.identity=identity
            self.hp=state.hp
        if self.hp is not None and state.hp<self.hp-1:
            self.hurt_until=now+1.2
        self.hp=state.hp
        if not self.pending:return
        pending=self.pending
        changed=[]
        for name,move in data.get('move_events',{}).items():
            baseline=pending['before'].get(name)
            if baseline and move['instance']==baseline[0] and move['count']>baseline[1]:
                changed.append(move)
        if changed:
            exact=[m for m in changed if m['path']==pending['expected']]
            if len(changed)==1 and exact:
                move=exact[0]
                self.last=dict(move,at_game=state.game_time)
                self.confirmed+=1
                self.pending=None
                self.step+=1
                if self.plan and self.step>=len(self.plan.path):
                    self.history.append(self.plan.path)
                    self.completed+=1
                    self.plan=None
                    self.step=0
            else:
                self.repairs+=1
                self.interrupt()
                self.wait_until=now+.25
                self.status='实际招式与计划不同，等待收招后重新衔接'
        elif now-pending['at']>1.5:
            self.repairs+=1
            self.interrupt()
            self.wait_until=now+.25
            self.status='本次输入未观察到对应招式，等待重新起手'

    def can_start(self,state):
        return can_start_offense(state.tactics)

    def proposal(self,now,state):
        return choose_combo(state,list(self.history),now<self.hurt_until)

    def tick(self,now,state,config):
        data=state.tactics
        if now<self.wait_until:return None
        if self.pending:
            self.status='确认实际招式，防御继续监测'
            return None
        if not data.get('move_model_ready'):
            self.status='当前武器招式状态尚未就绪'
            return None
        tags=data.get('owned_tags') or []
        if 'CharacterState.Space.Ground' not in tags:
            self.interrupt()
            self.status='等待落地后衔接地面连招'
            return None
        proposal=self.proposal(now,state)
        if not proposal:
            self.interrupt()
            self.status='等待确认目标与可用招式'
            return None
        # Reassess danger between steps without changing a healthy chain arbitrarily.
        if self.plan and ((proposal.mode in ('保命','回气') and self.plan.mode!=proposal.mode)
                         or proposal.mode in ('群战','双敌','Boss 试探') and len(self.plan.path)>len(proposal.path)):
            self.interrupt()
        if self.plan is None:
            if not self.can_start(state):
                self.status='等待收招，保留本次攻击效果'
                return None
            self.plan=proposal
            self.step=0
            self.last=None
        plan=self.plan
        expected=plan.path[:self.step+1]
        meta=data.get('moves',{}).get(expected)
        if not meta or not meta.get('available'):
            self.interrupt()
            self.status='招式条件已变化，重新选择连段'
            return None
        if not affordable_health([meta],state,policy_for(data)):
            self.interrupt()
            self.status='保留生命余量，结束本次派生'
            return None
        if not next_move_safe(meta,state):
            self.interrupt()
            self.status='来招早于收招，保留防御空隙'
            return None
        if (any(tag_matches(tags,t) for t in meta.get('blocked_tags',[]))
                or not all(tag_matches(tags,t) for t in meta.get('required_tags',[]))):
            self.status='当前状态暂不能出招，等待恢复'
            return None
        cost=meta.get('cost')
        if not finite_cost(cost) or cost and state.shachi-cost<proposal.reserve:
            self.interrupt()
            self.status='保留防御杀气，结束本次派生'
            return None
        phase=data.get('phase','unknown')
        if self.step:
            if self.last is None:
                self.interrupt()
                return None
            if phase.endswith('.Cancel') or not phase:
                self.repairs+=1
                self.interrupt()
                self.status='连招窗口已结束，重新起手'
                return None
            elapsed=state.game_time-self.last['at_game']
            opening=self.last.get('combo_open')
            closing=self.last.get('combo_close')
            if elapsed<.1:
                self.status='等待新招式状态稳定'
                return None
            if closing is not None and elapsed>closing+.12 and not phase.endswith('.Combo'):
                self.repairs+=1
                self.interrupt()
                self.status='衔接时间已过，重新选择起手'
                return None
            if opening is None:
                if not phase.endswith('.Combo'):
                    self.status='等待可接招阶段'
                    return None
            elif elapsed<max(.1,opening-.08) and not phase.endswith('.Combo'):
                self.status=plan.label+' · 等待衔接窗口'
                return None
        key=config.heavy_key if expected.endswith('B') else config.attack_key
        self.pending=dict(at=now,expected=expected,before={n:(m['instance'],m['count']) for n,m in data['move_events'].items()})
        self.strategy=plan.mode+' · '+plan.reason+' · '+plan.label
        self.status=plan.label+f' · 第 {self.step+1}/{len(plan.path)} 招'
        return ('tap',key)
