from pathlib import Path

import lupa


MODULE = (Path(__file__).parent / "mod-dev-v0.10" / "mod" / "PBZSmartCombat"
          / "Scripts" / "weapon_switch.lua")


def runtime():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.globals()["_MODULE_SOURCE"] = MODULE.read_text(encoding="utf-8")
    lua.execute("WeaponSwitchModule=assert(load(_MODULE_SOURCE,'weapon_switch.lua'))()\n"
                "Switch=WeaponSwitchModule.new()\n"
                "function state() return {enabled=true,weapons_component=true,input_api=true,"
                "change_action=true,weapon_count=2,cooldown=0,weapon_index=1,"
                "transition=false,in_combat=true,broken=false,shachi=10,shachi_max=100} end")
    return lua


def test_unconfirmed_low_shachi_does_not_spam_or_toggle_weapon():
    lua = runtime()
    lua.execute("S=state(); calls=0\n"
                "R1=Switch:tick(100,S,function() calls=calls+1; return true end)\n"
                "R2=Switch:tick(100.49,S,function() calls=calls+1; return true end)\n"
                "R3=Switch:tick(100.50,S,function() calls=calls+1; return true end)\n"
                "R4=Switch:tick(100.76,S,function() calls=calls+1; return true end)")
    assert lua.eval("R1.state") == "attempted_low_shachi"
    assert lua.eval("R1.yield_action") is True
    assert lua.eval("R2.state") == "awaiting_confirmation"
    assert lua.eval("R3.state") == "unconfirmed_low_shachi"
    assert lua.eval("R4.state") == "latched_low_shachi"
    assert lua.eval("calls") == 1
    assert lua.eval("Switch.confirmations") == 0


def test_weapon_index_change_confirms_and_started_cooldown_prevents_retrigger():
    lua = runtime()
    lua.execute("S=state(); calls=0\n"
                "R1=Switch:tick(10,S,function() calls=calls+1; return true end)\n"
                "S.weapon_index=2; R2=Switch:tick(10.1,S,function() calls=calls+1; return true end)\n"
                "S.cooldown=14.9; R3=Switch:tick(10.2,S,function() calls=calls+1; return true end)\n"
                "S.cooldown=0; R4=Switch:tick(12,S,function() calls=calls+1; return true end)")
    assert lua.eval("R2.state") == "confirmed_low_shachi"
    assert lua.eval("R3.state") == "cooldown"
    assert lua.eval("R4.state") == "latched_low_shachi"
    assert lua.eval("calls") == 1
    assert lua.eval("Switch.confirmations") == 1


def test_cooldown_start_alone_confirms_switch():
    lua = runtime()
    lua.execute("S=state(); Switch:tick(1,S,function() return true end)\n"
                "S.cooldown=15; R=Switch:tick(1.1,S,function() return true end)")
    assert lua.eval("R.state") == "confirmed_low_shachi"
    assert lua.eval("Switch.confirmations") == 1


def test_missing_weapon_component_or_input_api_is_reported_unavailable():
    lua = runtime()
    lua.execute("S=state(); S.weapons_component=false\n"
                "R1=Switch:tick(1,S,function() error('must not call') end)\n"
                "S.weapons_component=true; S.input_api=false\n"
                "R2=Switch:tick(2,S,function() error('must not call') end)")
    assert lua.eval("R1.state") == "unavailable_weapons_component"
    assert lua.eval("R2.state") == "unavailable_api"
    assert lua.eval("Switch.attempts") == 0


def test_break_can_trigger_switch_without_a_readable_shachi_value():
    lua = runtime()
    lua.execute("S=state(); S.shachi=nil; S.shachi_max=nil; S.broken=true\n"
                "R=Switch:tick(1,S,function() return true end)")
    assert lua.eval("R.state") == "attempted_break"


def test_low_shachi_latch_rearms_after_resource_recovery():
    lua = runtime()
    lua.execute("S=state(); calls=0\n"
                "Switch:tick(1,S,function() calls=calls+1; return true end)\n"
                "S.weapon_index=2; Switch:tick(1.1,S,function() end)\n"
                "S.cooldown=0; S.shachi=60; R1=Switch:tick(2.2,S,function() end)\n"
                "S.shachi=10; R2=Switch:tick(3.2,S,function() calls=calls+1; return true end)")
    assert lua.eval("R1.state") == "ready"
    assert lua.eval("R2.state") == "attempted_low_shachi"
    assert lua.eval("calls") == 2


def test_break_latch_rearms_after_break_state_clears():
    lua = runtime()
    lua.execute("S=state(); S.broken=true; calls=0\n"
                "Switch:tick(1,S,function() calls=calls+1; return true end)\n"
                "S.cooldown=1; R1=Switch:tick(1.1,S,function() end)\n"
                "S.broken=false; S.cooldown=0; S.shachi=60; R2=Switch:tick(2.2,S,function() end)\n"
                "S.shachi=10\n"
                "S.broken=true; R3=Switch:tick(3,S,function() calls=calls+1; return true end)")
    assert lua.eval("R1.state") == "confirmed_break"
    assert lua.eval("R2.state") == "ready"
    assert lua.eval("R3.state") == "attempted_break"
    assert lua.eval("calls") == 2

