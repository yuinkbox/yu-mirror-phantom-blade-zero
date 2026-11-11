"""Inspect the mod environment and install the bundled UE4SS when it is missing.

The desktop helper used to assume that UE4SS, the Mods folder and this mod were
already present.  On a new machine that is exactly what is unknown, so this
module answers, without asking the user:

* is the game directory writable,
* is UE4SS installed, is it complete, is the proxy DLL in place,
* is ``PBZSmartCombat`` installed/enabled, and which version,
* are legacy or competing combat mods enabled,
* where is the UE4SS package inside this bundle, and can it be installed.

Installing UE4SS is deliberately conservative: it refuses to overwrite an
existing loader, verifies the package checksum, extracts into a staging folder
and only then moves the files into the game directory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
import zipfile

MOD_NAME = 'PBZSmartCombat'
LEGACY_MODS = ('PBZAssist', 'PBZAutoCombat', 'PBZAutoGuard')
COMPETING_HINTS = ('autoparry', 'parrydodge', 'autoperfectdefense', 'perfectdefense',
                   'autoguard', 'autocombat', 'autoattack', 'autododge', 'autodeflect')
LOADER_MARKER = '.PBZ-loader.json'
PACKAGE_NAME = 'phantombladezero_ue4ss.zip'
_VERSION = re.compile(r"local VERSION = '([^']+)'")
_PACKAGE_VERSION = re.compile(r'ue4ss[_-]?(v[\w.\-]+)', re.I)


def file_sha256(path):
    try:
        stat = Path(path).stat()
    except OSError:
        return ''
    return _cached_sha(str(Path(path)), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=32)
def _cached_sha(path, _mtime, _size):
    try:
        digest = hashlib.sha256()
        with open(path, 'rb') as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ''


def read_mod_version(mod_dir):
    """The VERSION line of an installed ``Scripts/main.lua``."""
    for name in ('Scripts/main.lua', 'main.lua'):
        try:
            head = (Path(mod_dir) / name).read_text(encoding='utf-8', errors='replace')[:4096]
        except OSError:
            continue
        match = _VERSION.search(head)
        if match:
            return match.group(1)
    return ''


def _enabled_in_mods_txt(mods_txt, name):
    try:
        text = Path(mods_txt).read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return False
    return bool(re.search(rf'^\s*{re.escape(name)}\s*:\s*1\s*(?:;.*)?$', text, re.I | re.M))


@dataclass(frozen=True)
class ModEntry:
    name: str
    enabled: bool
    version: str = ''
    managed: bool = False
    kind: str = 'other'


@dataclass(frozen=True)
class UE4SSPackage:
    zip_path: str
    version: str = ''
    sha256: str = ''
    dll_bytes: int = 0
    dll_sha256: str = ''

    @property
    def ready(self):
        return bool(self.zip_path) and Path(self.zip_path).is_file()

    @classmethod
    def discover(cls, bases=None):
        override = os.environ.get('PBZ_UE4SS_PACKAGE', '')
        if override and Path(override).is_file():
            return cls(override, **_package_meta(Path(override)))
        for base in bases or _package_bases():
            folders = [Path(base) / relative for relative in
                       ('dependencies/ue4ss', 'ue4ss', '..')] + [Path(base)]
            for folder in folders:
                candidate = folder / PACKAGE_NAME
                if candidate.is_file():
                    return cls(str(candidate), **_package_meta(candidate))
                if folder.is_dir():
                    for other in sorted(folder.glob('*ue4ss*.zip')):
                        return cls(str(other), **_package_meta(other))
        return None


def _package_bases():
    bases = []
    try:
        root = Path(sys.executable).parent if getattr(sys, 'frozen', False) \
            else Path(__file__).resolve().parent
    except OSError:
        root = Path.cwd()
    for candidate in (root, root / '..', root / '..' / '..', Path.cwd()):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in bases:
            bases.append(resolved)
    return bases


def _package_meta(zip_path):
    """Version and checksums from the bundle, without trusting the folder name."""
    expected = ''
    version = ''
    for digest_file in (zip_path.parent / 'SHA256.txt', zip_path.parent / 'sha256.txt'):
        try:
            text = digest_file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        match = re.search(r'\b([0-9a-fA-F]{64})\b\s+\*?(\S+)', text)
        if match:
            expected = match.group(1).lower()
            version = _PACKAGE_VERSION.search(match.group(2))
            version = version.group(1) if version else ''
            version = re.sub(r'\.zip$', '', version, flags=re.I)
        break
    dll_bytes = dll_sha = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if name.replace('\\', '/').casefold().endswith('ue4ss/ue4ss.dll'):
                    with archive.open(name) as stream:
                        digest = hashlib.sha256()
                        size = 0
                        for block in iter(lambda: stream.read(1 << 20), b''):
                            digest.update(block)
                            size += len(block)
                    dll_bytes, dll_sha = size, digest.hexdigest()
                    break
    except (OSError, zipfile.BadZipFile):
        pass
    return {'version': version, 'sha256': expected,
            'dll_bytes': dll_bytes, 'dll_sha256': dll_sha}


@dataclass
class EnvironmentReport:
    game_path: str = ''
    game_dir: str = ''
    game_exists: bool = False
    game_writable: bool = False
    ue4ss_dir: str = ''
    ue4ss_installed: bool = False
    ue4ss_complete: bool = False
    ue4ss_proxy: bool = False
    ue4ss_settings: bool = False
    ue4ss_version: str = ''
    ue4ss_dll_bytes: int = 0
    mods_dir: str = ''
    mods_txt: bool = False
    mod_installed: bool = False
    mod_enabled: bool = False
    mod_version: str = ''
    mod_matches_app: bool = False
    mods: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)          # blocking (legacy)
    competing: list = field(default_factory=list)          # warnings
    package: UE4SSPackage = None
    issues: list = field(default_factory=list)
    summary: str = ''

    @property
    def ok(self):
        return not self.issues

    @property
    def can_install_loader(self):
        return (self.game_exists and not self.ue4ss_installed
                and self.package is not None and self.package.ready)


def _probe_write(directory):
    try:
        probe = Path(directory) / '.pbz-write-probe'
        probe.write_bytes(b'')
        probe.unlink()
        return True
    except OSError:
        return False


def _loader_marker(ue4ss_dir):
    try:
        return json.loads((Path(ue4ss_dir) / LOADER_MARKER).read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def inspect_environment(game_exe, app_version='', package=None, *, deep=True):
    """Describe everything about the game folder the helper needs to know."""
    game = Path(game_exe) if game_exe else Path('')
    report = EnvironmentReport(game_path=str(game), game_dir=str(game.parent))
    report.game_exists = bool(game_exe) and game.name.casefold() == 'pbz.exe' and game.is_file()
    if not report.game_exists:
        report.issues.append('没有找到 PBZ.exe，请手动选择游戏目录')
        report.summary = '游戏目录未确认'
        return report
    report.game_writable = _probe_write(game.parent)
    if not report.game_writable:
        report.issues.append('游戏目录不可写：请用管理员身份运行，或把助手放在可写目录')
    ue4ss = game.parent / 'ue4ss'
    report.ue4ss_dir = str(ue4ss)
    report.ue4ss_installed = (ue4ss / 'UE4SS.dll').is_file()
    report.ue4ss_complete = report.ue4ss_installed and (ue4ss / 'Mods').is_dir()
    report.ue4ss_proxy = (game.parent / 'dwmapi.dll').is_file()
    report.ue4ss_settings = (ue4ss / 'UE4SS-settings.ini').is_file()
    report.mods_dir = str(ue4ss / 'Mods')
    report.mods_txt = (ue4ss / 'Mods' / 'mods.txt').is_file()
    marker = _loader_marker(ue4ss)
    report.ue4ss_version = str(marker.get('version') or '')
    if report.ue4ss_installed:
        try:
            report.ue4ss_dll_bytes = (ue4ss / 'UE4SS.dll').stat().st_size
        except OSError:
            report.ue4ss_dll_bytes = 0
    pkg = package if package is not None else UE4SSPackage.discover()
    report.package = pkg if pkg and pkg.ready else None
    if report.ue4ss_installed and not report.ue4ss_version and pkg and pkg.dll_sha256:
        if deep and file_sha256(ue4ss / 'UE4SS.dll') == pkg.dll_sha256:
            report.ue4ss_version = pkg.version or '与包内一致'
    if not report.ue4ss_installed:
        if report.ue4ss_proxy:
            report.issues.append('游戏目录有 dwmapi.dll，但找不到 ue4ss/UE4SS.dll：安装不完整')
        else:
            report.issues.append('未安装 UE4SS：没有 UE4SS 的 Mod 无法加载'
                                 + ('（可用包内一键安装）' if report.package else '（包内也没有安装包，需手动安装）'))
    elif not report.ue4ss_complete:
        report.issues.append('UE4SS 安装不完整：缺少 Mods 目录')
    # ------------------------------------------------------------- installed mods
    mods_dir = ue4ss / 'Mods'
    mods = []
    if mods_dir.is_dir():
        for entry in sorted(mods_dir.iterdir()):
            try:
                if not entry.is_dir() or entry.name.startswith('.'):
                    continue
            except OSError:
                continue
            name = entry.name
            if name in ('shared', 'Keybinds'):
                continue
            enabled = ((entry / 'enabled.txt').is_file()
                       or _enabled_in_mods_txt(mods_dir / 'mods.txt', name))
            try:
                managed = json.loads((entry / 'installation.json').read_text('utf-8')
                                     ).get('managed_by') == MOD_NAME
            except (OSError, ValueError):
                managed = False
            kind = 'other'
            lowered = name.casefold()
            if name == MOD_NAME:
                kind = 'smart'
            elif name in LEGACY_MODS:
                kind = 'legacy'
            elif any(hint in lowered for hint in COMPETING_HINTS):
                kind = 'competing'
            mods.append(ModEntry(name=name, enabled=enabled,
                                 version=read_mod_version(entry),
                                 managed=managed, kind=kind))
    report.mods = mods
    for entry in mods:
        if not entry.enabled:
            continue
        if entry.kind == 'legacy':
            report.conflicts.append(entry.name)
        elif entry.kind == 'competing':
            report.competing.append(entry.name)
    for entry in mods:
        if entry.name == MOD_NAME:
            report.mod_installed = True
            report.mod_enabled = entry.enabled
            report.mod_version = entry.version
    if not report.mod_installed:
        report.issues.append('未安装 PBZSmartCombat：点“安装/升级 Mod”')
    elif not report.mod_enabled:
        report.issues.append('PBZSmartCombat 未启用：点“安装/升级 Mod”或检查 mods.txt')
    if report.mod_installed and app_version and report.mod_version and \
            report.mod_version != app_version:
        report.issues.append(f'游戏内 Mod 是 {report.mod_version}，桌面是 {app_version}：'
                             '退出游戏后安装/升级 Mod')
    report.mod_matches_app = bool(app_version) and report.mod_version == app_version
    if report.conflicts:
        report.issues.append('旧战斗 Mod 仍启用：' + '、'.join(report.conflicts))
    if report.competing:
        report.issues.append('检测到其他自动防御/战斗 Mod：' + '、'.join(report.competing)
                             + '（会抢输入，建议只留一个）')
    report.summary = _summary(report)
    return report


def _summary(report):
    loader = 'UE4SS 未安装'
    if report.ue4ss_installed:
        version = report.ue4ss_version or '版本未知'
        loader = f'UE4SS {version}' + ('' if report.ue4ss_complete else '（不完整）')
    mod = 'Mod 未安装'
    if report.mod_installed:
        mod = f'Mod {report.mod_version or "?"}' + ('·已启用' if report.mod_enabled else '·未启用')
    extra = ''
    if report.conflicts:
        extra = ' · 冲突：' + '、'.join(report.conflicts)
    elif report.competing:
        extra = ' · 其他战斗 Mod：' + '、'.join(report.competing)
    return f'{loader} · {mod}{extra}'


# --------------------------------------------------------------------- install
class LoaderInstallError(RuntimeError):
    pass


def install_ue4ss(game_exe, package=None):
    """Install the bundled UE4SS into the game folder; never overwrites one."""
    game = Path(game_exe)
    if game.name.casefold() != 'pbz.exe' or not game.is_file():
        raise LoaderInstallError('请先确认 PBZ.exe 路径')
    pkg = package if package is not None else UE4SSPackage.discover()
    if not pkg or not pkg.ready:
        raise LoaderInstallError('包内没有 UE4SS 安装包（dependencies\\ue4ss）')
    ue4ss = game.parent / 'ue4ss'
    proxy = game.parent / 'dwmapi.dll'
    if (ue4ss / 'UE4SS.dll').is_file():
        raise FileExistsError('UE4SS 已经安装，未做任何改动')
    if ue4ss.exists():
        raise LoaderInstallError('游戏目录已有 ue4ss 文件夹但不完整，请手动清理后再试')
    if proxy.exists():
        raise LoaderInstallError('游戏目录已有 dwmapi.dll（可能是别的注入器），请手动处理')
    archive_hash = file_sha256(pkg.zip_path)
    if pkg.sha256 and archive_hash and archive_hash.casefold() != pkg.sha256.casefold():
        raise LoaderInstallError('UE4SS 安装包校验失败，请重新解压整合包')
    staging = game.parent / f'.pbz-ue4ss-staging-{uuid.uuid4().hex[:8]}'
    moved = []
    try:
        staging.mkdir()
        with zipfile.ZipFile(pkg.zip_path) as archive:
            archive.extractall(staging)
        staged_loader = staging / 'ue4ss' / 'UE4SS.dll'
        staged_proxy = staging / 'dwmapi.dll'
        if not staged_loader.is_file() or not staged_proxy.is_file():
            raise LoaderInstallError('UE4SS 安装包结构不正确，未做任何改动')
        (staging / 'ue4ss').replace(ue4ss)
        moved.append(ue4ss)
        staged_proxy.replace(proxy)
        moved.append(proxy)
        (ue4ss / LOADER_MARKER).write_text(json.dumps({
            'installed_by': 'PBZ战斗助手',
            'version': pkg.version,
            'package': Path(pkg.zip_path).name,
            'package_sha256': archive_hash,
            'installed_at': datetime.now().isoformat(timespec='seconds'),
        }, ensure_ascii=False, indent=2), 'utf-8')
        return {'version': pkg.version, 'proxy': str(proxy),
                'ue4ss': str(ue4ss), 'files': _count_files(ue4ss)}
    except Exception:
        for item in reversed(moved):
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                elif item.exists():
                    item.unlink()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _count_files(root):
    try:
        return sum(1 for path in Path(root).rglob('*') if path.is_file())
    except OSError:
        return 0
