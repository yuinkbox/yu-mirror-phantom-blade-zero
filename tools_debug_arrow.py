"""Debug the projectile classification scenario."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_mod_lua as S


def main() -> int:
    mod_dir = Path(r"C:\dev\PBZ-assistant-next\tmp-debug\arrow")
    mod_dir.mkdir(parents=True, exist_ok=True)
    sim = S.Sim(mod_dir)
    sim.actions = [S.Actor('IA_Stance', 'InputAction'), S.Actor('IA_Deflect', 'InputAction'),
                   S.Actor('IA_Dodge', 'InputAction'), S.Actor('IA_DodgeHold', 'InputAction')]
    sim.actor = S.Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = S.AttributeSet(sim.actor, {'Health': 1000.0, 'Health_Max': 1000.0,
                                                      'Shachi': 400.0, 'Shachi_Max': 400.0})
    sim.deflect = S.DeflectComponent(sim.actor)
    S.build(sim, S.MOD_SOURCE.read_text(encoding='utf-8'))
    sim.lua.globals()['PBZ_DEBUG'] = True
    sim.write_control()
    sim.step(1 / 60, 2)
    sim.deflect.ReceiveFrames.frames[11] = S.Frame(
        sim.game_time + 0.04, source=S.BulletSource('BP_Bullet_Bandit_Arrow_C'))
    sim.step(1 / 60, 10)
    probe = sim.lua.eval('''
        function()
            local list = FindAllOf('DeflectComponent')
            local comp = list and list[1] or nil
            if not comp then return 'no-comp' end
            local out = {}
            comp.ReceiveFrames:ForEach(function(k, v)
                local f = v:get()
                local b = f['Bullet']
                local cls = b and b:GetClass() and b:GetClass():GetFName():ToString() or '?'
                out[#out + 1] = type(b) .. '/' .. tostring(cls)
                return false
            end)
            return table.concat(out, ',')
        end
    ''')
    print("lua probe:", probe())
    diag = sim.read_diag()
    print("kinds:", diag.get('frame_kinds'))
    print("src:", diag.get('frame_src_field'), "types:", diag.get('src_classes'))
    print("injections:", [n for n, _ in sim.injections])
    return 0


if __name__ == '__main__':
    sys.exit(main())
