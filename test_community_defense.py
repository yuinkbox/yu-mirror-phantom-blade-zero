from pathlib import Path

import lupa


MODULE = (Path(__file__).parent / "mod-dev-v0.10" / "mod" / "PBZSmartCombat"
          / "Scripts" / "community_defense.lua")


STUBS = r"""
local function fname(name)
  return { ToString=function() return name end }
end
local function object(name, className, owner)
  local o = { name=name, className=className or name, owner=owner, valid=true, _id=math.random(1, 100000000) }
  o.IsValid=function(self) return self.valid end
  o.GetAddress=function(self) return self._id end
  o.GetFName=function(self) return fname(self.name) end
  o.GetClass=function(self) return object(self.className, 'Class') end
  o.GetOuter=function(self) return self.owner end
  o.GetOwner=function(self) return self.owner end
  o.GetWorld=function(self) return World end
  o.location_calls=0; o.velocity_calls=0; o.location={X=0,Y=0,Z=0}
  o.K2_GetActorLocation=function(self) self.location_calls=self.location_calls+1; return self.location end
  o.GetVelocity=function(self) self.velocity_calls=self.velocity_calls+1; return {X=500,Y=0,Z=0} end
  return o
end
World=object('World','World')
newObject=object
Pawn=object('Hun_1','BP_Renewal_Hun_C')
Pawn.Health=nil
Passive=object('Passive_1','BP_PlayerDeflectPassive_C',Pawn)
Passive['Stance Key Down']=false
Passive['Stance Key Down'] = function(self) return true end
Passive['Give Perfect Window']=function(self) return true end
Passive['Activate Stance Ability']=function(self) return true end
Passive['On Stance Key Release']=function(self) self.releases=(self.releases or 0)+1 end
Passive['Break Forbid Stance']=function(self, out) return false end
Passive['Global Config']={
  ['Get Deflect Frame Rate']=function(self) return 30 end,
  ['Get Deflect Perfect Window Frames']=function(self) return 8 end,
  ['Get Deflect Perfect Window Frames On Break']=function(self) return 4 end,
}
Attr=object('Attributes','BasicAttributeSet',Pawn)
Attr.Health={CurrentValue=1000}
Attr.Health_Max={CurrentValue=1000}
DC=object('Deflect','DeflectComponent',Pawn)
DC.ReceiveFrames={ rows={} }
DC.ReceiveFrames.ForEach=function(self, callback)
  for _, row in ipairs(self.rows) do callback({get=function() return row.handle end},
    {get=function() return row.frame end}) end
end
Input=object('AbilityInput','AbilityInputComponent',Pawn)
Input.TryActivateExternalActionAbilityInterruptLowerByTag=function(self, tag, action, priority)
  self.calls=(self.calls or 0)+1
  return true
end
DodgeAction=object('IA_Dodge','InputAction')
FIND_COUNTS={}
NOTIFY_CALLS=0
function NotifyOnNewObject(path, callback) NOTIFY_CALLS=NOTIFY_CALLS+1; return true end
Classes={
  BP_PlayerDeflectPassive_C={Passive}, DeflectComponent={DC},
  AbilityInputComponent={Input}, BasicAttributeSet={Attr},
  PBZ_ProjectileBullet={}, PBZ_ProjectileEmitter={}, PBZ_ParticleBullet={}, Character={},
}
function FindAllOf(name)
  FIND_COUNTS[name]=(FIND_COUNTS[name] or 0)+1
  return Classes[name] or {}
end
function StaticFindObject(path) if path=='/PBZDeflectSystem/Input/IA_Dodge.IA_Dodge' then return DodgeAction end end
FName=function(name) return fname(name) end
function setFrame(handle, hit, isDodge, isHit)
  local frame={HitGameTime=hit,IsDodge=isDodge or false,IsHit=isHit or false}
  DC.ReceiveFrames.rows={{handle=handle,frame=frame}}
end
function clearFrames() DC.ReceiveFrames.rows={} end
"""


def runtime(projectiles=False):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS)
    lua.globals()["_MODULE_SOURCE"] = MODULE.read_text(encoding="utf-8")
    lua.execute("DefenseModule=assert(load(_MODULE_SOURCE,'community_defense.lua'))()\n"
                f"Defense=DefenseModule.new({{projectiles={'true' if projectiles else 'false'}}})")
    return lua


def test_uses_native_stance_path_and_releases_on_stop():
    lua = runtime()
    lua.execute("setFrame(11,1.20,false,false)\nR1=Defense:tick(Pawn,1.0,{})\n"
                "R2=Defense:tick(Pawn,1.15,{})")
    assert lua.eval("R1.ready") is True
    assert lua.eval("R2.parries") == 1
    assert lua.eval("R2.api_ok") >= 3
    assert lua.eval("R2.action") == "native_parry"
    assert lua.eval("R2.checks_no_loss") == 0
    lua.execute("Defense:stop('paused')")
    assert lua.eval("Passive.releases") == 1


def test_isdodge_is_authoritative_and_does_not_raise_stance():
    lua = runtime()
    lua.execute("setFrame(22,1.08,true,false)\nR1=Defense:tick(Pawn,1.0,{})\n"
                "R2=Defense:tick(Pawn,1.05,{})")
    assert lua.eval("Input.calls") == 1
    assert lua.eval("R2.dodges") == 1
    assert lua.eval("Passive.releases or 0") == 0


def test_untimed_frame_only_acts_when_game_sets_ishit():
    lua = runtime()
    lua.execute("setFrame(33,-3.4e38,false,false)\nR1=Defense:tick(Pawn,1.0,{})")
    assert lua.eval("R1.parries") == 0
    lua.execute("setFrame(34,-3.4e38,false,true)\nR2=Defense:tick(Pawn,1.05,{})")
    assert lua.eval("R2.parries") == 1


def test_tracks_hp_effect_separately_and_observes_damage():
    lua = runtime()
    lua.execute("setFrame(44,1.1,false,false)\nDefense:tick(Pawn,1.0,{})\n"
                "Defense:tick(Pawn,1.05,{})\nclearFrames()\n"
                "Defense:tick(Pawn,1.4,{})\nR=Defense:tick(Pawn,1.5,{})")
    assert lua.eval("R.checks_no_loss") == 1
    lua.execute("setFrame(45,1.7,false,false)\nDefense:tick(Pawn,1.6,{})\n"
                "Defense:tick(Pawn,1.65,{})\nAttr.Health.CurrentValue=900\n"
                "clearFrames()\nDefense:tick(Pawn,2.0,{})\nR2=Defense:tick(Pawn,2.1,{})")
    assert lua.eval("R2.checks_damage") == 1
    assert lua.eval("R2.damage_events") == 1


def test_pause_and_missing_components_release_and_report_not_ready():
    lua = runtime()
    lua.execute("setFrame(55,1.2,false,false)\nDefense:tick(Pawn,1.0,{})\nDefense:tick(Pawn,1.15,{})\n"
                "R=Defense:tick(Pawn,1.15,{})")
    assert lua.eval("R.ready") is False
    assert lua.eval("Passive.releases") == 1
    lua.execute("Classes.DeflectComponent={}\nDC.valid=false\nR2=Defense:tick(Pawn,2.3,{})")
    assert lua.eval("R2.ready") is False
    assert lua.eval("R2.action") == "defense_components_missing"


def test_disabled_dodge_falls_back_to_parry_and_reports_controls():
    lua = runtime()
    lua.execute("setFrame(66,1.08,true,false)\nR1=Defense:tick(Pawn,1.0,{dodge_enabled=false})\n"
                "R2=Defense:tick(Pawn,1.05,{dodge_enabled=false})")
    assert lua.eval("R2.parries") == 1
    assert lua.eval("R2.dodges") == 0
    assert lua.eval("R2.reset_spam_penalty") is False


def test_missing_dodge_input_does_not_disable_native_parry():
    lua = runtime()
    lua.execute("Classes.AbilityInputComponent={}\nInput.valid=false\n"
                "setFrame(77,1.20,false,false)\nR1=Defense:tick(Pawn,1.0,{})\n"
                "R2=Defense:tick(Pawn,1.15,{})")
    assert lua.eval("R2.ready") is True
    assert lua.eval("R2.parry_ready") is True
    assert lua.eval("R2.dodge_ready") is False
    assert lua.eval("R2.parries") == 1


def test_projectile_discovery_is_staggered_bounded_and_does_not_hook_every_actor():
    lua = runtime(projectiles=True)
    lua.execute(r"""
      for _, cls in ipairs({'PBZ_ProjectileBullet','PBZ_ProjectileEmitter','PBZ_ParticleBullet'}) do
        Classes[cls]={}
        for i=1,90 do
          local o=newObject(cls..'_'..i,cls)
          o._id=100000+i+(cls=='PBZ_ProjectileBullet' and 0 or cls=='PBZ_ProjectileEmitter' and 1000 or 2000)
          Classes[cls][i]=o
        end
      end
      R1=Defense:tick(Pawn,1.00,{})
      R2=Defense:tick(Pawn,1.36,{})
      R3=Defense:tick(Pawn,1.72,{})
      Classes.PBZ_ProjectileBullet[1].location={X=90000,Y=0,Z=0}
      RFAR=Defense:tick(Pawn,1.76,{})
      for i=1,60 do R=Defense:tick(Pawn,1.76+i/60,{}) end
      local velocityCalls=0
      for _,cls in ipairs({'PBZ_ProjectileBullet','PBZ_ProjectileEmitter','PBZ_ParticleBullet'}) do
        for _,o in ipairs(Classes[cls]) do velocityCalls=velocityCalls+o.velocity_calls end
      end
      STRESS_VELOCITY_CALLS=velocityCalls
    """)
    assert lua.eval("NOTIFY_CALLS") == 0
    assert lua.eval("R3.projectile_scans") == 3
    assert lua.eval("RFAR.projectile_tracked") == 95
    assert lua.eval("R.projectile_tracked") == 96
    assert lua.eval("Classes.PBZ_ProjectileBullet[1].location_calls") < 10
    assert lua.eval("FIND_COUNTS.PBZ_ProjectileBullet") == 2
    assert lua.eval("FIND_COUNTS.PBZ_ProjectileEmitter") == 2
    assert lua.eval("FIND_COUNTS.PBZ_ParticleBullet") == 1
    assert lua.eval("R3.projectile_bullet_count") == 32
    assert lua.eval("R3.projectile_emitter_count") == 32
    assert lua.eval("R3.particle_bullet_count") == 32
    assert lua.eval("STRESS_VELOCITY_CALLS") < 3600
    assert lua.eval("R.tick_ms_max") >= lua.eval("R.tick_ms_last")

