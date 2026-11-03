import math
import pytest
from projectiles import incoming_time
from test_combat import runner,tick


def test_incoming_bolt_has_collision_time():
    assert incoming_time((1000,0,0),(-2000,0,0),(0,0,0))==pytest.approx(.4625,abs=.001)


@pytest.mark.parametrize('p,v',[( (1000,300,0),(-2000,0,0)),((1000,0,300),(-2000,0,0)),
                              ((1000,0,0),(2000,0,0)),((0,0,0),(0,0,0))])
def test_misses_away_stationary_are_not_threats(p,v):
    assert incoming_time(p,v,(0,0,0)) is None


def test_fast_projectile_sweeps_between_samples():
    assert incoming_time((510,0,0),(-60000,0,0),(0,0,0)) is not None


def test_player_moving_out_of_trajectory_is_accounted_for():
    assert incoming_time((1000,0,0),(-2000,0,0),(0,0,0),(0,1000,0)) is None


def test_arcing_molotov_near_feet_warns_before_landing():
    t=incoming_time((400,180,300),(-500,0,-300),(0,0,0),gravity=-980,explosive=True)
    assert t is not None and 0<t<.9


def test_molotov_far_from_player_is_ignored():
    assert incoming_time((2000,500,300),(-100,0,-300),(0,0,0),gravity=-980,explosive=True) is None


@pytest.mark.parametrize('v',[(math.nan,0,0),(math.inf,0,0),(1000000,0,0)])
def test_invalid_velocity_never_produces_input(v):
    assert incoming_time((100,0,0),v,(0,0,0)) is None


def projectile(hit=100.15):
    return dict(id='projectile:123:456',source=789,hit_time=hit,dodge=True,attack_type='projectile',projectile=True)


def test_projectile_preempts_attack_without_lock_and_counts_once():
    s=runner();s.next_attack=0
    assert tick(s,.02,target=0,threats=[projectile()])==[('tap','Shift')]
    assert tick(s,.04,target=0,threats=[projectile(100.16)])==[]
    assert s.actions_count['projectile_dodge']==1


def test_projectile_reserves_upcoming_heal_and_skill_window():
    s=runner();s.next_attack=0
    assert tick(s,.02,hp=200,threats=[projectile(100.85)])==[]
    assert tick(s,.2,hp=200,threats=[projectile(101.0)])==[]
    assert s.actions_count['heal']==0 and s.actions_count['attack']==0


def test_projectile_option_can_be_disabled():
    s=runner(projectile_dodge_enabled=False)
    assert tick(s,.02,threats=[projectile()])==[]
    assert s.actions_count['projectile_dodge']==0
