-- PBZSmartCombat 0.13.2
-- In-game combat controller for Phantom Blade Zero.
--   * typed defense: blue = parry (IA_Deflect), red = dodge (IA_Dodge + hold),
--     normal = parry/guard, all verified against FDeflectReceiveFrame.IsHit;
--   * ability closed loop combo: reads GA_Hun_Tang_<chain> ActiveCount, so the
--     next key press follows the chain the game actually entered;
--   * everything the game does not expose falls back to timed rotation.
local UEHelpers = require('UEHelpers')

local VERSION = '0.16.0'
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
local ControlPath = ModFile('control.ini')
local DiagPath = ModFile('diag.txt')

local Config = {
  run = false, lease_until = 0, last_control = 0,
  heal_enabled = true, attack_enabled = true, guard_enabled = true,
  dodge_enabled = true, execution_enabled = true, skills_enabled = true,
  shadow_enabled = true, switch_enabled = true, lock_enabled = true,
  combo_enabled = true,
  heal_threshold = 0.4, attack_distance = 3.5, attack_period = 0.46,
  combo_preset = 'balanced', combo_rotate = true,
  blue_response = 'deflect', red_response = 'dodge', normal_response = 'deflect',
  parry_lead = 0.16, guard_lead = 0.18, dodge_lead = 0.11,
  block_hold = 0.22, dodge_hold = 0.12, stale_after = 0.12, max_burst = 60,
  chain_ratio = 0.58, chain_timeout = 0.70, press_gap = 0.12,
  enemy_scan_interval = 2.5, enemy_retry_interval = 0.8,
  action_overrides = {},
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
  heal          = { {'IA_YaoHuLu', 'IA_Heal', 'IA_Medicine'}, {'yaohulu', 'heal'} },
  heal_alt      = { {'IA_UseTopItem', 'IA_UseBottomItem'}, {'usetopitem', 'usebottomitem'} },
  execution     = { {'IA_Execution', 'IA_Execute'}, {'execution', 'execute'} },
  combat_art    = { {'IA_CombatArt', 'IA_Skill'}, {'combatart', 'skill'} },
  shadow_arm    = { {'IA_ShachiAbility', 'IA_ShadowArm', 'IA_ChangeArmed', 'IA_ChangeShachiWeapon'},
                    {'shachiability', 'shadowarm', 'changearmed', 'changeshachi'} },
  change_weapon = { {'IA_ChangeWeapon', 'IA_SwitchWeapon'}, {'changeweapon', 'switchweapon'} },
  lock_on       = { {'IA_LockTarget', 'IA_Lock'}, {'locktarget', 'lockon'} },
}

-- The game resolves A/B presses into GA_Hun_Tang_<chain> abilities; a plan is
-- only the key sequence to feed.  Chain depth is read back from the running
-- ability, so the loop follows the game instead of guessing at timings.
local PLANS = {
  balanced = { 'A', 'A', 'A', 'B' },
  pressure = { 'A', 'A', 'B', 'B' },
  swift    = { 'A', 'A', 'A', 'A', 'A' },
  light    = { 'A' },
}
local KEY_ROLE = { A = 'attack_light', B = 'attack_heavy' }
-- rotating plans keeps the strings varied without giving up the heavy finisher
local PLAN_ORDER = { 'balanced', 'pressure', 'swift', 'balanced', 'light' }

local Status = {
  schema = SCHEMA, mod_version = VERSION, loaded = '1', running = '0',
  heartbeat = '0', player = '', hp = '', hp_max = '', shachi = '',
  enemy_count = '0', target = '', target_distance = '', weapon = 'unknown',
  q_action = '', charge_action = '', action = 'idle', reason = 'waiting_player',
  attacks = '0', blocks = '0', dodges = '0', heals = '0', skills = '0', executions = '0',
  inj_ok = '0', inj_fail = '0', enemy_scan_ms = '0', actions_used = '', error = '',
  heal_ok = '0', heal_fail = '0', attack_ok = '0', attack_no_damage = '0',
  enemy_classes_seen = '', combo_state = '', combo_step = '', parry = '0',
  dodge_red = '0', def_ok = '0', def_fail = '0', frame_types = '',
}

local Cache = {
  controller = nil, pawn = nil, deflect = nil, subsystem = nil,
  action_index = nil, action_names = {}, action_count = 0, next_action_scan = 0,
  roles = nil, next_roles = 0,
  attr_by_owner = {}, next_attr_scan = 0, attr_scans = 0,
  enemies = {}, next_enemy_scan = 0, enemy_scan_ms = 0, actors_scanned = 0,
  enemy_classes_seen = {}, faction_seen = 0, faction_components = 0, enemy_list = '',
  specs = nil, next_spec_scan = 0, move_durations = {},
  src_field = '', type_field = '', src_info = '', src_classes = {},
  pawn_key = '', pawn_misses = 0, hit_dist = 0, whiff_dist = 0, lock_key = '',
  heal_prefer_alt = false, heal_gourd_fails = 0, spec_field = nil, area_until = 0, area_dodges = 0,
}

local Tracked = {}
local RefreshSpecs, ReadChain, SpecProbe  -- defined with the combo engine; used by diag
local Inj = { block_until = -1, dodge_until = -1, press_pending = false, held_last = false, burst = 0, released = true }
local FrameDt, LastFrameClock, LastGameTime = 1 / 60, nil, nil
local NextAttack, NextHeal, NextSkill, NextExecute, NextSwitch, NextShadow, NextLock = 0, 0, 0, 0, 0, 0, 0
local NextGuardHold, GuardUntil = 0, 0
local ShadowPending, ShadowDone = nil, {}
local HealPending, AttackPending = nil, nil
local HealPulseUntil, HealPulseRole = 0, 'heal'
local AttackCount = 0
local LastStatusWrite, LastDiagWrite, StatusSlot = 0, 0, 0
local ErrorStreak = 0
local Counters = {
  attacks = 0, blocks = 0, dodges = 0, heals = 0, skills = 0, executions = 0,
  inj_ok = 0, inj_fail = 0, missing = 0, frames = 0, frames_late = 0, attack_fail = 0,
  heal_ok = 0, heal_fail = 0, attack_ok = 0, attack_no_damage = 0,
  parry = 0, dodge_red = 0, guard = 0, def_ok = 0, def_fail = 0, combo_press = 0, guard_hold = 0,
  shadow_uses = 0, shield_breaks = 0,
}
local Kinds = { normal = 0, blue = 0, red = 0, power = 0, hold = 0, unknown = 0 }
local Combo = { chain = '', since = 0, step = 0, last_press = 0, plan_index = 1, target_key = '' }

local function PlanFor()
  if Config.combo_rotate then
    return PLANS[PLAN_ORDER[Combo.plan_index] or 'balanced'] or PLANS.balanced
  end
  return PLANS[Config.combo_preset] or PLANS.balanced
end

local function PlanName()
  if Config.combo_rotate then return PLAN_ORDER[Combo.plan_index] or 'balanced' end
  return Config.combo_preset
end
-- Timed fallback used when the ability array cannot be read: follow the same
-- A/A/A/B plan and adapt the cadence from the measured hit rate.
local Plan = { step = 0, period = 0.46, delay = {}, ok = 0, miss = 0, mode = 'ability' }

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

local function Address(o)
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

local function ReadControl(now)
  if now - Config.last_control < 0.20 then
    if now >= Config.lease_until then Config.run = false end
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
  for _, key in ipairs({
    'heal_enabled', 'attack_enabled', 'guard_enabled', 'dodge_enabled',
    'execution_enabled', 'skills_enabled', 'shadow_enabled', 'switch_enabled',
    'lock_enabled', 'combo_enabled', 'combo_rotate',
  }) do
    if data[key] ~= nil then Config[key] = data[key] == '1' end
  end
  local threshold = tonumber(data.heal_threshold)
  if threshold and threshold >= 0.1 and threshold <= 0.9 then Config.heal_threshold = threshold end
  local distance = tonumber(data.attack_distance)
  if distance and distance >= 1 and distance <= 15 then Config.attack_distance = distance end
  local period = tonumber(data.attack_period)
  if period and period >= 0.25 and period <= 1.5 then Config.attack_period = period end
  if PLANS[data.combo_preset] then Config.combo_preset = data.combo_preset end
  if data.combo_plan and PLANS[data.combo_plan] then Config.combo_preset = data.combo_plan end
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
  Status.running = Config.run and '1' or '0'
  Status.attacks, Status.blocks, Status.dodges = tostring(Counters.attacks), tostring(Counters.blocks), tostring(Counters.dodges)
  Status.heals, Status.skills, Status.executions = tostring(Counters.heals), tostring(Counters.skills), tostring(Counters.executions)
  Status.inj_ok, Status.inj_fail = tostring(Counters.inj_ok), tostring(Counters.inj_fail)
  Status.enemy_scan_ms = string.format('%.1f', Cache.enemy_scan_ms or 0)
  Status.frames, Status.frames_late = tostring(Counters.frames), tostring(Counters.frames_late)
  Status.faction_seen, Status.attack_fail = tostring(Cache.faction_seen), tostring(Counters.attack_fail)
  Status.heal_ok, Status.heal_fail = tostring(Counters.heal_ok), tostring(Counters.heal_fail)
  Status.attack_ok, Status.attack_no_damage = tostring(Counters.attack_ok), tostring(Counters.attack_no_damage)
  Status.enemy_classes_seen = table.concat(Cache.enemy_classes_seen, ',')
  Status.parry, Status.dodge_red = tostring(Counters.parry), tostring(Counters.dodge_red)
  Status.def_ok, Status.def_fail = tostring(Counters.def_ok), tostring(Counters.def_fail)
  Status.combo_state = Combo.chain ~= '' and Combo.chain or 'idle'
  Status.combo_step = string.format('%d/%d', Combo.step, #PLANS[Config.combo_preset])
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
  line('heartbeat', os.time())
  line('scanned_actions', Cache.action_count)
  line('inj_ok', Counters.inj_ok)
  line('inj_fail', Counters.inj_fail)
  line('inj_missing', Counters.missing)
  line('enemy_scan_ms', string.format('%.1f', Cache.enemy_scan_ms or 0))
  line('faction_seen', Cache.faction_seen)
  line('faction_components', Cache.faction_components)
  line('enemy_list', Cache.enemy_list)
  line('enemy_classes_seen', table.concat(Cache.enemy_classes_seen, ','))
  line('attack_ok', Counters.attack_ok)
  line('attack_no_damage', Counters.attack_no_damage)
  line('attack_fail', Counters.attack_fail)
  line('shadow_uses', Counters.shadow_uses)
  line('shield_breaks', Counters.shield_breaks)
  line('target_shield', tostring((Cache.enemies[1] and Cache.enemies[1].shield) or 'nil'))
  line('shield_field', (Cache.enemies[1] and Cache.enemies[1].shield_field) or 'none')
  line('heal_ok', Counters.heal_ok)
  line('heal_fail', Counters.heal_fail)
  line('heal_last', Cache.heal_last or 'none')
  line('heal_prefer_alt', tostring(Cache.heal_prefer_alt == true))
  line('heal_gourd_fails', tostring(Cache.heal_gourd_fails or 0))
  line('shadow_action', (RoleEntry('shadow_arm') and RoleEntry('shadow_arm').name) or 'missing')
  line('reach_m', string.format('%.2f', Cache.reach or Config.attack_distance))
  line('hit_dist_m', string.format('%.2f', Cache.hit_dist or 0))
  line('whiff_dist_m', string.format('%.2f', Cache.whiff_dist or 0))
  line('frames', Counters.frames)
  line('frames_late', Counters.frames_late)
  line('parry', Counters.parry)
  line('dodge_red', Counters.dodge_red)
  line('guard', Counters.guard)
  line('guard_hold', Counters.guard_hold)
  line('def_ok', Counters.def_ok)
  line('def_fail', Counters.def_fail)
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
  line('combo_mode', Plan.mode)
  line('combo_period_ms', string.format('%.0f', Plan.period * 1000))
  local delays = {}
  for step = 1, 5 do
    if Plan.delay[step] then delays[#delays + 1] = string.format('%d:%.0f', step, Plan.delay[step] * 1000) end
  end
  line('plan_delays_ms', table.concat(delays, ','))
  line('io_relay', RelayPrefix ~= '' and (BaseDir .. RelayPrefix) or 'native')
  line('spec_field', Cache.spec_field or 'none')
  line('combo_chain', Combo.chain ~= '' and Combo.chain or 'idle')
  line('combo_press', Counters.combo_press)
  local specs = RefreshSpecs(now)
  line('spec_count', #specs)
  local sample = {}
  for i = 1, math.min(#specs, 4) do sample[i] = specs[i].name end
  line('spec_sample', table.concat(sample, ','))
  local live = ReadChain(now)
  line('spec_live', live ~= '' and live or '-')
  line('spec_probe', SpecProbe())
  local durations = {}
  for name, value in pairs(Cache.move_durations) do
    durations[#durations + 1] = string.format('%s:%.0f', name, value * 1000)
  end
  table.sort(durations)
  line('move_times_ms', table.concat(durations, ','))
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
  local index = Cache.action_index or {}
  local names = {}
  local count = 0
  local found = Try(function() return FindAllOf('InputAction') end)
  for _, object in ipairs(found or {}) do
    if Valid(object) then
      local name = Name(object)
      if name ~= '' then
        if not index[name] then names[#names + 1] = name end
        index[name] = index[name] or object
        count = count + 1
      end
    end
  end
  if count > Cache.action_count then
    Cache.action_count = count
    table.sort(names)
    for _, name in ipairs(names) do Cache.action_names[#Cache.action_names + 1] = name end
    table.sort(Cache.action_names)
  end
  Cache.action_index = index
  return index
end

local function ResolveRole(role, index)
  local spec = ROLES[role]
  local override = Config.action_overrides[role]
  if override and index[override] then return index[override], override end
  for _, name in ipairs(spec[1]) do
    if index[name] then return index[name], name end
  end
  local best, best_score
  for name, object in pairs(index) do
    local lower = name:lower()
    for _, hint in ipairs(spec[2]) do
      if lower:find(hint, 1, true) then
        local score = #hint
        if not best_score or score > best_score then best, best_score = name, score end
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
  return roles
end

local function GetSubsystem()
  if Valid(Cache.subsystem) then return Cache.subsystem end
  Cache.subsystem = Try(function() return FindFirstOf('EnhancedInputLocalPlayerSubsystem') end)
  return Valid(Cache.subsystem) and Cache.subsystem or nil
end

local function InjectAction(action, value)
  local subsystem = GetSubsystem()
  if not Valid(action) or not subsystem then
    Counters.inj_fail = Counters.inj_fail + 1
    return false
  end
  local x = value or 1.0
  local ok = pcall(function()
    subsystem:InjectInputVectorForAction(action, { X = x, Y = 0.0, Z = 0.0 }, {}, {})
  end)
  if ok then Counters.inj_ok = Counters.inj_ok + 1 else Counters.inj_fail = Counters.inj_fail + 1 end
  return ok
end

local function InjectRoleValue(role, value)
  local entry = RoleEntry(role)
  if not entry or not Valid(entry.action) then
    Counters.missing = Counters.missing + 1
    return false
  end
  return InjectAction(entry.action, value)
end

local function InjectRole(role)
  local entry = RoleEntry(role)
  if not entry or not Valid(entry.action) then
    Counters.missing = Counters.missing + 1
    return false
  end
  return InjectAction(entry.action)
end

local function Position(actor)
  local v = Try(function() return actor:K2_GetActorLocation() end)
  if type(v) == 'table' and type(v.X) == 'number' and type(v.Y) == 'number' and type(v.Z) == 'number' then
    return v.X, v.Y, v.Z
  end
  return nil
end

local function RefreshAttrIndex(now)
  if now < Cache.next_attr_scan and next(Cache.attr_by_owner) ~= nil then return end
  Cache.next_attr_scan = now + 2
  Cache.attr_scans = Cache.attr_scans + 1
  local by = {}
  for _, set in ipairs(FindAllOf('BasicAttributeSet') or {}) do
    if Valid(set) then
      local outer = Try(function() return set:GetOuter() end)
      local key = Address(outer)
      if key and not by[key] then by[key] = set end
    end
  end
  Cache.attr_by_owner = by
end

local function AttrSet(actor)
  if not Valid(actor) then return nil end
  local key = Address(actor)
  if key and Cache.attr_by_owner[key] then return Cache.attr_by_owner[key] end
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

local SHIELD_FIELDS = { 'Shield', 'Shield_Max', 'BreakShield', 'BreakValue', 'Poise',
                        'GuardValue', 'ShieldValue', 'ArmorValue' }

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
    if not Valid(Cache.deflect) then
      for _, component in ipairs(FindAllOf('DeflectComponent') or {}) do
        if Valid(component) and SamePtr(Try(function() return component:GetOwner() end), candidate) then
          Cache.deflect = component
          break
        end
      end
    end
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
end

local function ComponentTag(component)
  local tag = Try(function() return component.Faction end)
  if tag == nil then return nil end
  if type(tag) == 'string' then return tag end
  local text = Try(function() return tag:ToString() end)
  if type(text) == 'string' and text ~= '' then return text end
  return nil
end

local function CollectEnemy(enemies, actor, class_name, px, py, pz, faction)
  if class_name ~= '' and class_name:find('Spawn', 1, true) then return end
  local ex, ey, ez = Position(actor)
  if not ex then return end
  local dx, dy, dz = px - ex, py - ey, pz - ez
  local distance = math.sqrt(dx * dx + dy * dy + dz * dz) / 100
  if distance > 30 or math.abs(dz) > 500 then return end
  local set = AttrSet(actor)
  local hp = set and Attribute(set, 'Health')
  local maximum = set and Attribute(set, 'Health_Max')
  if hp ~= nil and maximum ~= nil and (maximum <= 0 or hp <= 0) then return end
  local shield, shield_field = nil, nil
  if set then shield, shield_field = ShieldOf(set) end
  enemies[#enemies + 1] = {
    actor = actor, name = Name(actor), class = class_name, distance = distance,
    hp = hp, hp_max = maximum, faction = faction or '', has_attr = set ~= nil,
    shield = shield, shield_field = shield_field,
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
        if distance <= 35 and math.abs(dz) <= 500 then
          enemy.distance = distance
          local set = AttrSet(enemy.actor)
          if set then
            local hp, maximum = Attribute(set, 'Health'), Attribute(set, 'Health_Max')
            if hp ~= nil and maximum ~= nil and (maximum <= 0 or hp <= 0) then
              -- dead, drop
            else
              enemy.hp, enemy.hp_max = hp, maximum
              enemy.shield, enemy.shield_field = ShieldOf(set)
              alive[#alive + 1] = enemy
            end
          else
            alive[#alive + 1] = enemy
          end
        end
      end
    end
  end
  table.sort(alive, function(a, b) return a.distance < b.distance end)
  Cache.enemies = alive
end

local function DiscoverEnemies(pawn, enemies)
  local px, py, pz = Position(pawn)
  if not px then return end
  local component_count = 0
  Cache.faction_seen = 0
  local classes = {}
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
          CollectEnemy(enemies, owner, class_name, px, py, pz, faction or '')
        end
      end
    end
  end
  Cache.faction_components = component_count
  Cache.enemy_classes_seen = classes
end

local function ScanEnemies(pawn, now)
  local started = os.clock()
  RefreshEnemySnapshot(pawn)
  if now < Cache.next_enemy_scan then
    Cache.enemy_scan_ms = (os.clock() - started) * 1000
    return Cache.enemies
  end
  local have = #Cache.enemies > 0
  Cache.next_enemy_scan = now + (have and Config.enemy_scan_interval or Config.enemy_retry_interval)
  local enemies = {}
  DiscoverEnemies(pawn, enemies)
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
  DiscoverSource(source, class)
  local field = Cache.type_field
  if field ~= '' then
    local value = Num(Try(function() return source[field] end))
    if value then return TYPE_NAMES[value] or 'unknown', value end
  end
  for _, candidate in ipairs(TYPE_FIELDS) do
    local value = Num(Try(function() return source[candidate] end))
    if value then
      Cache.type_field = candidate
      return TYPE_NAMES[value] or 'unknown', value
    end
  end
  local lower = class:lower()
  if lower:find('blue') then return 'blue', 2 end
  if lower:find('red') then return 'red', 3 end
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

local function DodgeNow(tracked, now)
  Inj.block_until, Inj.press_pending = -1, false
  Inj.dodge_until = math.max(Inj.dodge_until, now + Config.dodge_hold)
  Inj.released = false
  tracked.acted, tracked.action = true, 'dodge'
end

local function DeflectNow(tracked, now)
  Inj.block_until = math.max(Inj.block_until, now + Config.block_hold, tracked.hit + 0.04)
  Inj.press_pending = true
  Inj.released = false
  tracked.acted, tracked.action = true, 'deflect'
end

local function GuardNow(tracked, now)
  Inj.block_until = math.max(Inj.block_until, now + Config.block_hold + 0.1)
  Inj.press_pending = true
  Inj.released = false
  tracked.acted, tracked.action = true, 'guard'
end

local function Act(tracked, now)
  local response = ResponseFor(tracked.kind)
  if response == 'dodge' and not Config.dodge_enabled then response = 'deflect' end
  if response ~= 'dodge' and not Config.guard_enabled then response = 'dodge' end
  if response == 'dodge' then
    DodgeNow(tracked, now)
    Counters.dodge_red = Counters.dodge_red + 1
  elseif response == 'guard' then
    GuardNow(tracked, now)
    Counters.guard = Counters.guard + 1
  else
    DeflectNow(tracked, now)
    Counters.parry = Counters.parry + 1
  end
  if Config.run then Status.action = 'defend_' .. tracked.kind .. '_' .. response end
  return LeadFor(response)
end

local function DefenseTick(now, game_time, shachi)
  if not Config.guard_enabled and not Config.dodge_enabled then return false end
  -- Projectiles and delayed explosions publish their frame too late for a
  -- parry, so once one is seen the controller keeps dodging for a moment.
  if now < Cache.area_until and now >= NextGuardHold and Cache.area_dodges < 3 then
    if InjectRole('dodge') then
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
    if dt < 0.35 then
      -- an attack is imminent: keep the blade up until it has resolved
      GuardUntil = math.max(GuardUntil, now + 0.45)
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
    if not tracked.evaluated and game_time > tracked.hit + 0.10 then
      tracked.evaluated = true
      if frame.isHit then Counters.def_fail = Counters.def_fail + 1 else Counters.def_ok = Counters.def_ok + 1 end
    end
    if not frame.isHit and not tracked.acted and dt > -Config.stale_after then
      local response = ResponseFor(tracked.kind)
      local lead = LeadFor(response)
      local frame_dt = math.max(FrameDt, 0.001)
      if dt - frame_dt < lead then Act(tracked, now) end
    end
  end
  for handle, tracked in pairs(Tracked) do
    if not present[handle] then
      if not tracked.acted and tracked.hit - game_time > -Config.stale_after then
        local response = ResponseFor(tracked.kind)
        local lead = LeadFor(response)
        if tracked.hit - game_time - FrameDt < lead then Act(tracked, now) end
      else
        if not tracked.evaluated then
          tracked.evaluated = true
          if tracked.isHit then Counters.def_fail = Counters.def_fail + 1 else Counters.def_ok = Counters.def_ok + 1 end
        end
        Tracked[handle] = nil
      end
    end
  end
  if now < Inj.dodge_until then
    if InjectRole('dodge') then Counters.dodges = Counters.dodges + 1 end
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
      local deflect = InjectRole('deflect')
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
  if not Inj.released then
    -- releasing matters: an injected press without a release reads as a held
    -- button, which keeps the character in the block animation indefinitely.
    InjectRoleValue('deflect', 0.0)
    InjectRoleValue('guard', 0.0)
    InjectRoleValue('dodge', 0.0)
    Inj.released = true
  end
  Inj.held_last, Inj.burst = false, 0
  return false
end

-- ------------------------------------------------------------------ combo
local SPEC_ABILITY_FIELDS = { 'Ability', 'AbilityTemplate', 'AbilityClass', 'GameplayAbility',
                              'AbilityCDO', 'AbilitySpec', 'Class' }

local function SpecAbility(spec)
  for _, field in ipairs(SPEC_ABILITY_FIELDS) do
    local value = Try(function() return spec[field] end)
    if Valid(value) then return value, field end
  end
  return nil
end

function RefreshSpecs(now)
  if Cache.specs and now < Cache.next_spec_scan then return Cache.specs end
  Cache.next_spec_scan = now + 5
  if Cache.specs and #Cache.specs > 0 and Valid(Cache.specs[1].ability) then
    return Cache.specs
  end
  local pawn = Cache.pawn
  if not Valid(pawn) then return {} end
  local asc = Try(function() return pawn.AbilitySystemComponent end)
  if not Valid(asc) then return {} end
  local container = Try(function() return asc.ActivatableAbilities end)
  local items = container
  if container ~= nil and type(container) ~= 'table' then
    items = Try(function() return container.Items end) or container
  end
  if items == nil then return {} end
  local n = Try(function() return items:GetArrayNum() end)
  if type(n) ~= 'number' then return {} end
  local found = {}
  for i = 0, math.min(n, 160) - 1 do
    local spec = Try(function() return items:Get(i) end)
    if spec ~= nil then
      local ability, field = SpecAbility(spec)
      if not Cache.spec_field and field then Cache.spec_field = field end
      -- class default objects are named Default__GA_Hun_Tang_A_C
      local name = Name(ability):gsub('^Default__', '')
      local chain = name:match('^GA_Hun_Tang_([AB]+)_C$')
      if chain then found[#found + 1] = { spec = spec, ability = ability, name = name, chain = chain } end
    end
  end
  table.sort(found, function(a, b) return #a.chain < #b.chain end)
  Cache.specs = found
  return found
end

function SpecProbe()
  local pawn = Cache.pawn
  if not Valid(pawn) then return 'no-pawn' end
  local asc = Try(function() return pawn.AbilitySystemComponent end)
  if not Valid(asc) then return 'no-asc' end
  local container = Try(function() return asc.ActivatableAbilities end)
  if container == nil then return 'no-container' end
  local items = container
  if type(container) ~= 'table' then
    items = Try(function() return container.Items end) or container
  end
  local n = Try(function() return items:GetArrayNum() end)
  if type(n) ~= 'number' then return 'no-n' end
  local counts, sample = {}, {}
  for i = 0, math.min(n, 160) - 1 do
    local spec = Try(function() return items:Get(i) end)
    if spec ~= nil then
      local ability, field = SpecAbility(spec)
      if field then counts[field] = (counts[field] or 0) + 1 end
      local name = Name(ability)
      if name ~= '' and #sample < 2 then sample[#sample + 1] = name end
    end
  end
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

local function PlanPrefix(chain)
  local plan = PlanFor()
  for i = 1, #chain do
    if plan[i] ~= chain:sub(i, i) then return false end
  end
  return true
end

local function ComboStep(now, chain)
  if chain ~= Combo.chain then
    if Combo.chain ~= '' then
      local span = now - Combo.since
      if span > 0.10 and span < 2.0 then
        local previous = Cache.move_durations[Combo.chain]
        Cache.move_durations[Combo.chain] = previous and (previous * 0.7 + span * 0.3) or span
      end
    end
    Combo.chain, Combo.since = chain, now
  end
  if not Config.combo_enabled then return nil end
  if not PlanPrefix(chain) then
    if now - Combo.since > Config.chain_timeout then
      Combo.chain, Combo.step, Combo.last_press = '', 0, now
    end
    return nil
  end
  local plan = PlanFor()
  local key = plan[#chain + 1]
  if key == nil then
    if now - Combo.since > Config.chain_timeout * 0.6 then
      Combo.chain, Combo.step, Combo.last_press = '', 0, now
    end
    return nil
  end
  local elapsed = now - Combo.since
  local expected = Cache.move_durations[chain]
  local wait = expected and expected * Config.chain_ratio or (chain == '' and 0.05 or 0.24)
  if elapsed < wait then return nil end
  if now - Combo.last_press < Config.press_gap then return nil end
  Combo.last_press = now
  Combo.step = #chain + 1
  return KEY_ROLE[key], key
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
        Cache.reach = math.min(Config.attack_distance, math.max(2.6, entry.dist + 0.8))
      end
      if entry and entry.dist then Cache.hit_dist = entry.dist end
      if entry and entry.step then
        local base = Plan.delay[entry.step] or Plan.period
        Plan.delay[entry.step] = math.max(0.30, math.min(0.75, base * 0.97))
      end
    end
  end
  if hp then TargetHp = hp end
  while #PendingHits > 0 and now - PendingHits[1].at > 0.9 do
    local entry = table.remove(PendingHits, 1)
    Counters.attack_no_damage = Counters.attack_no_damage + 1
    Cache.reach = math.max(2.6, (Cache.reach or Config.attack_distance) - 0.04)
    if entry and entry.dist then Cache.whiff_dist = entry.dist end
    if entry and entry.step then
      local base = Plan.delay[entry.step] or Plan.period
      Plan.delay[entry.step] = math.min(0.75, base * 1.05)
    end
  end
end

local function NoteAttack(now, distance, step)
  PendingHits[#PendingHits + 1] = { at = now, dist = distance, step = step }
end

local function HeavyDesired()
  if Config.combo_preset == 'pressure' then return AttackCount % 2 == 1 end
  if Config.combo_preset == 'swift' then return AttackCount % 4 == 3 end
  if Config.combo_preset == 'balanced' then return AttackCount % 3 == 2 end
  return false
end

local function HealTick(now, hp, hp_max)
  local ratio = (hp_max and hp and hp_max > 0) and hp / hp_max or 1
  -- A single injected press is swallowed by the drink animation, so the action
  -- is pulsed for a short window instead of fired once.
  if now < HealPulseUntil then
    InjectRole(HealPulseRole)
    return true
  end
  if HealPending then
    local gain = (hp_max or 0) * 0.005
    if gain < 0.5 then gain = 0.5 end
    if hp and HealPending.hp and hp > HealPending.hp + gain then
      Counters.heal_ok = Counters.heal_ok + 1
      Counters.heals = Counters.heals + 1
      Cache.heal_last = HealPending.stage <= 1 and 'heal' or 'heal_alt'
      HealPending = nil
      NextHeal = now + 4
      Status.action = 'heal_ok'
      return true
    end
    if now >= HealPending.until_time then
      if HealPending.stage == 0 then
        Cache.heal_gourd_fails = (Cache.heal_gourd_fails or 0) + 1
        if Cache.heal_gourd_fails >= 3 then Cache.heal_prefer_alt = true end
      end
      -- a drink can be swallowed by an attack animation: try the gourd one
      -- more time before falling back to the quick-item slot
      if HealPending.stage == 0 and InjectRole('heal') then
        HealPending = { stage = 1, hp = hp, until_time = now + 1.6 }
        HealPulseUntil, HealPulseRole = now + 0.6, 'heal'
        Status.action = 'heal_retry'
        return true
      end
      if HasRole('heal_alt') and InjectRole('heal_alt') then
        HealPending = { stage = 2, hp = hp, until_time = now + 2.5 }
        HealPulseUntil, HealPulseRole = now + 0.35, 'heal_alt'
        Status.action = 'heal_alt'
        return true
      end
      Counters.heal_fail = Counters.heal_fail + 1
      HealPending = nil
      NextHeal = now + 3
      Status.action = 'heal_no_effect'
      return true
    end
    Status.action = 'heal_waiting'
    return true
  end
  if not Config.heal_enabled or ratio > Config.heal_threshold or now < NextHeal then return false end
  -- the gourd action is swallowed by this build; after two failures stop
  -- wasting a 2.5 s window on it and drink from the quick-item slot instead
  if Cache.heal_prefer_alt and HasRole('heal_alt') and InjectRole('heal_alt') then
    HealPending = { stage = 2, hp = hp, until_time = now + 2.5 }
    HealPulseUntil, HealPulseRole = now + 0.35, 'heal_alt'
    Status.action = 'heal_alt'
    return true
  end
  if InjectRole('heal') then
    HealPending = { stage = 0, hp = hp, until_time = now + 2.0 }
    HealPulseUntil, HealPulseRole = now + 0.6, 'heal'
    Status.action = 'heal'
    return true
  end
  if HasRole('heal_alt') and InjectRole('heal_alt') then
    HealPending = { stage = 2, hp = hp, until_time = now + 2.5 }
    HealPulseUntil, HealPulseRole = now + 0.35, 'heal_alt'
    Status.action = 'heal_alt'
    return true
  end
  return false
end

local function CombatTick(now, game_time, pawn, hp, hp_max, enemies)
  local ratio = hp_max and hp and hp_max > 0 and hp / hp_max or 1
  local target = enemies[1]
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
  Status.enemy_count = tostring(count)
  Status.target = target and target.name or ''
  Status.target_distance = target and string.format('%.2f', target.distance) or ''
  if target then
    local key = Address(target.actor)
    if key and key ~= Combo.target_key then
      -- a fresh target gets the next plan in the rotation: the strings stay
      -- varied, and every third one still ends on a heavy finisher.  The very
      -- first engagement keeps the plan the user picked.
      if Combo.target_key ~= '' then
        Combo.plan_index = (Combo.plan_index % #PLAN_ORDER) + 1
      end
      Combo.target_key = key
      Combo.chain, Combo.step = '', 0
    end
  end
  NoteTargetHP(target, now)

  if HealTick(now, hp, hp_max) then return end

  local reach = math.min(Config.attack_distance, Cache.reach or Config.attack_distance)
  -- IA_LockTarget is a toggle: press it once per target, otherwise the second
  -- press releases the lock again.
  if Config.lock_enabled and target and target.distance <= 18 then
    local key = Address(target.actor)
    if key and key ~= Cache.lock_key then
      if InjectRole('lock_on') then
        Cache.lock_key = key
        NextLock = now + 1.5
        Status.action = 'lock_on'
        return
      end
    end
  end
  local in_range = target and target.distance <= reach
  if not in_range then
    Combo.chain, Combo.step = '', 0
    Status.action = target and 'out_of_reach' or 'search_target'
    return
  end

  if Config.execution_enabled and target.distance <= 3.2 and target.hp and target.hp_max
      and target.hp_max > 0 and target.hp / target.hp_max <= 0.25 and now >= NextExecute then
    if InjectRole('execution') then
      NextExecute = now + 2
      Counters.executions = Counters.executions + 1
      Status.action = 'execution'
      return
    end
  end

  local player_set = AttrSet(pawn)
  local shadow_resource = player_set and Attribute(player_set, 'SpiritShadow')
  if ShadowPending then
    local same_target = target and SamePtr(target.actor, ShadowPending.target)
    local shield_broken = same_target and type(target.shield) == 'number' and target.shield <= 0
    if not same_target or shield_broken or now >= ShadowPending.until_time then
      local done_key = same_target and Address(target.actor) or nil
      if same_target and shield_broken then
        if done_key then ShadowDone[done_key] = true end
        Counters.shield_breaks = Counters.shield_breaks + 1
        Status.action = 'shield_broken'
      elseif same_target and shadow_resource and shadow_resource < ShadowPending.resource then
        if done_key then ShadowDone[done_key] = true end
        Status.action = 'shadow_used'
      else
        Status.action = 'shadow_charge_ended'
        NextShadow = now + 5
      end
      ShadowPending = nil
      if Status.action == 'shield_broken' or Status.action == 'shadow_used' then return end
    elseif RoleEntry('shadow_arm').action and InjectRole('shadow_arm') then
      Status.action = 'shadow_charge'
      return
    end
  end

  if Config.shadow_enabled and target and target.distance <= math.min(5, Config.attack_distance)
      and shadow_resource and shadow_resource > 0 and not ShadowDone[Address(target.actor)]
      and now >= NextShadow then
    if InjectRole('shadow_arm') then
      ShadowPending = { target = target.actor, until_time = now + 0.9, resource = shadow_resource }
      Counters.shadow_uses = Counters.shadow_uses + 1
      Status.action = 'shadow_charge'
      return
    end
  end

  local shachi = Attribute(player_set, 'Shachi')
  local shachi_max = Attribute(player_set, 'Shachi_Max')
  if Config.switch_enabled and shachi and shachi_max and shachi_max > 0
      and shachi / shachi_max < 0.18 and now >= NextSwitch then
    if InjectRole('change_weapon') then
      NextSwitch = now + 16
      Status.action = 'switch_weapon_low_shachi'
      return
    end
  end

  if Config.skills_enabled and target.distance <= math.min(5, Config.attack_distance) and now >= NextSkill then
    local charges = player_set and Attribute(player_set, 'UltimateChargeCount')
    if charges and charges >= 1 then
      if InjectRole('combat_art') then
        NextSkill = now + 7
        Counters.skills = Counters.skills + 1
        Status.action = 'weapon_skill'
        return
      end
    end
  end

  if not Config.attack_enabled then return end

  if #RefreshSpecs(now) == 0 then
    -- No readable ability array: follow the same plan, and let every step
    -- learn its own delay from whether that swing actually landed.
    Combo.chain, Combo.step = '', 0
    if now < NextAttack then return end
    local plan = PlanFor()
    Plan.step = (Plan.step % #plan) + 1
    local key = plan[Plan.step]
    local role = KEY_ROLE[key]
    local step = Plan.step
    Plan.mode = 'timed'
    if InjectRole(role) then
      Counters.attacks = Counters.attacks + 1
      Counters.combo_press = Counters.combo_press + 1
      NextAttack = now + (Plan.delay[step] or Plan.period)
      NoteAttack(now, target and target.distance, step)
      Combo.last_press = now
      Status.action = 'plan_' .. key
    else
      Counters.attack_fail = Counters.attack_fail + 1
      Status.action = 'attack_action_missing'
      NextAttack = now + 0.5
    end
    return
  end
  Plan.mode = 'ability'

  local chain = ReadChain(now)
  local role, key = ComboStep(now, chain)
  if role and now >= NextAttack then
    if InjectRole(role) then
      Counters.attacks = Counters.attacks + 1
      Counters.combo_press = Counters.combo_press + 1
      NextAttack = now + 0.08
      NoteAttack(now, target and target.distance, nil)
      Status.action = 'combo_' .. key
      return
    end
    Counters.attack_fail = Counters.attack_fail + 1
    NextAttack = now + 0.5
    Status.action = 'attack_action_missing'
    return
  end
  if chain ~= '' then Status.action = 'combo_' .. chain else Status.action = 'combo_ready' end
end

local function Snapshot()
  local now = os.clock()
  ReadControl(now)
  RefreshRoles(now)
  RefreshAttrIndex(now)
  local pawn = RefreshPlayer()
  if not Valid(pawn) then
    Status.running, Status.reason, Status.player, Status.weapon = '0', 'waiting_player', '', ''
    Status.action = 'waiting_player'
    return
  end
  local hp, hp_max = ReadHP(pawn)
  local set = AttrSet(pawn)
  local shachi = set and Attribute(set, 'Shachi')
  local shachi_max = set and Attribute(set, 'Shachi_Max')
  local game_time = Try(function()
    return UEHelpers.GetGameplayStatics():GetTimeSeconds(UEHelpers.GetWorld())
  end)
  if type(game_time) ~= 'number' then game_time = 0 end
  if LastFrameClock then FrameDt = math.min(0.1, math.max(0.001, now - LastFrameClock)) end
  LastFrameClock = now
  local enemies = ScanEnemies(pawn, now)
  Status.player = Try(function() return pawn:GetFullName() end) or Name(pawn)
  Status.hp, Status.hp_max = hp and string.format('%.1f', hp) or '', hp_max and string.format('%.1f', hp_max) or ''
  Status.shachi = shachi and string.format('%.1f/%.1f', shachi, shachi_max or 0) or ''
  Status.weapon = Try(function() return pawn.CurrentWeapon:GetFName():ToString() end)
      or Try(function() return pawn.Weapon:GetFName():ToString() end) or 'unknown'
  Status.reason = Config.run and 'running' or 'paused'
  if Config.run then
    local paused = LastGameTime and math.abs(game_time - LastGameTime) < 0.000001
    if not paused then
      local defended = DefenseTick(now, game_time, shachi)
      if not defended then CombatTick(now, game_time, pawn, hp, hp_max, enemies) end
    else
      Status.action, Inj.block_until, Inj.dodge_until = 'game_paused', -1, -1
    end
  else
    Inj.block_until, Inj.dodge_until, Inj.press_pending, Inj.held_last = -1, -1, false, false
    Status.action = 'idle'
  end
  LastGameTime = game_time
end

local function Tick()
  local ok, err = pcall(Snapshot)
  if not ok then
    ErrorStreak = ErrorStreak + 1
    Status.reason = 'runtime_error'
    Status.error = tostring(err):gsub('[\r\n=]', ' ')
    if ErrorStreak >= 3 then Config.run = false end
  else
    ErrorStreak = 0
    Status.error = ''
  end
  WriteStatus(false)
  WriteDiag(false)
end

WriteStatus(true)
WriteDiag(true)
LoopInGameThreadAfterFrames(1, function()
  Tick()
end)
print(string.format('[PBZSmartCombat] Loaded %s; typed defense + ability combo engine ready.\n', VERSION))
