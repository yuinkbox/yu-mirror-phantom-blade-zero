-- A short, session-scoped lease for the physical combo key. Missing, stale or
-- old desktop packets never opt into autonomous combat. No game inputs here.
local Module = {}
function Module.new(read_ini, path)
  local self = { session = '', seq = -1, renewed = -10, next_read = 0,
                 held = false, state = 'input_missing' }
  function self:poll(now, session)
    if session ~= self.session then
      self.session, self.seq, self.renewed = session or '', -1, -10
      self.next_read, self.held, self.state = 0, false, 'input_missing'
    end
    if now >= self.next_read then
      self.next_read = now + 0.02
      local packet = read_ini(path)
      local seq = packet and tonumber(packet.seq)
      local stamp = packet and tonumber(packet.stamp)
      local age = stamp and os.time() - stamp
      if self.session == '' or not packet or packet.schema ~= '1'
          or packet.session ~= self.session or not seq or seq < 1 or seq % 1 ~= 0
          or not age or age < 0 or age > 1
          or (packet.hold ~= '0' and packet.hold ~= '1')
          or (packet.focused ~= '0' and packet.focused ~= '1') then
        self.held, self.state = false, 'input_missing'
      elseif seq > self.seq then
        self.seq, self.renewed = seq, now
        self.held = packet.hold == '1' and packet.focused == '1'
        self.state = packet.focused ~= '1' and 'background'
            or self.held and 'held' or 'waiting_key'
      elseif seq < self.seq then
        self.held, self.state = false, 'input_expired'
      end
    end
    if now - self.renewed > 0.25 and self.state ~= 'input_missing' then
      self.held, self.state = false, 'input_expired'
    end
    return self.held, self.state
  end
  return self
end
return Module
