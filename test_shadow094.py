"""0.9.4: shadow arms without charge stages, and heal confirmation that cannot stop the run."""
from test_combo_intelligence import state
from test_fixes091 import scheduler


def step(c,s,now):
    s.game_time=100+now
    return c.tick(now,True,s)


def strike_state():
    s=state();s.execution_ready=False
    s.tactics['enemies'][0].update(shield_present=False,shield=0,shield_max=0)
    s.tactics['shadow']=dict(weapon='Weapon_HuPao',family='HuPao',label='HuPao',config=987,instance=789,
        supported=True,ammo=4.,ammo_max=4.,cost=1.,active=False,activation=11,charge_stage=None,
        cooldown_known=True,required_tags=[],blocked_tags=[],charge_times=[],min_duration=.6)
    return s


def test_non_charge_shadow_arm_strikes_unshielded_target():
    s=strike_state();c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','Ctrl')]
    assert c.actions_count['shadow']==1


def test_charge_arm_still_requires_a_shield():
    s=strike_state();s.tactics['shadow'].update(weapon='Weapon_BrutalAxe',family='BrutalAxe',label='巨凶斧',
        charge_times=[1.701,2.179],min_duration=1.432)
    assert ('down','Ctrl') not in scheduler(s).tick(.01,True,s)


def test_unsupported_arm_never_presses_the_key():
    s=strike_state();s.tactics['shadow']['supported']=False
    assert ('tap','Ctrl') not in scheduler(s).tick(.01,True,s)


def test_unconfirmed_heal_does_not_pause_the_assistant():
    s=state();s.hp=200.;s.hp_max=850.;s.medicine=2
    c=scheduler(s)
    pressed=False
    for t in [x*.2 for x in range(1,26)]:
        if ('tap','R') in step(c,s,t):pressed=True
        assert c.armed, f'辅助在 {t:.1f}s 被暂停：{c.status}'
    assert pressed and c.actions_count['heal']==1
    assert c.pending_heal is None


def test_heal_confirmation_accepts_slow_recovery():
    s=state();s.hp=200.;s.hp_max=850.;s.medicine=2
    c=scheduler(s)
    step(c,s,.01);step(c,s,.2)
    assert c.pending_heal is not None
    s.hp=240.
    step(c,s,.4)
    assert c.pending_heal is None and c.status=='已确认喝药'
