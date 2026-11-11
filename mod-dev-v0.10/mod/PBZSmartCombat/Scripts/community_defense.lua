-- Adapted from Muspelion/PBZ-AutoParryDodge v1.1.0, MIT License.
-- Upstream commit: 99d30203cdbd4b1ad9f5e3eaebf1a398445f1a7b
-- Copyright (c) 2026 Muspelion. See ../third-party/PBZ-AutoParryDodge/LICENSE.
-- Integration changes: caller-owned pawn, clock and lifecycle; no loops, keybinds,
-- files or UI; bounded projectile discovery; explicit API/HP observation counters.
local M = {}
local DEFAULTS = {
  parryLeadFraction = 0.45, parryMinLead = 0.05, parryMaxLead = 0.15,
  dodgeLead = 0.045, releaseAfterHit = 0.10, holdGap = 0.35, maxHold = 2.5,
  ghostWithin = 0.4, bothWithin = 0.10, lookahead = 1.5,
  resetSpamPenalty = false, projectiles = true, projectileRadius = 180,
  unannouncedRange = 6000, wideProjectileRadius = 380,
  projectileScanInterval = 0.35, projectileSampleInterval = 1 / 30,
  projectileTrackDistance = 80000, maxProjectiles = 96,
}
local DODGE_TAG, DASH_TAG = 'Ability.Action.Hun.PerfectDodge', 'Ability.Action.Hun.Dash'
local PROJECTILE_CLASSES = { 'PBZ_ProjectileBullet', 'PBZ_ProjectileEmitter', 'PBZ_ParticleBullet' }
local UNANNOUNCED_HITS = { ZuoShang_MiddleAttack_03_Montage = { 0.685, 0.936 } }
local TIMED_QTE = {
  GuiChai_Horse_Gun_Skill_01_Montage = {
    from = 0.777, to = 1.296, action = '/PBZDeflectSystem/Input/IA_Deflect.IA_Deflect', role = 'parry' },
  GuiChai_Horse_Gun_Skill_03_Montage = {
    from = 0.865, to = 1.392, action = '/PBZDeflectSystem/Input/IA_Dodge.IA_Dodge', role = 'dodge' },
}
local function try(fn, ...)
  local ok, value = pcall(fn, ...)
  if ok then return value end
end
local function valid(o)
  return o ~= nil and try(function() return o:IsValid() end) == true
end
local function address(o)
  if valid(o) then return try(function() return o:GetAddress() end) end
end
local function same(a, b)
  local aa, bb = address(a), address(b)
  return aa ~= nil and aa == bb
end
local function finite(n)
  return type(n) == 'number' and n == n and math.abs(n) < math.huge
end
local function objectName(o)
  return try(function() return o:GetFName():ToString() end) or ''
end
local function findLive(className, owner)
  for _, o in ipairs(FindAllOf(className) or {}) do
    if valid(o) and not objectName(o):find('^Default__') then
      if not owner then return o end
      local outer = try(function() return o:GetOuter() end)
      local actor = try(function() return o:GetOwner() end)
      if same(outer, owner) or same(actor, owner) then return o end
    end
  end
end

-- Upstream trajectory model: direct closest approach, or sampled gravitational arc.
local function closestApproach(rx, ry, rz, vx, vy, vz, az)
  if az > -100 then
    local v2 = vx * vx + vy * vy + vz * vz
    if v2 <= 0 then return math.huge, math.huge end
    local t = (rx * vx + ry * vy + rz * vz) / v2
    local x, y, z = rx - vx * t, ry - vy * t, rz - vz * t
    return t, math.sqrt(x * x + y * y + z * z)
  end
  local bestT, best = 0, math.huge
  for i = 0, 200 do
    local t = i * 0.01
    local x, y, z = rx - vx * t, ry - vy * t, rz - vz * t - 0.5 * az * t * t
    local d = x * x + y * y + z * z
    if d < best then bestT, best = t, d end
  end
  return bestT, math.sqrt(best)
end

function M.new(opts)
  local cfg = {}
  for k, v in pairs(DEFAULTS) do cfg[k] = v end
  for k, v in pairs(opts or {}) do if cfg[k] ~= nil then cfg[k] = v end end
  cfg.projectileScanInterval = math.max(0.25, math.min(5.0, cfg.projectileScanInterval))
  cfg.maxProjectiles = math.max(1, math.min(512, math.floor(cfg.maxProjectiles)))
  cfg.projectileSampleInterval = math.max(1 / 60, math.min(0.2, cfg.projectileSampleInterval))
  cfg.projectileTrackDistance = math.max(10000, math.min(200000, cfg.projectileTrackDistance))
  local S = {
    done = {}, seen = {}, checks = {}, proj = {}, unannounced = {}, ghosts = {}, near = {},
    releaseAt = 0, refreshAt = 0, projectileAt = 0, projectileClassIndex = 1,
    projectileActors = {}, nextThreat = math.huge,
    busyUntil = 0, action = 'idle', running = false,
  }
  local stats = {
    parries = 0, dodges = 0, checks_no_loss = 0, checks_damage = 0, checks_unknown = 0,
    errors = 0, frames = 0, frames_late = 0, projectiles = 0,
    api_ok = 0, api_fail = 0, projectile_scans = 0, qtes = 0, damage_events = 0,
    projectile_queue_dropped = 0, tick_ms_last = 0, tick_ms_max = 0,
    projectile_bullet_count = 0, projectile_emitter_count = 0, particle_bullet_count = 0,
  }
  local engine = {}
  local function api(fn)
    local ok, result = pcall(fn)
    local accepted = ok and result ~= false
    if accepted then stats.api_ok = stats.api_ok + 1 else stats.api_fail = stats.api_fail + 1 end
    if not ok then stats.errors = stats.errors + 1; S.error = tostring(result) end
    return accepted, result
  end
  local function health()
    local value = try(function() return S.attr.Health.CurrentValue end)
    return finite(value) and value or nil
  end
  local function releaseKey()
    if S.releaseAt > 0 and valid(S.deflect) then
      api(function() return S.deflect['On Stance Key Release'](S.deflect) end)
    end
    S.releaseAt, S.heldSince = 0, nil
  end
  function engine:stop(reason)
    S.running = false
    releaseKey()
    S.done, S.seen, S.checks, S.proj = {}, {}, {}, {}
    S.unannounced, S.ghosts, S.near, S.projectileActors = {}, {}, {}, {}
    S.nearAt, S.projectileAt, S.projectileClassIndex, S.busyUntil, S.nextThreat = 0, 0, 1, 0, math.huge
    S.lastHp = nil
    S.action = reason or 'stopped'
  end
  local function snapshot(ready, now)
    local result = { ready = ready, busy = S.releaseAt > 0 or now < S.busyUntil,
      action = S.action, error = S.error or '', reset_spam_penalty = cfg.resetSpamPenalty,
      parry_ready = valid(S.dc) and valid(S.deflect),
      dodge_ready = valid(S.dc) and valid(S.input) and valid(S.dodgeAction),
      projectile_notify = false, projectile_scan_interval = cfg.projectileScanInterval,
      projectile_tracked = #S.projectileActors }
    for k, v in pairs(stats) do result[k] = v end
    return result
  end
  local function refresh(now)
    S.refreshAt = now + 1.0
    S.deflect = findLive('BP_PlayerDeflectPassive_C', S.pawn)
    S.dc = findLive('DeflectComponent', S.pawn)
    S.input = findLive('AbilityInputComponent', S.pawn)
    S.attr = findLive('BasicAttributeSet', S.pawn)
    if not valid(S.attr) then
      local ps = try(function() return S.pawn.PlayerState end)
      if valid(ps) then S.attr = findLive('BasicAttributeSet', ps) end
    end
    S.window, S.windowBreak = nil, nil
    if valid(S.deflect) then
      pcall(function()
        local global = S.deflect['Global Config']
        local rate = global['Get Deflect Frame Rate'](global)
        if finite(rate) and rate > 0 then
          S.window = global['Get Deflect Perfect Window Frames'](global) / rate
          S.windowBreak = global['Get Deflect Perfect Window Frames On Break'](global) / rate
        end
      end)
    end
    if not valid(S.dodgeAction) then
      S.dodgeAction = try(function() return StaticFindObject('/PBZDeflectSystem/Input/IA_Dodge.IA_Dodge') end)
    end
  end
  local function parryLead(inBreak)
    local w = (inBreak and S.windowBreak or S.window) or (8 / 30)
    if not finite(w) or w <= 0 then w = 8 / 30 end
    return math.max(cfg.parryMinLead, math.min(cfg.parryMaxLead, w * cfg.parryLeadFraction))
  end
  local function stanceForbidden()
    if not valid(S.deflect) then return true end
    return try(function() return S.deflect['Break Forbid Stance'](S.deflect, {}) end) == true
  end
  local function expectHit(kind, hit, handle)
    S.checks[#S.checks + 1] = { kind = kind, at = hit + 0.25, hp = health(), handle = handle }
  end
  local function doParry(now, hit, handle)
    if not valid(S.deflect) or stanceForbidden() then return false end
    local d = S.deflect
    if cfg.resetSpamPenalty then api(function() return d['Reset Perfect Window Punishment'](d) end) end
    local down = api(function() d['Stance Key Down'] = true end)
    local window = api(function() return d['Give Perfect Window'](d) end)
    local stance = api(function() return d['Activate Stance Ability'](d, {}) end)
    -- Release even after partial failure; a successful property write must not leave stance stuck.
    if down then
      if S.releaseAt == 0 then S.heldSince = now end
      S.releaseAt = math.max(S.releaseAt, hit + cfg.releaseAfterHit)
    end
    if not (down and window and stance) then S.action = 'parry_api_failed'; return false end
    stats.parries = stats.parries + 1
    expectHit('parry', hit, handle)
    S.action = 'native_parry'
    return true
  end
  local function doDodge(now, hit, handle)
    if not (valid(S.input) and valid(S.dodgeAction)) then return false end
    releaseKey()
    local ok, result = api(function()
      return S.input:TryActivateExternalActionAbilityInterruptLowerByTag(
        { TagName = FName(DODGE_TAG) }, S.dodgeAction, -5)
    end)
    local dash = false
    if result == false then
      ok = api(function()
        return S.input:TryActivateExternalActionAbilityInterruptLowerByTag(
          { TagName = FName(DASH_TAG) }, S.dodgeAction, -5)
      end)
      dash = true
    end
    if not ok then S.action = 'dodge_api_failed'; return false end
    stats.dodges = stats.dodges + 1
    S.busyUntil = math.max(S.busyUntil, now + 0.10)
    expectHit('dodge', hit, handle)
    S.action = dash and 'native_dash' or 'native_perfect_dodge_request'
    return true
  end
  local function maybeRelease(now)
    if S.releaseAt == 0 or now < S.releaseAt then return end
    if S.nextThreat - now <= cfg.holdGap and now - (S.heldSince or now) < cfg.maxHold then
      S.releaseAt = now + 0.05
    else releaseKey() end
  end
  local function refreshProjectiles(now)
    if now < S.projectileAt then return end
    S.projectileAt = now + cfg.projectileScanInterval
    local cls = PROJECTILE_CLASSES[S.projectileClassIndex]
    S.projectileClassIndex = (S.projectileClassIndex % #PROJECTILE_CLASSES) + 1
    local actors, known = {}, {}
    for _, row in ipairs(S.projectileActors) do
      if row.class ~= cls then
        local addr = address(row.actor)
        if addr and not known[addr] and #actors < cfg.maxProjectiles then
          known[addr] = true
          actors[#actors + 1] = row
        end
      end
    end
    stats.projectile_scans = stats.projectile_scans + 1
    local classLimit = math.max(1, math.floor(cfg.maxProjectiles / #PROJECTILE_CLASSES))
    local found = 0
    local me = valid(S.pawn) and try(function() return S.pawn:K2_GetActorLocation() end) or nil
    for _, o in ipairs(FindAllOf(cls) or {}) do
      local addr = address(o)
      if addr and not known[addr] and not objectName(o):find('^Default__') then
        if found >= classLimit or #actors >= cfg.maxProjectiles then break end
        local p = try(function() return o:K2_GetActorLocation() end)
        local dx = me and p and me.X - p.X or 0
        local dy = me and p and me.Y - p.Y or 0
        local dz = me and p and me.Z - p.Z or 0
        if not me or not p or dx*dx + dy*dy + dz*dz <= cfg.projectileTrackDistance^2 then
          known[addr] = true
          actors[#actors + 1] = { actor = o, class = cls }
          found = found + 1
        end
      end
    end
    S.projectileActors = actors
    stats.projectile_bullet_count, stats.projectile_emitter_count, stats.particle_bullet_count = 0, 0, 0
    for _, row in ipairs(actors) do
      if row.class == 'PBZ_ProjectileBullet' then stats.projectile_bullet_count = stats.projectile_bullet_count + 1
      elseif row.class == 'PBZ_ProjectileEmitter' then stats.projectile_emitter_count = stats.projectile_emitter_count + 1
      elseif row.class == 'PBZ_ParticleBullet' then stats.particle_bullet_count = stats.particle_bullet_count + 1 end
    end
  end
  local function trackProjectiles(now, dt, wantParry, wantDodge)
    local me = S.pawn:K2_GetActorLocation()
    local alive = {}
    local relevant = {}
    for _, item in ipairs(S.projectileActors) do
      local o, cls = item.actor, item.class
      local addr = address(o)
      if addr then
        alive[addr] = true
        local st = S.proj[addr]
        local imminent = st and not st.done and st.hit and st.hit - now <= parryLead(false) + dt
        if st and now < (st.nextSample or 0) and not imminent then
          relevant[#relevant + 1] = item
          if st.hit and st.hit > now then S.nextThreat = math.min(S.nextThreat, st.hit) end
        else
        if imminent and not st.done then
          local acted
          if cls == 'PBZ_ProjectileEmitter' and wantDodge then acted = doDodge(now, st.hit, -1)
          elseif wantParry and not stanceForbidden() then acted = doParry(now, st.hit, -1)
          elseif wantDodge then acted = doDodge(now, st.hit, -1) end
          st.done = acted == true
        end
        local p = o:K2_GetActorLocation()
        local dx, dy, dz = me.X-p.X, me.Y-p.Y, me.Z-p.Z
        if dx*dx + dy*dy + dz*dz <= cfg.projectileTrackDistance^2 then
        relevant[#relevant + 1] = item
        local step = st and now - st.t or 0
        local jump = st and math.max(800, 45000 * step) or 0
        local d2 = st and (p.X-st.x)^2 + (p.Y-st.y)^2 + (p.Z-st.z)^2 or 0
        local fresh = not st or d2 > jump * jump
        if fresh then
          local owner = try(function() return o:GetOwner() end)
          if not valid(owner) then owner = try(function() return o.BulletOwnerAvatar end) end
          local mine = same(owner, S.pawn)
          local name = objectName(o)
          local wide = name:find('Wave') or name:find('Slash') or name:find('Blade')
          st = { mine = mine, done = mine, radius = wide and cfg.wideProjectileRadius or cfg.projectileRadius }
          S.proj[addr] = st
        end
        if not st.done or now < (st.hit or 0) then
          local vx, vy, vz = 0, 0, 0
          local v = try(function() return o:GetVelocity() end)
          if v and finite(v.X) and finite(v.Y) and finite(v.Z) then vx, vy, vz = v.X, v.Y, v.Z end
          if vx*vx + vy*vy + vz*vz <= 100 and not fresh and step > 0 then
            vx, vy, vz = (p.X-st.x)/step, (p.Y-st.y)/step, (p.Z-st.z)/step
          end
          if vx*vx + vy*vy + vz*vz > 100 and (fresh or d2 > 0) then
            local az = st.vz ~= nil and step > 0 and math.max(-4000, math.min(0, (vz-st.vz)/step)) or 0
            st.vz = vz
            local t, miss = closestApproach(me.X-p.X, me.Y-p.Y, me.Z-p.Z, vx, vy, vz, az)
            if t > 0 and t <= cfg.lookahead and miss <= st.radius then
              if not st.seen then stats.projectiles = stats.projectiles + 1; st.seen = true end
              st.hit = now + t
              S.nextThreat = math.min(S.nextThreat, st.hit)
              if not st.done and t-dt <= parryLead(false) then
                local acted
                if cls == 'PBZ_ProjectileEmitter' and wantDodge then acted = doDodge(now, st.hit, -1)
                elseif wantParry and not stanceForbidden() then acted = doParry(now, st.hit, -1)
                elseif wantDodge then acted = doDodge(now, st.hit, -1) end
                st.done = acted == true
              end
            end
          end
        end
        st.x, st.y, st.z, st.t = p.X, p.Y, p.Z, now
        st.nextSample = now + cfg.projectileSampleInterval
        else
          S.proj[addr] = nil
          alive[addr] = nil
        end
        end
      end
    end
    S.projectileActors = relevant
    for addr in pairs(S.proj) do if not alive[addr] then S.proj[addr] = nil end end
  end
  local function pressQte(qte, pos, now)
    if pos < qte.from + 0.1 or pos > qte.to - 0.1 then return end
    if not valid(S.enhanced) then S.enhanced = findLive('EnhancedInputLocalPlayerSubsystem') end
    if not valid(S.enhanced) then return end
    local action = StaticFindObject(qte.action)
    if not valid(action) then return end
    if api(function() S.enhanced:InjectInputVectorForAction(action, { X=1, Y=0, Z=0 }, {}, {}) end) then
      S.busyUntil, S.action = now + 0.05, 'fixed_qte'
      stats.qtes = stats.qtes + 1
    end
  end
  local function trackUnannounced(now, dt, wantParry, wantDodge)
    if now >= (S.nearAt or 0) then
    S.near, S.nearAt = {}, now + 1.0
      local me = S.pawn:K2_GetActorLocation()
      for _, c in ipairs(FindAllOf('Character') or {}) do
        if valid(c) and not same(c, S.pawn) and objectName(c:GetClass()):find('^BP_Monster_') then
          local p = c:K2_GetActorLocation()
          if (p.X-me.X)^2 + (p.Y-me.Y)^2 + (p.Z-me.Z)^2 < cfg.unannouncedRange^2 then S.near[#S.near+1] = c end
        end
      end
    end
    for _, c in ipairs(S.near) do
      if valid(c) then
        local anim = try(function() return c.Mesh:GetAnimInstance() end)
        local montage = anim and try(function() return anim:GetCurrentActiveMontage() end)
        local name = valid(montage) and objectName(montage) or nil
        local hits, qte = name and UNANNOUNCED_HITS[name], name and TIMED_QTE[name]
        local pos = montage and try(function() return anim:Montage_GetPosition(montage) end)
        if finite(pos) and qte and ((qte.role == 'parry' and wantParry) or (qte.role == 'dodge' and wantDodge)) then
          pressQte(qte, pos, now)
        end
        local addr = address(c)
        if finite(pos) and hits then
          local st = S.unannounced[addr]
          if not st or pos < st.pos then st = { next = 1 }; S.unannounced[addr] = st end
          st.pos = pos
          while hits[st.next] and pos > hits[st.next] + 0.05 do st.next = st.next + 1 end
          local hit = hits[st.next]
          if hit and wantDodge and pos + dt + cfg.dodgeLead >= hit then
            st.next = st.next + 1
            doDodge(now, now + math.max(0, hit-pos), -2)
          end
        elseif addr then S.unannounced[addr] = nil end
      end
    end
  end
  local function guarded(fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok then stats.errors = stats.errors + 1; S.error = tostring(err) end
    return ok
  end
  local function tickImpl(pawn, now, options)
    options = options or {}
    local wantParry = options.guard_enabled ~= false
    local wantDodge = options.dodge_enabled ~= false
    -- IsDodge remains authoritative. A dodge preference applies to parryable attacks only.
    local dodgePreferred = options.blue_response == 'dodge' or options.defense_mode == 'dodge'
    if not valid(pawn) or not finite(now) then engine:stop('waiting_player'); S.pawn = nil; return snapshot(false, 0) end
    local world = try(function() return pawn:GetWorld() end)
    local worldAddress = address(world)
    if not same(pawn, S.pawn) or (S.world and worldAddress and S.world ~= worldAddress)
        or (S.lastNow and now < S.lastNow - 0.001) then
      engine:stop('world_changed')
      S.pawn, S.world, S.refreshAt, S.lastNow = pawn, worldAddress, 0, nil
      S.deflect, S.dc, S.input, S.attr = nil, nil, nil, nil
    end
    if S.lastNow ~= nil and now == S.lastNow then engine:stop('game_paused'); return snapshot(false, now) end
    S.running = true
    local dt = math.min(0.1, math.max(0, now - (S.lastNow or now)))
    S.lastNow, S.action = now, 'idle'
    if now >= S.refreshAt then refresh(now) end
    local hpNow = health()
    if hpNow and hpNow <= 0 then engine:stop('player_dead'); return snapshot(false, now) end
    if not wantParry and not wantDodge then engine:stop('defense_disabled'); return snapshot(true, now) end
    local parryReady = wantParry and valid(S.deflect)
    local dodgeReady = wantDodge and valid(S.input) and valid(S.dodgeAction)
    local ready = valid(S.dc) and (parryReady or dodgeReady)
    if not ready then engine:stop('defense_components_missing'); return snapshot(false, now) end
    if S.lastHp and hpNow and hpNow < S.lastHp - 0.5 then stats.damage_events = stats.damage_events + 1 end
    S.lastHp = hpNow
    local parryAllowed = wantParry and not (dodgePreferred and wantDodge)
    local fp, fd, hp, hd = math.huge, math.huge, nil, nil
    local present, nextFuture = {}, math.huge
    S.dc.ReceiveFrames:ForEach(function(key, value)
      local handle, frame = key:get(), value:get()
      local hit = frame.HitGameTime
      if not finite(hit) then return end
      present[handle] = true
      if not S.seen[handle] then
        stats.frames = stats.frames + 1
        if hit < -1e30 or hit - now < 0.02 then stats.frames_late = stats.frames_late + 1 end
      end
      S.seen[handle] = { hit = hit, dodge = frame.IsDodge == true }
      if hit < -1e30 then
        if frame.IsHit and not S.done[handle] then
          local acted
          if frame.IsDodge then
            if wantDodge then acted = doDodge(now, now, handle)
            elseif wantParry then acted = doParry(now, now, handle) end
          elseif parryAllowed and not stanceForbidden() then acted = doParry(now, now, handle)
          elseif wantDodge then acted = doDodge(now, now, handle) end
          S.done[handle] = acted == true
        end
        return
      end
      -- Timed IsHit is NOT an already-took-damage flag; timing is authoritative here.
      if hit < now - 0.2 or hit > now + cfg.lookahead then return end
      if frame.IsDodge then
        if hit < fd and not S.done[handle] then hd, fd = handle, hit end
      else
        if hit < fp and not S.done[handle] then hp, fp = handle, hit end
        if hit > now then nextFuture = math.min(nextFuture, hit) end
      end
    end)
    for handle, info in pairs(S.seen) do
      if not present[handle] then
        local left = info.hit - now
        if not S.done[handle] and info.hit > -1e30 and left > 0 and left < cfg.ghostWithin then
          S.ghosts[#S.ghosts + 1] = { hit = info.hit, handle = handle, dodge = info.dodge }
        end
        S.seen[handle], S.done[handle] = nil, nil
      end
    end
    S.nextThreat = nextFuture
    -- Reserve the imminent hit interval for defense before the native activation.
    if fd < math.huge and wantDodge then S.nextThreat = math.min(S.nextThreat, fd) end
    if S.nextThreat > now and S.nextThreat-now <= math.max(parryLead(false), cfg.dodgeLead) + dt then
      S.busyUntil = math.max(S.busyUntil, S.nextThreat + 0.05)
    end
    for i = #S.ghosts, 1, -1 do
      local g, acted = S.ghosts[i], false
      local left = g.hit - now
      if left < -0.1 then table.remove(S.ghosts, i)
      else
        S.nextThreat = math.min(S.nextThreat, g.hit)
        if g.dodge then
          if left-dt <= cfg.dodgeLead then
            if wantDodge then acted = doDodge(now, g.hit, g.handle)
            elseif wantParry then acted = doParry(now, g.hit, g.handle) end
          end
        elseif left-dt <= parryLead(false) then
          if parryAllowed and not stanceForbidden() then acted = doParry(now, g.hit, g.handle)
          elseif wantDodge then acted = doDodge(now, g.hit, g.handle) end
        end
        if acted then table.remove(S.ghosts, i) end
      end
    end
    for i = #S.checks, 1, -1 do
      local c = S.checks[i]
      if now >= c.at then
        local current = health()
        if current and c.hp then
          if c.hp - current > 0.5 then stats.checks_damage = stats.checks_damage + 1
          else stats.checks_no_loss = stats.checks_no_loss + 1 end
        else stats.checks_unknown = stats.checks_unknown + 1 end
        table.remove(S.checks, i)
      end
    end
    local dodged = false
    if hd and fd-now >= -0.1 and fd-now-dt <= cfg.dodgeLead then
      if wantDodge then dodged = doDodge(now, fd, hd)
      elseif wantParry then dodged = doParry(now, fd, hd) end
      if dodged then
        S.done[hd] = true
        if hp and math.abs(fp-fd) <= cfg.bothWithin then S.done[hp] = true end
      end
    end
    if hp and not S.done[hp] and not dodged then
      local bothSoon = hd and math.abs(fd-fp) <= cfg.bothWithin
      if fp-now >= -0.1 and not (bothSoon and wantDodge) and fp-now-dt <= parryLead(false) then
        local acted
        if parryAllowed and not stanceForbidden() then acted = doParry(now, fp, hp)
        elseif wantDodge then acted = doDodge(now, fp, hp) end
        S.done[hp] = acted == true
      end
    end
    -- Handle announced attacks before tracking or any global discovery calls.
    if cfg.projectiles then guarded(trackProjectiles, now, dt, parryAllowed, wantDodge) end
    guarded(trackUnannounced, now, dt, parryAllowed, wantDodge)
    maybeRelease(now)
    -- Reconciliation covers already spawned / pooled actors and loaders without
    -- constructor notifications. Defer slow GUObjectArray scans near a known hit.
    if cfg.projectiles and S.nextThreat-now > 0.25 and now >= S.busyUntil then
      guarded(refreshProjectiles, now)
    end
    return snapshot(true, now)
  end
  function engine:tick(pawn, game_time, options)
    local started = os.clock()
    local ok, result = pcall(tickImpl, pawn, game_time, options)
    stats.tick_ms_last = (os.clock() - started) * 1000
    stats.tick_ms_max = math.max(stats.tick_ms_max, stats.tick_ms_last)
    if ok then
      result.tick_ms_last = stats.tick_ms_last
      result.tick_ms_max = stats.tick_ms_max
      result.projectile_tracked = #S.projectileActors
      return result
    end
    stats.errors = stats.errors + 1
    S.error = tostring(result)
    self:stop('defense_error')
    return snapshot(false, finite(game_time) and game_time or 0)
  end
  return engine
end
return M
