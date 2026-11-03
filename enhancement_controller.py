"""Stance then heavy, confirmed through the game's held-stance tag and ability."""
from action_windows import can_start_offense
from move_runtime import tag_matches
from shadow_controller import shielded


class EnhancementController:
    def __init__(self):
        self.pending=None;self.holding=False;self.next_try=0;self.confirmed=0;self.status=''

    def cancel(self):
        actions=[('up',self.pending['guard'])] if self.holding and self.pending else []
        self.pending=None;self.holding=False
        return actions

    def observe(self,now,state,cfg):
        p=self.pending
        if not p:return []
        data=state.tactics;e=data.get('enhancement',{});tags=data.get('owned_tags') or []
        invalid=(state.player!=p['player'] or state.target!=p['target'] or e.get('instance')!=p['instance']
            or not cfg.skills_enabled or not cfg.attack_enabled or not cfg.adaptive_enabled
            or not e.get('unlocked') or state.hp<p['hp']-.5 or getattr(state,'execution_ready',False)
            or state.hp/state.hp_max<max(.5,cfg.threshold)
            or any(-.07<=t['hit_time']-state.game_time<=1.5 for t in state.threats))
        if invalid:
            self.next_try=now+8;return self.cancel()
        if p['phase']=='stance':
            if tag_matches(tags,'CharacterState.AbilityState.Stance.HoldingStanceKey'):
                p['phase']='confirm';p['sent']=now
                self.status='黑伤强化 · 衔接重击'
                return [('tap',p['heavy'])]
            if now-p['at']>.6:
                self.next_try=now+8;return self.cancel()
            return []
        activation=e.get('activation')
        activated=(type(activation) is int and activation>p['activation'])
        actions=[]
        if self.holding and (now-p['sent']>=.08 or activated):
            self.holding=False;actions=[('up',p['guard'])]
        if activated and not p['started']:
            p['started']=True;self.confirmed+=1
            self.status='黑伤强化 · 等待生效'
        if e.get('buff_active'):
            self.next_try=now+40
            return actions+self.cancel()
        if (not p['started'] and now-p['sent']>1.5
                or p['started'] and (not e.get('active') or now-p['sent']>p['duration']+1.5)):
            self.next_try=now+8
            return actions+self.cancel()
        return actions

    def request(self,now,state,cfg,hurt=False):
        if self.pending or now<self.next_try or hurt or not cfg.skills_enabled:return None
        data=state.tactics;e=data.get('enhancement',{});tags=data.get('owned_tags') or []
        if (not e.get('unlocked') or not e.get('cooldown_known') or e.get('active')
            or e.get('buff_active') is not False or not e.get('instance') or type(e.get('activation')) is not int):return None
        target=next((x for x in data.get('enemies',[]) if x['actor']==state.target),None)
        if (not target or shielded(target) or target['hp']/target['hp_max']<.3
            or state.hp/state.hp_max<max(.55,cfg.threshold) or not can_start_offense(data)
            or 'CharacterState.Space.Ground' not in tags or tag_matches(tags,'Ability.Action.Stance')
            or any(tag_matches(tags,t) for t in e.get('blocked_tags',[]))
            or not all(tag_matches(tags,t) for t in e.get('required_tags',[]))):return None
        if sum(x['distance']<=5 for x in data.get('enemies',[]))>1:return None
        duration=e.get('combo_open')
        if type(duration) not in (float,int) or not 0<duration<5:return None
        if any(-.07<=t['hit_time']-state.game_time<=duration+.5 for t in state.threats):return None
        self.pending=dict(at=now,player=state.player,target=state.target,instance=e['instance'],activation=e['activation'],
            hp=state.hp,guard=cfg.guard_key,heavy=cfg.heavy_key,phase='stance',started=False,duration=duration)
        self.holding=True;self.status='黑伤强化 · 起势'
        return ('down',cfg.guard_key)
