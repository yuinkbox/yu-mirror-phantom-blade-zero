"""Detect community defense ownership without changing third-party files."""
from datetime import datetime
from pathlib import Path
import re

AUTOGUARD_URL = 'https://www.nexusmods.com/phantombladezero/mods/67'
MANAGER_URL = 'https://github.com/KintaroModding/uemodmanager/releases/latest'


def inspect_autoguard(game_exe, session_start=None):
    ue = Path(game_exe).parent/'ue4ss'
    mod = ue/'Mods'/'PBZAutoGuard'
    info = {'installed': False, 'enabled': False, 'loader_started': False,
            'message': '未安装 PBZAutoGuard，使用助手防御', 'status': 'missing'}
    try:
        info['installed'] = (mod/'Scripts'/'main.lua').is_file()
        enabled = info['installed'] and (mod/'enabled.txt').is_file()
        modlist = ue/'Mods'/'mods.txt'
        if modlist.is_file():
            for line in modlist.read_text('utf-8-sig', errors='replace').splitlines():
                match = re.match(r'^\s*PBZAutoGuard\s*:\s*([01])\s*(?:;.*)?$', line, re.I)
                if info['installed'] and match and match[1] == '1':
                    enabled = True
        info['enabled'] = enabled
        if info['installed'] and not enabled:
            info.update(status='disabled', message='PBZAutoGuard 未启用，使用助手防御')
        elif enabled:
            info.update(status='pending', message='PBZAutoGuard 已启用，等待本次游戏的加载记录')
        if not session_start:
            return info
        log = ue/'UE4SS.log'
        if not log.is_file():
            return info
        with log.open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size-1_000_000))
            lines = f.read().decode('utf-8', errors='replace').splitlines()
        for line in lines:
            if 'PBZAutoGuard' not in line:
                continue
            match = re.match(r'\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\]', line)
            if not match:
                continue
            stamp = datetime.fromisoformat(match[1]).timestamp()
            if stamp < session_start-1:
                continue
            lower = line.lower()
            if any(x in lower for x in ('error', 'failed', 'exception', 'stack traceback')):
                info.update(enabled=True, status='error', message='PBZAutoGuard 加载有报错，请在模组管理器中检查')
                return info
            if 'starting mod' in lower or ('[lua]' in lower and 'loaded' in lower):
                info['loader_started'] = True
        if info['loader_started']:
            # Removing enabled.txt does not unload an already running Lua mod.
            info.update(enabled=True, status='started', message='PBZAutoGuard 已启动；防御由插件处理，游戏中 F5 设置')
        return info
    except (OSError, ValueError):
        if info['installed']:
            info['enabled'] = True
        info.update(status='unreadable', message='社区插件状态暂不可读')
        return info
