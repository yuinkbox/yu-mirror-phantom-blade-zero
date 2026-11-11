# PBZSmartCombat 0.18.0 · 按住连招

自动防御、自动喝药和连招均在 UE4SS 游戏线程执行。
桌面端负责设置、启停、状态显示和读取物理连招键，不模拟攻击键。

退出游戏后用同版本桌面助手安装/升级 Mod。进入游戏按 F8 开启辅助，
自己走位与锁定，按住「鼠标侧键 1」连招，松开停止后续输入。
可以改为鼠标侧键 2、F6 或 F7。F9 停止全部辅助。

- 格挡、闪避、R 恢复药、T 减伤恢复药不依赖连招键，沿用原有防御/治疗逻辑。
- 按住期间允许轻重击、Q、处决尝试、破盾蓄力和低杀气换刀；附加动作可关闭。
- 均衡＝轻轻轻重，压制＝轻轻重重，疾风＝五段轻击，轻击＝轻击循环。
- 杀气不足转轻击。防御/喝药优先，结束后仅在仍按住时继续连招。
- 移除自动追怪和自动锁定；目标数据缺失也能按住出招。
- 松键不会强行取消游戏已开始的动画；蓄力松开可能按游戏规则释放。
- 保持助手运行，连招键避免与游戏动作重复。切屏不清除启动状态，返回重新按住即可。

`control.ini`（schema 2）增加 `combat_mode=hold`、`combo_session`。
`assist.ini`（schema 1）传递 session、递增 seq、stamp、hold、focused。
`Scripts/combo_input.lua` 每约 20ms 读取，序号 250ms 未更新即停止连招。
缺失、错误会话或过期数据不会恢复全自动。桌面与 Mod 必须同版本。
`combo_gate` 显示等待按键、连招、防御优先、喝药优先、切屏或输入超时。

招式窗口沿用原有标签/能力/阶段识别和估计兜底，尚未证明所有武器和升级
等级均有精确时序。离线测试不能代替实机战斗验证。

切刀使用 MIT 许可 PBZ-AutoWeaponSwitch；未启用的 community_defense.lua
和其原始许可保留在 third-party 中以便追溯，当前不加载该社区防御模块。

---

## 0.17.x 历史说明（追怪、锁定和全自动部分已由上述 0.18.0 行为取代）

0.17.18 removes the three "wait for the timeout" stalls around a finished combo:
a completed plan restarts as soon as the game reports no running move instead of
waiting for `combo_close` (up to ~1.9 s after a finisher), a dropped press is
retried within 0.16 s instead of the full window timeout, and a break/stun edge
cancels the remaining wait while a break punish lets its heavy finish first. On
the windowed game model the longest idle time between moves drops from 1.77 s to
0.18 s and hits per second rise from 1.03 to 1.23 with the same input accuracy.

0.17.17 fixes the quick-slot medicine and the defense loop. Medicine resolves its
key deterministically (the support/T slot can no longer be picked by a fuzzy
match), holds the key long enough to survive the drink animation (0.20 s, growing
to 0.40 s after a press that restored nothing), and refuses to be starved by
endless attack announcements: below the HP threshold, after 3.5 s of deferral it
drinks anyway. Defense now keeps a 0.42 s gap between block inputs (a second hit
inside that gap is answered with a dodge, because another block input within
0.4 s shrinks the game's perfect-parry window), holds the defensive input only
until the announced hit has resolved, and adapts `parry_lead` from the one signal
the game gives: whether that hit still took HP (`def_ok` / `def_fail`).

0.17.15 replaced the fixed combo cadence with the game's own chain windows. Each
chain move accepts the next key only inside `combo_open..combo_close`, so the mod
now presses just after that window opens and never after it closes. The running
move is confirmed from owned `Ability.Action.Hun.*` tags first, then from
`GA_Hun_Tang_*` ActiveCount, then from the `Action.Phase.Combo` phase tag, and
only then falls back to estimated windows. Heavy steps keep a Sha-Chi reserve
because the same gauge pays for guarding; a low gauge continues on the free light
chain. Defense pauses a chain instead of restarting it.

The desktop app writes `control.ini` and reads the alternating `status-*.ini`
files. Game inputs are scheduled from the UE4SS game-thread callback.

## Combat

- Targets are selected from hostile faction components first, then named enemy
  `Character` instances. Faction tags are read from both direct gameplay tags
  and the nested `TagName` field; the global `Actor` table is not scanned.
- The controller approaches a distant target when `IA_MoveInput` is available.
  It reports `approach_input_missing` instead of silently waiting if movement
  cannot be resolved.
- When defense is busy and no target is cached, enemy discovery runs at most
  once every two seconds. `enemy_rejected` records candidate rejection reasons.
- The native lock target is checked as another enemy source, with class and
  distance checks. When no enemy or native lock is present, the game targeting
  component can be asked to search at most once every five seconds.
- Light/heavy timing follows the game's per-move input window: the next key is
  pressed just after `combo_open` and never after `combo_close`, measured from
  the move that actually started. Plans are real chains (balanced `AAAB`,
  pressure `AABB`, swift `AAAAA`, light `A`); a plan step that has no such chain
  is dropped instead of guessed. Ability specs can be read from either native
  arrays or an `Items` table wrapper; failed reads are cached briefly instead of
  being retried every frame. Native arrays are traversed through UE4SS `ForEach`
  before indexed compatibility fallbacks.
- The running move is confirmed from owned `Ability.Action.Hun.Light.*` /
  `Heavy.*` tags first (reported as `combo_mode=tag`), then from
  `GA_Hun_Tang_*` ActiveCount (`ability`), then from the `Action.Phase.Combo`
  phase tag (`phase`), and only then from the stored windows (`timed`). A press
  that the game ignores is retried once inside the same window and re-centres
  the estimate; three seconds without any move signal falls back to `timed`.
- Heavy steps are only taken while `cost + shachi_reserve` stays in the gauge, so
  guarding keeps its share. A low gauge continues on the free light chain, and a
  break/stun window may spend part of the reserve.
- Q uses `IA_CombatArt` only when `UltimateChargeCount` is available and at least
  one charge is present. The status file reports `skill_state` and
  `skill_charges` so unknown resources are not mistaken for a failed key press.
- Healing prioritizes `IA_UseBottomItem`, the R quick slot in the recorded
  default mappings, then makes one bounded fallback attempt through
  `IA_YaoHuLu`. Equip medicine in the R slot. Only observed HP gain is counted.
- A shield tag or shield attribute can trigger a held shadow-arm input and
  release. Actual recognition and charge timing require in-game validation.
- Weapon switching uses the MIT-licensed `PBZ-AutoWeaponSwitch` native ability
  path. It attempts once below 20% Shachi or during Break, then waits for the
  slot/cooldown confirmation. Low-Shachi switching rearms at 55%; Break switching
  rearms when Break ends, preventing repeated toggles when cooldown data is absent.

## Control

The desktop helper owns a short control lease. Press F8 or **Start assistant** to
run the mod, and keep the helper open while playing. If the lease expires, the
mod remains loaded but stops sending inputs; the desktop status shows connected
and paused separately.

## Defense

The active defense loop comes from PBZSmartCombat 0.16.2 and responds to
announced attack frames. The community module below remains bundled for its
license and comparison but is not loaded in 0.17.9. Projectile coverage
depends on the game publishing attack frames.

`Scripts/community_defense.lua` adapts Muspelion's MIT-licensed
`PBZ-AutoParryDodge` v1.1.0. The adapter keeps its native timed parry/dodge,
projectile trajectory, and named QTE paths, while the host owns control and
pause lifecycle. The original notice is in
`third-party/PBZ-AutoParryDodge/LICENSE`.

`defense_checks_no_loss`, `defense_checks_damage`, and
`defense_checks_unknown` are post-action HP observations. They do not prove a
perfect parry: an enemy may miss or another game effect may prevent damage.
`defense_api_fail` counts failed API calls separately. Parry and dodge component
readiness are reported independently. Projectile classes are reconciled one at
a time every 0.35 seconds, limited to 32 tracked actors per class within 800 m.
Trajectory samples run at 30 Hz and return to every-frame checks near the
predicted hit time. This spreads enumeration and avoids a global `Actor` creation
callback. A very fast projectile can arrive before reconciliation.

Do not enable another automatic parry/dodge mod at the same time. The Nexus
Auto Perfect Defense mod is a separate alternative and is not bundled here;
its author requires permission before modification or redistribution.
