from types import SimpleNamespace
import math
import pytest
from combat import CombatSettings, CombatScheduler


def state(**values):
    data=dict(valid=True,reason='ok',player=10,component=20,world=30,hp=850.,hp_max=850.,
              game_time=100.,deflect_component=40,medicine=2,target=50,target_distance=2.,threats=[])
    data.update(values)
    return SimpleNamespace(**data)


def runner(**config):
    scheduler=CombatScheduler(CombatSettings(**config));scheduler.arm(0,state())
    return scheduler


def tick(s,t,**values):
    return s.tick(t,True,state(game_time=100+t,**values))


def test_live_recorded_preimpact_timing():
    # First live frame was visible 0.285 s before impact. Defend in the final 0.18 s.
    s=runner();threat=[dict(id=1,hit_time=100.285,dodge=False)]
    assert tick(s,.02,threats=threat)==[]
    assert tick(s,.10,threats=threat)==[]
    assert tick(s,.12,threats=threat)==[('down','鼠标右键')]
    assert tick(s,.20,threats=threat)==[]
    assert tick(s,.30,threats=threat)==[]
    assert ('up','鼠标右键') in tick(s,.45,threats=[])


def test_defense_preempts_ready_attack():
    s=runner();s.next_attack=0
    assert tick(s,.02,threats=[dict(id=1,hit_time=100.15,dodge=False)])==[('down','鼠标右键')]


def test_dodge_releases_existing_guard():
    s=runner();s.guard_down=True;s.guard_until=101
    assert tick(s,.02,threats=[dict(id=2,hit_time=100.17,dodge=True)])==[('up','鼠标右键'),('tap','Shift')]
    assert tick(s,.04,threats=[dict(id=2,hit_time=100.17,dodge=True)])==[]


def test_second_combo_hit_requests_fresh_guard_press():
    s=runner();s.guard_down=True;s.guard_until=100.5
    assert tick(s,.02,threats=[dict(id=3,hit_time=100.18,dodge=False)])==[('up','鼠标右键'),('down','鼠标右键')]


@pytest.mark.parametrize('changes',[dict(target=0),dict(target_distance=None),dict(target_distance=8)])
def test_does_not_attack_without_close_target(changes):
    s=runner();s.next_attack=0
    assert tick(s,.02,**changes)==[]


def test_attacks_in_range_and_does_not_spam():
    s=runner();s.next_attack=0
    assert tick(s,.02)==[('tap','鼠标左键')]
    assert tick(s,.04)==[]


def test_holds_attack_during_approaching_threat():
    s=runner();s.next_attack=0
    assert tick(s,.02,threats=[dict(id=4,hit_time=100.6,dodge=False)])==[]


@pytest.mark.parametrize('medicine',[0,None])
def test_no_heal_without_known_inventory(medicine):
    s=runner(attack_enabled=False)
    assert tick(s,.02,hp=200,medicine=medicine)==[]
    assert tick(s,.25,hp=200,medicine=medicine)==[]


def test_heal_confirmed_by_inventory_consumption_despite_new_damage():
    s=runner(attack_enabled=False)
    tick(s,.02,hp=200)
    assert tick(s,.20,hp=200)==[('tap','R')]
    for t in [.4,.7,1.,1.3,1.6,1.9,2.1]:
        tick(s,t,hp=190,medicine=1)
    assert s.armed and s.pending_heal is None
    assert s.actions_count['heal']==1


def test_healing_defers_until_threat_passes():
    s=runner(attack_enabled=False);tick(s,.02,hp=200)
    assert tick(s,.20,hp=200,threats=[dict(id=7,hit_time=100.8,dodge=False)])==[]


@pytest.mark.parametrize('changes',[dict(player=99),dict(component=99),dict(world=99),dict(valid=False),dict(hp=0),dict(hp=float('nan'))])
def test_invalid_state_stops_and_releases_guard(changes):
    s=runner();s.guard_down=True
    assert tick(s,.02,**changes)==[('up','鼠标右键')]
    assert not s.armed


def test_focus_loss_stops_guard():
    s=runner();s.guard_down=True
    assert s.tick(.02,False,state())==[('up','鼠标右键')]
    assert not s.armed


def test_frozen_game_clock_stops():
    s=runner()
    s.tick(.1,True,state());s.tick(.2,True,state())
    assert not s.armed


def test_long_sampling_gap_stops():
    s=runner();s.tick(.6,True,state(game_time=100.6))
    assert not s.armed


def test_missing_defense_component_prevents_start():
    s=CombatScheduler(CombatSettings())
    with pytest.raises(ValueError):s.arm(0,state(deflect_component=0))


def test_balanced_combo_contains_real_heavy_inputs():
    s=runner();s.next_attack=0;actions=[]
    t=.01
    for _ in range(5):
        actions.extend(tick(s,t))
        due=s.next_attack
        while t+.25<due:
            t+=.25;tick(s,t)
        t=due+.001
    assert actions==[('tap','鼠标左键'),('tap','鼠标左键'),('tap','F'),('tap','鼠标左键'),('tap','F')]


def test_defense_resets_combo_to_opener():
    s=runner();s.combo_index=2
    tick(s,.02,threats=[dict(id=10,hit_time=100.15,dodge=False)])
    assert s.combo_index==0 and s.guard_down


def test_low_shachi_avoids_heavy():
    s=runner();s.next_attack=0;s.combo_index=2;s.combo_target=50;s.last_combo_at=0
    assert tick(s,.02,shachi=10,shachi_max=400)==[('tap','鼠标左键')]


def test_execution_press_once_per_opportunity_then_rearms():
    s=runner();s.next_attack=0
    assert tick(s,.02,execution_ready=True,execution_target=99)==[('tap','E')]
    assert tick(s,.04,execution_ready=True,execution_target=99)==[]
    tick(s,.06,execution_ready=False)
    assert tick(s,.08,execution_ready=True,execution_target=99)==[('tap','E')]


def test_no_execution_without_actual_selected_target():
    s=runner(attack_enabled=False)
    assert tick(s,.02,execution_ready=True,execution_target=0)==[]


def test_execution_can_be_disabled():
    s=runner(execution_enabled=False,attack_enabled=False)
    assert tick(s,.02,execution_ready=True,execution_target=99)==[]


def test_single_torn_defense_snapshot_preserves_held_guard():
    s=runner();s.guard_down=True;s.guard_until=100.5
    assert tick(s,.02,defense_consistent=False)==[]
    assert s.armed and s.guard_down
    assert tick(s,.04)==[]
    assert s.armed and s.guard_down


def test_persistently_unreadable_defense_stops():
    s=runner();s.guard_down=True
    tick(s,.02,defense_consistent=False)
    assert tick(s,.16,defense_consistent=False)==[('up','鼠标右键')]
    assert not s.armed


def test_focus_loss_still_stops_during_container_update():
    s=runner();s.guard_down=True;tick(s,.02,defense_consistent=False)
    assert s.tick(.04,False,state(defense_consistent=False))==[('up','鼠标右键')]
    assert not s.armed


@pytest.mark.parametrize('color',['blue','red'])
def test_colored_attack_respects_native_blue_and_red_counter_paths(color):
    s=runner();s.next_attack=0
    threat=[dict(id=80,hit_time=100.3,dodge=False,attack_type=color)]
    assert tick(s,.02,threats=threat,execution_ready=True,execution_target=99)==[]
    assert tick(s,.14,threats=threat)==[]  # ordinary guard would already press here
    assert tick(s,.20,threats=threat)==([('down','鼠标右键')] if color=='blue' else [('tap','Shift')])
    assert s.actions_count['blue_guard' if color=='blue' else 'red_dodge']==1
    assert s.guard_down==(color=='blue')
    assert tick(s,.22,threats=threat)==[]


def test_colored_dodge_releases_guard_and_preempts_heal_and_execution():
    s=runner();s.guard_down=True;s.guard_until=101;s.low_since=-1
    assert tick(s,.02,hp=100,execution_ready=True,execution_target=99,
                threats=[dict(id=81,hit_time=100.10,dodge=False,attack_type='red')])==[
                    ('up','鼠标右键'),('tap','Shift')]
    assert s.actions_count['heal']==0 and s.actions_count['execution']==0


@pytest.mark.parametrize('color',['blue','red'])
def test_disabling_one_color_does_not_convert_it_to_a_guard(color):
    s=runner(attack_enabled=False,**{color+'_dodge_enabled':False})
    assert tick(s,.02,threats=[dict(id=82,hit_time=100.10,dodge=True,attack_type=color)])==[]


def test_colored_dodge_respects_master_defense_switch():
    s=runner(guard_enabled=False,attack_enabled=False)
    assert tick(s,.02,threats=[dict(id=83,hit_time=100.10,dodge=True,attack_type='red')])==[]


def test_dodge_cooldown_never_substitutes_a_guard_or_attack():
    s=runner();s.next_dodge=1;s.next_attack=0
    assert tick(s,.02,threats=[dict(id=84,hit_time=100.10,dodge=True,attack_type='blue')])==[]
    assert not s.guard_down and not s.handled


def test_custom_colored_timing_does_not_change_ordinary_guard_timing():
    s=runner(colored_dodge_lead=.06)
    threat=[dict(id=85,hit_time=100.2,dodge=False,attack_type='red')]
    assert tick(s,.10,threats=threat)==[]
    assert tick(s,.15,threats=threat)==[('tap','Shift')]
    s=runner(colored_dodge_lead=.06)
    assert tick(s,.03,threats=[dict(id=86,hit_time=100.2,dodge=False,attack_type='normal')])==[('down','鼠标右键')]


@pytest.mark.parametrize('lead',[.039,.221,float('nan'),float('inf')])
def test_invalid_colored_timing_is_rejected(lead):
    with pytest.raises(ValueError):CombatSettings(colored_dodge_lead=lead).validate()


@pytest.mark.parametrize('color',['normal','blue','red'])
def test_community_defense_ownership_suppresses_external_guard_and_dodge(color):
    s=runner();s.next_attack=0
    assert tick(s,.02,community_defense=True,
                threats=[dict(id=90,hit_time=100.1,dodge=color=='red',attack_type=color)])==[]
    assert s.actions_count['guard']==s.actions_count['dodge']==0


def test_switch_to_community_defense_releases_external_held_guard():
    s=runner();s.guard_down=True;s.guard_until=101
    assert tick(s,.02,community_defense=True)==[('up','鼠标右键')]
    assert not s.guard_down


def test_community_defense_still_allows_combo_healing_and_execution():
    s=runner();s.next_attack=0
    assert tick(s,.02,community_defense=True)==[('tap','鼠标左键')]
    assert tick(s,.04,community_defense=True,execution_ready=True,execution_target=99)==[('tap','E')]
    s=runner(attack_enabled=False)
    tick(s,.02,community_defense=True,hp=200)
    assert tick(s,.2,community_defense=True,hp=200)==[('tap','R')]
