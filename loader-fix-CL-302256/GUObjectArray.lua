-- Candidate UE4SS signature for Phantom Blade Zero CL-302256_JKS-17280.
-- Pattern and address calculation adapted from the MIT-licensed UE4SS
-- custom game configuration included in PBZ-AutoParryDodge v1.1.0.
-- Static scan: one match at RVA 0x166B657, result RVA 0xDE28C50.
-- This candidate must be verified against the running game after restart.
function Register()
  return "48 8D 0D ? ? ? ? E8 ? ? ? ? E8 ? ? ? ? E8 ? ? ? ? C6 05 ? ? ? ? 01"
end

function OnMatchFound(MatchAddress)
  return MatchAddress + 0x7 + DerefToInt32(MatchAddress + 0x3) - 0x10
end
