"""Find Phantom Blade Zero on this machine without asking the user first.

The shipped launcher lives in ``<library>\\steamapps\\common\\PBZ\\platform\\steam\\
PBZ.exe`` and loads UE4SS from that same directory, but machines differ: Steam
libraries move, the game is sometimes copied to another drive, and a new PC has
no saved path at all.  Sources are tried from most to least trustworthy:

1. ``PBZ_GAME_PATH`` environment override (portable/USB use),
2. the path saved by the desktop helper,
3. the running game window,
4. Steam's registry entries plus every library in ``libraryfolders.vdf``,
5. Steam ``appmanifest`` entries whose installdir is ``PBZ``,
6. a bounded scan of the usual install roots on every fixed drive.

Nothing here needs the game to be installed in a specific place, and every
candidate is validated before it is offered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import string
import sys

GAME_EXE = 'PBZ.exe'
ENV_OVERRIDE = 'PBZ_GAME_PATH'
# Relative layouts seen in the wild. The first one is what Steam ships.
LAYOUTS = (
    ('platform', 'steam', GAME_EXE),
    (GAME_EXE,),
)
# Install roots that do not need a recursive search.
COMMON_ROOTS = (
    '',
    'Steam',
    'SteamLibrary',
    'steam',
    'Games',
    'Games\\Steam',
    'SteamGames',
    'Program Files (x86)\\Steam',
    'Program Files\\Steam',
    'gailungame\\Phantom Blade Zero',
)
SKIP_DIRS = frozenset({
    'windows', '$recycle.bin', 'system volume information', 'node_modules', '.git',
    'programdata', 'recovery', 'perflogs', 'config.msi', 'msocache', 'appdata',
})
MIN_EXE_BYTES = 256 * 1024
_SCAN_DEPTH = 4
_SCAN_NODES = 20000


@dataclass
class Candidate:
    path: str
    source: str
    score: int
    note: str = ''


@dataclass
class LocateResult:
    path: str = ''
    source: str = ''
    score: int = 0
    candidates: list = field(default_factory=list)
    message: str = ''

    @property
    def found(self):
        return bool(self.path)


def is_game_exe(path):
    """True when the path really looks like the shipped launcher."""
    try:
        target = Path(path)
        if target.name.casefold() != GAME_EXE.casefold() or not target.is_file():
            return False
        return target.stat().st_size >= MIN_EXE_BYTES
    except OSError:
        return False


def score_exe(path):
    """Rank a validated launcher: the canonical Steam layout wins."""
    target = Path(path)
    text = str(target).casefold().replace('/', '\\')
    score = 0
    if text.endswith('\\common\\pbz\\platform\\steam\\pbz.exe'):
        score += 3
    if '\\platform\\steam\\' in text:
        score += 1
    if '\\steamapps\\common\\' in text:
        score += 1
    try:
        if (target.parent / 'ue4ss' / 'UE4SS.dll').is_file():
            score += 2
    except OSError:
        pass
    # The install folder itself is called PBZ in every layout seen so far.
    for parent in target.parents:
        if parent.name.casefold() == 'pbz':
            score += 2
            break
    return score


def _candidate(path, source, note=''):
    return Candidate(str(Path(path)), source, score_exe(path), note)


def _unique(candidates):
    seen, result = set(), []
    for item in candidates:
        key = str(Path(item.path)).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _from_layouts(root, source, note=''):
    found = []
    root = Path(root)
    for layout in LAYOUTS:
        target = root.joinpath(*layout)
        if is_game_exe(target):
            found.append(_candidate(target, source, note))
    return found


# --------------------------------------------------------------------- saved
def _from_override(env):
    value = (env or {}).get(ENV_OVERRIDE, '')
    if value and is_game_exe(value):
        return [_candidate(value, 'env', '环境变量 ' + ENV_OVERRIDE)]
    return []


def _from_saved(saved):
    if saved and is_game_exe(saved):
        return [_candidate(saved, 'saved', '上次使用的路径')]
    return []


# -------------------------------------------------------------------- running
def _from_window():
    if os.name != 'nt':
        return []
    try:
        from windows_io import find_game_by_name
    except (ImportError, OSError):
        return []
    try:
        window = find_game_by_name(GAME_EXE)
    except OSError:
        return []
    if window and is_game_exe(window.path):
        return [_candidate(window.path, 'running', '游戏正在运行')]
    return []


# ---------------------------------------------------------------------- steam
def _unescape_vdf(value):
    return value.replace('\\\\', '\\')


def steam_libraries(steam_root):
    """Every library root listed by Steam, including the Steam folder itself."""
    roots = []
    root = Path(steam_root)
    if root.is_dir():
        roots.append(root)
    for relative in ('steamapps/libraryfolders.vdf', 'config/libraryfolders.vdf'):
        path = root / relative
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for match in re.finditer(r'"path"\s*"([^"]*)"', text):
            candidate = Path(_unescape_vdf(match.group(1)))
            if candidate.is_dir():
                roots.append(candidate)
        # The pre-2020 format listed "1" "D:\\SteamLibrary" instead of objects.
        for match in re.finditer(r'"\d+"\s*"([A-Za-z]:[^"]*)"', text):
            candidate = Path(_unescape_vdf(match.group(1)))
            if candidate.is_dir():
                roots.append(candidate)
    unique, seen = [], set()
    for item in roots:
        key = str(item).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _steam_registry_roots(registry):
    """Steam install folders from the registry; ``registry`` is injectable."""
    if registry is None:
        return []
    keys = (
        ('HKCU', r'Software\Valve\Steam', 'SteamPath'),
        ('HKCU', r'Software\Valve\Steam', 'SteamExe'),
        ('HKLM', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
        ('HKLM', r'SOFTWARE\Valve\Steam', 'InstallPath'),
    )
    roots = []
    for base, key, name in keys:
        value = registry(base, key, name)
        if not value:
            continue
        path = Path(value)
        if path.suffix.casefold() == '.exe':
            path = path.parent
        if path.is_dir():
            roots.append(path)
    return roots


def _library_candidates(library, source='steam'):
    """Check a Steam library, including renamed install folders."""
    common = Path(library) / 'steamapps' / 'common'
    found = []
    for name in ('PBZ', 'Phantom Blade Zero', 'PhantomBladeZero'):
        found += _from_layouts(common / name, source, str(common / name))
    for manifest in sorted(common.parent.glob('appmanifest_*.acf')):
        try:
            text = manifest.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        match = re.search(r'"installdir"\s*"([^"]*)"', text)
        if not match:
            continue
        found += _from_layouts(common / match.group(1), source + ':appmanifest', manifest.name)
    return found


def _drive_candidates(drive_roots):
    found = []
    for root in drive_roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for relative in COMMON_ROOTS:
            target = base / relative if relative else base
            if not target.is_dir():
                continue
            found += _from_layouts(target, 'drive', str(target))
            found += _library_candidates(target, 'drive:library')
    return found


def _scan_candidates(drive_roots, depth=_SCAN_DEPTH, nodes=_SCAN_NODES):
    """Bounded breadth-first search for PBZ.exe under the usual roots."""
    queue, visited = [], 0
    for root in drive_roots:
        if Path(root).is_dir():
            queue.append((Path(root), 0))
    found = []
    while queue and visited < nodes:
        directory, level = queue.pop(0)
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            visited += 1
            if visited >= nodes:
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    lowered = entry.name.casefold()
                    if lowered in SKIP_DIRS:
                        continue
                    if lowered == 'steamapps':
                        # A Steam library can sit anywhere; its install folders
                        # follow the known layout, so do not spend depth on it.
                        found += _library_candidates(Path(entry.path).parent, 'scan:library')
                        continue
                    if level >= depth:
                        continue
                    queue.append((Path(entry.path), level + 1))
                elif entry.name.casefold() == GAME_EXE.casefold() and is_game_exe(entry.path):
                    found.append(_candidate(entry.path, 'scan', f'扫描 {directory}'))
            except OSError:
                continue
    return found


def fixed_drives():
    if os.name != 'nt':
        return []
    drives = []
    for letter in string.ascii_uppercase:
        root = f'{letter}:\\'
        if os.path.isdir(root):
            drives.append(root)
    return drives


def _windows_registry(base, key, name):
    """Read one registry value; any failure means 'not available'."""
    if os.name != 'nt':
        return None
    try:
        import winreg
    except ImportError:
        return None
    hive = {'HKCU': winreg.HKEY_CURRENT_USER, 'HKLM': winreg.HKEY_LOCAL_MACHINE}.get(base)
    if hive is None:
        return None
    try:
        with winreg.OpenKey(hive, key) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def locate(saved=None, *, env=None, window=None, registry=None, drives=None,
           scan=True, threshold=2):
    """Return the best validated launcher and how it was found.

    ``window``/``registry``/``drives`` are injectable so the search can be tested
    without touching the real machine.
    """
    candidates = []
    candidates += _from_override(os.environ if env is None else env)
    candidates += _from_saved(saved)
    candidates += (_from_window() if window is None else window())
    steam_roots = []
    if registry is None:
        registry = _windows_registry
    for root in _steam_registry_roots(registry):
        steam_roots.append(root)
        candidates += _library_candidates(root, 'steam:registry')
    for root in steam_roots:
        for library in steam_libraries(root):
            candidates += _library_candidates(library, 'steam:library')
    drive_roots = fixed_drives() if drives is None else list(drives)
    candidates += _drive_candidates(drive_roots)
    # The deep scan is the only slow step; it only runs when nothing else matched.
    if scan and not [item for item in candidates if is_game_exe(item.path)]:
        candidates += _scan_candidates(drive_roots)
    candidates = _unique([item for item in candidates if is_game_exe(item.path)])
    if not candidates:
        return LocateResult(message='没有自动找到 PBZ.exe，请手动选择游戏目录')
    # A saved or running path is user intent; Steam records are install metadata.
    # A loose PBZ.exe found by scanning a drive needs a recognisable layout first.
    weight = {'env': 100, 'saved': 90, 'running': 80}
    candidates.sort(key=lambda item: (-weight.get(item.source, 0), -item.score,
                                      len(item.path)))
    best = candidates[0]
    trusted = best.source in weight or best.source.startswith('steam')
    if not trusted and best.score < threshold:
        return LocateResult(candidates=candidates,
                            message='找到疑似路径但不像正式安装，请手动确认：' + best.path)
    folder = str(Path(best.path).parent)
    return LocateResult(path=best.path, source=best.source, score=best.score,
                        candidates=candidates,
                        message=f'自动检测到游戏（{source_label(best.source)}） · {folder}')


def source_label(source):
    if source.endswith(':appmanifest'):
        return 'Steam 安装记录'
    return {
        'env': '环境变量',
        'saved': '上次保存的路径',
        'running': '正在运行的游戏',
        'steam:registry': 'Steam 注册表',
        'steam:library': 'Steam 库',
        'steam': 'Steam 库',
        'drive': '磁盘常见安装目录',
        'drive:library': '磁盘上的 Steam 库',
        'scan': '磁盘扫描',
        'scan:library': '磁盘扫描到的 Steam 库',
    }.get(source, source)
