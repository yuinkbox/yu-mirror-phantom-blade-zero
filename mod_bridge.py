"""Versioned file bridge between the desktop helper and PBZSmartCombat."""
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

SCHEMA = "2"
MOD_NAME = "PBZSmartCombat"
STATUS_MAX_AGE = 4
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


def encode_ini(values):
    rows = []
    for key, value in values.items():
        key = str(key).lower()
        if not _KEY.fullmatch(key):
            raise ValueError(f"invalid protocol key: {key!r}")
        text = str(value)
        if any(char in text for char in "\r\n="):
            raise ValueError(f"invalid protocol value for {key}")
        rows.append(f"{key}={text}")
    return "\n".join(rows) + "\n"


def parse_ini(text):
    values = {}
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if _KEY.fullmatch(key):
            values[key] = value.strip()
    return values


def _enabled_from_mods_txt(path):
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            match = re.match(r"^\s*PBZSmartCombat\s*:\s*([01])\s*(?:;.*)?$", line, re.I)
            if match:
                return match.group(1) == "1"
    except OSError:
        pass
    return False


def _legacy_conflicts(mods_dir):
    conflicts = []
    mods_txt = mods_dir / "mods.txt"
    for name in ("PBZAssist", "PBZAutoCombat", "PBZAutoGuard"):
        if (mods_dir / name / "enabled.txt").is_file() or _enabled_from_named_mod(mods_txt, name):
            conflicts.append(name)
    return conflicts


def _enabled_from_named_mod(path, name):
    try:
        pattern = re.compile(rf"^\s*{re.escape(name)}\s*:\s*([01])\s*(?:;.*)?$", re.I)
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            match = pattern.match(line)
            if match:
                return match.group(1) == "1"
    except OSError:
        pass
    return False


class ModBridge:
    def __init__(self, game_exe):
        game_dir = Path(game_exe).resolve().parent
        self.mod_dir = game_dir / "ue4ss" / "Mods" / MOD_NAME
        self.mods_dir = self.mod_dir.parent
        self.script = self.mod_dir / "Scripts" / "main.lua"
        self.control_path = self.mod_dir / "control.ini"
        self.status_path = self.mod_dir / "status.ini"
        self.assist_session = uuid.uuid4().hex
        self.assist_seq = 0

    def inspect(self, now=None):
        now = int(time.time() if now is None else now)
        if not self.script.is_file():
            return {"status": "missing", "installed": False, "enabled": False,
                    "loaded": False, "running": False, "message": "未安装 PBZSmartCombat"}
        enabled = (self.mod_dir / "enabled.txt").is_file() or _enabled_from_mods_txt(
            self.mods_dir / "mods.txt")
        conflicts = _legacy_conflicts(self.mods_dir)
        status = self._read_status()
        heartbeat = _integer(status.get("heartbeat"))
        fresh = heartbeat is not None and 0 <= now - heartbeat <= STATUS_MAX_AGE
        loaded = status.get("schema") == SCHEMA and status.get("loaded") == "1" and fresh
        running = loaded and status.get("running") == "1"
        if conflicts:
            code, message = "conflict", "旧战斗 Mod 已启用：" + "、".join(conflicts)
        elif not enabled:
            code, message = "disabled", "PBZSmartCombat 未启用"
        elif loaded:
            code, message = ("running", "Mod 已连接 · 辅助运行中" if running else "Mod 已连接 · 已暂停")
        elif status or self._status_problem:
            code, message = "stale", "Mod 心跳过期或协议版本不兼容"
        else:
            code, message = "pending", "等待游戏加载 PBZSmartCombat"
        result = {"status": code, "installed": True, "enabled": enabled,
                  "loaded": loaded, "running": running, "message": message,
                  "conflicts": conflicts}
        result.update({key: status.get(key, "") for key in (
            "mod_version", "player", "hp", "hp_max", "enemy_count", "target",
            "target_distance", "weapon", "q_action", "charge_action", "action",
            "reason", "error", "attacks", "blocks", "dodges", "heals",
            "skills", "executions", "inj_ok", "inj_fail", "enemy_scan_ms",
            "runtime_tick_ms", "runtime_tick_ms_max",
            "attack_ok", "attack_no_damage", "attack_unverified", "def_ok", "def_fail", "def_unknown",
            "def_perfect", "def_great", "def_normal", "def_other", "parry",
            "dodge_red", "guard_hold", "combo_state", "combo_step", "heal_last", "heal_ok", "heal_fail",
            "combo_mode", "combo_plan", "combo_signal", "combo_confirm", "combo_reject",
            "combo_wait_ms", "combo_tempo", "chain_tag", "phase_tag", "heavy_ok",
            "combo_last_reset", "combo_stalls", "combo_stall_ms", "openings",
            "defense_engine", "defense_ready", "defense_parry_ready", "defense_dodge_ready",
            "defense_error", "defense_checks_no_loss", "defense_checks_damage",
            "defense_checks_unknown", "defense_api_fail", "defense_projectiles",
            "defense_projectile_tracked", "defense_tick_ms", "defense_tick_ms_max",
            "defense_frames_late", "heal_state", "skill_state", "skill_charges",
            "heal_action", "heal_state_detail", "heal_candidates", "heal_blocked_ms", "heal_hold_ms",
            "item_actions", "parry_lead_ms", "deflect_gap_ms", "def_rate",
            "deflect_deferred",
            "actions_used", "support_state", "support_uses", "support_confirmed",
            "attack_interrupted", "medicine_interrupted", "combo_gate", "assist_hold",
            "hold_to_combo")})
        result["heartbeat"] = heartbeat
        result["heartbeat_age"] = now - heartbeat if heartbeat is not None else None
        return result

    def _read_status(self):
        candidates = []
        self._status_problem = False
        for path in (self.mod_dir / "status-a.ini", self.mod_dir / "status-b.ini", self.status_path):
            try:
                before = path.stat()
                data = parse_ini(path.read_text(encoding="utf-8-sig"))
                after = path.stat()
            except (OSError, UnicodeError):
                continue
            heartbeat = _integer(data.get("heartbeat"))
            # A slot may be truncated while the game is rewriting it. A newer
            # heartbeat alone must not displace a complete, compatible slot.
            complete = (data.get("schema") == SCHEMA and heartbeat is not None
                        and data.get("loaded") in {"0", "1"}
                        and data.get("running") in {"0", "1"})
            stable = (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
            if complete and stable:
                candidates.append((heartbeat, after.st_mtime_ns, data))
            else:
                self._status_problem = True
        return max(candidates, key=lambda row: row[:2])[2] if candidates else {}

    def write_assist(self, hold, now=None, *, focused=True):
        """Publish a physical gesture, not an attack command. Lua owns combat."""
        if not self.mod_dir.is_dir():
            return
        now = int(time.time() if now is None else now)
        self.assist_seq += 1
        text = encode_ini({"schema": 1, "session": self.assist_session,
                           "seq": self.assist_seq, "hold": "1" if hold and focused else "0",
                           "focused": "1" if focused else "0", "stamp": now})
        target = self.mod_dir / "assist.ini"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(text, "utf-8")
        os.replace(temporary, target)

    def write_control(self, run, settings=None, now=None):
        if not self.script.is_file():
            raise FileNotFoundError("PBZSmartCombat is not installed")
        if not ((self.mod_dir / "enabled.txt").is_file()
                or _enabled_from_mods_txt(self.mods_dir / "mods.txt")):
            raise RuntimeError("PBZSmartCombat is installed but not enabled in UE4SS")
        conflicts = _legacy_conflicts(self.mods_dir)
        if conflicts and run:
            raise RuntimeError("legacy combat mod conflict: " + ", ".join(conflicts))
        now = int(time.time() if now is None else now)
        settings = settings or {}
        values = {
            "schema": SCHEMA,
            "request_id": uuid.uuid4().hex,
            "run": "1" if run else "0",
            "lease_until": now + 4,
            "combo_session": self.assist_session,
            "combat_mode": "hold",
            "heal_enabled": _flag(settings, "heal_enabled", True),
            "attack_enabled": _flag(settings, "attack_enabled", True),
            "guard_enabled": _flag(settings, "guard_enabled", True),
            "dodge_enabled": _flag(settings, "dodge_enabled", True),
            "execution_enabled": _flag(settings, "execution_enabled", True),
            "skills_enabled": _flag(settings, "skills_enabled", True),
            "shadow_enabled": _flag(settings, "shadow_enabled", True),
            "switch_enabled": _flag(settings, "switch_enabled", True),
            "combo_enabled": _flag(settings, "combo_enabled", True),
            "combo_macro": _flag(settings, "combo_macro", True),
            "heal_threshold": _number(settings, "threshold", 0.4),
            "attack_distance": _number(settings, "attack_distance", 3.5),
            "attack_period": _number(settings, "attack_period", 0.35),
            "combo_speed": _number(settings, "combo_speed", 1.0),
            "shachi_reserve": _number(settings, "shachi_reserve", 0.30),
            "combo_preset": _choice(settings, "combo_preset", "balanced",
                                     {"balanced", "pressure", "swift", "light"}),
            "blue_response": _choice(settings, "blue_response", "deflect",
                                     {"deflect", "dodge", "guard"}),
            "red_response": _choice(settings, "red_response", "dodge",
                                    {"dodge", "deflect", "guard"}),
        }
        self.mod_dir.mkdir(parents=False, exist_ok=True)
        data = encode_ini(values).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix="control-", suffix=".tmp", dir=self.mod_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.control_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return values["request_id"]


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _flag(settings, key, default):
    value = settings.get(key, default) if isinstance(settings, dict) else getattr(settings, key, default)
    return "1" if bool(value) else "0"


def _number(settings, key, default):
    value = settings.get(key, default) if isinstance(settings, dict) else getattr(settings, key, default)
    return format(float(value), ".6g")


def _choice(settings, key, default, choices):
    value = settings.get(key, default) if isinstance(settings, dict) else getattr(settings, key, default)
    if value not in choices:
        raise ValueError(f"invalid {key}: {value!r}")
    return value
