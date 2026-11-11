-- Adapted from PBZ-AutoWeaponSwitch v1.0.1 (MIT); see third-party provenance.
local M = {}

function M.new()
  local self = { pending = nil, next_attempt_at = 0,
                 low_shachi_latched = false, break_latched = false,
                 attempts = 0, confirmations = 0, api_errors = 0 }

  function self:reset()
    self.pending, self.next_attempt_at = nil, 0
    self.low_shachi_latched, self.break_latched = false, false
  end

  function self:tick(now, state, activate)
    if not state.enabled then return { state = 'disabled' } end
    if type(state.shachi) == 'number' and type(state.shachi_max) == 'number'
        and state.shachi_max > 0 and state.shachi / state.shachi_max >= 0.55 then
      self.low_shachi_latched = false
    end
    if not state.broken then self.break_latched = false end
    if not state.weapons_component then
      return { state = 'unavailable_weapons_component' }
    end
    if not state.input_api or not state.change_action then
      return { state = 'unavailable_api' }
    end
    if type(state.weapon_count) ~= 'number' then
      return { state = 'unavailable_weapon_count' }
    end
    if state.weapon_count < 2 then return { state = 'unavailable_second_weapon' } end
    if type(state.cooldown) ~= 'number' then
      return { state = 'unavailable_cooldown_api' }
    end

    local attempt = self.pending
    if attempt then
      local index_changed = type(state.weapon_index) == 'number'
          and type(attempt.weapon_index) == 'number'
          and state.weapon_index ~= attempt.weapon_index
      if index_changed or state.cooldown > 0 then
        self.pending = nil
        self.confirmations = self.confirmations + 1
        self.next_attempt_at = now + 1.0
        return { state = 'confirmed_' .. attempt.reason, yield_action = true }
      end
      if now - attempt.at >= 0.5 then
        self.pending = nil
        return { state = 'unconfirmed_' .. attempt.reason }
      end
      return { state = 'awaiting_confirmation', yield_action = true }
    end

    if not state.in_combat then return { state = 'idle' } end
    if state.transition then return { state = 'weapon_transition' } end
    if state.cooldown > 0 then return { state = 'cooldown' } end
    if now < self.next_attempt_at then return { state = 'min_interval' } end

    local reason
    if state.broken then
      if self.break_latched then return { state = 'latched_break' } end
      reason = 'break'
    elseif type(state.shachi) == 'number' and type(state.shachi_max) == 'number'
        and state.shachi_max > 0 and state.shachi / state.shachi_max < 0.20 then
      if self.low_shachi_latched then return { state = 'latched_low_shachi' } end
      reason = 'low_shachi'
    elseif type(state.shachi) ~= 'number' or type(state.shachi_max) ~= 'number' then
      return { state = 'unavailable_shachi' }
    else
      return { state = 'ready' }
    end

    local ok = pcall(activate, reason)
    if not ok then self.api_errors = self.api_errors + 1 end
    self.attempts = self.attempts + 1
    if reason == 'break' then self.break_latched = true
    else self.low_shachi_latched = true end
    self.pending = {
      at = now,
      reason = reason,
      weapon_index = state.weapon_index,
    }
    return { state = 'attempted_' .. reason, yield_action = true }
  end

  return self
end

return M
