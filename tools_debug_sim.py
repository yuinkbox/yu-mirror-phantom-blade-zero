"""Step one simulation scenario and print the mod state each second."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim_mod_lua as S


def main() -> int:
    mod_dir = Path(r"C:\dev\PBZ-assistant-next\tmp-debug\heal")
    mod_dir.mkdir(parents=True, exist_ok=True)
    sim = S.Sim(mod_dir)
    sim.actions = [S.Actor('IA_YaoHuLu', 'InputAction'), S.Actor('IA_AttackL_Press', 'InputAction')]
    sim.actor = S.Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = S.AttributeSet(sim.actor, {'Health': 200.0, 'Health_Max': 1000.0})
    sim.deflect = S.DeflectComponent(sim.actor)
    sim.heal_on_inject = {'IA_YaoHuLu': 120.0}
    S.build(sim, S.MOD_SOURCE.read_text(encoding='utf-8'))
    sim.lua.globals()['PBZ_DEBUG'] = True
    probe = sim.lua.eval('''
        function()
            local UEH = require('UEHelpers')
            local c = UEH.GetPlayerController()
            local p = c.Pawn
            local ok, err = pcall(function() return p:IsValid() end)
            return type(c), tostring(p), type(p), tostring(ok) .. '/' .. tostring(err)
        end
    ''')
    print('probe:', probe())
    print('python sees:', sim.player_controller().Pawn is sim.actor)
    sim.write_control()
    for block in range(8):
        sim.step(1 / 60, 30)
        status = sim.read_status()
        hp = sim.actor.attributes._values.get('Health')
        print(f"t={sim.clock_value:.2f} actual_hp={hp} hp_field={status.get('hp')} "
              f"heals={status.get('heals')} heal_ok={status.get('heal_ok')} "
              f"fail={status.get('heal_fail')} action={status.get('action')} "
              f"error={status.get('error')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
