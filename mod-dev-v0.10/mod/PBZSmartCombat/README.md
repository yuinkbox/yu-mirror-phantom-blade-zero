# PBZSmartCombat (UE4SS Lua mod) - 0.12.0

In-game combat controller driven by an external control file. The desktop app only
writes `control.ini` and reads `status-*.ini`; all input goes through the game thread.

## Protocol

- `control.ini` (read every 0.2 s): `schema=2`, `run=0|1`, `lease_until=<epoch>`,
  the `*_enabled` switches, `heal_threshold`, `attack_distance`, `attack_period`,
  `combo_preset`, optional `action_<role>=<InputAction name>` overrides.
- `status-a.ini` / `status-b.ini` (written alternately, ~0.5 s): `hp`, `hp_max`,
  `enemy_count`, `target`, `target_distance`, `weapon`, `action`, `reason`,
  `attacks`, `blocks`, `dodges`, `heals`, `skills`, `executions`, `attack_ok`,
  `attack_no_damage`, `heal_ok`, `heal_fail`, `faction_seen`, `frames`, `frames_late`.
- `diag.txt` (every 2 s): resolved action name per role, the full `actions=` list,
  `inj_ok/inj_fail/inj_missing`, `enemy_scan_ms`, `enemy_classes_seen`, `enemy_list`,
  `actions_used`.

## How enemies are found (0.12.0)

`FindAllOf('FactionComponent')` returns only a handful of components per level.
The owner class name is the primary test (`BP_Monster_*` / `*Enemy*`); the
`Faction` tag is only a fallback when the class name looks innocent. Positions and
health are refreshed every tick from the cached actor list; full rediscovery runs
at 2.5 s with a target and 0.8 s without one.

## Limits

- No damage/hit validation beyond comparing target health before/after a swing;
  a miss and a whiff look the same to the mod.
- Defense is a single-frame window (`block_lead`, `dodge_lead`); late frames are
  counted in `frames_late`.
- No shadow-arm action exists in this game build (`shadow_arm=missing`).
