# PBZ Auto Weapon Switch

Adapted logic: `Scripts/weapon_switch.lua`.

- Upstream: https://github.com/Muspelion/PBZ-AutoWeaponSwitch
- Version: v1.0.1
- Commit: `d53ec71dd6baf23b9c10c5f7137d7b33cd87539a`
- License: MIT; copyright and permission notice are in `LICENSE`.

This single-Mod integration keeps the upstream switch confirmation and retry
approach for two cases: Shachi below 20% and the player's Break state. A
successful input call is not treated as a successful switch; the host waits for
the weapon index to change or the weapon-switch cooldown to start, then retries
after an unconfirmed 0.5-second attempt. Combo-extension rules, hotkeys, UI,
persistent settings, installer, and independent tick loop are not included.

The host owns game lifecycle, player discovery, and settings. Runtime support
requires the game's main weapon slot to contain two weapons and its UE4SS
components and input API to be readable. The status reports missing components
or API as unavailable rather than pretending a switch occurred.
