-- PBZSmartCombat 0.18.0: hold-to-combo, independent defense and medicine.
-- In-game combat controller for Phantom Blade Zero.
--   * typed defense: blue = parry (IA_Deflect), red = dodge (IA_Dodge + hold),
--     normal = parry/guard, all verified against FDeflectReceiveFrame.IsHit;
--   * window aligned combo: every chain move publishes combo_open/combo_close,
--     so the next key is pressed when the game opens that move's input window
--     instead of on a fixed cadence.  The running move is confirmed from the
--     owned Ability.Action.Hun.* tags first, then from GA_Hun_Tang_* ActiveCount;
--   * heavy steps keep a Sha-Chi reserve, because the same gauge pays for
--     guarding and the character must not spend it all on offence;
--   * everything the game does not expose falls back to the window estimate.
local UEHelpers = require('UEHelpers')

local VERSION = '0.18.7'
local SCHEMA = '2'
local ScriptDir = debug.getinfo(1, 'S').source:match('^@(.*[\\/])') or './'

-- UE4SS Lua uses the ANSI code page for file IO, so a game installed under a
-- non-ASCII path cannot read or write the control/status files.  Detect that
-- once and relay telemetry to %TEMP%: the fight itself still runs, and the
-- problem shows up in the log instead of failing silently.
local BaseDir = ScriptDir .. '../'
local RelayPrefix = ''
local function Writable(dir)
  local path = dir .. '.pbz-write-probe'
  local probe = io.open(path, 'w')
  if not probe then return false end
  probe:close()
  if os.remove then pcall(os.remove, path) end
  return true
end
if not Writable(BaseDir) then
  local temp = os.getenv and os.getenv('TEMP')
  if temp and Writable(temp .. '\\') then
    BaseDir, RelayPrefix = temp .. '\\', 'PBZSmartCombat-'
  end
end
local function ModFile(name)
  return BaseDir .. RelayPrefix .. name
end
local Tracked = {}
local WeaponSwitch = dofile(ScriptDir .. 'weapon_switch.lua').new()
local MAIN_WEAPON_SLOT = { TagName = FName('Weapon.Slot.Main') }
local SWITCH_NORMAL_TAG = { TagName = FName('Ability.Action.Hun.Weapon.Switch.Normal') }
local SWITCH_DASH_TAG = { TagName = FName('Ability.Action.Hun.Weapon.Switch.Dash') }
local SWITCH_AIR_TAG = { TagName = FName('Ability.Action.Hun.Weapon.Switch.Air') }
local BREAK_TAG = { TagName = FName('CharacterState.Stand.Break') }
local AIR_TAG = { TagName = FName('CharacterState.Space.Air') }
local SPRINT_TAG = { TagName = FName('CharacterState.Native.Attitude.Sprinting') }
local DASH_TAG = { TagName = FName('Ability.Action.Hun.Dash') }
local IN_COMBAT_TAG = { TagName = FName('CharacterState.InCombat') }
local SHIELD_TAG = { TagName = FName('CharacterState.AbilityState.Shield.HasShield') }
local ENEMY_OPENING_TAGS = {
  { tag = BREAK_TAG, name = 'Stand.Break' },
  { tag = { TagName = FName('CharacterState.Hitreact.Phase') }, name = 'Hitreact.Phase' },
}
local CHANGE_WEAPON_ACTION_PATH = '/SGameCombatSystem/Weapon/Input/IA_ChangeWeapon.IA_ChangeWeapon'
local ControlPath = ModFile('control.ini')
local DiagPath = ModFile('diag.txt')

local Config = {
  run = false, lease_until = 0, last_control = 0,
  heal_enabled = true, attack_enabled = true, guard_enabled = true,
  dodge_enabled = true, execution_enabled = true, skills_enabled = true,
  shadow_enabled = false, auto_items = false, switch_enabled = true,
  combo_enabled = true,
  heal_threshold = 0.4, attack_distance = 3.5, attack_period = 0.35,
  support_hold = 0.12,
  combo_preset = 'balanced', combo_rotate = false,
  -- macro mode: a held key drives the whole string on a cadence, with no
  -- chain tag, no target and no distance gate.
  combo_macro = false,
  pre_guard_distance = 4.0,
  pre_guard_hold = 0.22,
  pre_guard_interval = 0.80,
  pre_guard_quiet = 0.55,
  blue_response = 'deflect', red_response = 'dodge', normal_response = 'deflect',
  -- Defense timing. The parry window is ~10 frames (0.17 s) and starts on the
  -- press, so pressing 0.16 s early wastes it; parry_lead walks down toward
  -- parry_lead_min whenever an announced hit still lands, and recovers on a
  -- clean streak. deflect_gap respects the game's own rule that another block
  -- input within ~0.4 s shrinks the perfect window to a third.
  parry_lead = 0.16, parry_lead_min = 0.05, parry_lead_max = 0.16,
  deflect_gap = 0.42, guard_lead = 0.16, dodge_lead = 0.10,
  block_hold = 0.24, block_pad = 0.06, dodge_pad = 0.05, guard_pad = 0.10,
  dodge_hold = 0.12, stale_after = 0.12, max_burst = 60,
  -- combo timing: the key is accepted only inside the running move's own input
  -- window, so the press is scheduled just after combo_open (combo_lead is the
  -- safety margin, never a pre-window guess). combo_speed scales every window,
  -- shachi_reserve is the share of the gauge kept for guarding, and heavy_cost is
  -- the share one heavy input spends (82 of 400 in the shipped data).
  combo_lead = 0.02, combo_speed = 1.0, combo_recover = 0.16,
  shachi_reserve = 0.30, heavy_cost = 0.205,
  press_gap = 0.12,
  press_hold = 0.08, heal_hold = 0.20, shadow_hold = 0.12, macro_hold = 0.22,
  shadow_release = 0.80, shadow_until = 1.40, shadow_retry = 8.0,
  late_dodge = 0.03, unknown_response = 'dodge',
  approach_enabled = true, approach_max = 30.0, approach_gap = 0.35,
  hit_floor = 2.1, hit_step = 0.10,
  enemy_scan_interval = 1.2, enemy_retry_interval = 0.35,
  action_overrides = {},
  combo_session = '',
}

local ROLES = {
  attack_light  = { {'IA_AttackL_Press', 'IA_AttackL', 'IA_LightAttack', 'IA_AttackLight'},
                    {'attackl', 'lightattack', 'attacklight'} },
  attack_heavy  = { {'IA_AttackH_Press', 'IA_AttackH', 'IA_HeavyAttack', 'IA_AttackHeavy'},
                    {'attackh', 'heavyattack', 'attackheavy'} },
  shachi_attack = { {'IA_ShachiAbility', 'IA_ShachiAttack'}, {'shachiability', 'shachi'} },
  guard         = { {'IA_Stance', 'IA_Guard', 'IA_Block'}, {'stance', 'guard', 'block'} },
  deflect       = { {'IA_Deflect', 'IA_Parry', 'IA_PerfectBlock'}, {'deflect', 'parry'} },
  dodge         = { {'IA_Dodge', 'IA_Evade', 'IA_Roll'}, {'dodge', 'evade'} },
  dodge_hold    = { {'IA_DodgeHold'}, {'dodgehold'} },
  perfect_dodge = { {'IA_PerfectDodge'}, {'perfectdodge'} },
  heal          = { {'IA_UseBottomItem', 'IA_UseMedicine', 'IA_UseItem', 'IA_QuickItem',
                     'IA_BottomItem', 'IA_UseQuickSlot', 'IA_UsePotion', 'IA_Heal'},
                    {'usebottomitem', 'usemedicine', 'usepotion', 'bottomitem', 'quickitem',
                     'useitem', 'consumable'},
                    -- never bind the support/T slot or a weapon skill to "heal"
                    {'topitem', 'support', 'buff', 'shachi', 'change', 'attack', 'deflect',
                     'dodge', 'move', 'lock', 'combatart', 'skill'} },
  heal_alt      = { {'IA_YaoHuLu'}, {'yaohulu'} },
  support_item  = { {'IA_UseTopItem'}, {'usetopitem'} },
  execution     = { {'IA_Execution', 'IA_Execute'}, {'execution', 'execute'} },
  combat_art    = { {'IA_CombatArt', 'IA_Skill'}, {'combatart', 'skill'} },
  shadow_arm    = { {'IA_ShachiAbility', 'IA_ShadowArm', 'IA_ChangeArmed', 'IA_ChangeShachiWeapon'},
                    {'shachiability', 'shadowarm', 'changearmed', 'changeshachi'} },
  change_weapon = { {'IA_ChangeWeapon', 'IA_SwitchWeapon'}, {'changeweapon', 'switchweapon'} },
  lock_on       = { {'IA_LockTarget', 'IA_Lock'}, {'locktarget', 'lockon'} },
  move          = { {'IA_MoveInput', 'IA_KeyboardMoveInput', 'IA_JoystickMoveInput'}, {'moveinput'} },
}

-- The game resolves A/B presses into GA_Hun_Tang_<chain> abilities; a plan is
-- only the key sequence to feed.  The live chain is read back from the running
-- move, so the loop follows the game instead of guessing at timings.
-- Every path below exists in the shipped weapon data (AAAB, AABB, AAABB, AAAAA,
-- AB, ABB, AAB, AABB ...), so a plan step can always be confirmed by name.
local COMBO_PATHS = {
  balanced = 'AAAB',   -- 3 light hits, then the heavy finisher (ComboAtk_N3H1)
  pressure = 'AABB',   -- 2 light hits, then two heavy inputs (N2H2)
  swift    = 'AAAAA',  -- full light chain, no Sha-Chi cost
  light    = 'A',
}
-- Used when the Sha-Chi gauge is below the reserve or the heavy input is
-- missing: these paths stay inside the light chain and cost nothing.
local LIGHT_PATHS = {
  balanced = 'AAAAA', pressure = 'AAAA', swift = 'AAAAA', light = 'A',
}
local KEY_ROLE = { A = 'attack_light', B = 'attack_heavy' }
-- rotating plans keeps the strings varied without giving up the heavy finisher
local PLAN_ORDER = { 'balanced', 'pressure', 'swift', 'balanced', 'pressure' }

local Status = {
  schema = SCHEMA, mod_version = VERSION, loaded = '1', running = '0', control_run = '0',
  heartbeat = '0', player = '', hp = '', hp_max = '', shachi = '',
  enemy_count = '0', target = '', target_distance = '', weapon = 'unknown',
  q_action = '', charge_action = '', action = 'idle', reason = 'waiting_player',
  attacks = '0', blocks = '0', dodges = '0', heals = '0', skills = '0', executions = '0',
  combat_yields = '0', combat_resumes = '0', combat_yield_reason = '',
  boss_opening_tag = '', boss_punishes = '0', combo_mode = '', combo_plan = '',
  combo_signal = '', combo_confirm = '0', combo_reject = '0', combo_wait_ms = '0',
  combo_tempo = '100', chain_tag = '', phase_tag = '', heavy_ok = '',
  parry_lead_ms = '160', deflect_gap_ms = '420', def_rate = '', deflect_deferred = '0',
  combo_last_reset = '', combo_stalls = '0', combo_stall_ms = '0', openings = '0',
  heal_candidates = '', heal_blocked_ms = '0', item_actions = '',
  support_state = 'idle', support_uses = '0', support_confirmed = '0',
  attack_interrupted = '0', medicine_interrupted = '0',
  weapon_switch_state = 'idle', weapon_switch_attempts = '0', weapon_switch_confirmed = '0',
  inj_ok = '0', inj_fail = '0', enemy_scan_ms = '0', actions_used = '', error = '',
  runtime_tick_ms = '0', runtime_tick_ms_max = '0',
  defense_engine = '', defense_ready = '0', defense_parry_ready = '0', defense_dodge_ready = '0',
  defense_error = '', defense_checks_no_loss = '0', defense_checks_damage = '0',
  defense_checks_unknown = '0', defense_api_fail = '0', defense_projectiles = '0',
  defense_tick_ms = '0', defense_tick_ms_max = '0', defense_projectile_tracked = '0',
  defense_frames_late = '0', skill_state = 'unknown', skill_charges = '',
  heal_state = 'idle', attack_unverified = '0', heal_last = '',
  heal_ok = '0', heal_fail = '0', attack_ok = '0', attack_no_damage = '0',
  heal_action = '', heal_state_detail = '', heal_hold_ms = '200',
  enemy_classes_seen = '', combo_state = '', combo_step = '', parry = '0',
  dodge_red = '0', def_ok = '0', def_fail = '0', def_unknown = '0',
  def_perfect = '0', def_great = '0', def_normal = '0', def_other = '0', frame_types = '',
}

local Cache = {
  controller = nil, pawn = nil, deflect = nil, subsystem = nil,
  action_index = nil, action_names = {}, action_count = 0, next_action_scan = 0,
  roles = nil, next_roles = 0,
  attr_by_owner = {}, next_attr_scan = 0, attr_scans = 0,
  enemies = {}, next_enemy_scan = 0, enemy_scan_ms = 0, actors_scanned = 0,
  next_defense_enemy_scan = 0,
  targeting = nil, targeting_owner = '', next_targeting_scan = 0, next_native_lock = 0,
  enemy_miss_streak = 0,
  weapon_component = nil, weapon_input = nil, weapon_switch_action = nil,
  weapon_switch_owner = '', next_weapon_component_scan = 0, next_weapon_action_scan = 0,
  enemy_classes_seen = {}, actor_enemy_classes_seen = {}, actor_scan_count = 0,
  actor_fallback_scans = 0, actor_fallback_ms = 0, next_actor_fallback = 0, next_deflect_scan = 0,
  faction_seen = 0, faction_components = 0, enemy_list = '', enemy_rejected = {},
  specs = nil, next_spec_scan = 0, heal_pool = {}, item_actions = {},
  enemy_montages = {}, next_montage_scan = 0,
  src_field = '', type_field = '', src_info = '', src_classes = {},
  pawn_key = '', pawn_misses = 0, hit_dist = 0, whiff_dist = 0, lock_key = '',
  heal_prefer_alt = false, heal_primary_fails = 0, spec_field = nil, spec_scan_error = '', area_until = 0, area_dodges = 0,
}

local RefreshSpecs, ReadChain, SpecProbe  -- defined later; used by the runtime
local HeavyAffordable                      -- needs the attribute reader below
local Inj = { block_until = -1, dodge_until = -1, press_pending = false, held_last = false, burst = 0, released = true }
local FrameDt, LastFrameClock, LastGameTime = 1 / 60, nil, nil
local NextAttack, NextHeal, NextSkill, NextExecute, NextShadow, NextLock = 0, 0, 0, 0, 0, 0
local NextSupport = 0
local NextGuardHold, GuardUntil = 0, 0
local ShadowPending, ShadowDone = nil, {}
local HealPending, AttackPending = nil, nil
local HealDeferMs = 0
local HealPulseUntil, HealPulseRole = 0, 'heal'
local SupportPending = nil
local LastStatusWrite, LastDiagWrite, StatusSlot = 0, 0, 0
local NextIdleProbe, DefenseActive, WasRunning = 0, false, false
local CombatYielded, CombatYieldReason = false, ''
local OpeningActorKey, OpeningTag, OpeningScanAt, OpeningPunished = '', '', 0, false
local OpeningSince, ActionRecoveryUntil, MedicineRecoveryUntil = 0, 0, 0
local OpeningReadable = false
local Approach = { key = '', best = 0, since = 0, next_probe = 0 }
local RuntimeTickMs, RuntimeTickMsMax = 0, 0
local Perf = { roles = 0, attrs = 0, defense = 0, enemies = 0, heal = 0, combat = 0, writes = 0 }
local RecentInputs = {}
local RecordInput
local ErrorStreak = 0
local Counters = {
  attacks = 0, blocks = 0, dodges = 0, heals = 0, skills = 0, executions = 0,
  inj_ok = 0, inj_fail = 0, missing = 0, frames = 0, frames_late = 0, attack_fail = 0,
  heal_ok = 0, heal_fail = 0, attack_ok = 0, attack_no_damage = 0, attack_unverified = 0,
  parry = 0, dodge_red = 0, guard = 0, def_ok = 0, def_fail = 0, combo_press = 0, guard_hold = 0,
  deflect_deferred = 0, parry_tightened = 0,
  def_unknown = 0, def_perfect = 0, def_great = 0, def_normal = 0, def_other = 0,
  shadow_uses = 0, shield_breaks = 0, shadow_fails = 0, support_uses = 0, heal_brave = 0, openings = 0,
  combat_yields = 0, combat_resumes = 0, boss_punishes = 0,
  attack_interrupted = 0, medicine_interrupted = 0, support_confirmed = 0,
  support_unconfirmed = 0,
}
local Kinds = { normal = 0, blue = 0, red = 0, power = 0, hold = 0, unknown = 0 }
local Combo = {
  path = '', step = 0, started = 0, pressed_at = -1,
  awaiting = '', timeout = 0, ready = nil,
  plan_index = 1, targets_seen = 0, target_key = '',
  idle_until = 0, last_press = 0, signal = 'none', feedback = false,
  phase = '', phase_since = 0, phase_seen = false, phase_used = -1,
  confirms = 0, rejects = 0, resyncs = 0, resets = 0, presses = 0, streak = 0,
  tempo = 1.0, delay = 0.0, wait_ms = 0, accept_ms = 0, last_reset = '',
}

local function PlanName()
  if Config.combo_rotate then return PLAN_ORDER[Combo.plan_index] or 'balanced' end
  return Config.combo_preset
end

local function PlanPath()
  if not Config.combo_enabled or Config.combo_preset == 'light' then return COMBO_PATHS.light end
  return COMBO_PATHS[PlanName()] or COMBO_PATHS.balanced
end

-- How the current chain is being tracked; shown in the status and the diag so a
-- live fight explains itself: move tags > listed abilities > phase tag > clock.
local function ComboMode()
  if Combo.feedback then
    return Combo.signal == 'tag' and 'tag' or 'ability'
  end
  if Combo.phase_seen then return 'phase' end
  return 'timed'
end

local function Try(fn, ...)
  -- single return on purpose: multi-value passing through pcall is a source of
  -- silent type confusion on some Luna/UE4SS combinations.
  local ok, a = pcall(fn, ...)
  if ok then return a end
  return nil
end

local function Valid(o)
  return o ~= nil and type(o) == 'userdata' and Try(function() return o:IsValid() end) == true
end

local function SamePtr(a, b)
  return Valid(a) and Valid(b) and Try(function() return a:GetAddress() == b:GetAddress() end) == true
end

local Address

local function FindOwnedComponent(class_name, pawn)
  for _, component in ipairs(FindAllOf(class_name) or {}) do
    if Valid(component) then
      local outer = Try(function() return component:GetOuter() end)
      local owner = Try(function() return component:GetOwner() end)
      if SamePtr(outer, pawn) or SamePtr(owner, pawn) then return component end
    end
  end
  return nil
end

local function WeaponSwitchBindings(pawn, now)
  local owner = Address(pawn) or ''
  if owner ~= Cache.weapon_switch_owner then
    Cache.weapon_component, Cache.weapon_input = nil, nil
    Cache.next_weapon_component_scan = 0
  end
  if (not Valid(Cache.weapon_component) or not Valid(Cache.weapon_input))
      and now >= Cache.next_weapon_component_scan then
    Cache.weapon_switch_owner = owner
    Cache.next_weapon_component_scan = now + 2
    Cache.weapon_component = FindOwnedComponent('CharacterWeaponsComponent', pawn)
    Cache.weapon_input = FindOwnedComponent('AbilityInputComponent', pawn)
  end
  if not Valid(Cache.weapon_switch_action) and now >= Cache.next_weapon_action_scan then
    Cache.next_weapon_action_scan = now + 5
    Cache.weapon_switch_action = Try(function() return StaticFindObject(CHANGE_WEAPON_ACTION_PATH) end)
  end
  return Cache.weapon_component, Cache.weapon_input, Cache.weapon_switch_action
end

local function HasGameplayTag(component, tag)
  return Valid(component) and Try(function() return component:HasMatchingGameplayTag(tag) end) == true
end

Address = function(o)
  if not Valid(o) then return nil end
  return Try(function() return tostring(o:GetAddress()) end)
end

local function Name(o)
  if not Valid(o) then return '' end
  return Try(function() return o:GetFName():ToString() end) or ''
end

local function Clean(value)
  return tostring(value):gsub('[\r\n=]', ' ')
end

local function ReadIni(path)
  local f = io.open(path, 'r')
  if not f then return nil end
  local result = {}
  for raw in f:lines() do
    local line = raw:gsub('^\239\187\191', '')
    local k, v = line:match('^%s*([%w_]+)%s*=%s*(.-)%s*$')
    if k then result[k:lower()] = v end
  end
  f:close()
  return result
end

Cache.combo_input = dofile(ScriptDir .. 'combo_input.lua').new(ReadIni, ModFile('assist.ini'))

local function ReadControl(now)
  if now - Config.last_control < 0.20 then
    if os.time() > Config.lease_until then Config.run = false end
    return
  end
  Config.last_control = now
  local data = ReadIni(ControlPath)
  if not data or data.schema ~= SCHEMA then
    Config.run = false
    return
  end
  Config.lease_until = tonumber(data.lease_until) or 0
  Config.run = data.run == '1' and os.time() <= Config.lease_until
  Config.combo_session = data.combat_mode == 'hold' and (data.combo_session or '') or ''
  for _, key in ipairs({
    'heal_enabled', 'attack_enabled', 'guard_enabled', 'dodge_enabled',
    'execution_enabled', 'skills_enabled', 'shadow_enabled', 'switch_enabled',
    'auto_items',
    'combo_enabled', 'combo_rotate', 'combo_macro',
  }) do
    if data[key] ~= nil then Config[key] = data[key] == '1' end
  end
  local threshold = tonumber(data.heal_threshold)
  if threshold and threshold >= 0.1 and threshold <= 0.9 then Config.heal_threshold = threshold end
  local distance = tonumber(data.attack_distance)
  if distance and distance >= 1 and distance <= 15 then Config.attack_distance = distance end
  local period = tonumber(data.attack_period)
  if period and period >= 0.25 and period <= 1.5 then Config.attack_period = period end
  local speed = tonumber(data.combo_speed)
  if speed and speed >= 0.7 and speed <= 1.4 then Config.combo_speed = speed end
  local reserve = tonumber(data.shachi_reserve)
  if reserve and reserve >= 0 and reserve <= 0.85 then Config.shachi_reserve = reserve end
  if COMBO_PATHS[data.combo_preset] then Config.combo_preset = data.combo_preset end
  if data.combo_plan and COMBO_PATHS[data.combo_plan] then Config.combo_preset = data.combo_plan end
  local function response(value, fallback, allowed)
    if value == nil then return fallback end
    for _, option in ipairs(allowed) do
      if value == option then return value end
    end
    return fallback
  end
  Config.blue_response = response(data.blue_response, Config.blue_response, { 'deflect', 'dodge', 'guard' })
  Config.red_response = response(data.red_response, Config.red_response, { 'dodge', 'deflect', 'guard' })
  Config.normal_response = response(data.normal_response, Config.normal_response, { 'deflect', 'guard', 'dodge' })
  local overrides = {}
  for role in pairs(ROLES) do
    local value = data['action_' .. role]
    if value and value ~= '' and value ~= 'auto' then overrides[role] = value end
  end
  Config.action_overrides = overrides
end

local function WriteStatus(force)
  local now = os.clock()
  if not force and now - LastStatusWrite < 0.5 then return end
  LastStatusWrite = now
  Status.heartbeat = tostring(os.time())
  Status.running = Config.run and Status.reason == 'running' and '1' or '0'
  Status.attacks, Status.blocks, Status.dodges = tostring(Counters.attacks), tostring(Counters.blocks), tostring(Counters.dodges)
  Status.combat_yields, Status.combat_resumes = tostring(Counters.combat_yields), tostring(Counters.combat_resumes)
  Status.combat_yield_reason = CombatYieldReason
  Status.boss_punishes = tostring(Counters.boss_punishes)
  Status.support_uses = tostring(Counters.support_uses)
  Status.support_confirmed = tostring(Counters.support_confirmed)
  Status.attack_interrupted = tostring(Counters.attack_interrupted)
  Status.medicine_interrupted = tostring(Counters.medicine_interrupted)
  Status.heals, Status.skills, Status.executions = tostring(Counters.heals), tostring(Counters.skills), tostring(Counters.executions)
  Status.weapon_switch_attempts = tostring(WeaponSwitch.attempts)
  Status.weapon_switch_confirmed = tostring(WeaponSwitch.confirmations)
  Status.inj_ok, Status.inj_fail = tostring(Counters.inj_ok), tostring(Counters.inj_fail)
  Status.enemy_scan_ms = string.format('%.1f', Cache.enemy_scan_ms or 0)
  Status.frames, Status.frames_late = tostring(Counters.frames), tostring(Counters.frames_late)
  Status.faction_seen, Status.attack_fail = tostring(Cache.faction_seen), tostring(Counters.attack_fail)
  Status.heal_ok, Status.heal_fail = tostring(Counters.heal_ok), tostring(Counters.heal_fail)
  Status.attack_ok, Status.attack_no_damage = tostring(Counters.attack_ok), tostring(Counters.attack_no_damage)
  Status.attack_unverified = tostring(Counters.attack_unverified)
  Status.defense_tick_ms = string.format('%.2f', tonumber(Status.defense_tick_ms) or 0)
  Status.defense_tick_ms_max = string.format('%.2f', tonumber(Status.defense_tick_ms_max) or 0)
  Status.heal_last = Cache.heal_last or ''
  Status.heal_action = Cache.heal_last_name or ''
  Status.heal_state_detail = Status.heal_state or ''
  Status.heal_hold_ms = tostring(math.floor(math.max(Config.heal_hold, Cache.heal_hold or 0) * 1000))
  Status.enemy_classes_seen = table.concat(Cache.enemy_classes_seen, ',')
  Status.parry, Status.dodge_red = tostring(Counters.parry), tostring(Counters.dodge_red)
  Status.def_ok, Status.def_fail = tostring(Counters.def_ok), tostring(Counters.def_fail)
  Status.def_unknown = tostring(Counters.def_unknown)
  local scored = Counters.def_ok + Counters.def_fail
  Status.def_rate = scored > 0 and string.format('%.0f', 100 * Counters.def_ok / scored) or ''
  Status.parry_lead_ms = string.format('%.0f', Config.parry_lead * 1000)
  Status.deflect_gap_ms = string.format('%.0f', Config.deflect_gap * 1000)
  Status.deflect_deferred = tostring(Counters.deflect_deferred)
  Status.heal_candidates = table.concat(Cache.heal_pool or {}, ',')
  Status.heal_blocked_ms = tostring(math.floor((HealDeferMs or 0)))
  Status.item_actions = table.concat(Cache.item_actions or {}, ',')
  Status.def_perfect, Status.def_great = tostring(Counters.def_perfect), tostring(Counters.def_great)
  Status.def_normal, Status.def_other = tostring(Counters.def_normal), tostring(Counters.def_other)
  Status.combo_state = Combo.path ~= '' and Combo.path or 'idle'
  Status.combo_step = string.format('%d/%d', #Combo.path, #PlanPath())
  Status.combo_mode, Status.combo_plan = ComboMode(), PlanName()
  Status.hold_to_combo = '1'
  Status.combo_macro = Config.combo_macro and '1' or '0'
  Status.assist_hold = Cache.assist_hold and '1' or '0'
  Status.shadow_fails = tostring(Counters.shadow_fails or 0)
  Status.combo_signal = Combo.feedback and (Combo.signal or '') or ''
  Status.combo_confirm, Status.combo_reject = tostring(Combo.confirms), tostring(Combo.rejects)
  Status.combo_wait_ms = tostring(Combo.wait_ms or 0)
  Status.combo_tempo = tostring(math.floor(Combo.tempo * 100 + 0.5))
  Status.combo_last_reset = Combo.last_reset or ''
  Status.combo_stalls = tostring(Combo.stalls or 0)
  Status.combo_stall_ms = tostring(Combo.stall_ms or 0)
  Status.openings = tostring(Counters.openings)
  Status.chain_tag = Cache.chain_tag_name or ''
  Status.phase_tag = Combo.phase ~= '' and Combo.phase or 'none'
  local kinds = {}
  for name, count in pairs(Kinds) do
    if count > 0 then kinds[#kinds + 1] = name .. '=' .. count end
  end
  table.sort(kinds)
  Status.frame_types = table.concat(kinds, ',')
  local parts = {}
  for role in pairs(ROLES) do
    local entry = Cache.roles and Cache.roles[role]
    parts[#parts + 1] = role .. '=' .. (entry and entry.name or 'missing')
  end
  table.sort(parts)
  local used = table.concat(parts, ',')
  if #used > 400 then used = used:sub(1, 400) end
  Status.actions_used = used
  StatusSlot = 1 - StatusSlot
  local path = ModFile(StatusSlot == 0 and 'status-a.ini' or 'status-b.ini')
  local f = io.open(path, 'w')
  if not f then return end
  for key, value in pairs(Status) do
    f:write(key, '=', Clean(value), '\n')
  end
  f:close()
end

local function RoleEntry(role)
  return Cache.roles and Cache.roles[role] or nil
end

local function HasRole(role)
  local entry = RoleEntry(role)
  return entry ~= nil and entry.action ~= nil and Valid(entry.action)
end

local function WriteDiag(force)
  local now = os.clock()
  if not force and now - LastDiagWrite < 2 then return end
  LastDiagWrite = now
  local f = io.open(DiagPath, 'w')
  if not f then return end
  local function line(key, value) f:write(key, '=', Clean(value), '\n') end
  line('version', VERSION)
  line('control_run', tostring(Config.run))
  line('heartbeat', os.time())
  line('scanned_actions', Cache.action_count)
  line('inj_ok', Counters.inj_ok)
  line('inj_fail', Counters.inj_fail)
  line('inj_missing', Counters.missing)
  line('enemy_scan_ms', string.format('%.1f', Cache.enemy_scan_ms or 0))
  line('faction_seen', Cache.faction_seen)
  line('faction_components', Cache.faction_components)
  line('enemy_list', Cache.enemy_list)
  line('enemy_rejected', table.concat(Cache.enemy_rejected, ','))
  line('native_lock_state', Cache.native_lock_state or 'unavailable')
  line('weapon_switch_state', Status.weapon_switch_state)
  line('weapon_switch_attempts', WeaponSwitch.attempts)
  line('weapon_switch_confirmed', WeaponSwitch.confirmations)
  line('weapon_switch_api_errors', WeaponSwitch.api_errors)
  line('enemy_classes_seen', table.concat(Cache.enemy_classes_seen, ','))
  line('actor_scan_count', Cache.actor_scan_count)
  line('actor_enemy_classes_seen', table.concat(Cache.actor_enemy_classes_seen, ','))
  line('actor_fallback_scans', Cache.actor_fallback_scans)
  line('actor_fallback_ms', Cache.actor_fallback_ms)
  line('runtime_tick_ms', string.format('%.2f', RuntimeTickMs))
  line('runtime_tick_ms_max', string.format('%.2f', RuntimeTickMsMax))
  line('attack_ok', Counters.attack_ok)
  line('attack_no_damage', Counters.attack_no_damage)
  line('attack_unverified', Counters.attack_unverified)
  for _, key in ipairs({'defense_engine', 'defense_ready', 'defense_parry_ready', 'defense_dodge_ready',
      'defense_error', 'defense_checks_no_loss', 'defense_checks_damage', 'defense_checks_unknown',
      'defense_api_fail', 'defense_projectiles', 'defense_projectile_tracked', 'defense_tick_ms',
      'defense_tick_ms_max', 'defense_frames_late', 'heal_state',
      'skill_state', 'skill_charges'}) do line(key, Status[key] or '') end
  line('attack_fail', Counters.attack_fail)
  line('shadow_uses', Counters.shadow_uses)
  line('shield_breaks', Counters.shield_breaks)
  line('shadow_fails', Counters.shadow_fails)
  line('target_shield', tostring((Cache.enemies[1] and Cache.enemies[1].shield) or 'nil'))
  line('shield_field', (Cache.enemies[1] and Cache.enemies[1].shield_field) or 'none')
  line('heal_ok', Counters.heal_ok)
  line('heal_fail', Counters.heal_fail)
  line('heal_last', Cache.heal_last or 'none')
  line('heal_action', Cache.heal_last_name or 'none')
  line('heal_brave', Counters.heal_brave)
  line('heal_hold_ms', math.floor((math.max(Config.heal_hold, Cache.heal_hold or 0)) * 1000))
  line('heal_prefer_alt', tostring(Cache.heal_prefer_alt == true))
  line('heal_primary_fails', tostring(Cache.heal_primary_fails or 0))
  line('support_uses', Counters.support_uses)
  line('support_state', Status.support_state)
  line('support_confirmed', Counters.support_confirmed)
  line('support_unconfirmed', Counters.support_unconfirmed)
  line('attack_interrupted', Counters.attack_interrupted)
  line('medicine_interrupted', Counters.medicine_interrupted)
  line('recent_inputs', table.concat(RecentInputs, ','))
  line('shadow_action', (RoleEntry('shadow_arm') and RoleEntry('shadow_arm').name) or 'missing')
  line('reach_m', string.format('%.2f', math.min(Config.attack_distance, Cache.reach or Config.hit_floor)))
  line('hit_dist_m', string.format('%.2f', Cache.hit_dist or 0))
  line('whiff_dist_m', string.format('%.2f', Cache.whiff_dist or 0))
  line('frames', Counters.frames)
  line('frames_late', Counters.frames_late)
  line('parry', Counters.parry)
  line('dodge_red', Counters.dodge_red)
  line('parry_lead_ms', Status.parry_lead_ms)
  line('deflect_gap_ms', Status.deflect_gap_ms)
  line('deflect_deferred', Counters.deflect_deferred)
  line('parry_tightened', Counters.parry_tightened)
  line('def_rate_pct', Status.def_rate)
  line('heal_candidates', Status.heal_candidates)
  line('heal_blocked_ms', Status.heal_blocked_ms)
  line('item_actions', Status.item_actions)
  line('guard', Counters.guard)
  line('guard_hold', Counters.guard_hold)
  line('def_ok', Counters.def_ok)
  line('def_fail', Counters.def_fail)
  line('def_unknown', Counters.def_unknown)
  line('def_perfect', Counters.def_perfect)
  line('def_great', Counters.def_great)
  line('def_normal', Counters.def_normal)
  line('def_other', Counters.def_other)
  local kinds = {}
  for name, count in pairs(Kinds) do kinds[#kinds + 1] = name .. '=' .. count end
  table.sort(kinds)
  line('frame_kinds', table.concat(kinds, ','))
  line('frame_src_field', Cache.src_field ~= '' and Cache.src_field or 'none')
  line('frame_type_field', Cache.type_field ~= '' and Cache.type_field or 'none')
  line('src_info', Cache.src_info ~= '' and Cache.src_info or 'none')
  local classes = {}
  for name, count in pairs(Cache.src_classes) do classes[#classes + 1] = name .. ':' .. count end
  table.sort(classes)
  line('src_classes', table.concat(classes, ','))
  line('combo_preset', Config.combo_preset)
  line('combo_plan', PlanName())
  line('combo_rotate', tostring(Config.combo_rotate == true))
  line('combo_macro', tostring(Config.combo_macro == true))
  line('macro_step', tostring(Cache.macro_step or 0))
  line('macro_pattern', Cache.macro_pattern or '')
  line('combo_mode', ComboMode())
  line('combo_plan_mode', Combo.plan_mode or '')
  line('combo_signal', Combo.feedback and (Combo.signal or 'none') or 'none')
  line('combo_path', Combo.path ~= '' and Combo.path or '-')
  line('combo_step', string.format('%d/%d', #Combo.path, #PlanPath()))
  line('combo_wait_ms', Combo.wait_ms or 0)
  line('combo_confirm', Combo.confirms)
  line('combo_reject', Combo.rejects)
  line('combo_resync', Combo.resyncs)
  line('combo_resets', Combo.resets)
  line('combo_last_reset', Combo.last_reset ~= '' and Combo.last_reset or '-')
  line('combo_accept_ms', Combo.accept_ms)
  line('combo_tempo_pct', math.floor(Combo.tempo * 100 + 0.5))
  line('combo_delay_ms', math.floor(Combo.delay * 1000 + 0.5))
  line('combo_lead_ms', math.floor(Config.combo_lead * 1000 + 0.5))
  line('combo_speed', string.format('%.2f', Config.combo_speed))
  line('shachi_reserve', string.format('%.2f', Config.shachi_reserve))
  line('chain_tag', Cache.chain_tag_name ~= '' and Cache.chain_tag_name or 'none')
  line('chain_path', Cache.chain_tag ~= '' and Cache.chain_tag or 'none')
  line('chain_tag_seen', table.concat(Cache.chain_tags or {}, ','))
  line('phase_tag', Combo.phase ~= '' and Combo.phase or 'none')
  line('phase_seen', tostring(Combo.phase_seen == true))
  line('tag_readable', tostring(Cache.tag_readable == true))
  line('combo_period_ms', string.format('%.0f', Config.attack_period * 1000))
  local delays = {}
  for path, value in pairs(Combo.delay_by_path or {}) do
    delays[#delays + 1] = string.format('%s:%.0f', path, value * 1000)
  end
  table.sort(delays)
  line('plan_delays_ms', table.concat(delays, ','))
  line('io_relay', RelayPrefix ~= '' and (BaseDir .. RelayPrefix) or 'native')
  line('spec_field', Cache.spec_field or 'none')
  line('spec_scan_error', Cache.spec_scan_error or '')
  line('combo_chain', Combo.path ~= '' and Combo.path or 'idle')
  line('combo_press', Counters.combo_press)
  line('combat_yields', Counters.combat_yields)
  line('combat_resumes', Counters.combat_resumes)
  line('combat_yield_reason', CombatYieldReason)
  line('boss_opening_tag', OpeningTag)
  line('boss_punishes', Counters.boss_punishes)
  line('openings', Counters.openings)
  line('combo_last_reset', Combo.last_reset ~= '' and Combo.last_reset or '-')
  line('combo_stalls', Combo.stalls or 0)
  line('combo_stall_ms', Combo.stall_ms or 0)
  line('combo_opening_ms', math.max(0, math.floor(((Combo.opening_until or 0) - os.clock()) * 1000)))
  line('tick_roles_ms', string.format('%.2f', Perf.roles))
  line('tick_attrs_ms', string.format('%.2f', Perf.attrs))
  line('tick_defense_ms', string.format('%.2f', Perf.defense))
  line('tick_enemies_ms', string.format('%.2f', Perf.enemies))
  line('tick_heal_ms', string.format('%.2f', Perf.heal))
  line('tick_combat_ms', string.format('%.2f', Perf.combat))
  line('tick_writes_ms', string.format('%.2f', Perf.writes))
  -- Diagnostics must not perform another expensive UObject traversal.
  local specs = Cache.specs or {}
  line('spec_count', #specs)
  local sample = {}
  for i = 1, math.min(#specs, 4) do sample[i] = specs[i].name end
  line('spec_sample', table.concat(sample, ','))
  line('spec_live', Combo.path ~= '' and Combo.path or (Cache.chain_tag ~= '' and Cache.chain_tag or '-'))
  line('spec_probe', Cache.spec_probe or '')
  line('enemy_montages', table.concat(Cache.enemy_montages, ','))
  line('move_times_ms', '')
  for role in pairs(ROLES) do
    local entry = RoleEntry(role)
    line('action_' .. role, entry and entry.name or 'missing')
  end
  line('actions', table.concat(Cache.action_names, ','))
  f:close()
end

local function BuildActionIndex(now)
  if Cache.action_index and now < Cache.next_action_scan then return Cache.action_index end
  Cache.next_action_scan = now + 5
  local index = {}
  local names = {}
  local count = 0
  local found = Try(function() return FindAllOf('InputAction') end)
  for _, object in ipairs(found or {}) do
    if Valid(object) then
      local name = Name(object)
      if name ~= '' then
        if not index[name] then names[#names + 1] = name end
        index[name] = object
        count = count + 1
      end
    end
  end
  Cache.action_count = count
  table.sort(names)
  Cache.action_names = names
  Cache.action_index = index
  return index
end

local function RoleExcluded(spec, name)
  if not spec[3] then return false end
  local lower = name:lower()
  for _, hint in ipairs(spec[3]) do
    if lower:find(hint, 1, true) then return true end
  end
  return false
end

local function ResolveRole(role, index)
  local spec = ROLES[role]
  local override = Config.action_overrides[role]
  if override and index[override] and not RoleExcluded(spec, override) then
    return index[override], override
  end
  for _, name in ipairs(spec[1]) do
    if index[name] and not RoleExcluded(spec, name) then return index[name], name end
  end
  -- Hint matching must be deterministic: the longest hint wins and the shorter
  -- name breaks a tie. The old `pairs` order picked a different action from run
  -- to run, which is how "heal" could end up bound to the support slot.
  local best, best_score
  for name, object in pairs(index) do
    if not RoleExcluded(spec, name) then
      local lower = name:lower()
      for _, hint in ipairs(spec[2]) do
        if lower:find(hint, 1, true) then
          local score = #hint * 1000 - #name
          if not best_score or score > best_score then best, best_score = name, score end
        end
      end
    end
  end
  if best then return index[best], best end
  return nil
end

local function RefreshRoles(now)
  local index = BuildActionIndex(now)
  if Cache.roles and now < Cache.next_roles then return Cache.roles end
  Cache.next_roles = now + 3
  local roles = {}
  for role in pairs(ROLES) do
    local action, name = ResolveRole(role, index)
    roles[role] = { action = action, name = name or 'missing' }
  end
  Cache.roles = roles
  local q = roles.combat_art
  Status.q_action = q and q.name or 'missing'
  local charge = roles.shadow_arm
  Status.charge_action = charge and charge.name or 'missing'
  -- Which keys the heal ladder actually resolved, plus every input action whose
  -- name looks like an item: the live diag then names the real R binding.
  local pool = {}
  for _, role in ipairs({ 'heal', 'heal_alt' }) do
    local entry = roles[role]
    -- '=' would be stripped by the ini writer, so keep a readable separator
    pool[#pool + 1] = role .. ':' .. (entry and entry.name or 'missing')
  end
  Cache.heal_pool = pool
  local items = {}
  for _, name in ipairs(Cache.action_names or {}) do
    local lower = name:lower()
    if lower:find('item', 1, true) or lower:find('medicine', 1, true)
        or lower:find('potion', 1, true) or lower:find('hulu', 1, true)
        or lower:find('heal', 1, true) or lower:find('consum', 1, true) then
      if #items < 10 then items[#items + 1] = name end
    end
  end
  Cache.item_actions = items
  return roles
end

local function GetSubsystem()
  if Valid(Cache.subsystem) then return Cache.subsystem end
  Cache.subsystem = Try(function() return FindFirstOf('EnhancedInputLocalPlayerSubsystem') end)
  return Valid(Cache.subsystem) and Cache.subsystem or nil
end

local function InjectActionVector(action, x, y)
  local subsystem = GetSubsystem()
  if not Valid(action) or not subsystem then
    Counters.inj_fail = Counters.inj_fail + 1
    return false
  end
  local ok = pcall(function()
    subsystem:InjectInputVectorForAction(action, { X = x or 0.0, Y = y or 0.0, Z = 0.0 }, {}, {})
  end)
  if ok then Counters.inj_ok = Counters.inj_ok + 1 else Counters.inj_fail = Counters.inj_fail + 1 end
  return ok
end

local function InjectAction(action, value)
  return InjectActionVector(action, value or 1.0, 0.0)
end

local function InjectRoleValue(role, value)
  local entry = RoleEntry(role)
  if not entry or not Valid(entry.action) then
    Counters.missing = Counters.missing + 1
    return false
  end
  return InjectAction(entry.action, value)
end

local function InjectRoleVector(role, x, y)
  local entry = RoleEntry(role)
  if not entry or not Valid(entry.action) then
    Counters.missing = Counters.missing + 1
    return false
  end
  return InjectActionVector(entry.action, x, y)
end

local function InjectRole(role)
  local entry = RoleEntry(role)
  if not entry or not Valid(entry.action) then
    Counters.missing = Counters.missing + 1
    return false
  end
  return InjectAction(entry.action)
end

-- One input pulse per discrete action; track its cooldown until a release frame.
local Pressed = {}
RecordInput = function(action)
  RecentInputs[#RecentInputs + 1] = string.format('%s@%d', action, math.floor(os.clock() * 1000))
  if #RecentInputs > 18 then table.remove(RecentInputs, 1) end
end

local function NextRelease()
  if next(Pressed) == nil then return end
  local now = os.clock()
  for role, release_at in pairs(Pressed) do
    if now >= release_at then
      InjectRoleValue(role, 0.0)
      Pressed[role] = nil
      RecordInput('up:' .. role)
    end
  end
end

local function Press(role, hold)
  if InjectRole(role) then
    Pressed[role] = os.clock() + (hold or Config.press_hold)
    if role == 'attack_light' or role == 'attack_heavy' or role == 'shachi_attack'
        or role == 'combat_art' then
      Cache.last_attack_press = os.clock()
    end
    RecordInput('down:' .. role)
    return true
  end
  return false
end

-- Movement is injected each game tick and explicitly cleared when yielding.
local Held = {}
local function HoldRole(role, x, y)
  if Held[role] then
    if x ~= nil or y ~= nil then return InjectRoleVector(role, x or 0.0, y or 0.0) end
    return true
  end
  local ok
  if x ~= nil or y ~= nil then
    ok = InjectRoleVector(role, x or 0.0, y or 0.0)
  else
    ok = InjectRole(role)
  end
  if ok then
    Held[role] = true
    return true
  end
  return false
end

local function ReleaseRole(role)
  if not Held[role] then return false end
  Held[role] = nil
  InjectRoleValue(role, 0.0)
  return true
end

local OFFENSE_PULSES = {
  'attack_light', 'attack_heavy', 'shachi_attack', 'combat_art', 'execution',
  'change_weapon', 'lock_on',
}

local function ReleaseOffense(interrupt_medicine)
  for _, role in ipairs(OFFENSE_PULSES) do
    if Pressed[role] then
      InjectRoleValue(role, 0.0)
      Pressed[role] = nil
      Counters.attack_interrupted = Counters.attack_interrupted + 1
      RecordInput('interrupt:' .. role)
    end
  end
  if interrupt_medicine then
    -- Medicine is a one-shot item action with a short press edge. Let its
    -- scheduled release run; clearing it here when defense arrives can cancel
    -- the item before the game consumes the input.
    ActionRecoveryUntil = 0
  end
  ReleaseRole('move')
  ReleaseRole('shadow_arm')
end

local function Position(actor)
  local v = Try(function() return actor:K2_GetActorLocation() end)
  if type(v) == 'table' and type(v.X) == 'number' and type(v.Y) == 'number' and type(v.Z) == 'number' then
    return v.X, v.Y, v.Z
  end
  return nil
end

local function MoveAxes(pawn, target)
  local px, py = Position(pawn)
  local tx, ty = Position(target)
  if not px or not tx then return nil, nil end
  local dx, dy = tx - px, ty - py
  local yaw = Try(function() return Cache.controller:GetControlRotation().Yaw end)
  if type(yaw) ~= 'number' then yaw = Try(function() return pawn:K2_GetActorRotation().Yaw end) end
  if type(yaw) ~= 'number' then yaw = Try(function() return pawn:GetActorRotation().Yaw end) end
  if type(yaw) ~= 'number' then return nil, nil end
  local radians = math.rad(yaw)
  local forward_x, forward_y = math.cos(radians), math.sin(radians)
  local right_x, right_y = -forward_y, forward_x
  local forward = dx * forward_x + dy * forward_y
  local right = dx * right_x + dy * right_y
  local length = math.sqrt(forward * forward + right * right)
  if length <= 0.001 then return 0.0, 0.0 end
  -- IA_MoveInput is a 2D action: X is strafe, Y is forward/back.
  return right / length, forward / length
end

local function RefreshAttrIndex(now)
  if now < Cache.next_attr_scan and next(Cache.attr_by_owner) ~= nil then return end
  Cache.next_attr_scan = now + 5.0
  Cache.attr_scans = Cache.attr_scans + 1
  local by = {}
  for _, set in ipairs(FindAllOf('BasicAttributeSet') or {}) do
    if Valid(set) then
      local outer = Try(function() return set:GetOuter() end)
      local key = Address(outer)
      if key and not by[key] then by[key] = set end
      local owner = Try(function() return outer:GetOwner() end)
      local owner_key = Address(owner)
      if owner_key and not by[owner_key] then by[owner_key] = set end
    end
  end
  Cache.attr_by_owner = by
end

local function AttrSet(actor)
  if not Valid(actor) then return nil end
  local key = Address(actor)
  if key and Valid(Cache.attr_by_owner[key]) then return Cache.attr_by_owner[key] end
  local asc = Try(function() return actor.AbilitySystemComponent end)
  local asc_key = Address(asc)
  if asc_key and Valid(Cache.attr_by_owner[asc_key]) then return Cache.attr_by_owner[asc_key] end
  local state = Try(function() return actor.PlayerState end)
  if Valid(state) then
    local state_key = Address(state)
    if state_key and Cache.attr_by_owner[state_key] then return Cache.attr_by_owner[state_key] end
  end
  return nil
end

local function Attribute(set, name)
  if not Valid(set) then return nil end
  local value = Try(function() return set[name].CurrentValue end)
  if type(value) == 'number' and value == value and math.abs(value) < 1e12 then return value end
  return nil
end

local SHIELD_FIELDS = { 'Shield', 'BreakShield', 'ShieldValue' }

local function ShieldOf(set)
  for _, name in ipairs(SHIELD_FIELDS) do
    local value = Attribute(set, name)
    if type(value) == 'number' and value > 0 then return value, name end
  end
  return nil
end

local function ReadHP(actor)
  local set = AttrSet(actor)
  if not set then return nil, nil end
  return Attribute(set, 'Health'), Attribute(set, 'Health_Max')
end

-- Sha-Chi pays for heavy chain steps and also for guarding, so a finisher is
-- only affordable while a reserve stays behind. `opening` marks a break/stun
-- window where spending the reserve is worth it. A gauge that cannot be read
-- must not silently disable the finisher.
function HeavyAffordable(player_set, opening, presses)
  if not HasRole('attack_heavy') then return false end
  local value, maximum = Attribute(player_set, 'Shachi'), Attribute(player_set, 'Shachi_Max')
  if type(value) ~= 'number' or type(maximum) ~= 'number' or maximum <= 0 then return true end
  local reserve = Config.shachi_reserve * (opening and 0.35 or 1.0)
  local need = (Config.heavy_cost + reserve) * math.max(1, presses or 1)
  return value >= maximum * need
end

local function IsPawn(o)
  -- controller.Pawn is occasionally not an actor; only accept something that
  -- answers an actor query.
  if not Valid(o) then return false end
  local location = Try(function() return o:K2_GetActorLocation() end)
  return type(location) == 'table' and type(location.X) == 'number'
end

local function RefreshPlayer()
  local candidate
  if Valid(Cache.controller) then
    candidate = Try(function() return Cache.controller.Pawn end)
  end
  if not IsPawn(candidate) then
    Cache.controller = Try(function() return UEHelpers.GetPlayerController() end)
    candidate = Cache.controller and Try(function() return Cache.controller.Pawn end) or nil
  end
  if IsPawn(candidate) then
    local key = Address(candidate)
    if key ~= Cache.pawn_key then
      Cache.pawn_key, Cache.deflect = key, nil
    end
    Cache.pawn, Cache.pawn_misses = candidate, 0
    return candidate
  end
  -- A single unreadable field must not stop the controller: keep the last good
  -- pawn for half a second before declaring the player gone.
  Cache.pawn_misses = (Cache.pawn_misses or 0) + 1
  if Cache.pawn_misses >= 30 then
    Cache.controller, Cache.pawn = nil, nil
    return nil
  end
  return Valid(Cache.pawn) and Cache.pawn or nil
end

local function ClassIsEnemy(class_name)
  if class_name == '' then return false end
  return class_name:find('Monster', 1, true) ~= nil or class_name:find('Enemy', 1, true) ~= nil
      or class_name:find('Boss', 1, true) ~= nil
end

local function EnemyApproachLimit(class_name)
  if class_name and class_name:find('JingYe', 1, true) then return 90 end
  return 30
end

local function ComponentTag(component)
  local tag = Try(function() return component.Faction end)
  if tag == nil then return nil end
  if type(tag) == 'string' then return tag end
  local text = Try(function() return tag:ToString() end)
  if type(text) ~= 'string' or text == '' then
    local name = Try(function() return tag.TagName end)
    text = name and Try(function() return name:ToString() end) or nil
  end
  if type(text) == 'string' and text ~= '' then return text end
  return nil
end

local function CollectEnemy(enemies, actor, class_name, px, py, pz, faction)
  local function reject(reason)
    if #Cache.enemy_rejected < 12 then
      Cache.enemy_rejected[#Cache.enemy_rejected + 1] = class_name .. ':' .. reason
    end
  end
  if class_name ~= '' and class_name:find('Spawn', 1, true) then reject('spawn'); return end
  local ex, ey, ez = Position(actor)
  if not ex then reject('position_unavailable'); return end
  local dx, dy, dz = px - ex, py - ey, pz - ez
  local distance = math.sqrt(dx * dx + dy * dy + dz * dz) / 100
  local approach_limit = EnemyApproachLimit(class_name)
  if distance > approach_limit or math.abs(dz) > 500 then
    reject(string.format('distance_%.1fm_height_%.1fm', distance, math.abs(dz) / 100))
    return
  end
  local set = AttrSet(actor)
  local hp = set and Attribute(set, 'Health')
  local maximum = set and Attribute(set, 'Health_Max')
  if hp ~= nil and maximum ~= nil and (maximum <= 0 or hp <= 0) then
    reject(string.format('health_%.1f_of_%.1f', hp, maximum))
    return
  end
  local shield, shield_field = nil, nil
  if set then shield, shield_field = ShieldOf(set) end
  local asc = Try(function() return actor.AbilitySystemComponent end)
  local shield_tag = nil
  if Valid(asc) then shield_tag = Try(function() return asc:HasMatchingGameplayTag(SHIELD_TAG) end) end
  if shield_tag == true then shield, shield_field = 1, 'gameplay_tag' end
  enemies[#enemies + 1] = {
    actor = actor, name = Name(actor), class = class_name, distance = distance,
    approach_limit = approach_limit,
    hp = hp, hp_max = maximum, faction = faction or '', has_attr = set ~= nil,
    shield = shield, shield_field = shield_field, shield_tag = shield_tag,
  }
end

local function RefreshEnemySnapshot(pawn)
  if #Cache.enemies == 0 then return end
  local px, py, pz = Position(pawn)
  if not px then return end
  local alive = {}
  for _, enemy in ipairs(Cache.enemies) do
    if Valid(enemy.actor) then
      local ex, ey, ez = Position(enemy.actor)
      if ex then
        local dx, dy, dz = px - ex, py - ey, pz - ez
        local distance = math.sqrt(dx * dx + dy * dy + dz * dz) / 100
        if distance <= (enemy.approach_limit or EnemyApproachLimit(enemy.class)) + 5
            and math.abs(dz) <= 500 then
          enemy.distance = distance
          local asc = Try(function() return enemy.actor.AbilitySystemComponent end)
          local shield_tag = nil
          if Valid(asc) then shield_tag = Try(function() return asc:HasMatchingGameplayTag(SHIELD_TAG) end) end
          enemy.shield_tag = shield_tag
          local set = AttrSet(enemy.actor)
          if set then
            local hp, maximum = Attribute(set, 'Health'), Attribute(set, 'Health_Max')
            if hp ~= nil and maximum ~= nil and (maximum <= 0 or hp <= 0) then
              -- dead, drop
            else
              enemy.hp, enemy.hp_max = hp, maximum
              enemy.shield, enemy.shield_field = ShieldOf(set)
              if shield_tag == true then enemy.shield, enemy.shield_field = 1, 'gameplay_tag' end
              alive[#alive + 1] = enemy
            end
          else
            enemy.shield, enemy.shield_field = shield_tag == true and 1 or nil, shield_tag == true and 'gameplay_tag' or nil
            alive[#alive + 1] = enemy
          end
        end
      end
    end
  end
  table.sort(alive, function(a, b) return a.distance < b.distance end)
  Cache.enemies = alive
end

local function DiscoverEnemies(pawn, enemies, now)
  local px, py, pz = Position(pawn)
  if not px then return end
  local component_count = 0
  Cache.faction_seen = 0
  Cache.enemy_rejected = {}
  local classes = {}
  local known = {}
  for _, component in ipairs(FindAllOf('FactionComponent') or {}) do
    if Valid(component) then
      component_count = component_count + 1
      local owner = Try(function() return component:GetOwner() end)
      if Valid(owner) and not SamePtr(owner, pawn) then
        local class_name = Try(function() return owner:GetClass():GetFName():ToString() end) or ''
        if #classes < 12 then classes[#classes + 1] = class_name end
        local faction = ComponentTag(component)
        local hostile = ClassIsEnemy(class_name)
            or (faction ~= nil and faction:find('Enemy', 1, true) ~= nil)
        if hostile then
          Cache.faction_seen = Cache.faction_seen + 1
          Cache.faction_reliable = true
          CollectEnemy(enemies, owner, class_name, px, py, pz, faction or '')
          local key = Address(owner)
          if key then known[key] = true end
        end
      end
    end
  end
  Cache.faction_components = component_count
  Cache.enemy_classes_seen = classes

  -- Some arenas expose only environment FactionComponents. Use a throttled
  -- actor scan only when that path found no usable enemy.
  Cache.actor_scan_count = 0
  Cache.actor_enemy_classes_seen = {}
  if #enemies == 0 and not Cache.faction_reliable and now >= (Cache.next_actor_fallback or 0) then
    Cache.next_actor_fallback = now + 1.2
    local actors = Try(function() return FindAllOf('Character') end) or {}
    local actor_classes = {}
    for _, actor in ipairs(actors) do
      if Valid(actor) and not SamePtr(actor, pawn) then
        local class_name = Try(function() return actor:GetClass():GetFName():ToString() end) or ''
        if ClassIsEnemy(class_name) then
          Cache.actor_scan_count = Cache.actor_scan_count + 1
          if #actor_classes < 12 then actor_classes[#actor_classes + 1] = class_name end
          local key = Address(actor)
          if not key or not known[key] then
            CollectEnemy(enemies, actor, class_name, px, py, pz, 'class_fallback')
            if key then known[key] = true end
          end
        end
      end
    end
    table.sort(actor_classes)
    Cache.actor_enemy_classes_seen = actor_classes
  end

end

local function TargetingComponent(pawn, now)
  local key = Address(pawn) or ''
  if key ~= Cache.targeting_owner then
    Cache.targeting, Cache.targeting_owner, Cache.next_targeting_scan = nil, key, 0
  end
  if not Valid(Cache.targeting) and now >= Cache.next_targeting_scan then
    Cache.next_targeting_scan = now + 5
    Cache.targeting = FindOwnedComponent('TargetingComponent', pawn)
  end
  return Valid(Cache.targeting) and Cache.targeting or nil
end

local function LockedEnemy(pawn, now)
  local component = TargetingComponent(pawn, now)
  local locked = component and Try(function() return component:HasLockTarget() end)
  local candidates = {}
  for _, holder in ipairs({pawn, Cache.controller}) do
    if Valid(holder) then
      candidates[#candidates + 1] = Try(function() return holder.TrueLockTargetActor end)
      candidates[#candidates + 1] = Try(function() return holder.LockTargetActor end)
    end
  end
  if component and locked == true then
    local target = Try(function() return component:GetLockTarget() end)
    local socket = target and Try(function() return target.Component end)
    if not Valid(socket) and target then
      local nested = Try(function() return target.Target end)
      socket = nested and Try(function() return nested.Component end) or nil
    end
    if not Valid(socket) and Valid(target) then socket = target end
    if Valid(socket) then
      candidates[#candidates + 1] = Try(function() return socket:GetOwner() end) or socket
    end
  end
  for _, actor in ipairs(candidates) do
    if Valid(actor) and not SamePtr(actor, pawn) then
      local class = Try(function() return actor:GetClass():GetFName():ToString() end) or ''
      if ClassIsEnemy(class) then return actor, class, locked end
    end
  end
  return nil, nil, locked
end

local function ScanEnemies(pawn, now)
  local started = os.clock()
  RefreshEnemySnapshot(pawn)
  if now < Cache.next_enemy_scan then
    Cache.enemy_scan_ms = (os.clock() - started) * 1000
    return Cache.enemies
  end
  local have = #Cache.enemies > 0
  local retry_delay
  if have then
    Cache.enemy_miss_streak = 0
    retry_delay = Config.enemy_scan_interval
  else
    Cache.enemy_miss_streak = Cache.enemy_miss_streak + 1
    retry_delay = math.min(1.25, Config.enemy_retry_interval + 0.15 * (Cache.enemy_miss_streak - 1))
  end
  Cache.next_enemy_scan = now + retry_delay
  local enemies = {}
  DiscoverEnemies(pawn, enemies, now)
  local actor, class, native_locked = LockedEnemy(pawn, now)
  Cache.native_locked = native_locked == true
  Cache.native_target_key = actor and native_locked ~= false and Address(actor) or ''
  if native_locked == false then Cache.lock_key = '' end
  if actor then
    local present = false
    for _, enemy in ipairs(enemies) do
      if SamePtr(enemy.actor, actor) then present = true; break end
    end
    if not present then
      local px, py, pz = Position(pawn)
      if px then CollectEnemy(enemies, actor, class, px, py, pz, 'native_lock') end
    end
    if native_locked ~= false then Cache.lock_key = Address(actor) or '' end
    Cache.native_lock_state = 'locked_enemy'
  elseif native_locked == true then
    Cache.native_lock_state = 'locked_target_unavailable'
  else
    Cache.native_lock_state = Valid(Cache.targeting) and 'unlocked' or 'unavailable'
  end
  if #enemies > 0 then Cache.next_actor_fallback = now + 1.0 end
  local known = {}
  for _, enemy in ipairs(enemies) do
    local key = Address(enemy.actor)
    if key then known[key] = true end
  end
  for _, enemy in ipairs(Cache.enemies) do
    local key = Address(enemy.actor)
    if key and not known[key] and Valid(enemy.actor) then
      enemies[#enemies + 1] = enemy
    end
  end
  table.sort(enemies, function(a, b) return a.distance < b.distance end)
  local listed = {}
  for i = 1, math.min(#enemies, 6) do
    local e = enemies[i]
    listed[i] = string.format('%s@%.1f', e.class ~= '' and e.class or e.name, e.distance)
  end
  Cache.enemy_list = table.concat(listed, ',')
  Cache.enemies = enemies
  Cache.actors_scanned = #enemies
  Cache.enemy_scan_ms = (os.clock() - started) * 1000
  return enemies
end

local function ObserveEnemyMontages(enemies, now)
  if now < Cache.next_montage_scan then return end
  Cache.next_montage_scan = now + 0.12
  local current, signatures = {}, Cache.montage_signatures or {}
  for i = 1, math.min(#enemies, 3) do
    local enemy = enemies[i]
    local mesh = Try(function() return enemy.actor.Mesh end)
    local anim = Valid(mesh) and Try(function() return mesh:GetAnimInstance() end)
    local montage = Valid(anim) and Try(function() return anim:GetCurrentActiveMontage() end)
    local position = Valid(montage) and Try(function() return anim:Montage_GetPosition(montage) end)
    if Valid(montage) and type(position) == 'number' then
      local name, key = Name(montage), Address(enemy.actor) or ''
      current[#current + 1] = string.format('%s:%s@%.3f', enemy.class, name, position)
      if name ~= '' and signatures[key] ~= name then
        signatures[key] = name
        RecordInput('montage:' .. name)
        if Inj.IsAttackMontage(name) then Inj.PreGuard(enemy, now) end
      end
    end
  end
  Cache.enemy_montages, Cache.montage_signatures = current, signatures
end

-- ------------------------------------------------------------ frame typing
local SOURCE_FIELDS = {
  'Source', 'SourceObject', 'SourceActor', 'AttackSource', 'WeakSource', 'Sender',
  'From', 'Action', 'Ability', 'Causer', 'Instigator', 'Bullet', 'Owner', 'Origin',
}
local TYPE_FIELDS = {
  'DeflectBulletType', 'DeflectType', 'DeflectAttackType', 'BulletDeflectType',
  'AttackType', 'BulletType', 'DamageType', 'DeflectKind', 'Kind', 'Type',
}
local TYPE_NAMES = { [0] = 'normal', [1] = 'normal', [2] = 'blue', [3] = 'red', [4] = 'power', [5] = 'hold' }

-- Once the first frame arrives, record what the source object actually exposes
-- so the next build only has to read real names instead of the candidate list.
local DISCOVERY_FIELDS = {
  'DeflectType', 'DeflectBulletType', 'DeflectAttackType', 'BulletDeflectType',
  'BulletType', 'AttackType', 'DamageType', 'Type', 'Kind', 'DeflectKind',
  'Result', 'DeflectResult', 'Color', 'AttackColor', 'DeflectColor',
  'IsRed', 'IsBlue', 'bRed', 'bBlue', 'Red', 'Blue', 'Category', 'AttackCategory',
  'HitType', 'DeflectEnum', 'BulletDataType',
}

local function Num(value)
  if type(value) == 'number' then return value end
  if type(value) == 'userdata' then
    local direct = Try(function() return value:get() end)
    if type(direct) == 'number' then return direct end
    for _, key in ipairs({ 'Value', 'ValueInt', 'ValueByte' }) do
      local inner = Try(function() return value[key] end)
      if type(inner) == 'number' then return inner end
    end
    local text = Try(function() return tostring(value) end)
    if text then
      local number = tonumber(string.match(text, '(%d+)%s*$'))
      if number then return number end
    end
  end
  return nil
end

local function DiscoverSource(source, class)
  if Cache.src_info ~= '' then return end
  local parts = { 'class=' .. (class ~= '' and class or '?') }
  for _, field in ipairs(DISCOVERY_FIELDS) do
    local value = Try(function() return source[field] end)
    if value ~= nil then
      local number = Num(value)
      parts[#parts + 1] = field .. '=' .. (number and tostring(number) or tostring(value))
      if #parts >= 14 then break end
    end
  end
  local text = table.concat(parts, ' ')
  if #text > 420 then text = text:sub(1, 420) end
  Cache.src_info = text
end

local function ResolveObject(value)
  if Valid(value) then return value end
  if type(value) == 'table' then
    for _, key in ipairs({ 'Object', 'ObjectPtr', 'Ptr', 'Value', 'Actor' }) do
      local inner = value[key]
      if Valid(inner) then return inner end
    end
    local get = value.get
    if type(get) == 'function' then
      local inner = Try(function() return get(value) end)
      if Valid(inner) then return inner end
    end
  end
  return nil
end

local function FrameSource(frame)
  local field = Cache.src_field
  if field ~= '' then
    local object = ResolveObject(Try(function() return frame[field] end))
    if object then return object, field end
  end
  for _, candidate in ipairs(SOURCE_FIELDS) do
    local object = ResolveObject(Try(function() return frame[candidate] end))
    if object then
      Cache.src_field = candidate
      return object, candidate
    end
  end
  return nil
end

local function SourceKind(source)
  if not Valid(source) then return 'unknown', 0 end
  local class = Name(Try(function() return source:GetClass() end) or nil)
  if class ~= '' then
    Cache.src_classes[class] = (Cache.src_classes[class] or 0) + 1
  end
  local lower = class:lower()
  -- Sweep traces are melee even when their numeric type field is unrelated to
  -- the small enum table below.
  if lower:find('sweeptrace') then return 'normal', 0 end
  if lower:find('blue') then return 'blue', 2 end
  if lower:find('red') then return 'red', 3 end
  DiscoverSource(source, class)
  local field = Cache.type_field
  if field ~= '' then
    local value = Num(Try(function() return source[field] end))
    if value and TYPE_NAMES[value] then return TYPE_NAMES[value], value end
  end
  for _, candidate in ipairs(TYPE_FIELDS) do
    local value = Num(Try(function() return source[candidate] end))
    if value then
      Cache.type_field = candidate
      if TYPE_NAMES[value] then return TYPE_NAMES[value], value end
    end
  end
  return 'unknown', 0
end

local PROJECTILE_HINTS = { 'Grenade', 'Projectile', 'Explosive', 'Arrow', 'Bolt', 'Dart',
                           'Throwable', 'Missile', 'Bullet', 'Shot' }

local function IsAreaAttack(source)
  -- Anything that flies at you cannot be answered by standing still and
  -- parrying.  Melee sweeps published as *SweepTraceBullet* are the one
  -- exception and must stay parryable.
  if not Valid(source) then return false end
  local class = Name(Try(function() return source:GetClass() end) or nil)
  if class == '' then return false end
  if class:find('SweepTrace', 1, true) then return false end
  for _, hint in ipairs(PROJECTILE_HINTS) do
    if class:find(hint, 1, true) then return true end
  end
  return false
end

local function ResponseFor(kind)
  if kind == 'red' then return Config.red_response end
  if kind == 'blue' then return Config.blue_response end
  -- an unreadable source used to fall through to "parry", which eats every
  -- unblockable hit; dodging covers every attack kind, so that is the default
  if kind == 'unknown' then return Config.unknown_response end
  return Config.normal_response
end

local function LeadFor(action)
  if action == 'dodge' then return Config.dodge_lead end
  if action == 'guard' then return Config.guard_lead end
  return Config.parry_lead
end

-- ---------------------------------------------------------------- defense
local function ReadFrames(component)
  local frames = {}
  component.ReceiveFrames:ForEach(function(key, value)
    local frame, handle = value:get(), key:get()
    local hit = tonumber(frame.HitGameTime)
    if hit then
      frames[#frames + 1] = {
        handle = handle, hit = hit,
        isDodge = frame.IsDodge == true, isHit = frame.IsHit == true,
        raw = frame,
      }
    end
  end)
  return frames
end

local function CountKind(kind)
  Kinds[kind] = (Kinds[kind] or 0) + 1
end

-- How long to keep the defensive input down: long enough to cover the hit that
-- this frame announces, no longer. Holding guard keeps the character planted and
-- drains Sha-Chi, which is what made the character look frozen.
local function HoldFor(dt, pad, cap)
  return math.max(0.10, math.min(cap, (dt or 0.10) + pad))
end

local function DodgeNow(tracked, now, dt)
  Inj.block_until, Inj.press_pending = -1, false
  Inj.dodge_until = math.max(Inj.dodge_until, now + HoldFor(dt, Config.dodge_pad, Config.dodge_hold))
  Inj.released = false
  tracked.acted, tracked.action = true, 'dodge'
end

local function DeflectNow(tracked, now, dt)
  Inj.block_until = math.max(Inj.block_until, now + HoldFor(dt, Config.block_pad, Config.block_hold))
  Inj.press_pending = true
  Inj.released = false
  tracked.acted, tracked.action = true, 'deflect'
end

local function GuardNow(tracked, now, dt)
  Inj.block_until = math.max(Inj.block_until, now + HoldFor(dt, Config.guard_pad, Config.block_hold))
  Inj.press_pending = true
  Inj.released = false
  tracked.acted, tracked.action = true, 'guard'
end

local LastDeflectPress, DefenseStreak = -100, 0

local function Act(tracked, now, dt, hp)
  ReleaseOffense(true)
  -- you cannot sidestep or parry while sprinting into the attack
  ReleaseRole('move')
  local response = ResponseFor(tracked.kind)
  RecordInput('defend:' .. tracked.kind)
  if response == 'dodge' and not Config.dodge_enabled then response = 'deflect' end
  if response ~= 'dodge' and not Config.guard_enabled then response = 'dodge' end
  -- A second block input inside ~0.4 s makes the game cut the perfect-parry
  -- window to a third, so answer a quick follow-up with a dodge instead.
  if response == 'deflect' and now - LastDeflectPress < Config.deflect_gap then
    Counters.deflect_deferred = Counters.deflect_deferred + 1
    response = Config.dodge_enabled and 'dodge' or 'guard'
  end
  tracked.hp_before = hp
  tracked.defense = response
  if response == 'dodge' then
    DodgeNow(tracked, now, dt)
    Counters.dodge_red = Counters.dodge_red + 1
  elseif response == 'guard' then
    GuardNow(tracked, now, dt)
    Counters.guard = Counters.guard + 1
  else
    -- Remembered at decision time too: two frames answered in the same tick
    -- must not both count as a fresh parry input.
    LastDeflectPress = now
    DeflectNow(tracked, now, dt)
    Counters.parry = Counters.parry + 1
  end
  if Config.run then Status.action = 'defend_' .. tracked.kind .. '_' .. response end
  return LeadFor(response)
end

-- Score a defence by the only signal the game gives us: did the announced hit
-- take HP. A failed parry tightens the lead, a clean streak relaxes it again.
local function ScoreDefense(tracked, hp, hp_max)
  if tracked.scored or not tracked.acted then return end
  tracked.scored = true
  local before = tracked.hp_before
  if not hp or not before then
    Counters.def_unknown = Counters.def_unknown + 1
    return
  end
  local tolerance = math.max(0.5, (hp_max or 0) * 0.002)
  if hp < before - tolerance then
    Counters.def_fail = Counters.def_fail + 1
    DefenseStreak = 0
    if tracked.defense == 'deflect' then
      Config.parry_lead = math.max(Config.parry_lead_min, Config.parry_lead - 0.015)
      Counters.parry_tightened = Counters.parry_tightened + 1
    end
  else
    Counters.def_ok = Counters.def_ok + 1
    DefenseStreak = DefenseStreak + 1
    if DefenseStreak >= 4 and Config.parry_lead < Config.parry_lead_max then
      Config.parry_lead = math.min(Config.parry_lead_max, Config.parry_lead + 0.005)
    end
  end
end

local function DefenseTick(now, game_time, shachi, hp, hp_max)
  if not Config.guard_enabled and not Config.dodge_enabled then return false end
  -- Projectiles and delayed explosions publish their frame too late for a
  -- parry, so once one is seen the controller keeps dodging for a moment.
  if now < Cache.area_until and now >= NextGuardHold and Cache.area_dodges < 3 then
    ReleaseRole('move')
    if Press('dodge', Config.press_hold) then
      InjectRole('dodge_hold')
      NextGuardHold = now + 0.22
      Cache.area_dodges = (Cache.area_dodges or 0) + 1
      Counters.dodge_red = Counters.dodge_red + 1
      Counters.dodges = Counters.dodges + 1
      if Config.run then Status.action = 'projectile_dodge' end
      return true
    end
  end
  -- once the dodge budget for this volley is spent, keep swinging: standing
  -- there waiting for the next frame is worse than trading hits
  if not Valid(Cache.deflect) then return false end
  local ok, frames = pcall(ReadFrames, Cache.deflect)
  if not ok then return false end
  -- earliest hit first: with several enemies the frame that lands soonest is
  -- the one that must be answered first
  table.sort(frames, function(a, b) return a.hit < b.hit end)
  local present = {}
  for _, frame in ipairs(frames) do
    local handle, dt = frame.handle, frame.hit - game_time
    present[handle] = true
    if not frame.isHit and dt >= 0 and dt < 0.35 then
      -- Defer new consumables until this announced attack has resolved.
      GuardUntil = math.max(GuardUntil, now + math.min(0.20, dt + 0.06))
    end
    local tracked = Tracked[handle]
    if not tracked then
      tracked = { hit = frame.hit, isDodge = frame.isDodge, first_dt = dt }
      local source = FrameSource(frame.raw)
      local kind, raw = SourceKind(source)
      local area = IsAreaAttack(source)
      if area then
        kind = 'red'
        tracked.area = true
        Cache.area_until = now + 0.55
        Cache.area_dodges = 0
      end
      if kind == 'unknown' and frame.isDodge then kind = 'red' end
      -- a frame that only becomes visible with no parry window left is a cheap
      -- shot: the i-frames of a dodge still cover it, a late parry cannot
      if dt < Config.late_dodge and kind ~= 'red' and Config.dodge_enabled then
        kind = 'red'
        tracked.late = true
      end
      if PBZ_DEBUG then
        print(string.format('[PBZ] frame kind=%s area=%s srcfield=%s class=%s',
          tostring(kind), tostring(area), tostring(Cache.src_field),
          tostring(Name(Try(function() return source:GetClass() end) or nil))))
      end
      tracked.kind, tracked.raw = kind, raw
      CountKind(kind)
      Tracked[handle] = tracked
      Counters.frames = Counters.frames + 1
      if dt < 0.02 then Counters.frames_late = Counters.frames_late + 1 end
    end
    tracked.hit, tracked.isDodge = frame.hit, frame.isDodge
    if not frame.isHit and not tracked.acted and dt > -Config.stale_after then
      local response = ResponseFor(tracked.kind)
      local lead = LeadFor(response)
      local frame_dt = math.max(FrameDt, 0.001)
      if dt - frame_dt < lead then Act(tracked, now, dt, hp) end
    end
  end
  for handle, tracked in pairs(Tracked) do
    local dt = tracked.hit - game_time
    if not tracked.acted and (present[handle] or dt > -Config.stale_after) then
      local response = ResponseFor(tracked.kind)
      local lead = LeadFor(response)
      if dt - FrameDt < lead then Act(tracked, now, dt, hp) end
    end
    -- Once the announced hit has resolved, the player's HP says whether the
    -- defence worked; that is the only feedback the game gives for parries.
    if tracked.acted and not tracked.scored and game_time >= tracked.hit + 0.12 then
      ScoreDefense(tracked, hp, hp_max)
    end
    if not present[handle] and (tracked.scored or not tracked.acted) then
      Tracked[handle] = nil
    end
  end
  if now < Inj.dodge_until then
    -- a dodge only triggers on a press edge; re-injecting 1.0 every frame kept
    -- the button held and swallowed the next dodge
    if not Pressed.dodge and Press('dodge', Config.press_hold) then
      Counters.dodges = Counters.dodges + 1
    end
    InjectRole('dodge_hold')
    Inj.held_last = false
    Inj.burst = Inj.burst + 1
    return true
  end
  if now < Inj.block_until then
    if Inj.press_pending and Inj.held_last then
      Inj.held_last = false
    elseif Inj.press_pending then
      local guard = InjectRole('guard')
      local deflect = (not Pressed.deflect) and Press('deflect', Config.press_hold) or false
      if deflect then LastDeflectPress = now end
      Inj.press_pending, Inj.held_last = false, true
      if guard or deflect then Counters.blocks = Counters.blocks + 1 end
    else
      InjectRole('guard')
      Inj.held_last = true
    end
    Inj.burst = Inj.burst + 1
    if Inj.burst > Config.max_burst then
      Inj.block_until, Inj.dodge_until, Inj.press_pending, Inj.held_last, Inj.burst = -1, -1, false, false, 0
    end
    return true
  end
  if now < GuardUntil then
    -- Threat anticipation still blocks medicine, but it is not an active
    -- defense. Release dodge-hold/guard inputs here and let combat continue;
    -- continuously refreshed enemy frames otherwise held both indefinitely.
    if not Inj.released then
      InjectRoleValue('deflect', 0.0)
      InjectRoleValue('guard', 0.0)
      InjectRoleValue('dodge', 0.0)
      InjectRoleValue('dodge_hold', 0.0)
      Inj.released = true
    end
    Inj.held_last, Inj.burst = false, 0
    Status.action = 'threat_wait'
    return false
  end
  if not Inj.released then
    -- releasing matters: an injected press without a release reads as a held
    -- button, which keeps the character in the block animation indefinitely.
    InjectRoleValue('deflect', 0.0)
    InjectRoleValue('guard', 0.0)
    InjectRoleValue('dodge', 0.0)
    InjectRoleValue('dodge_hold', 0.0)
    Inj.released = true
  end
  Inj.held_last, Inj.burst = false, 0
  return false
end

-- ------------------------------------------------------------------ combo
local SPEC_ABILITY_FIELDS = { 'Ability', 'AbilityTemplate', 'AbilityClass', 'GameplayAbility',
                              'AbilityCDO', 'AbilitySpec', 'Class' }

local function AbilityArray(container)
  if container == nil then return nil, nil, 'no-container' end
  local items = Try(function() return container.Items end) or container
  local count = Try(function() return items:GetArrayNum() end)
  if type(count) ~= 'number' then count = tonumber(Try(function() return items.Num end)) end
  if type(count) ~= 'number' and type(items) == 'table' then
    count = #items
    if count == 0 and items[0] ~= nil then
      while count < 160 and items[count] ~= nil do count = count + 1 end
    end
  end
  if type(count) ~= 'number' then return nil, nil, 'count-unavailable' end
  return items, math.max(0, math.floor(count))
end

local function AbilityAt(items, index)
  local spec = Try(function() return items:Get(index) end)
  if spec ~= nil then return spec end
  if type(items) == 'table' then
    return Try(function() return items[index + 1] end) or Try(function() return items[index] end)
  end
  return Try(function() return items[index + 1] end)
end

local function EachAbility(items, n, visit)
  local visited = 0
  local ok = pcall(function()
    items:ForEach(function(_, element)
      if visited >= 160 then return true end
      local spec = Try(function() return element:get() end)
      if spec == nil then spec = element end
      visited = visited + 1
      if spec ~= nil then visit(spec) end
      return false
    end)
  end)
  if ok and (visited > 0 or n == 0) then return end
  for i = 0, math.min(n, 160) - 1 do
    local spec = AbilityAt(items, i)
    if spec ~= nil then visit(spec) end
  end
end

local function SpecAbility(spec)
  for _, field in ipairs(SPEC_ABILITY_FIELDS) do
    local value = Try(function() return spec[field] end)
    if Valid(value) then return value, field end
    if value ~= nil then
      local name = Try(function() return value:GetFName():ToString() end)
          or Try(function() return tostring(value.Name) end)
      if type(name) == 'string' and name ~= '' then return value, field end
      local object = Try(function() return value.Object end)
      if Valid(object) then return object, field end
    end
  end
  return nil
end

local function SpecName(value)
  if Valid(value) then return Name(value) end
  return Try(function() return value:GetFName():ToString() end)
      or Try(function() return tostring(value.Name) end) or ''
end

function RefreshSpecs(now)
  if Cache.specs ~= nil and now < Cache.next_spec_scan then return Cache.specs end
  local pawn = Cache.pawn
  if not Valid(pawn) then
    Cache.specs, Cache.spec_scan_error, Cache.next_spec_scan = {}, 'no-pawn', now + 1
    return Cache.specs
  end
  local asc = Try(function() return pawn.AbilitySystemComponent end)
  if not Valid(asc) then
    Cache.specs, Cache.spec_scan_error, Cache.next_spec_scan = {}, 'no-asc', now + 1
    return Cache.specs
  end
  local container = Try(function() return asc.ActivatableAbilities end)
  local items, n, array_error = AbilityArray(container)
  if not items then
    Cache.specs, Cache.spec_scan_error, Cache.next_spec_scan = {}, array_error, now + 1
    return Cache.specs
  end
  local found, sample, fields = {}, {}, {}
  EachAbility(items, n, function(spec)
    local ability, field = SpecAbility(spec)
    if not Cache.spec_field and field then Cache.spec_field = field end
    local name = SpecName(ability):gsub('^Default__', '')
    if field then fields[field] = (fields[field] or 0) + 1 end
    if name ~= '' and #sample < 3 then sample[#sample + 1] = name end
    local chain = name:match('^GA_Hun_Tang_([AB]+)_C$')
    if chain then found[#found + 1] = { spec = spec, ability = ability, name = name, chain = chain } end
  end)
  table.sort(found, function(a, b) return #a.chain < #b.chain end)
  Cache.specs = found
  Cache.spec_scan_error = #found > 0 and '' or (n == 0 and 'empty-array' or 'no-matching-ability-name')
  Cache.next_spec_scan = now + 5
  local hits = {}
  for field, count in pairs(fields) do hits[#hits + 1] = field .. ':' .. count end
  table.sort(hits)
  Cache.spec_probe = string.format('n:%d hit:%s sample:%s', n, table.concat(hits, ','), table.concat(sample, '|'))
  return Cache.specs
end

function SpecProbe()
  local pawn = Cache.pawn
  if not Valid(pawn) then return 'no-pawn' end
  local asc = Try(function() return pawn.AbilitySystemComponent end)
  if not Valid(asc) then return 'no-asc' end
  local container = Try(function() return asc.ActivatableAbilities end)
  if container == nil then return 'no-container' end
  local items, n, array_error = AbilityArray(container)
  if not items then return array_error end
  local counts, sample = {}, {}
  EachAbility(items, n, function(spec)
    local ability, field = SpecAbility(spec)
    if field then counts[field] = (counts[field] or 0) + 1 end
    local name = SpecName(ability)
    if name ~= '' and #sample < 2 then sample[#sample + 1] = name end
  end)
  local parts = {}
  for field, count in pairs(counts) do parts[#parts + 1] = field .. '=' .. count end
  table.sort(parts)
  return string.format('n=%d hit=%s sample=%s', n, table.concat(parts, ','),
    table.concat(sample, '|'))
end

function ReadChain(now)
  local best, best_len = '', 0
  for _, entry in ipairs(RefreshSpecs(now)) do
    local active = Try(function() return entry.spec.ActiveCount end)
    if type(active) == 'number' and active > 0 and #entry.chain > best_len then
      best, best_len = entry.chain, #entry.chain
    end
  end
  return best
end

-- Input windows of the chain moves, in seconds after that move starts.  These
-- are the game's own combo_open/combo_close values for the default weapon: the
-- next key is only accepted inside [open, close], and pressing on a fixed
-- cadence therefore lost most of its inputs.  A different weapon reuses the
-- table and Combo.tempo stretches it when presses keep being rejected.
local MOVE_WINDOWS = {
  A     = { open = 0.37, close = 0.77 },
  AA    = { open = 0.62, close = 1.04 },
  AAA   = { open = 0.72, close = 1.06 },
  AAAA  = { open = 0.63, close = 0.93 },
  AAAAA = { open = 0.66, close = 0.75 },
  B     = { open = 1.00, close = 1.45 },
  BB    = { open = 1.04, close = 1.43 },
  BBB   = { open = 0.74, close = 0.88 },
  AB    = { open = 1.37, close = 1.88 },
  ABB   = { open = 1.19, close = 1.31 },
  AAB   = { open = 1.43, close = 1.85 },
  AABB  = { open = 1.77, close = 1.95 },
  AAAB  = { open = 1.31, close = 1.81 },
  AAABB = { open = 0.92, close = 1.07 },
}
local DEFAULT_WINDOW = { open = 0.46, close = 1.05 }

-- Gameplay tags the character owns while a chain move runs.  These are the most
-- reliable chain source: on the live build the GA_Hun_Tang_* listed abilities
-- frequently come back unreadable, while owned tags still answer.
local MOVE_TAG_LIST = {
  { 'Ability.Action.Hun.Light.A', 'A' },
  { 'Ability.Action.Hun.Light.AA', 'AA' },
  { 'Ability.Action.Hun.Light.AAA', 'AAA' },
  { 'Ability.Action.Hun.Light.AAAA', 'AAAA' },
  { 'Ability.Action.Hun.Light.AAAAA', 'AAAAA' },
  { 'Ability.Action.Hun.Heavy.Atk01', 'B' },
  { 'Ability.Action.Hun.Heavy.Atk02', 'BB' },
  { 'Ability.Action.Hun.Heavy.Atk03', 'BBB' },
  { 'Ability.Action.Hun.Heavy.ComboAtk_N1H1', 'AB' },
  { 'Ability.Action.Hun.Heavy.ComboAtk_N1H2', 'ABB' },
  { 'Ability.Action.Hun.Heavy.ComboAtk_N2H1', 'AAB' },
  { 'Ability.Action.Hun.Heavy.ComboAtk_N2H2', 'AABB' },
  { 'Ability.Action.Hun.Heavy.ComboAtk_N3H1', 'AAAB' },
  { 'Ability.Action.Hun.Heavy.ComboAtk_N3H2', 'AAABB' },
}
-- The action phase the character reports while it accepts a chain input.
local PHASE_TAG_LIST = {
  { 'CharacterState.Action.Phase.Combo', 'combo' },
  { 'CharacterState.AbilityState.GlobalActionComboAvaliable', 'combo' },
  { 'CharacterState.Action.Phase.Act', 'act' },
  { 'CharacterState.Action.Phase.Cancel', 'cancel' },
}
local function TagOf(name) return { TagName = FName(name) } end
for _, entry in ipairs(MOVE_TAG_LIST) do entry.tag = TagOf(entry[1]) end
for _, entry in ipairs(PHASE_TAG_LIST) do entry.tag = TagOf(entry[1]) end

local function ProbeCombatTags(now)
  if now < (Cache.next_tag_probe or 0) then return end
  -- Move tags are probed at 20 Hz while they answer.  A build that never
  -- publishes them is re-checked four times a second instead, so a timed fight
  -- does not pay for 14 tag queries on every 50 ms.
  local quiet = (Cache.tag_hits or 0) == 0 and (Cache.tag_probes or 0) > 100
  Cache.next_tag_probe = now + (quiet and 0.25 or 0.05)
  Cache.tag_probes = (Cache.tag_probes or 0) + 1
  local pawn = Cache.pawn
  if not Valid(pawn) then return end
  local asc = Try(function() return pawn.AbilitySystemComponent end)
  local owner = Valid(asc) and asc or pawn
  local chain, chain_name, seen = '', '', {}
  for _, entry in ipairs(MOVE_TAG_LIST) do
    local present = Try(function() return owner:HasMatchingGameplayTag(entry.tag) end)
    if type(present) == 'boolean' then Cache.tag_readable = true end
    if present == true then
      seen[#seen + 1] = entry[2]
      Cache.tag_hits = (Cache.tag_hits or 0) + 1
      if #entry[2] > #chain then chain, chain_name = entry[2], entry[1] end
    end
  end
  Cache.chain_tags, Cache.chain_tag, Cache.chain_tag_name = seen, chain, chain_name
  Cache.chain_tag_at = now
  local phase = ''
  for _, entry in ipairs(PHASE_TAG_LIST) do
    local present = Try(function() return owner:HasMatchingGameplayTag(entry.tag) end)
    if type(present) == 'boolean' then Cache.tag_readable = true end
    if present == true then
      if entry[2] == 'combo' then phase = 'combo'; break end
      if phase == '' then phase = entry[2] end
    end
  end
  if phase ~= Combo.phase then
    Combo.phase, Combo.phase_since = phase, now
    if phase == 'combo' then Combo.phase_seen = true end
  end
end

-- Returns the open/close of a move window.  The luau chunk is close to the
-- 200-local limit, so single values are read straight from the table instead of
-- adding accessor locals; `WindowFor(x).close` would index a number.
local function WindowFor(path)
  local base = MOVE_WINDOWS[path] or DEFAULT_WINDOW
  return base.open, base.close
end

local function ComboReset(now, reason)
  Combo.path, Combo.step, Combo.awaiting, Combo.ready = '', 0, '', nil
  Combo.awaiting_key, Combo.retried = nil, false
  Combo.started, Combo.last_reset = now, reason or ''
  Combo.resets = Combo.resets + 1
end

-- Is a chain move running right now?  The move tag is authoritative when it was
-- sampled recently; the listed abilities are the fallback.
local function MoveActive(now)
  if (Cache.chain_tag or '') ~= '' and now - (Cache.chain_tag_at or 0) <= 0.30 then
    return true
  end
  return ReadChain(now) ~= ''
end

-- A move became visible. It either confirms the press we sent (that time is the
-- timing anchor for the next window) or it shows the game went somewhere else,
-- in which case the game wins and the plan follows it.
local function ObserveMove(now, path)
  local started = now
  if #path > #Combo.path and (Combo.pressed_at or -1) > 0 then
    -- The move we asked for can appear late (tag poll, a defense in between).
    -- Its window is timed from the press that started it, not from the read.
    started = math.min(now, math.max(Combo.pressed_at, now - 0.30))
  end
  if path == Combo.awaiting then
    Combo.confirms = Combo.confirms + 1
    Combo.streak = Combo.streak + 1
    Combo.accept_ms = math.max(0, math.floor((now - Combo.pressed_at) * 1000 + 0.5))
    if Combo.streak >= 4 then
      Combo.tempo = math.max(0.86, Combo.tempo - 0.02)
      Combo.streak = 0
    end
  else
    Combo.resyncs = Combo.resyncs + 1
    Combo.streak = 0
  end
  Combo.path, Combo.step = path, #path
  Combo.started, Combo.awaiting = started, ''
  Combo.awaiting_key, Combo.retried = nil, false
  Combo.last_seen = now
end

-- Pick the path the current gauge can actually pay for. Spending the whole
-- Sha-Chi gauge on heavy chains leaves nothing to guard with, so a low gauge
-- falls back to the light chain instead of pressing a key the game will refuse.
local function ComboPlanFor(player_set, opening)
  local path = PlanPath()
  if not path:find('B', 1, true) then return path, 'light' end
  local heavies = select(2, path:gsub('B', ''))
  if HeavyAffordable(player_set, opening, heavies) then return path, 'heavy' end
  return LIGHT_PATHS[PlanName()] or 'AAAA', 'light-degraded'
end

-- Returns the role/key to press now, or nil while the current window is closed.
-- The caller reports the press through ComboCommit so the next window is timed
-- from the move the game actually started.
local function ComboTick(now, player_set, target, opening)
  Combo.ready = nil
  if not Config.combo_enabled or not Config.attack_enabled then return nil end
  ProbeCombatTags(now)
  local tag_chain = Cache.chain_tag or ''
  local spec_chain = ReadChain(now)
  local observed = tag_chain ~= '' and tag_chain or spec_chain
  if observed ~= '' then
    Combo.feedback = true
    Combo.signal = tag_chain ~= '' and 'tag' or 'ability'
    Combo.last_seen = now
    if observed ~= Combo.path then ObserveMove(now, observed) end
  elseif Combo.feedback and Combo.awaiting == '' and Combo.path ~= '' then
    -- Between two chained moves the tag can be missing for a frame or two. Only
    -- a long silence means the chain really ended early. A listed ability can
    -- clear its counter early, so its silence is judged against the window.
    local _, close = WindowFor(Combo.path)
    local silence = Combo.signal == 'tag' and 0.35 or (close * Combo.tempo + 0.15)
    if now - (Combo.last_seen or 0) > silence then
      ComboReset(now, 'chain-end')
    end
  end
  -- A chain source that keeps going quiet must not freeze the controller: drop
  -- back to the estimated windows until the game reports a move again.
  if Combo.feedback and Combo.awaiting == '' and Combo.path == ''
      and now - (Combo.last_seen or 0) > 3.0 then
    Combo.feedback = false
    Combo.last_reset = 'no-signal'
  end
  if Combo.awaiting ~= '' then
    -- The move we asked for never appeared and the game shows nothing at all:
    -- the press was dropped (the character was still busy). Retry at once
    -- instead of waiting out the whole window timeout.
    local silent = Combo.feedback and observed == ''
        and now - (Combo.pressed_at or now) > 0.16
    if silent or now >= Combo.timeout then
      Combo.rejects = Combo.rejects + 1
      Combo.streak = 0
      -- Only a sustained reject rate means the windows are wrong; a single miss
      -- usually comes from a move the mod cannot name or from being mid-swing.
      if not silent and (Combo.rejects + 1) * 10 > (Combo.confirms + Combo.rejects + 1) * 3 then
        Combo.tempo = math.min(1.75, Combo.tempo + 0.04)
        Combo.delay = math.min(0.18, Combo.delay + 0.015)
        Combo.delay_by_path = Combo.delay_by_path or {}
        local key = Combo.path ~= '' and Combo.path or '-'
        Combo.delay_by_path[key] = (Combo.delay_by_path[key] or 0) + 0.03
      end
      Combo.last_reset = (silent and 'dropped:' or 'rejected:') .. (Combo.awaiting or '')
      Combo.resets = Combo.resets + 1
      Combo.path, Combo.step, Combo.awaiting = '', 0, ''
      Combo.awaiting_key, Combo.retried = nil, false
      Combo.idle_until = now + (silent and 0.05 or Config.combo_recover * 2)
      return nil
    end
  end
  if Combo.awaiting ~= '' then
    -- The game still shows the same move, so the window is open and our press
    -- was ignored: retry once instead of waiting for the whole chain to expire.
    local window_open, window_close = WindowFor(Combo.path)
    local open = Combo.started + window_open * Combo.tempo * Config.combo_speed + Config.combo_lead
    local close = Combo.started + window_close * Combo.tempo * Config.combo_speed
    if Combo.signal == 'tag' and not Combo.retried and Combo.awaiting_key
        and now >= open + 0.14 and now < close - 0.05 and now - Combo.last_press >= 0.16 then
      Combo.retried = true
      -- The first press was too early: the real window opens between the two,
      -- so re-centre the estimate instead of repeating the same miss.
      local estimate = (MOVE_WINDOWS[Combo.path] or DEFAULT_WINDOW).open
          * Combo.tempo * Config.combo_speed
      local learned = (Combo.last_press - Combo.started + now - Combo.started) / 2 - estimate + 0.01
      if learned > Combo.delay then Combo.delay = math.min(0.22, learned) end
      Combo.ready = { key = Combo.awaiting_key, role = KEY_ROLE[Combo.awaiting_key],
                      path = Combo.awaiting, prev = Combo.path }
      return Combo.ready.role, Combo.ready.key
    end
    Combo.wait_ms = math.max(0, math.floor((Combo.timeout - now) * 1000))
    return nil
  end
  -- Any deliberate wait ends as soon as the game reports that the character is
  -- free again, so a dropped punish press cannot lock the engine for its whole
  -- window estimate.
  if now < Combo.idle_until and Combo.feedback and observed == ''
      and (Combo.last_seen or 0) > 0 and now - (Combo.last_seen or 0) > 0.12 then
    Combo.idle_until = now
    Combo.last_reset = 'free'
  end
  if now < Combo.idle_until then return nil end

  local plan, mode = ComboPlanFor(player_set, opening ~= '')
  Combo.plan_mode = mode
  if #Combo.path >= #plan then
    -- The plan is finished.  Waiting for combo_close here meant standing still
    -- for up to ~1.9 s after a finisher, which is exactly the stun window the
    -- player wanted to use, so restart as soon as the move is really over.
    local _, close = WindowFor(Combo.path)
    local deadline = Combo.started + math.min(2.20, close * Combo.tempo + 0.10)
    -- Only a move tag tells us the animation itself ended; a listed ability
    -- counter drops early and must not be read as "the character is free".
    local gone = Combo.signal == 'tag' and observed == ''
        and (Combo.last_seen or 0) > 0 and now - (Combo.last_seen or 0) > 0.12
    local burst = now < (Combo.opening_until or 0)
    local early = burst and Combo.started + close * 0.75 * Combo.tempo <= now
    if gone or early or now >= deadline then
      Combo.stalls = (Combo.stalls or 0) + 1
      Combo.stall_ms = math.max(Combo.stall_ms or 0, math.floor((deadline - now) * 1000))
      ComboReset(now, gone and 'complete' or (early and 'opening-burst' or 'chain-timeout'))
      Combo.idle_until = now + (gone and 0.0 or 0.05)
      return nil
    end
    return nil
  end
  local next_key = plan:sub(#Combo.path + 1, #Combo.path + 1)
  local next_path = Combo.path .. next_key
  if not MOVE_WINDOWS[next_path] or KEY_ROLE[next_key] == nil then
    ComboReset(now, 'no-such-chain')
    Combo.idle_until = now + Config.combo_recover
    return nil
  end
  if next_key == 'B' and not HeavyAffordable(player_set, opening ~= '', 1) then
    ComboReset(now, 'sha-chi-reserve')
    Combo.idle_until = now + Config.combo_recover
    return nil
  end
  local press_at
  if Combo.path == '' then
    press_at = now          -- the first hit of a new chain waits for no window
  else
    local open, close = WindowFor(Combo.path)
    open = open * Combo.tempo * Config.combo_speed + Combo.delay + Config.combo_lead
    close = close * Combo.tempo * Config.combo_speed + Combo.delay
    -- A press before combo_open is dropped by the game, and one after
    -- combo_close chains into a different move, so the press stays inside.
    press_at = Combo.started + math.min(math.max(0.08, open), math.max(0.08, close - 0.02))
    -- When the action phase is readable it says the window is open right now;
    -- use that edge (once per edge) instead of the estimated clock.
    if not Combo.feedback and Combo.phase == 'combo'
        and now - Combo.phase_since > 0.02 and Combo.phase_used ~= Combo.phase_since then
      press_at = now
    end
  end
  Combo.wait_ms = math.max(0, math.floor((press_at - now) * 1000))
  if now < press_at then return nil end
  if now - Combo.last_press < Config.press_gap then return nil end
  Combo.ready = { key = next_key, role = KEY_ROLE[next_key], path = next_path, prev = Combo.path }
  return Combo.ready.role, next_key
end

local function ComboCommit(now)
  local ready = Combo.ready
  Combo.ready = nil
  if not ready then return end
  Combo.presses, Combo.last_press, Combo.pressed_at = Combo.presses + 1, now, now
  if Combo.phase == 'combo' then Combo.phase_used = Combo.phase_since end
  if Combo.feedback then
    -- Wait for the game to show the move before timing the next window.
    Combo.awaiting, Combo.awaiting_key, Combo.retried = ready.path, ready.key, false
    local _, close = WindowFor(ready.prev)
    Combo.timeout = now + math.min(2.0, (ready.prev == '' and 0.9 or close * Combo.tempo + 0.30))
  else
    -- No readable move: keep an optimistic timeline so the chain still advances.
    Combo.path, Combo.step = ready.path, #ready.path
    Combo.started = now
  end
end

-- Hit accounting: every swing is queued and credited only when the target's
-- health really drops.  A swing that never lands expires as a whiff, so the
-- counters describe the fight rather than the injection calls.
local PendingHits = {}
local TargetKey, TargetHp = '', nil

local function NoteTargetHP(target, now)
  local key = target and Address(target.actor) or ''
  local hp = target and target.hp
  if key ~= TargetKey then
    PendingHits = {}
    TargetKey, TargetHp = key, hp
    return
  end
  if hp and TargetHp and hp < TargetHp - 0.001 then
    if #PendingHits > 0 then
      local entry = table.remove(PendingHits, 1)
      Counters.attack_ok = Counters.attack_ok + 1
      -- learn from the swing that landed, but never crawl: below ~2.6 m the
      -- character just stands there while the enemy circles
      if entry and entry.dist then
        Cache.reach = math.min(Config.attack_distance, math.max(Config.hit_floor, entry.dist + 0.8))
      end
      if entry and entry.dist then Cache.hit_dist = entry.dist end
      -- HP changes may be delayed, DOT, or another actor's damage. They are
      -- not reliable animation/cancel timings and must not retime the combo.
    end
  end
  if hp then TargetHp = hp end
  while #PendingHits > 0 and now - PendingHits[1].at > 0.9 do
    local entry = table.remove(PendingHits, 1)
    if not hp or not entry.hp_known then
      Counters.attack_unverified = Counters.attack_unverified + 1
    else
    Counters.attack_no_damage = Counters.attack_no_damage + 1
    if entry and entry.dist then Cache.whiff_dist = entry.dist end
    end
  end
end

local function NoteAttack(now, distance, step, key)
  PendingHits[#PendingHits + 1] = { at = now, dist = distance, step = step, key = key,
                                    hp_known = TargetHp ~= nil }
end

local function YieldCombat(now, reason)
  ReleaseOffense(reason == 'defense')
  ShadowPending = nil
  CombatYieldReason = reason
  if not CombatYielded then
    CombatYielded = true
    Counters.combat_yields = Counters.combat_yields + 1
    Combo.awaiting, Combo.ready, Combo.pressed_at = '', nil, -1
    Combo.awaiting_key, Combo.retried = nil, false
    if reason == 'healing' then
      -- A healing animation really does break the chain; a defense only pauses
      -- it, and the next observed move decides whether it survived.
      ComboReset(now, 'healing')
    end
  end
end

local function ResumeCombat(now)
  if not CombatYielded then return end
  local reason = CombatYieldReason
  CombatYielded = false
  Counters.combat_resumes = Counters.combat_resumes + 1
  NextAttack = math.max(NextAttack, now + 0.08)
  Cache.macro_next = math.max(Cache.macro_next or 0, now + 0.12)
  if reason == 'healing' then ComboReset(now, 'healing') end
  Combo.awaiting, Combo.ready = '', nil
  -- Defense pauses a sequence instead of restarting it at light one.  Without a
  -- readable move the current window is treated as open so the counterattack
  -- leaves at once; with a readable move the real window stays authoritative.
  if not Combo.feedback and Combo.path ~= '' then
    Combo.started = now - (MOVE_WINDOWS[Combo.path] or DEFAULT_WINDOW).open
        * Combo.tempo * Config.combo_speed
  end
  Combo.idle_until = now
  CombatYieldReason = ''
end

local HealChainWait = nil
local function HealTick(now, hp, hp_max, can_start, enemies)
  if not Config.heal_enabled then HealPending, HealChainWait = nil, nil; return false end
  if not hp or not hp_max or hp_max <= 0 or hp <= 0 then
    HealPending, HealChainWait = nil, nil
    Status.heal_state = 'health_unavailable'
    return false
  end
  local ratio = hp / hp_max
  if SupportPending then
    if hp > SupportPending.hp + math.max(0.5, hp_max * 0.005) then
      Counters.support_confirmed = Counters.support_confirmed + 1
      Status.support_state = 'hp_increased'
      SupportPending = nil
    elseif now >= SupportPending.until_time then
      Counters.support_unconfirmed = Counters.support_unconfirmed + 1
      Status.support_state = 'no_hp_increase_observed'
      SupportPending = nil
    else
      Status.support_state = 'sent_waiting_effect'
    end
  end
  local function attempt(role, stage)
    local entry = RoleEntry(role)
    if not entry or not Valid(entry.action) then return false, 'action_missing:' .. role end
    -- Some builds swallow a short tap on the quick slot; the hold grows after a
    -- press that produced nothing and returns to the base after a success.
    local hold = math.max(Config.heal_hold, Cache.heal_hold or 0)
    if not Press(role, hold) then return false, 'input_failed:' .. entry.name end
    MedicineRecoveryUntil = now + 0.65
    HealPending = { stage = stage, hp = hp, name = entry.name,
                    until_time = now + 2.0, defer_until = now + 2.5 }
    Cache.heal_last_name = entry.name
    Status.action, Status.heal_state = role, 'waiting_effect'
    return true
  end
  if now < MedicineRecoveryUntil and not HealPending then
    Status.action, Status.heal_state = 'item_recovery', 'waiting_animation'
    return true
  end
  if HealPending then
    if hp > HealPending.hp + math.max(0.5, hp_max * 0.005) then
      Counters.heal_ok, Counters.heals = Counters.heal_ok + 1, Counters.heals + 1
      Cache.heal_hold = Config.heal_hold
      Cache.heal_last = HealPending.stage <= 1 and 'heal' or 'heal_alt'
      -- Prefer the fallback only after it actually restored HP this session.
      Cache.heal_prefer_alt = HealPending.stage == 2
      HealPending, HealChainWait, NextHeal = nil, nil, now + 4
      Status.action, Status.heal_state = 'heal_ok', 'hp_increased'
      return true
    end
    if now < HealPending.until_time then
      Status.action, Status.heal_state = 'heal_waiting', 'waiting_effect'
      return now < MedicineRecoveryUntil
    end
    -- Defer one fallback briefly if a defensive animation overlaps the timeout.
    if not can_start and now < (HealPending.defer_until or 0) then
      HealPending.until_time = now + 0.2
      Status.action, Status.heal_state = 'heal_wait_defense', 'waiting_defense'
      return true
    end
    local stage = HealPending.stage
    HealPending = nil
    if can_start and stage == 0 then
      Cache.heal_primary_fails = (Cache.heal_primary_fails or 0) + 1
      if attempt('heal_alt', 2) then return true end
    end
    Counters.heal_fail = Counters.heal_fail + 1
    Cache.heal_hold = math.min(0.40, (Cache.heal_hold or Config.heal_hold) + 0.06)
    Cache.heal_prefer_alt = false
    NextHeal, HealChainWait = now + 8, nil
    Status.action, Status.heal_state = 'heal_no_effect',
      'no_hp_gain:' .. tostring(HealPending and HealPending.name or Cache.heal_last_name or '?')
    return false
  end
  local nearby, boss = 0, false
  for _, enemy in ipairs(enemies or {}) do
    if enemy.distance <= 6 then
      nearby = nearby + 1
    end
    if enemy.distance <= 12 then
      local name = enemy.class or ''
      if name:find('Boss', 1, true) or name:find('JingYe', 1, true) then boss = true end
    end
  end
  local use_support = Config.auto_items and ratio <= 0.95 and ratio > 0.20
      and now >= NextSupport and (boss or nearby >= 2) and HasRole('support_item')
  if not use_support and (ratio > Config.heal_threshold or now < NextHeal) then
    HealChainWait = nil
    Status.heal_state = ratio > Config.heal_threshold and 'healthy' or 'cooldown'
    return false
  end
  -- A threat may defer a new drink, but feedback above is always processed.
  -- Bosses announce frames almost continuously, which used to mean "never
  -- drinks"; after a few seconds of being blocked, low HP wins over the threat.
  if can_start then
    HealDeferMs = 0
  elseif ratio <= Config.heal_threshold then
    HealDeferMs = HealDeferMs + (FrameDt * 1000)
  end
  if not can_start and HealDeferMs >= 3500 and ratio <= Config.heal_threshold * 0.95 then
    Counters.heal_brave = Counters.heal_brave + 1
    Cache.heal_last_name = Cache.heal_last_name or 'brave'
    can_start = true
    Status.heal_state = 'heal_brave_threat'
  end
  if not can_start then Status.heal_state = 'waiting_defense'; return true end
  local active_chain = MoveActive(now)
  HealChainWait = HealChainWait or now
  local settle_time = active_chain and 0.8 or 0.35
  if now - HealChainWait < settle_time then
    Status.action, Status.heal_state = 'heal_wait_chain', 'waiting_animation'
    return true -- stop feeding attacks, allowing the animation to finish
  end
  HealChainWait = nil
  if use_support then
    if Press('support_item', Config.heal_hold) then
      Pressed.support_item = now + Config.support_hold
      MedicineRecoveryUntil = now + 1.0
      NextSupport = now + 16
      NextHeal = math.max(NextHeal, now + 2.0)
      SupportPending = { hp = hp, until_time = now + 10.5 }
      Status.support_state = 'sent_waiting_effect'
      Counters.support_uses = Counters.support_uses + 1
      Status.action, Status.heal_state = 'support_item', 'support_sent'
      return true
    end
    NextSupport = now + 5
    Status.heal_state = 'support_action_missing'
    return false
  end
  if Cache.heal_prefer_alt and attempt('heal_alt', 2) then return true end
  local tried, missing = {}, nil
  local ok1, why1 = attempt('heal', 0)
  if ok1 then return true end
  missing = why1
  tried[#tried + 1] = tostring(why1)
  local ok2, why2 = attempt('heal_alt', 2)
  if ok2 then return true end
  tried[#tried + 1] = tostring(why2)
  NextHeal = now + 3
  Status.heal_state = 'action_missing'
  Cache.heal_last_name = table.concat(tried, ' ')
  return false
end

local function TickWeaponSwitch(now, pawn, target, player_set)
  if not Config.switch_enabled and not WeaponSwitch.pending then
    local result = WeaponSwitch:tick(now, { enabled = false })
    Status.weapon_switch_state = result.state
    return false
  end
  local weapons, input, action = WeaponSwitchBindings(pawn, now)
  local asc = Try(function() return pawn.AbilitySystemComponent end)
  local broken = HasGameplayTag(asc, BREAK_TAG)
  local state = {
    enabled = Config.switch_enabled,
    weapons_component = Valid(weapons),
    input_api = Valid(input)
        and Try(function() return input.TryActivateExternalActionAbilityInterruptLowerByTag end) ~= nil,
    change_action = Valid(action),
    weapon_count = Valid(weapons) and Try(function() return weapons:GetSlotWeapons(MAIN_WEAPON_SLOT) end) or nil,
    cooldown = Valid(weapons)
        and Try(function() return weapons:GetCurrentChangeWeaponCooldown(MAIN_WEAPON_SLOT) end) or nil,
    weapon_index = Valid(weapons)
        and Try(function() return weapons:GetCurrentWeaponIndex(MAIN_WEAPON_SLOT, true) end) or nil,
    transition = Valid(weapons)
        and Try(function() return weapons:IsInWeaponTransition() end) == true,
    in_combat = target ~= nil or broken or HasGameplayTag(asc, IN_COMBAT_TAG),
    broken = broken,
    shachi = Attribute(player_set, 'Shachi'),
    shachi_max = Attribute(player_set, 'Shachi_Max'),
  }
  local result = WeaponSwitch:tick(now, state, function()
    local switch_tag = SWITCH_NORMAL_TAG
    if HasGameplayTag(asc, AIR_TAG) then
      switch_tag = SWITCH_AIR_TAG
    elseif HasGameplayTag(asc, SPRINT_TAG) or HasGameplayTag(asc, DASH_TAG) then
      switch_tag = SWITCH_DASH_TAG
    end
    return input:TryActivateExternalActionAbilityInterruptLowerByTag(switch_tag, action, -4)
  end)
  Status.weapon_switch_state = result.state
  return result.yield_action == true
end

-- A stunned or broken enemy is the one window where waiting costs real damage.
-- The tags are read for every target; only the heavy punish stays restricted.
local function ReadBossOpening(target, now)
  local key = target and Address(target.actor) or ''
  if key ~= OpeningActorKey then
    OpeningActorKey, OpeningTag, OpeningScanAt, OpeningPunished = key, '', 0, false
    OpeningSince, OpeningReadable = now, false
  end
  if not target then
    OpeningTag, OpeningPunished = '', false
    return '', ''
  end
  if now >= OpeningScanAt then
    OpeningScanAt = now + 0.08
    local asc = Try(function() return target.actor.AbilitySystemComponent end)
    local observed, readable = '', false
    for _, tag in ipairs(ENEMY_OPENING_TAGS) do
      local present = Valid(asc) and Try(function() return asc:HasMatchingGameplayTag(tag.tag) end)
      if type(present) == 'boolean' then readable = true end
      if present == true then
        observed = tag.name
        break
      end
    end
    OpeningReadable = readable
    if readable then
      if observed ~= OpeningTag then OpeningSince = now end
      OpeningTag = observed
      if OpeningTag == '' then OpeningPunished = false end
    end
  end
  -- An unavailable ASC is not evidence that the stun ended and restarted.
  if not OpeningReadable or OpeningTag == '' then return '', '' end
  -- Ordinary short hit reactions must not be treated as long stun openings.
  if OpeningTag == 'Hitreact.Phase' and now - OpeningSince < 0.18 then return '', '' end
  local kind = OpeningTag == 'Stand.Break' and 'break' or 'flinch'
  return OpeningTag, kind
end

local function SelectCombatTarget(enemies)
  local closest, previous, native, rider = enemies[1], nil, nil, nil
  if not closest then Cache.combat_target_key = ''; return nil end
  for _, enemy in ipairs(enemies) do
    local key = Address(enemy.actor)
    if key == Cache.native_target_key then native = enemy end
    if key == Cache.combat_target_key then previous = enemy end
    if enemy.class:find('JingYe', 1, true) and not enemy.class:find('Horse', 1, true) then rider = enemy end
  end
  local target = closest
  -- Honor a readable native lock. Otherwise avoid oscillating between adjacent
  -- actors; prefer JingYe's rider to the horse when both occupy the same area.
  if native then target = native
  elseif previous and previous.distance <= closest.distance + 1.0 then target = previous end
  if not native and rider and target.class:find('Horse_JingYe', 1, true)
      and rider.distance <= target.distance + 1.5 then target = rider end
  Cache.combat_target_key = Address(target.actor) or ''
  return target
end

-- Only a fresh physical hold permits offense. There is no release grace,
-- active-animation bypass, or fallback to autonomous combat.
Inj.OffenseOpen = function(now)
  Cache.assist_hold, Status.combo_gate = Cache.combo_input:poll(now, Config.combo_session)
  if not Config.attack_enabled or not Config.combo_enabled then
    Status.combo_gate = 'disabled'
    return false
  end
  return Config.run and Cache.assist_hold
end

Inj.CloseCombo = function(now)
  -- Never cancel medicine or defense when the user releases the combo key.
  ReleaseOffense(false)
  Cache.macro_step, Cache.macro_next, Cache.macro_press_at, Cache.macro_tag = 0, 0, 0, ''
  ShadowPending = nil
  if Cache.combo_was_open then
    ComboReset(now, 'key-release')
    Combo.idle_until, Combo.opening_until, Combo.wait_ms = now, 0, 0
    NextAttack = now
  end
  Cache.combo_was_open = false
end

local function CombatTick(now, game_time, pawn, hp, hp_max, enemies)
  local ratio = hp_max and hp and hp_max > 0 and hp / hp_max or 1
  local target = SelectCombatTarget(enemies)
  local count = 0
  for _, enemy in ipairs(enemies) do
    if enemy.distance <= Config.attack_distance then count = count + 1 end
  end
  local active = {}
  for _, enemy in ipairs(enemies) do
    local key = Address(enemy.actor)
    if key then active[key] = true end
  end
  for key in pairs(ShadowDone) do
    if not active[key] then ShadowDone[key] = nil end
  end
  local player_set = AttrSet(pawn)
  if now < ActionRecoveryUntil then
    ReleaseRole('move')
    Status.action = 'skill_recovery'
    return
  end
  if TickWeaponSwitch(now, pawn, target, player_set) then
    ReleaseRole('move')
    Status.action = 'weapon_' .. Status.weapon_switch_state
    return
  end
  Status.enemy_count = tostring(#enemies)
  Status.target = target and target.name or ''
  Status.target_distance = target and string.format('%.2f', target.distance) or ''
  if not target then
    OpeningActorKey, OpeningTag, OpeningScanAt, OpeningPunished = '', '', 0, false
    Status.boss_opening_tag = ''
    ReleaseRole('shadow_arm')
    ShadowPending = nil
    Cache.lock_key = ''
    Approach.key = ''
    ReleaseRole('move')
  end
  local opening_tag, opening_kind = '', ''
  if target then opening_tag, opening_kind = ReadBossOpening(target, now) end
  Status.boss_opening_tag = opening_tag
  if opening_tag ~= '' then
    if opening_tag ~= Cache.last_opening then
      Cache.last_opening = opening_tag
      Counters.openings = Counters.openings + 1
      -- A fresh stun must not be spent waiting for a finished chain.
      if #Combo.path >= #PlanPath() then ComboReset(now, 'opening') end
      Combo.idle_until = math.min(Combo.idle_until, now)
    end
    -- Keep the burst alive for as long as the enemy stays stunned.
    Combo.opening_until = math.max(Combo.opening_until or 0, now + 0.30)
  else
    Cache.last_opening = ''
  end
  if target then
    local key = Address(target.actor)
    if key and key ~= Combo.target_key then
      -- a fresh target only steps the plan rotation every third kill: swapping
      -- the string on every target made the fight feel random instead of
      -- varied, and it kept resetting the learned timing
      Combo.targets_seen = (Combo.targets_seen or 0) + 1
      if Combo.target_key ~= '' and Combo.targets_seen % 3 == 0 then
        Combo.plan_index = (Combo.plan_index % #PLAN_ORDER) + 1
      end
      Combo.target_key = key
      ComboReset(now, 'new-target')
    end
  end
  NoteTargetHP(target, now)


  -- The user owns movement and targeting. A missing target (or a mount's
  -- misleading actor-center distance) must never block a requested combo.
  ReleaseRole('move')

  if Config.execution_enabled and target and target.distance <= 3.2 and target.hp and target.hp_max
      and target.hp_max > 0 and target.hp / target.hp_max <= 0.25 and now >= NextExecute then
    if Press('execution') then
      NextExecute = now + 2
      Counters.executions = Counters.executions + 1
      Status.action = 'execution'
      return
    end
  end

  local boss_target = target and (target.class:find('Boss', 1, true) or target.class:find('JingYe', 1, true))
  -- A normal enemy's flinch must not eat the chain with a heavy opener; a real
  -- break does deserve one, and bosses keep the flinch punish from before.
  local punishable = opening_tag ~= '' and (opening_kind == 'break' or boss_target)
  if Config.attack_enabled and Config.combo_enabled and Config.combo_preset ~= 'light'
      and punishable and not OpeningPunished and HeavyAffordable(player_set, true, 1)
      and not (Config.skills_enabled and HasRole('combat_art') and now >= NextSkill
        and (Attribute(player_set, 'UltimateChargeCount') or 0) >= 1)
      and target.distance <= math.min(5.0, Config.attack_distance) and now >= NextAttack then
    if Press('attack_heavy') then
      OpeningPunished = true
      -- The punish starts a fresh chain: it is the heavy opener by itself, and
      -- the heavy owns the character until its window closes. Pressing into it
      -- only produced dropped inputs and reject churn.
      ComboReset(now, 'boss-punish')
      Combo.idle_until = now + (MOVE_WINDOWS['B'] or DEFAULT_WINDOW).close * Combo.tempo
      NextAttack = now + 0.35
      Counters.attacks = Counters.attacks + 1
      Counters.boss_punishes = Counters.boss_punishes + 1
      NoteAttack(now, target.distance, 1, 'B')
      Status.action = 'boss_punish_' .. opening_tag:gsub('%.', '_')
      return
    end
  end

  if Config.skills_enabled and target and target.distance <= math.min(5, Config.attack_distance) and now >= NextSkill then
    local charges = player_set and Attribute(player_set, 'UltimateChargeCount')
    Status.skill_charges = charges ~= nil and tostring(math.floor(charges)) or ''
    if charges and charges >= 1 then
      if Press('combat_art') then
        NextSkill = now + 7
        ActionRecoveryUntil = now + 0.65
        NextAttack = math.max(NextAttack, ActionRecoveryUntil)
        Counters.skills = Counters.skills + 1
        Status.skill_state = 'sent'
        Status.action = 'weapon_skill'
        return
      end
      Status.skill_state = 'input_missing'
    elseif charges == nil then
      Status.skill_state = 'resource_unknown'
    else
      Status.skill_state = 'resource_empty'
    end
  elseif not Config.skills_enabled then
    Status.skill_state = 'disabled'
  elseif now < NextSkill then
    Status.skill_state = 'cooldown'
  end

  if not Config.attack_enabled then return end

  -- Window aligned chain: ComboTick decides the next key from the running move
  -- and its own input window, ComboCommit anchors the next window on the move
  -- the game actually started.
  local role, key = ComboTick(now, player_set, target, opening_tag)
  if role and now >= NextAttack then
    if Press(role) then
      Counters.attacks = Counters.attacks + 1
      Counters.combo_press = Counters.combo_press + 1
      ComboCommit(now)
      NextAttack = now + 0.05
      NoteAttack(now, target and target.distance, Combo.step + 1, key)
      Status.action = 'combo_' .. key
      return
    end
    Combo.ready = nil
    Counters.attack_fail = Counters.attack_fail + 1
    NextAttack = now + 0.5
    Status.action = 'attack_action_missing'
    return
  end
  if Combo.path ~= '' then
    Status.action = 'combo_' .. Combo.path
  elseif not HasRole('attack_light') and not HasRole('attack_heavy') then
    -- no attack mapping at all: say so on every tick instead of looking idle
    Status.action = 'attack_action_missing'
  else
    Status.action = 'combo_ready'
  end
end

local function StopInputs()
  if not Inj.released then
    InjectRoleValue('guard', 0.0)
    InjectRoleValue('deflect', 0.0)
    InjectRoleValue('dodge', 0.0)
    InjectRoleValue('dodge_hold', 0.0)
  end
  Inj.block_until, Inj.dodge_until, Inj.press_pending, Inj.held_last, Inj.burst, Inj.released = -1, -1, false, false, 0, true
  Tracked = {}
  ReleaseRole('move')
  ReleaseRole('shadow_arm')
  ShadowPending = nil
  for role in pairs(Pressed) do InjectRoleValue(role, 0.0) end
  Pressed = {}
  HealPending, HealChainWait = nil, nil
  CombatYielded, CombatYieldReason = false, ''
  ActionRecoveryUntil, MedicineRecoveryUntil = 0, 0
  SupportPending = nil
  ComboReset(os.clock(), 'stop')
  Approach.key = ''
end

local function ResetCombatSession()
  StopInputs()
  PendingHits, TargetKey, TargetHp = {}, '', nil
  NextAttack, NextHeal, NextSkill, NextExecute, NextShadow, NextLock = 0, 0, 0, 0, 0, 0
  WeaponSwitch:reset()
  ShadowPending, ShadowDone = nil, {}
  Cache.enemies, Cache.next_enemy_scan, Cache.next_defense_enemy_scan, Cache.lock_key = {}, 0, 0, ''
  Cache.targeting, Cache.targeting_owner, Cache.next_targeting_scan, Cache.next_native_lock = nil, '', 0, 0
  Cache.hit_dist, Cache.reach, Cache.heal_prefer_alt = 0, nil, false
  Cache.specs, Cache.next_spec_scan, Cache.spec_scan_error = nil, 0, ''
  ComboReset(os.clock(), 'session')
  Combo.feedback, Combo.signal, Combo.phase_seen = false, 'none', false
  Combo.confirms, Combo.rejects, Combo.resyncs, Combo.streak = 0, 0, 0, 0
  Combo.tempo, Combo.delay, Combo.delay_by_path = 1.0, 0.0, {}
  Combo.target_key = ''
  CombatYielded, CombatYieldReason = false, ''
  OpeningActorKey, OpeningTag, OpeningScanAt, OpeningPunished = '', '', 0, false
  Cache.native_locked, Cache.native_target_key, Cache.combat_target_key = false, '', ''
  Cache.deflect, Cache.next_deflect_scan = nil, 0
  LastFrameClock, GuardUntil = nil, 0
  Approach.key, NextSupport = '', 0
  SupportPending = nil
  Cache.next_montage_scan, Cache.enemy_montages, Cache.montage_signatures = 0, {}, {}
end

local function DefenseStatus(result)
  Status.defense_engine = 'PBZSmartCombat 0.17.17 defense'
  Status.defense_ready = result.ready and '1' or '0'
  Status.defense_parry_ready = result.parry_ready and '1' or '0'
  Status.defense_dodge_ready = result.dodge_ready and '1' or '0'
  Status.defense_error = result.error or ''
  -- Post-hit HP observation: did the announced attack that we answered still
  -- take HP? That is the only real success signal for a parry.
  Status.defense_checks_no_loss = tostring(Counters.def_ok)
  Status.defense_checks_damage = tostring(Counters.def_fail)
  Status.defense_checks_unknown = tostring(Counters.def_unknown)
  Status.defense_api_fail = tostring(result.api_fail or 0)
  Status.defense_projectiles = tostring(result.projectiles or 0)
  Status.defense_tick_ms = tostring(result.tick_ms_last or 0)
  Status.defense_tick_ms_max = tostring(result.tick_ms_max or 0)
  Status.defense_projectile_tracked = tostring(result.projectile_tracked or 0)
  Status.defense_frames_late = tostring(result.frames_late or 0)
end

-- Macro mode: the held key alone decides that the string goes on. Each weapon
-- keeps its own string (A light tap, B heavy tap, L charged light, H charged
-- heavy); the cadence is the configured attack period. No chain tag, target or
-- distance is consulted, so a dropped label never parks the assistant.
-- Execution is the one exception: a downed target inside reach is finished
-- automatically before the string continues.
Inj.WeaponPaths = {
  default = 'AAAB',
  Hun_Tang = 'AAAB',
  Hun_Double = 'AAB',
  Hun_Long = 'AAAAB',
  BrutalAxe = 'AAB',
  HuPao = 'AAB',
}

Inj.MacroKeyRole = function(key)
  if key == 'B' then return 'attack_heavy', false end
  if key == 'H' then return 'attack_heavy', true end
  if key == 'L' then return 'attack_light', true end
  return 'attack_light', false
end

Inj.IsAttackMontage = function(name)
  local upper = string.upper(name)
  local ignore = { 'HIT', 'DIE', 'DEATH', 'REACT', 'BLOCK', 'PARRY', 'DODGE', 'IDLE', 'RUN', 'WALK', 'STUN' }
  for _, word in ipairs(ignore) do
    if string.find(upper, word, 1, true) then return false end
  end
  local attack = { 'SKILL', 'ATTACK', 'COMBO' }
  for _, word in ipairs(attack) do
    if string.find(upper, word, 1, true) then return true end
  end
  return false
end

-- A monster montage starting is the only warning before the deflect frame is
-- published; block on that edge instead of waiting for a frame that is late
-- most of the time.
Inj.PreGuard = function(enemy, now)
  if not HasRole('guard') then return end
  -- Never trade a swing for a guard flick: only pre-block between our own attacks.
  if now - (Cache.macro_press_at or 0) < Config.pre_guard_quiet then return end
  if enemy.distance and enemy.distance > Config.pre_guard_distance then return end
  if now < (Cache.pre_guard_until or 0) then return end
  Cache.pre_guard_until = now + Config.pre_guard_interval
  Counters.pre_guards = (Counters.pre_guards or 0) + 1
  Status.pre_guards = tostring(Counters.pre_guards)
  Status.pre_guard_class = enemy.class or ''
  Press('guard', Config.pre_guard_hold)
end

Inj.MacroTick = function(now, pawn, target)
  if not (Config.attack_enabled and Config.combo_enabled) then return end
  if now - (Cache.macro_press_at or 0) < Config.press_gap then return end
  -- Auto execution: the assist still finishes a downed target by itself.
  if target and HasRole('execution') and now >= (NextExecute or 0)
      and target.distance and target.distance <= 3.2
      and target.hp and target.hp_max and target.hp_max > 0
      and target.hp / target.hp_max <= 0.25 then
    if Press('execution', Config.press_hold) then
      Counters.executions = (Counters.executions or 0) + 1
      NextExecute = now + 2
      Status.action = 'macro_execution'
      return
    end
  end
  -- The game publishes the move tag as soon as the previous swing connects;
  -- treat a new tag as the go-ahead instead of guessing a fixed period.
  local chain = ReadChain(now)
  if chain ~= '' and chain ~= (Cache.macro_tag or '') then
    Cache.macro_tag = chain
    if now < (Cache.macro_next or 0) then Cache.macro_next = now + Config.press_gap end
  end
  if now < (Cache.macro_next or 0) then
    -- Stall guard: if the string has not pressed anything for a while,
    -- drop the cadence so the next tick fires immediately.
    if now - (Cache.macro_press_at or 0) > 1.0 then Cache.macro_next = 0 end
    return
  end
  local weapon = Status.weapon or ''
  local family = weapon:gsub('^Weapon_', '')
  local pattern = Inj.WeaponPaths[family] or Inj.WeaponPaths.default
  if pattern == '' then pattern = Inj.WeaponPaths.default end
  local step = (Cache.macro_step or 0) % #pattern
  local key = pattern:sub(step + 1, step + 1)
  local role, charged = Inj.MacroKeyRole(key)
  if role == 'attack_heavy' and not HeavyAffordable(AttrSet(pawn), false, 1) then
    key, role, charged = 'A', 'attack_light', false
  end
  local hold = (charged or key == 'B') and math.max(Config.press_hold, 0.12) or Config.press_hold
  local ok = Press(role, hold)
  Cache.macro_step = step + 1
  Cache.macro_press_at = now
  Cache.macro_next = now + math.max(0.40, Config.attack_period)
      + (key == 'B' and 0.14 or 0)
  Cache.macro_pattern = pattern
  Status.macro_pattern = pattern
  Status.macro_step = tostring(step + 1) .. '/' .. tostring(#pattern)
  if ok then
    Counters.attacks = Counters.attacks + 1
    Status.action = 'macro_' .. key
  else
    Status.action = 'macro_missing'
  end
end

local function Snapshot()

  local now = os.clock()
  Perf.defense, Perf.enemies, Perf.heal, Perf.combat = 0, 0, 0, 0
  ReadControl(now)
  local combo_open = Inj.OffenseOpen(now)
  if not combo_open then Inj.CloseCombo(now) end
  Status.control_run = Config.run and '1' or '0'
  if not Config.run and WasRunning then
    StopInputs()
    WeaponSwitch:reset()
    DefenseActive, WasRunning = false, false
    Status.weapon_switch_state, Status.action, Status.reason = 'paused', 'idle', 'paused'
    Status.target, Status.target_distance, Status.enemy_count = '', '', '0'
  end
  if not Config.run and now < NextIdleProbe then return end
  if not Config.run then NextIdleProbe = now + 0.5 else NextIdleProbe, WasRunning = 0, true end
  local previous_pawn = Cache.pawn_key
  local pawn = RefreshPlayer()
  if not Valid(pawn) then
    StopInputs()
    Status.running, Status.reason, Status.player, Status.weapon = '0', 'waiting_player', '', ''
    Status.target, Status.target_distance, Status.enemy_count = '', '', '0'
    Status.action = 'waiting_player'
    return
  end
  local game_time = Try(function()
    return UEHelpers.GetGameplayStatics():GetTimeSeconds(UEHelpers.GetWorld())
  end)
  if type(game_time) ~= 'number' then StopInputs(); Status.reason = 'waiting_clock'; return end
  if previous_pawn ~= Cache.pawn_key or (LastGameTime and game_time < LastGameTime - 0.5) then
    ResetCombatSession()
  end
  local paused = LastGameTime and math.abs(game_time - LastGameTime) < 0.000001
  LastGameTime = game_time
  if not Config.run then
    StopInputs()
    WeaponSwitch:reset()
    Status.weapon_switch_state, Status.action, Status.reason = 'paused', 'idle', 'paused'
    Status.target, Status.target_distance, Status.enemy_count = '', '', '0'
    DefenseActive = false
    return
  end
  local part_started = os.clock()
  RefreshRoles(now)
  Perf.roles = (os.clock() - part_started) * 1000
  part_started = os.clock()
  RefreshAttrIndex(now)
  local hp, hp_max = ReadHP(pawn)
  local set = AttrSet(pawn)
  local shachi = set and Attribute(set, 'Shachi')
  local shachi_max = set and Attribute(set, 'Shachi_Max')
  local skill_charges = set and Attribute(set, 'UltimateChargeCount')
  Perf.attrs = (os.clock() - part_started) * 1000
  Status.heavy_ok = HeavyAffordable(set, false, 1) and '1' or '0'
  -- Sample the owned move tags before arbitration: medicine and the shadow
  -- charge must know that a chain move is still running even while defense or
  -- healing owns the frame.
  ProbeCombatTags(now)
  Status.player = Try(function() return pawn:GetFullName() end) or Name(pawn)
  Status.hp, Status.hp_max = hp and string.format('%.1f', hp) or '', hp_max and string.format('%.1f', hp_max) or ''
  Status.shachi = shachi and string.format('%.1f/%.1f', shachi, shachi_max or 0) or ''
  Status.skill_charges = skill_charges ~= nil and tostring(math.floor(skill_charges)) or ''
  if not Config.skills_enabled then Status.skill_state = 'disabled'
  elseif skill_charges == nil then Status.skill_state = 'resource_unknown'
  elseif skill_charges < 1 then Status.skill_state = 'resource_empty'
  else Status.skill_state = 'ready' end
  Status.weapon = Try(function() return pawn.CurrentWeapon:GetFName():ToString() end)
      or Try(function() return pawn.Weapon:GetFName():ToString() end) or 'unknown'
  if Status.weapon ~= 'unknown' and Status.weapon ~= Cache.weapon_key then
    if Cache.weapon_key then
      Cache.specs, Cache.next_spec_scan = nil, 0
      Cache.hit_dist, Cache.reach = 0, nil
      -- a different weapon has its own move windows: relearn the tempo
      ComboReset(now, 'weapon-change')
      Combo.tempo, Combo.delay, Combo.delay_by_path = 1.0, 0.0, {}
      Combo.feedback, Combo.phase_seen = false, false
    end
    Cache.weapon_key = Status.weapon
  end
  if not Config.run or paused or (hp and hp <= 0) then
    Status.combo_gate = 'disabled'
    StopInputs()
    WeaponSwitch:reset()
    Status.weapon_switch_state = 'paused'
    Status.action = not Config.run and 'idle' or paused and 'game_paused' or 'player_dead'
    Status.reason = not Config.run and 'paused' or Status.action
    Status.target, Status.target_distance, Status.enemy_count = '', '', '0'
    return
  end
  NextRelease()
  Status.reason = 'running'
  -- Legacy defense follows the game's announced attack frames.
  local defense
  if Config.guard_enabled or Config.dodge_enabled then
    if not Valid(Cache.deflect) and now >= Cache.next_deflect_scan then
      Cache.deflect = FindOwnedComponent('DeflectComponent', pawn)
      Cache.next_deflect_scan = now + (Valid(Cache.deflect) and 2 or 0.25)
    end
    if LastFrameClock then FrameDt = math.min(0.1, math.max(0.001, now - LastFrameClock)) end
    LastFrameClock = now
    part_started = os.clock()
    local busy = DefenseTick(now, game_time, shachi, hp, hp_max)
    Perf.defense = (os.clock() - part_started) * 1000
    defense = { ready = Valid(Cache.deflect),
      parry_ready = Valid(Cache.deflect) and (HasRole('guard') or HasRole('deflect')),
      dodge_ready = Valid(Cache.deflect) and HasRole('dodge'), busy = busy, action = Status.action }
    DefenseActive = true
  else
    if DefenseActive then StopInputs() end
    DefenseActive = false
    defense = { ready = false, busy = false, action = 'disabled' }
  end
  DefenseStatus(defense)
  local enemies
  part_started = os.clock()
  if defense.busy then
    RefreshEnemySnapshot(pawn)
    if #Cache.enemies == 0 and now >= Cache.next_defense_enemy_scan then
      Cache.next_defense_enemy_scan = now + 2.0
      enemies = ScanEnemies(pawn, now)
    else
      enemies = Cache.enemies
    end
  else enemies = ScanEnemies(pawn, now) end
  Perf.enemies = (os.clock() - part_started) * 1000
  ObserveEnemyMontages(enemies, now)
  local target = SelectCombatTarget(enemies)
  Status.enemy_count = tostring(#enemies)
  Status.target, Status.target_distance = target and target.name or '', target and string.format('%.2f', target.distance) or ''
  NoteTargetHP(target, now)
  -- Observe medicine results every tick, even during a defensive action.
  part_started = os.clock()
  local healing = HealTick(now, hp, hp_max, not defense.busy and now >= GuardUntil, enemies)
  Perf.heal = (os.clock() - part_started) * 1000
  if defense.busy or healing then
    if combo_open then Status.combo_gate = defense.busy and 'defense' or 'healing' end
    YieldCombat(now, defense.busy and 'defense' or 'healing')
    if defense.busy then Status.action = defense.action or 'defending' end
  else
    part_started = os.clock()
    if combo_open then
      ResumeCombat(now)
      Cache.combo_was_open = true
      if Config.combo_macro then
        Inj.MacroTick(now, pawn, target)
      else
        CombatTick(now, game_time, pawn, hp, hp_max, enemies)
      end
    else
      Status.action = Status.combo_gate == 'disabled' and 'combo_disabled' or 'wait_attack'
    end
    Perf.combat = (os.clock() - part_started) * 1000
  end
end

local function Tick()
  local started = os.clock()
  local ok, err = pcall(Snapshot)
  RuntimeTickMs = (os.clock() - started) * 1000
  RuntimeTickMsMax = math.max(RuntimeTickMsMax, RuntimeTickMs)
  Status.runtime_tick_ms = string.format('%.2f', RuntimeTickMs)
  Status.runtime_tick_ms_max = string.format('%.2f', RuntimeTickMsMax)
  if not ok then
    pcall(StopInputs)
    ErrorStreak = ErrorStreak + 1
    Status.reason = 'runtime_error'
    Status.error = tostring(err):gsub('[\r\n=]', ' ')
    if ErrorStreak >= 3 then Config.run = false end
  else
    ErrorStreak = 0
    Status.error = ''
  end
  local io_started = os.clock()
  WriteStatus(false)
  WriteDiag(false)
  Perf.writes = (os.clock() - io_started) * 1000
end

WriteStatus(true)
WriteDiag(true)
LoopInGameThreadAfterFrames(1, function()
  Tick()
end)
print(string.format('[PBZSmartCombat] Loaded %s; hold-to-combo with independent defense and medicine.\n', VERSION))
