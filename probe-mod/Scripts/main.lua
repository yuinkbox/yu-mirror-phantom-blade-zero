-- PBZProbe 0.6: continuous combat event log for Phantom Blade Zero.
-- Records attack-move activation windows and deflect frames as timestamped events.
-- Read-only: never injects input, never writes game state.
local UEHelpers = require('UEHelpers')

local VERSION = '0.6.0'
local ScriptDir = debug.getinfo(1, 'S').source:match('^@(.*[\\/])') or './'
local ModDir = ScriptDir .. '../'
local LogPath = ModDir .. 'combat-log.txt'
local BeatPath = ModDir .. 'probe-beat.txt'
local ErrPath = ModDir .. 'probe-error.txt'

local MAX_LINES = 6000
local log_lines = 0
local log_handle = nil
local log_started = 0

local function P(fn, ...)
  local ok, a = pcall(fn, ...)
  if ok then return a end
  return nil
end

local function IsValid(o)
  if o == nil or type(o) ~= 'userdata' then return false end
  return P(function() return o:IsValid() end) == true
end

local function NameOf(o)
  if not IsValid(o) then return '<invalid>' end
  return P(function() return o:GetFName():ToString() end) or '<noname>'
end

local function Show(v)
  local t = type(v)
  if v == nil then return 'nil' end
  if t == 'number' then
    if v ~= v then return 'nan' end
    return string.format('%.3f', v)
  end
  if t == 'boolean' then return v and 'true' or 'false' end
  if t == 'string' then return '"' .. v .. '"' end
  if t == 'table' then return 'table' end
  if IsValid(v) then return NameOf(v) end
  return tostring(v)
end

local function OpenLog()
  if log_handle then return log_handle end
  local f = P(function() return io.open(LogPath, 'w') end)
  if f then
    log_handle = f
    log_started = os.clock()
    f:write(string.format('# PBZProbe %s combat log t0=%.2f epoch=%d\n', VERSION, log_started, os.time()))
    f:flush()
  end
  return log_handle
end

local function L(fmt, ...)
  local f = OpenLog()
  if not f or log_lines >= MAX_LINES then return end
  local ok, text = pcall(string.format, fmt, ...)
  if not ok then return end
  f:write(string.format('%8.3f %s\n', os.clock() - log_started, text))
  log_lines = log_lines + 1
  f:flush()
end

local function PlayerPawn()
  local controller = P(function() return UEHelpers.GetPlayerController() end)
  local pawn = IsValid(controller) and P(function() return controller.Pawn end) or nil
  return IsValid(pawn) and pawn or nil
end

local function DeflectOf(pawn)
  for _, comp in ipairs(P(function() return FindAllOf('DeflectComponent') end) or {}) do
    if IsValid(comp) then
      local owner = P(function() return comp:GetOwner() end)
      if IsValid(owner) and IsValid(pawn)
          and P(function() return owner:GetAddress() == pawn:GetAddress() end) then
        return comp
      end
    end
  end
  return nil
end

local function MapEntries(map)
  local rows = {}
  if map == nil then return rows end
  P(function()
    map:ForEach(function(key, value)
      local frame = value and P(function() return value:get() end) or nil
      local handle = key and P(function() return key:get() end) or nil
      rows[#rows + 1] = { handle = handle, frame = frame }
      return false
    end)
  end)
  return rows
end

local FRAME_FIELDS = {
  'HitGameTime', 'HitTime', 'GameTime', 'IsDodge', 'IsHit', 'IsPaused',
  'AttackType', 'Type', 'DeflectType', 'DeflectBulletType', 'BulletType',
  'Source', 'SourceActor', 'SourceObject', 'AttackSource', 'WeakSource', 'Sender',
  'Action', 'Ability', 'Causer', 'Instigator', 'Bullet', 'From',
  'Color', 'IsRed', 'IsBlue', 'IsParry', 'IsUnblockable', 'Heavy', 'Result',
}

local function FrameFields(frame)
  local parts = {}
  if type(frame) == 'table' then
    for key, value in pairs(frame) do parts[#parts + 1] = tostring(key) .. '=' .. Show(value) end
    return table.concat(parts, ' ')
  end
  for _, field in ipairs(FRAME_FIELDS) do
    local got, value = pcall(function() return frame[field] end)
    if got and value ~= nil then parts[#parts + 1] = field .. '=' .. Show(value) end
  end
  return table.concat(parts, ' ')
end

local function Specs(pawn)
  local asc = P(function() return pawn.AbilitySystemComponent end)
  if not IsValid(asc) then return {} end
  local container = P(function() return asc.ActivatableAbilities end)
  local items = container
  if container ~= nil and type(container) ~= 'table' then
    items = P(function() return container.Items end) or container
  end
  local out = {}
  local n = items and P(function() return items:GetArrayNum() end)
  if type(n) == 'number' then
    for i = 0, math.min(n, 128) - 1 do
      local spec = P(function() return items:Get(i) end)
      if spec ~= nil then
        out[#out + 1] = {
          name = NameOf(P(function() return spec.Ability end) or nil),
          active = P(function() return spec.ActiveCount end),
        }
      end
    end
  end
  return out
end

-- ---- state ------------------------------------------------------------
local State = {
  pawn = nil,
  active_moves = {},   -- name -> start clock
  seen_frames = {},    -- handle -> first clock
  enemy_seen = {},     -- class -> count
  last_summary = 0,
  sample_count = 0,
}

local function Tick()
  local pawn = PlayerPawn()
  if not IsValid(pawn) then return end
  if State.pawn == nil then
    State.pawn = pawn
    L('PAWN %s', NameOf(pawn))
    OpenLog()
  end
  local now = os.clock()

  -- attack move windows
  local current = {}
  for _, spec in ipairs(Specs(pawn)) do
    local name = spec.name
    if name:find('Hun_Tang', 1, true) or name:find('PerformSpecialAttack', 1, true) then
      if type(spec.active) == 'number' and spec.active > 0 then
        current[name] = true
        if not State.active_moves[name] then
          State.active_moves[name] = now
          L('MOVE+ %s', name)
        end
      end
    end
  end
  for name, started in pairs(State.active_moves) do
    if not current[name] then
      State.active_moves[name] = nil
      L('MOVE- %s dur=%.0fms', name, (now - started) * 1000)
    end
  end

  -- deflect frames
  local comp = DeflectOf(pawn)
  if IsValid(comp) then
    local recv = MapEntries(P(function() return comp.ReceiveFrames end))
    local present = {}
    for _, row in ipairs(recv) do
      local key = tostring(row.handle)
      present[key] = true
      if not State.seen_frames[key] then
        State.seen_frames[key] = now
        L('RECV %s %s', key, FrameFields(row.frame))
      end
    end
    for key in pairs(State.seen_frames) do
      if not present[key] then
        State.seen_frames[key] = nil
        L('RECV- %s', key)
      end
    end
    local sent = MapEntries(P(function() return comp.SendFrames end))
    for _, row in ipairs(sent) do
      local key = 'S' .. tostring(row.handle)
      if not State.seen_frames[key] then
        State.seen_frames[key] = now
        L('SENT %s %s', key, FrameFields(row.frame))
      end
    end
  end

  -- enemies
  if State.sample_count % 30 == 0 then
    for _, c in ipairs(P(function() return FindAllOf('FactionComponent') end) or {}) do
      if IsValid(c) then
        local owner = P(function() return c:GetOwner() end)
        if IsValid(owner) and P(function() return owner:GetAddress() ~= pawn:GetAddress() end) then
          local class = NameOf(P(function() return owner:GetClass() end) or nil)
          if class:find('Monster', 1, true) or class:find('Enemy', 1, true) then
            if not State.enemy_seen[class] then
              State.enemy_seen[class] = 0
              L('ENEMY %s', class)
            end
            State.enemy_seen[class] = State.enemy_seen[class] + 1
          end
        end
      end
    end
  end

  State.sample_count = State.sample_count + 1

  if State.sample_count % 60 == 0 then
    local f = io.open(BeatPath, 'w')
    if f then
      f:write(string.format('samples=%d lines=%d moves=%d frames=%d clock=%.1f\n',
        State.sample_count, log_lines, 0, 0, os.clock()))
      f:close()
    end
  end
end

local early = io.open(ModDir .. 'probe-status.txt', 'w')
if early then
  early:write('loaded ' .. VERSION .. ' clock=' .. tostring(os.clock()) .. '\n')
  early:close()
end

if RegisterKeyBind and Key and Key.F11 then
  pcall(function()
    RegisterKeyBind(Key.F11, { ModifierKey.CONTROL }, function()
      ExecuteInGameThread(function()
        local pawn = PlayerPawn()
        if not IsValid(pawn) then return end
        L('---- SNAPSHOT ----')
        for _, spec in ipairs(Specs(pawn)) do
          local active = spec.active
          if type(active) == 'number' and active > 0 then
            L('ACTIVE %s %s', spec.name, Show(active))
          end
        end
        local comp = DeflectOf(pawn)
        if IsValid(comp) then
          for _, row in ipairs(MapEntries(P(function() return comp.ReceiveFrames end))) do
            L('SNAP-RECV %s %s', tostring(row.handle), FrameFields(row.frame))
          end
        end
        L('---- END SNAPSHOT ----')
      end)
    end)
  end)
end

LoopInGameThreadAfterFrames(1, function()
  local ok, err = pcall(Tick)
  if not ok then
    local f = io.open(ErrPath, 'a')
    if f then f:write(tostring(os.time()) .. ' tick ' .. tostring(err) .. '\n') f:close() end
  end
end)
print('[PBZProbe] loaded ' .. VERSION .. ' (event log)\n')
