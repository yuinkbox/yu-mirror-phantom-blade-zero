"""Bounded native lock-key acquisition. Never cycle a valid or manually unlocked target."""
import math


class TargetControl:
    def __init__(self):
        self.reset()
        self.confirmed=0
        self.failed=0

    def reset(self):
        self.identity=None
        self.previous=0
        self.pending=None
        self.next_at=0
        self.attempts=0
        self.encounter=None
        self.manual_until=0
        self.manual_unlock=False
        self.empty_since=None
        self.lost=None
        self.status=''

    def manual(self,now):
        self.pending=None
        self.manual_until=now+3
        self.manual_unlock=True

    def observe(self,now,state):
        identity=(state.player,state.world)
        if identity!=self.identity:
            self.reset()
            self.identity=identity
        enemies={e['actor']:e for e in state.tactics.get('enemies',[])}
        if self.pending is not None:
            if state.target and state.target in enemies:
                self.confirmed+=1
                self.pending=None
                self.attempts=0
                self.status='已确认锁定'
            elif now-self.pending>.9:
                self.pending=None
                self.failed+=1
                if self.attempts>=2:
                    self.next_at=max(self.next_at,now+6)
                self.status='未锁定到目标，稍后再试'
        # A live target disappearing from the lock means the user unlocked, or the
        # native system rejected it. Neither should trigger rapid relocking.
        if self.previous and not state.target:
            self.lost=(self.previous,now)
        if self.lost and now-self.lost[1]>=.6:
            self.lost=None
        if state.target:
            self.manual_unlock=False
            self.lost=None
        self.previous=state.target

    def request(self,now,state,config,blocked=False):
        if not config.autolock_enabled or blocked or state.target or self.pending is not None:
            return None
        data=state.tactics
        if not data or getattr(state,'input_blocked',False):
            return None
        tags=data.get('owned_tags') or []
        if 'CharacterState.Space.Ground' not in tags:
            return None
        # Only engage nearby hostile actors. Game targeting decides visibility,
        # camera angle, and the actual valid target; no guessed memory writes.
        nearby=[e for e in data.get('enemies',[]) if e.get('hp',0)>0
                and isinstance(e.get('distance'),(int,float)) and math.isfinite(e['distance'])
                and 0<=e['distance']<=min(8,config.attack_distance+2)
                and abs(e.get('height_delta',0))<=250]
        if not nearby:
            if self.empty_since is None:self.empty_since=now
            if now-self.empty_since>3:
                self.manual_unlock=False
                self.attempts=0
                self.encounter=None
            return None
        self.empty_since=None
        encounter=tuple(sorted(e['actor'] for e in nearby))
        if encounter!=self.encounter:
            self.encounter=encounter
            self.attempts=0
        if self.lost or self.manual_unlock or now<max(self.manual_until,self.next_at):
            return None
        if self.attempts>=2:self.attempts=0
        self.attempts+=1
        self.pending=now
        self.next_at=now+4
        self.status='寻找附近目标'
        return ('tap',config.lock_key)
