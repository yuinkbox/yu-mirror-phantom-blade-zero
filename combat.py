"""Combat decisions using observed game state; no OS or input access here."""
from dataclasses import dataclass
import math
from tactics import choose_plan
from combo_controller import ComboController
from weapon_switch import WeaponSwitch
from target_control import TargetControl
from shadow_controller import ShadowController,available as shadow_available,shielded
from enhancement_controller import EnhancementController
from weapon_policy import combat_art_decision
from copy import copy

COMBOS = {
    'balanced': [('light', 1.0), ('light', .95), ('heavy', 1.6), ('light', 1.0), ('heavy', 1.8)],
    'pressure': [('light', .9), ('heavy', 1.5), ('heavy', 1.8)],
    'swift': [('light', .85), ('light', .85), ('light', .9), ('heavy', 1.6)],
    'light': [('light', 1.0)],
}
COMBO_NAMES = {'balanced':'均衡连段', 'pressure':'重击压制', 'swift':'疾风连段', 'light':'仅轻攻击'}


@dataclass
class CombatSettings:
    heal_enabled: bool = True
    guard_enabled: bool = True
    attack_enabled: bool = True
    execution_enabled: bool = True
    adaptive_enabled: bool = True
    skills_enabled: bool = True
    shadow_enabled: bool = True
    switch_enabled: bool = True
    autolock_enabled: bool = True
    blue_dodge_enabled: bool = True
    red_dodge_enabled: bool = True
    projectile_dodge_enabled: bool = True
    heal_key: str = 'R'
    guard_key: str = '鼠标右键'
    attack_key: str = '鼠标左键'
    dodge_key: str = 'Shift'
    heavy_key: str = 'F'
    execution_key: str = 'E'
    skill_key: str = 'Q'
    shadow_key: str = 'Ctrl'
    switch_key: str = '1'
    lock_key: str = '鼠标中键'
    combo_preset: str = 'balanced'
    threshold: float = .4
    attack_period: float = .48
    attack_distance: float = 3.5
    defense_lead: float = .18
    colored_dodge_lead: float = .11

    def validate(self):
        if not .05 <= self.threshold <= .9:
            raise ValueError('喝药阈值应为 5%–90%')
        if not .25 <= self.attack_period <= 3:
            raise ValueError('攻击间隔应为 0.25–3 秒')
        if not 1 <= self.attack_distance <= 8:
            raise ValueError('攻击距离应为 1–8 米')
        if not math.isfinite(self.colored_dodge_lead) or not .04 <= self.colored_dodge_lead <= .22:
            raise ValueError('蓝红光闪避提前量应为 40–220 毫秒')
        if len({self.heal_key, self.guard_key, self.attack_key, self.dodge_key}) != 4:
            raise ValueError('四项操作需要使用不同按键')
        if self.combo_preset not in COMBOS or self.heavy_key == self.attack_key:
            raise ValueError('连招方案或重击按键无效')
        if self.skills_enabled and self.skill_key in {self.heal_key,self.guard_key,self.attack_key,
                                                     self.dodge_key,self.heavy_key,self.execution_key}:
            raise ValueError('武技按键不能与攻击、防御、喝药或处决重复')
        if self.switch_enabled and self.switch_key in {self.heal_key,self.guard_key,self.attack_key,self.dodge_key,self.heavy_key,self.execution_key,self.skill_key}:
            raise ValueError('换刀按键不能与其他辅助操作重复')
        if self.autolock_enabled and self.lock_key in {self.heal_key,self.guard_key,self.attack_key,self.dodge_key,self.heavy_key,self.execution_key,self.skill_key,self.switch_key}:
            raise ValueError('锁定按键不能与其他辅助操作重复')
        if self.shadow_enabled and self.shadow_key in {self.heal_key,self.guard_key,self.attack_key,self.dodge_key,self.heavy_key,self.execution_key,self.skill_key,self.switch_key,self.lock_key}:
            raise ValueError('影之武装按键不能与其他辅助操作重复')


class CombatScheduler:
    def __init__(self, config):
        config.validate()
        self.config = config
        self.armed = False
        self.status = '已暂停'
        self.guard_down = False
        self.guard_until = 0
        self.handled = {}
        self.last_now = None
        self.last_game_time = None
        self.advanced_at = None
        self.identity = None
        self.next_attack = 0
        self.next_dodge = 0
        self.last_heal = -100
        self.pending_heal = None
        self.low_since = None
        self.actions_count = {'heal': 0, 'guard': 0, 'dodge': 0, 'attack': 0, 'heavy':0, 'execution':0,
                              'blue_dodge':0, 'red_dodge':0}
        self.combo_index = 0
        self.combo_target = 0
        self.last_combo_at = -100
        self.execution_latch = 0
        self.unstable_since = None
        self.combo_cycle = 0
        self.plan_mode = ''
        self.strategy = '等待战况'
        self.last_defense = -100
        self.next_skill = 0
        self.pending_skill = None
        self.pending_attack = None
        self.fallback_target=0
        self.last_attack_input=-100
        self.actions_count.update(skill=0,skill_confirmed=0,attack_confirmed=0)
        self.actions_count['projectile_dodge']=0
        self.director=ComboController()
        self.actions_count.update(combo_completed=0,combo_repaired=0)
        self.switcher=WeaponSwitch()
        self.targeting=TargetControl()
        self.shadow=ShadowController()
        self.enhancement=EnhancementController()
        self.actions_count.update(enhancement=0,enhancement_confirmed=0)
        self.actions_count.update(shadow=0,shadow_confirmed=0,shield_broken=0)
        self.actions_count.update(lock=0,lock_confirmed=0)
        self.actions_count.update(switch=0,switch_confirmed=0,blue_guard=0,native_perfect=0)
        self.defense_attempts=[]
        self.perfect_seen=None

    def arm(self, now, state):
        if not state.valid or state.hp <= 0:
            raise ValueError('请先进入可操控角色的场景')
        if self.config.guard_enabled and not getattr(state,'community_defense',False) and not state.deflect_component:
            raise ValueError('防御组件尚未就绪')
        self.armed = True
        self.director.interrupt()
        self.targeting.reset()
        self.identity = (state.player, state.component, state.world)
        self.last_now = now
        self.last_game_time = state.game_time
        self.advanced_at = now
        self.next_attack = now+.4
        self.next_dodge = now
        self.guard_down = False
        self.handled.clear()
        self.pending_heal = None
        self.low_since = None
        self.combo_index = 0
        self.execution_latch = 0
        self.unstable_since = None
        self.pending_skill = None
        self.pending_attack = None
        self.fallback_target=state.target
        self.last_attack_input=now
        self.next_skill = now
        self.plan_mode = ''
        self.combo_cycle = 0
        self.last_defense = -100
        self.status = '运行中，等待战斗信号'

    def pause(self, reason='已暂停'):
        actions = [('up', self.config.guard_key)] if self.guard_down else []
        actions += self.shadow.cancel()
        actions += self.enhancement.cancel()
        self.armed = False
        self.guard_down = False
        self.pending_heal = None
        self.pending_skill = None
        self.pending_attack = None
        self.director.interrupt()
        self.targeting.pending=None
        self.status = reason
        return actions

    def defense_mode(self, threat):
        kind = threat.get('attack_type', 'unknown')
        if kind == 'projectile':
            return 'dodge' if self.config.projectile_dodge_enabled else None
        if kind == 'blue':
            # Native GetNearestValidReceivedFrame filters by IsDodge, not eye color.
            return ('dodge' if threat['dodge'] else 'guard') if self.config.blue_dodge_enabled else None
        if kind == 'red':
            return 'dodge' if self.config.red_dodge_enabled else None
        return 'dodge' if threat['dodge'] else 'guard'

    def defense_lead(self, threat):
        if threat.get('projectile'):return .22 if threat.get('explosive') else .18
        return (self.config.colored_dodge_lead if threat.get('attack_type') in ('blue', 'red')
                else self.config.defense_lead)

    def fallback_light(self,now,state,cfg,near_threat,tactical,status):
        if state.target!=self.fallback_target:
            self.fallback_target=state.target
            self.last_attack_input=now
            return None
        if (not state.target or not isinstance(state.target_distance,(int,float))
                or not math.isfinite(state.target_distance) or state.target_distance>cfg.attack_distance
                or near_threat or now<self.next_attack or now-self.last_attack_input<1.8
                or self.pending_heal is not None or self.pending_skill is not None
                or getattr(state,'input_blocked',False)):
            return None
        if tactical:
            tags=tactical.get('owned_tags') or []
            if ('CharacterState.Space.Ground' not in tags or tactical.get('active_moves')
                    or 'CharacterState.Hitreact.Phase' in tags
                    or tactical.get('skills',{}).get('combat_art',{}).get('active')):
                return None
            if self.director.pending or now<self.director.wait_until:
                return None
        self.director.interrupt()
        self.last_attack_input=now
        self.next_attack=now+max(1.0,cfg.attack_period*1.6)
        self.actions_count['attack']+=1
        self.status=status
        self.strategy='基础攻击恢复'
        return ('tap',cfg.attack_key)

    def tick(self, now, foreground, state):
        cfg = self.config
        guard_enabled = cfg.guard_enabled and not getattr(state, 'community_defense', False)
        if not self.armed:
            return []
        if not foreground:
            return self.pause('已切出游戏，辅助暂停')
        if not state.valid:
            return self.pause(state.reason)
        if not all(math.isfinite(v) for v in (state.hp, state.hp_max, state.game_time)) or state.hp_max <= 0 or not 0 < state.hp <= state.hp_max*1.01:
            return self.pause('角色死亡或血量数据失效')
        if self.identity != (state.player, state.component, state.world):
            return self.pause('角色或场景已切换，请按 F8 重新开始')
        if now < self.last_now or now-self.last_now > .4:
            return self.pause('采样中断，辅助暂停')
        self.last_now = now
        if state.game_time < self.last_game_time-.001:
            return self.pause('场景时间变化，辅助暂停')
        if state.game_time > self.last_game_time+.00001:
            self.advanced_at = now
        self.last_game_time = state.game_time
        if now-self.advanced_at > .18:
            return self.pause('游戏已暂停或正在加载')
        if guard_enabled and not state.deflect_component:
            return self.pause('防御组件暂不可用')
        if guard_enabled and not getattr(state,'defense_consistent',True):
            if self.unstable_since is None:self.unstable_since=now
            if now-self.unstable_since>.12:
                return self.pause('防御数据持续不可用，辅助暂停')
            self.status='防御数据更新中'
            return self.shadow.cancel()+self.enhancement.cancel()
        self.unstable_since=None
        if getattr(state,'tactics',{}):
            self.switcher.observe(now,state)
            self.actions_count['switch_confirmed']=self.switcher.confirmed
            self.targeting.observe(now,state)
            self.actions_count['lock_confirmed']=self.targeting.confirmed
        self.defense_attempts=[a for a in self.defense_attempts if now-a['at']<2]
        result=getattr(state,'diagnostics',{}).get('native_defense_result',{})
        token=(state.player,result.get('source'),result.get('hit_time'),result.get('result'))
        if (result.get('result')=='Deflect.Result.Perfect' and result.get('defender')==state.player
                and token!=self.perfect_seen and any(a['source']==result.get('source')
                and abs(a['hit_time']-result.get('hit_time',-100))<.02 for a in self.defense_attempts)):
            self.perfect_seen=token
            self.actions_count['native_perfect']+=1
        if cfg.adaptive_enabled and 'moves' in getattr(state,'tactics',{}):
            self.director.observe(now,state)
            self.actions_count['attack_confirmed']=self.director.confirmed
            self.actions_count['combo_completed']=self.director.completed
            self.actions_count['combo_repaired']=self.director.repairs
        actions = self.shadow.observe(now,state,cfg)
        actions += self.enhancement.observe(now,state,cfg)
        self.actions_count['enhancement_confirmed']=self.enhancement.confirmed
        self.actions_count['shadow_confirmed']=self.shadow.confirmed
        self.actions_count['shield_broken']=self.shadow.broken
        if not guard_enabled and self.guard_down:
            self.guard_down = False
            return actions+[('up', cfg.guard_key)]
        game_time = state.game_time
        self.handled = {key: due for key, due in self.handled.items() if due > game_time-3}
        threats = sorted((t for t in state.threats if -.07 <= t['hit_time']-game_time <= 1.5), key=lambda t: t['hit_time'])
        imminent = [t for t in threats if t['id'] not in self.handled
                    and t['hit_time']-game_time <= self.defense_lead(t) and self.defense_mode(t)]
        if guard_enabled and imminent:
            self.director.interrupt()
            self.combo_index = 0
            self.pending_attack = None
            dodge = next((t for t in imminent if self.defense_mode(t) == 'dodge'), None)
            if dodge and now >= self.next_dodge:
                if self.guard_down:
                    actions.append(('up', cfg.guard_key))
                    self.guard_down = False
                actions.append(('tap', cfg.dodge_key))
                self.handled[dodge['id']] = dodge['hit_time']
                self.next_dodge = now+.45
                self.next_attack = max(self.next_attack, now+.4)
                self.actions_count['dodge'] += 1
                self.last_defense = now
                self.defense_attempts.append(dict(at=now,source=dodge.get('source'),hit_time=dodge['hit_time']))
                kind = dodge.get('attack_type')
                if kind in ('blue', 'red'):
                    self.actions_count[kind+'_dodge'] += 1
                    self.status = ('蓝光' if kind == 'blue' else '红光')+'预警：按时闪避'
                elif kind=='projectile':
                    self.actions_count['projectile_dodge']+=1
                    self.status='投掷物接近：优先闪避'
                else:
                    self.status = '攻击预警：闪避'
                return actions
            if dodge:
                # A queued dodge must never become a guard press or a fresh attack.
                self.status = '等待下一次闪避动作'
                return actions
            normal = [t for t in imminent if self.defense_mode(t) == 'guard']
            if normal:
                # A new guard press can deflect a following combo strike.
                if self.guard_down:
                    actions.append(('up', cfg.guard_key))
                actions.append(('down', cfg.guard_key))
                self.guard_down = True
                self.guard_until = max(t['hit_time'] for t in normal)+.15
                self.handled.update({t['id']: t['hit_time'] for t in normal})
                self.next_attack = max(self.next_attack, now+.35)
                self.actions_count['guard'] += 1
                self.last_defense = now
                self.defense_attempts.extend(dict(at=now,source=t.get('source'),hit_time=t['hit_time']) for t in normal)
                blue=any(t.get('attack_type')=='blue' for t in normal)
                if blue:self.actions_count['blue_guard']+=1
                self.status = '蓝光原生判定：精准招架' if blue else '攻击预警：格挡'
                return actions
        colored = next((t for t in threats if t.get('attack_type') in ('blue','red')
                        and self.defense_mode(t) and t['id'] not in self.handled), None)
        if guard_enabled and colored:
            self.director.interrupt()
            self.combo_index = 0
            self.pending_attack = None
            # Release an old held guard before the blue perfect-parry press.
            if self.guard_down and self.defense_mode(colored)=='guard':
                actions.append(('up',cfg.guard_key))
                self.guard_down=False
            self.status = {'blue':'蓝光','red':'红光','projectile':'投掷物'}[colored['attack_type']]+'预警：等待应对时机'
            return actions
        if self.guard_down:
            if game_time < self.guard_until:
                self.status = '保持格挡'
                return actions
            actions.append(('up', cfg.guard_key))
            self.guard_down = False
        ratio = state.hp/state.hp_max
        ready = getattr(state, 'execution_ready', False)
        execution_target = getattr(state, 'execution_target', 0)
        if not ready:
            self.execution_latch = 0
        elif cfg.execution_enabled and execution_target and self.execution_latch != execution_target:
            self.director.interrupt()
            actions.append(('tap',cfg.execution_key))
            self.execution_latch = execution_target
            self.next_attack = now+2
            self.combo_index = 0
            self.pending_attack = None
            self.actions_count['execution'] += 1
            self.status = '处决机会：按 E'
            return actions
        if ratio < cfg.threshold:
            if self.low_since is None:
                self.low_since = now
        else:
            self.low_since = None
        if self.pending_heal:
            at, hp_before, medicine_before = self.pending_heal
            consumed = state.medicine is not None and medicine_before is not None and state.medicine < medicine_before
            recovered = ratio >= hp_before+.02
            if consumed or recovered:
                self.pending_heal = None
                self.status = '已确认喝药'
            elif now-at > 3.4:
                # The recovery animation is long and some builds keep the slot
                # counter unchanged. Stop blocking offense, keep a retry backoff,
                # and never pause the whole assistant for one unconfirmed heal.
                self.pending_heal = None
                self.last_heal = max(self.last_heal, now-1.6)
                self.status = '喝药动画中，未确认药槽变化'
            else:
                self.status = '等待喝药完成'
                return actions
        # A distant arrow must not freeze all offense for its entire flight. The
        # move planner still compares each next move's duration with impact time.
        threats=[t for t in threats if t['id'] not in self.handled]
        state=copy(state)
        state.threats=threats
        near_threat = any(t['hit_time']-game_time <= (.3 if t.get('projectile') else .75) for t in threats)
        tactical = getattr(state,'tactics',{})
        art = tactical.get('skills',{}).get('combat_art',{})
        if self.pending_skill:
            attempt = self.pending_skill
            same_instance = art.get('instance') == attempt['instance']
            activation = art.get('activation')
            activated = (isinstance(activation,int) and isinstance(attempt['activation'],int)
                         and activation > attempt['activation'])
            if same_instance and (activated or art.get('active') or
                                  (isinstance(art.get('charge'),int) and art['charge'] < attempt['charge'])):
                self.actions_count['skill_confirmed'] += 1
                self.pending_skill = None
            elif now-attempt['at'] > 1.5:
                self.pending_skill = None
                self.next_skill = max(self.next_skill,now+8)
                self.strategy = '技能未观察到生效，暂缓重试'
        if self.pending_attack:
            attempt=self.pending_attack
            counts=tactical.get('move_counts',{})
            if state.target != attempt['target'] or (tactical and tactical.get('weapon')!=attempt['weapon']):
                self.pending_attack=None
                self.combo_index=0
            elif any(count>attempt['counts'].get(name,count) for name,count in counts.items()):
                self.actions_count['attack_confirmed']+=1
                self.pending_attack=None
            elif now-attempt['at'] > max(.9,cfg.attack_period*2):
                self.pending_attack=None
                self.combo_index=0
                self.next_attack=max(self.next_attack,now+.25)
                self.strategy='上一招未观察到生效，重新起手'
        recovery_threat=any(t['hit_time']-game_time<=1.2 for t in threats)
        if (cfg.heal_enabled and self.low_since is not None and now-self.low_since >= .15
                and now-self.last_heal >= 4.5 and not recovery_threat
                and state.medicine is not None and state.medicine > 0):
            self.director.interrupt()
            actions.append(('tap', cfg.heal_key))
            self.last_heal = now
            self.pending_heal = (now, ratio, state.medicine)
            self.combo_index = 0
            self.pending_attack = None
            self.next_attack = now+2
            self.actions_count['heal'] += 1
            self.status = '低血量：使用恢复药'
            return actions
        if (cfg.heal_enabled and ratio<cfg.threshold and recovery_threat
                and state.medicine is not None and state.medicine>0):
            self.director.interrupt()
            self.status='低血量：先应对来招，再恢复生命'
            return actions
        if self.shadow.pending or tactical.get('shadow',{}).get('active'):
            self.director.interrupt()
            self.status=self.shadow.status if self.shadow.pending else '影之武装进行中'
            return actions
        if self.enhancement.pending or tactical.get('enhancement',{}).get('active'):
            self.director.interrupt()
            self.status=self.enhancement.status if self.enhancement.pending else '武器强化进行中'
            return actions
        if cfg.attack_enabled and cfg.adaptive_enabled and tactical:
            lock=self.targeting.request(now,state,cfg,blocked=(near_threat or self.pending_heal is not None
                or self.pending_skill is not None or art.get('active') or now-self.last_defense<.5
                or ratio<cfg.threshold))
            if lock:
                self.director.interrupt()
                self.next_attack=max(self.next_attack,now+.3)
                self.actions_count['lock']+=1
                self.status=self.targeting.status
                return actions+[lock]
        if cfg.attack_enabled and cfg.adaptive_enabled and 'moves' in tactical:
            if not state.target or state.target_distance is None:
                self.director.interrupt()
                self.status='等待锁定目标'
                return actions
            if state.target_distance>cfg.attack_distance:
                self.director.interrupt()
                self.status='目标超出攻击距离'
                return actions
            if self.pending_skill or art.get('active'):
                self.status='武技进行中，等待后续连招'
                return actions
            if near_threat:
                self.director.interrupt()
                self.status='等待攻击空隙'
                return actions
            if self.switcher.pending:
                self.director.interrupt()
                self.status=self.switcher.status
                return actions
            if now>=self.next_attack and not self.director.pending and not self.pending_skill:
                shadow=self.shadow.request(now,state,cfg,now<self.director.hurt_until)
                if shadow:
                    self.director.interrupt()
                    self.pending_attack=None
                    self.actions_count['shadow']+=1
                    self.status=self.strategy=self.shadow.status
                    return actions+[shadow]
            # If native bindings permit a shield-breaking follow-up, do not let
            # the next ordinary combo input consume that window first.
            shield_target=next((e for e in tactical.get('enemies',[]) if e['actor']==state.target),None)
            if cfg.shadow_enabled and shielded(shield_target) and shadow_available(tactical.get('shadow',{})):
                decision,reason=self.shadow.decision(now,state,cfg,now<self.director.hurt_until)
                self.shadow.status=reason
                if reason=='等待破盾衔接窗口' and not self.director.pending:
                    self.status=reason
                    return actions
            switch=self.switcher.request(now,state,cfg,blocked=(ratio<cfg.threshold or self.pending_skill is not None
                or any(t['hit_time']-game_time<=1 for t in threats) or now-self.last_defense<.4))
            if switch:
                self.director.interrupt()
                self.pending_attack=None
                self.next_attack=now+.4
                self.actions_count['switch']+=1
                self.status=self.switcher.status
                self.strategy=self.status
                return actions+[switch]
            if now<self.next_attack:return actions
            if not self.director.pending:
                enhancement=self.enhancement.request(now,state,cfg,now<self.director.hurt_until)
                if enhancement:
                    self.director.interrupt()
                    self.actions_count['enhancement']+=1
                    self.status=self.strategy=self.enhancement.status
                    return actions+[enhancement]
            skill_ready,skill_reason=combat_art_decision(state,now<self.director.hurt_until,
                .35<=now-self.last_defense<=2, cfg.threshold if cfg.heal_enabled else .35)
            target=next((e for e in tactical.get('enemies',[]) if e['actor']==state.target),None)
            if cfg.shadow_enabled and shielded(target) and shadow_available(tactical.get('shadow',{})):
                skill_ready=False  # Keep Q for after the equipped axe breaks this shield.
            if (cfg.skills_enabled and skill_ready and not self.director.pending and now>=self.next_skill):
                self.director.interrupt()
                actions.append(('tap',cfg.skill_key))
                self.next_skill=now+6
                self.next_attack=now+1.3
                self.pending_skill=dict(at=now,charge=art['charge'],instance=art.get('instance'),activation=art.get('activation'))
                self.actions_count['skill']+=1
                self.strategy='武技衔接 · 充能足够，在可出招窗口释放'
                self.status='释放武技，等待生效确认'
                return actions
            action=self.director.tick(now,state,cfg)
            self.status=self.director.status
            self.strategy=self.director.strategy
            self.combo_index=self.director.step
            if action:
                actions.append(action)
                self.actions_count['attack']+=1
                if action[1]==cfg.heavy_key:self.actions_count['heavy']+=1
                self.last_attack_input=now
                self.next_attack=now+.1
            else:
                fallback=self.fallback_light(now,state,cfg,near_threat,tactical,'已锁定近身目标：恢复基础轻击')
                if fallback:actions.append(fallback)
            return actions
        if cfg.attack_enabled and state.target and state.target_distance is not None:
            if cfg.adaptive_enabled and hasattr(state,'tactics') and not tactical:
                self.status = '等待最新战况，防御与恢复继续监测'
                fallback=self.fallback_light(now,state,cfg,near_threat,None,'近身目标战况读取中：恢复基础轻击')
                return actions+[fallback] if fallback else actions
            if self.pending_attack:
                self.status = '等待上一招生效，防御继续监测'
                return actions
            if self.pending_skill:
                self.status = '等待武技生效，防御继续监测'
                return actions
            if art.get('active'):
                self.status = '武技进行中，等待后续连招'
                return actions
            if state.target_distance <= cfg.attack_distance and not near_threat and now >= self.next_attack:
                if self.combo_target != state.target or now-self.last_combo_at > 2.5:
                    self.combo_index = 0
                    self.plan_mode = ''
                self.combo_target = state.target
                plan = choose_plan(state,self.combo_cycle,.25 <= now-self.last_defense <= 1.4) if cfg.adaptive_enabled else None
                if cfg.adaptive_enabled and tactical and plan is None:
                    self.status = '等待确认目标敌对状态与血量'
                    return actions
                if plan:
                    self.strategy = plan.mode+' · '+plan.reason
                    if plan.mode != self.plan_mode:
                        self.combo_index = 0
                        self.plan_mode = plan.mode
                    sequence = [(k,1.6 if k=='heavy' else 1.0) for k in plan.sequence]
                    long_threat = any(t['hit_time']-game_time <= 1.2 for t in threats)
                    if cfg.skills_enabled and plan.skill and now >= self.next_skill and not long_threat and not self.pending_skill:
                        actions.append(('tap',cfg.skill_key))
                        self.next_skill = now+6
                        self.next_attack = now+1.3
                        self.pending_skill = dict(at=now,charge=art['charge'],instance=art.get('instance'),activation=art.get('activation'))
                        self.combo_index = 0
                        self.pending_attack = None
                        self.actions_count['skill'] += 1
                        self.status = plan.mode+' · 释放武技，等待生效确认'
                        return actions
                else:
                    sequence = COMBOS[cfg.combo_preset]
                    self.strategy = '使用所选连招；等待完整战况数据' if cfg.adaptive_enabled else '使用所选固定连招'
                kind, delay = sequence[self.combo_index % len(sequence)]
                if kind == 'heavy' and getattr(state, 'shachi_max', 0) > 0 and state.shachi < state.shachi_max*.2:
                    kind = 'light'
                key = cfg.heavy_key if kind == 'heavy' else cfg.attack_key
                actions.append(('tap', key))
                counts=tactical.get('move_counts',{})
                if plan and counts:
                    self.pending_attack=dict(at=now,target=state.target,weapon=tactical.get('weapon'),counts=counts.copy())
                self.next_attack = now+cfg.attack_period*delay
                self.last_combo_at = now
                self.combo_index = (self.combo_index+1) % len(sequence)
                if self.combo_index == 0:
                    self.combo_cycle += 1
                self.actions_count['attack'] += 1
                if kind == 'heavy':self.actions_count['heavy'] += 1
                self.status = (plan.mode if plan else COMBO_NAMES[cfg.combo_preset])+' · '+('重击' if kind == 'heavy' else '轻击')
                return actions
            self.status = '等待攻击空隙' if near_threat else '目标超出攻击距离' if state.target_distance > cfg.attack_distance else '监测战斗状态'
        else:
            self.status = '等待锁定目标' if cfg.attack_enabled else '监测战斗状态'
        if cfg.heal_enabled and ratio < cfg.threshold:
            self.status += '；恢复药已用完' if state.medicine == 0 else '；等待喝药空隙'
        return actions
