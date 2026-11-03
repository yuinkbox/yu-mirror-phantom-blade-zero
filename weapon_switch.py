"""One bounded switch per low-resource episode, confirmed by native slot identity."""
import math


class WeaponSwitch:
    def __init__(self):
        self.identity=None
        self.weapon=None
        self.pending=None
        self.next_at=0
        self.latched=False
        self.confirmed=0
        self.failed=0
        self.status=''

    @staticmethod
    def cooldown(inventory):
        values=[s.get('cooldown') for s in inventory.get('slots',[])]
        return max([15.]+[v for v in values if isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=120])

    def observe(self,now,state):
        data=state.tactics.get('weapon_inventory',{})
        identity=(state.player,state.world)
        current=(data.get('index'),data.get('config')) if data else None
        if self.identity!=identity:
            self.identity=identity
            self.pending=None
            self.latched=False
            self.next_at=now
            self.weapon=current
        if self.pending:
            if current and current!=self.pending['before']:
                self.confirmed+=1
                self.pending=None
                self.status='已确认换刀，重新读取当前武器招式'
            elif now-self.pending['at']>1.5:
                self.failed+=1
                self.pending=None
                self.latched=False
                self.status='换刀未生效，等待冷却后再试'
        if current and self.weapon and current!=self.weapon:
            self.next_at=max(self.next_at,now+self.cooldown(data))
            self.latched=True
        if current:self.weapon=current
        if state.shachi_max>0 and state.shachi/state.shachi_max>=.55 and not self.pending:
            self.latched=False

    def request(self,now,state,config,blocked=False):
        if not config.switch_enabled or blocked or self.pending or self.latched or now<self.next_at:return None
        if state.shachi_max<=0:return None
        data=state.tactics;inv=data.get('weapon_inventory',{})
        costs=[m.get('cost') for p,m in data.get('moves',{}).items() if p.endswith('B') and m.get('available')]
        costs=[c for c in costs if isinstance(c,(int,float)) and math.isfinite(c) and c>0]
        ratio=state.shachi/state.shachi_max
        if ratio>.3 and not (ratio<=.5 and costs and state.shachi<min(costs)):return None
        if not inv or len({x.get('config') for x in inv.get('slots',[])})<2:return None
        tags=data.get('owned_tags') or []
        if 'CharacterState.Space.Ground' not in tags or 'CharacterState.Armed' not in tags:return None
        if any('Switch' in t and ('Cooldown' in t or 'Forbid' in t) for t in tags):return None
        phase=data.get('phase','unknown')
        if phase and not phase.endswith('.Cancel'):return None
        if not any(e['actor']==state.target for e in data.get('enemies',[])):return None
        if state.target_distance is None or state.target_distance>max(5,config.attack_distance):return None
        self.pending=dict(at=now,before=(inv['index'],inv['config']))
        self.latched=True
        self.next_at=now+self.cooldown(inv)
        self.status='杀气不足：切换备用武器，等待确认'
        return ('tap',config.switch_key)
