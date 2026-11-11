# PBZ Auto Parry & Dodge

Adapted module: `Scripts/community_defense.lua`.

- Upstream: https://github.com/Muspelion/PBZ-AutoParryDodge
- Version: v1.1.0
- Commit: `99d30203cdbd4b1ad9f5e3eaebf1a398445f1a7b`
- License: MIT; original copyright and permission notice are in `LICENSE`.

The source is adapted rather than loaded as a second standalone mod. This
integration removes upstream hotkeys, UI, persistent settings, its own tick
loop, and loader/config installation changes. The host owns the pawn, run/pause
lifecycle, and user settings. `resetSpamPenalty` defaults to `false` here; the
upstream default is `true` and intentionally widens the perfect-window penalty
rules when it resets the game's punishment counter.

Projectile actors are discovered through `NotifyOnNewObject` when available,
then reconciled with a throttled full scan. The callback queues object
references only; all UObject inspection runs from the host's game-thread tick.
When notification support is absent, discovery waits for the configured
reconciliation interval. Newly spawned, very fast projectiles may arrive
before the next reconciliation. The game-runner reports input API success
separately from later health observations and does not claim that a request was
a perfect defense.
