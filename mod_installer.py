"""Reversible UE4SS mod installation and legacy-plugin handoff."""
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

LEGACY_MODS = ('PBZAssist', 'PBZAutoCombat', 'PBZAutoGuard')
MANIFEST = 'migration.json'
# Files the mod writes at runtime; they must never block upgrade or rollback.
RUNTIME = frozenset({'installation.json', 'control.ini', 'status.ini', 'status-a.ini',
                       'status-b.ini', 'diag.txt', 'assist.ini', 'assist.tmp'})


class ModInstaller:
    def __init__(self, game_exe, source_mod):
        self.game_exe = Path(game_exe).resolve()
        self.ue4ss = self.game_exe.parent / 'ue4ss'
        self.mods = self.ue4ss / 'Mods'
        self.target = self.mods / 'PBZSmartCombat'
        self.source = Path(source_mod).resolve()
        self.backups = self.ue4ss / 'PBZSmartCombat-backups'
        self.mods_txt = self.mods / 'mods.txt'

    def install(self):
        if self.game_exe.name.casefold() != 'pbz.exe' or not self.game_exe.is_file():
            raise FileNotFoundError('请选择有效的 PBZ.exe')
        if not (self.ue4ss / 'UE4SS.dll').is_file() or not self.mods.is_dir():
            raise FileNotFoundError('未找到游戏现有 UE4SS 安装')
        if not (self.source / 'Scripts' / 'main.lua').is_file():
            raise FileNotFoundError('新 Mod 源文件不完整')
        if self.target.exists():
            raise FileExistsError('PBZSmartCombat 已存在；请先回滚现有安装')

        original_mods = self.mods_txt.read_bytes() if self.mods_txt.is_file() else None
        updated_mods = _disable_entries(original_mods, LEGACY_MODS)
        marker_rows = []
        for name in LEGACY_MODS:
            marker = self.mods / name / 'enabled.txt'
            if marker.is_file():
                marker_rows.append({
                    'relative': str(marker.relative_to(self.mods)),
                    'disabled': str((marker.parent / 'enabled.txt.disabled-by-PBZSmartCombat').relative_to(self.mods)),
                    'data': base64.b64encode(marker.read_bytes()).decode('ascii'),
                    'sha256': _digest(marker.read_bytes()),
                })

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.backups.mkdir(parents=True, exist_ok=True)
        backup = self.backups / f'{stamp}-{uuid.uuid4().hex[:8]}'
        backup.mkdir()
        staging = self.mods / f'.PBZSmartCombat-installing-{uuid.uuid4().hex}'
        manifest = {
            'schema': 1,
            'managed_by': 'PBZSmartCombat',
            'state': 'installing',
            'mods_txt_existed': original_mods is not None,
            'mods_txt_before': base64.b64encode(original_mods or b'').decode('ascii'),
            'markers': marker_rows,
        }
        manifest_path = backup / MANIFEST
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), 'utf-8')
        try:
            shutil.copytree(self.source, staging)
            enabled = staging / 'enabled.txt'
            enabled.touch(exist_ok=True)
            manifest['installed_files'] = _tree_hashes(staging)
            (staging / 'installation.json').write_text(
                json.dumps({'managed_by': 'PBZSmartCombat', 'backup': backup.name}), 'utf-8')
            staging.replace(self.target)
            for row in marker_rows:
                source = self.mods / row['relative']
                destination = self.mods / row['disabled']
                if destination.exists():
                    raise FileExistsError(f'旧插件启用标记备份已存在：{destination.name}')
                source.replace(destination)
            if updated_mods is not None and updated_mods != original_mods:
                _atomic_write(self.mods_txt, updated_mods)
            manifest['state'] = 'installed'
            manifest['mods_txt_after_sha256'] = _digest(updated_mods) if updated_mods is not None else None
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), 'utf-8')
            return {'backup': backup, 'disabled': [Path(row['relative']).parent.name for row in marker_rows]
                    + _disabled_entries(original_mods, LEGACY_MODS)}
        except Exception:
            _restore_files(self.mods, self.mods_txt, manifest)
            if self.target.exists():
                shutil.rmtree(self.target)
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def upgrade(self):
        """Replace a managed install with the bundled mod, keeping its backup record."""
        backup, manifest = self._latest_install()
        if not backup:
            raise FileNotFoundError('没有可升级的 PBZSmartCombat 安装')
        marker = self.target / 'installation.json'
        try:
            installed = json.loads(marker.read_text('utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('目标 Mod 不含本安装器的标记，未做任何改动')
        if installed.get('managed_by') != 'PBZSmartCombat' or installed.get('backup') != backup.name:
            raise RuntimeError('目标 Mod 与备份记录不匹配，未做任何改动')
        current = _tree_hashes(self.target, exclude=RUNTIME)
        if not (self.source / 'Scripts' / 'main.lua').is_file():
            raise FileNotFoundError('新 Mod 源文件不完整')
        if all(current.get(path) == digest for path, digest in _tree_hashes(self.source).items()):
            return {'backup': backup, 'changed': False}
        staging = self.mods / f'.PBZSmartCombat-upgrading-{uuid.uuid4().hex}'
        previous = self.mods / f'.PBZSmartCombat-previous-{uuid.uuid4().hex}'
        snapshots = backup / 'upgrade-snapshots'
        snapshots.mkdir(exist_ok=True)
        snapshot = snapshots / f'{datetime.now().strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}'
        try:
            shutil.copytree(self.target, snapshot)
            shutil.copytree(self.source, staging)
            (staging / 'enabled.txt').touch(exist_ok=True)
            (staging / 'installation.json').write_text(
                json.dumps({'managed_by': 'PBZSmartCombat', 'backup': backup.name}), 'utf-8')
            self.target.replace(previous)
            try:
                staging.replace(self.target)
            except Exception:
                previous.replace(self.target)
                raise
            old_manifest = dict(manifest)
            manifest['installed_files'] = _tree_hashes(self.target, exclude=RUNTIME)
            manifest['rollback_mod_archive'] = snapshot.relative_to(backup).as_posix()
            manifest['rollback_mod_hashes'] = _tree_hashes(snapshot)
            try:
                _write_manifest(backup, manifest)
            except Exception:
                shutil.rmtree(self.target)
                previous.replace(self.target)
                manifest.clear()
                manifest.update(old_manifest)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)
        return {'backup': backup, 'changed': True}

    def can_upgrade(self):
        if not self.target.is_dir() or not (self.source / 'Scripts' / 'main.lua').is_file():
            return False
        backup, manifest = self._latest_install()
        if not backup:
            return False
        try:
            installed = json.loads((self.target / 'installation.json').read_text('utf-8'))
        except (OSError, ValueError):
            return False
        if installed.get('managed_by') != 'PBZSmartCombat' or installed.get('backup') != backup.name:
            return False
        return True

    def rollback(self):
        backup, manifest = self._latest_install()
        if not backup:
            raise FileNotFoundError('没有可回滚的 PBZSmartCombat 安装')
        marker = self.target / 'installation.json'
        try:
            installed = json.loads(marker.read_text('utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('目标 Mod 不含本安装器的标记，未删除任何文件')
        if installed.get('managed_by') != 'PBZSmartCombat' or installed.get('backup') != backup.name:
            raise RuntimeError('目标 Mod 与备份记录不匹配，未删除任何文件')
        if manifest.get('mods_txt_after_sha256'):
            if not self.mods_txt.is_file() or _digest(self.mods_txt.read_bytes()) != manifest['mods_txt_after_sha256']:
                raise RuntimeError('mods.txt 在安装后已被修改；为保护新设置，回滚已停止')
        for row in manifest.get('markers', []):
            original = self.mods / row['relative']
            disabled = self.mods / row['disabled']
            if original.exists():
                raise RuntimeError(f'旧插件启用标记已被重新创建：{original}')
            if disabled.is_file() and _digest(disabled.read_bytes()) != row['sha256']:
                raise RuntimeError(f'旧插件备份标记已被修改：{disabled}')
        if _tree_hashes(self.target, exclude=RUNTIME) != manifest.get('installed_files', {}):
            raise RuntimeError('新 Mod 文件在安装后已被修改；为保护改动，回滚已停止')
        snapshot_relative = manifest.get('rollback_mod_archive')
        if snapshot_relative:
            snapshot = _safe_snapshot_path(backup, snapshot_relative)
            if not snapshot.is_dir() or _tree_hashes(snapshot) != manifest.get('rollback_mod_hashes'):
                raise RuntimeError('升级前的 Mod 备份缺失或已损坏；未改动当前安装')
            previous = self.mods / f'.PBZSmartCombat-rollback-{uuid.uuid4().hex}'
            self.target.replace(previous)
            try:
                snapshot.replace(self.target)
                manifest['installed_files'] = _tree_hashes(self.target, exclude=RUNTIME)
                manifest.pop('rollback_mod_archive', None)
                manifest.pop('rollback_mod_hashes', None)
                try:
                    _write_manifest(backup, manifest)
                except Exception:
                    self.target.replace(snapshot)
                    previous.replace(self.target)
                    raise
            except Exception:
                if previous.exists() and not self.target.exists():
                    previous.replace(self.target)
                raise
            shutil.rmtree(previous, ignore_errors=True)
        else:
            shutil.rmtree(self.target)
            _restore_files(self.mods, self.mods_txt, manifest)
            manifest['state'] = 'rolled_back'
            _write_manifest(backup, manifest)
        return backup

    def can_rollback(self):
        backup, manifest = self._latest_install()
        if not backup or not self.target.is_dir():
            return False
        try:
            installed = json.loads((self.target / 'installation.json').read_text('utf-8'))
        except (OSError, ValueError):
            return False
        if not (manifest.get('state') == 'installed'
                and installed.get('managed_by') == 'PBZSmartCombat'
                and installed.get('backup') == backup.name
                and _tree_hashes(self.target, exclude=RUNTIME) == manifest.get('installed_files', {})):
            return False
        snapshot_relative = manifest.get('rollback_mod_archive')
        if snapshot_relative:
            try:
                snapshot = _safe_snapshot_path(backup, snapshot_relative)
                return (snapshot.is_dir()
                        and _tree_hashes(snapshot) == manifest.get('rollback_mod_hashes'))
            except (OSError, ValueError):
                return False
        return True

    def rollback_restores_previous(self):
        backup, manifest = self._latest_install()
        return bool(backup and manifest.get('rollback_mod_archive'))

    def _latest_install(self):
        try:
            candidates = sorted(self.backups.iterdir(), reverse=True)
        except OSError:
            return None, {}
        for backup in candidates:
            path = backup / MANIFEST
            try:
                manifest = json.loads(path.read_text('utf-8'))
            except (OSError, ValueError):
                continue
            if manifest.get('managed_by') == 'PBZSmartCombat' and manifest.get('state') == 'installed':
                return backup, manifest
        return None, {}


def _disable_entries(data, names):
    if data is None:
        return None
    bom = data.startswith(b'\xef\xbb\xbf')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('mods.txt 编码不是 UTF-8，无法安全迁移') from exc
    for name in names:
        pattern = re.compile(rf'^(\s*{re.escape(name)}\s*:\s*)1(\s*(?:;.*)?)$', re.I | re.M)
        text = pattern.sub(r'\g<1>0\g<2>', text)
    return (b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8')


def _disabled_entries(data, names):
    if data is None:
        return []
    text = data.decode('utf-8-sig', errors='replace')
    return [name for name in names if re.search(
        rf'^\s*{re.escape(name)}\s*:\s*1\s*(?:;.*)?$', text, re.I | re.M)]


def _restore_files(mods, mods_txt, manifest):
    if manifest.get('mods_txt_existed'):
        original = base64.b64decode(manifest['mods_txt_before'])
        _atomic_write(mods_txt, original)
    for row in manifest.get('markers', []):
        original = mods / row['relative']
        disabled = mods / row['disabled']
        if disabled.is_file() and not original.exists():
            disabled.replace(original)
        elif not original.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(original, base64.b64decode(row['data']))


def _safe_snapshot_path(backup, relative):
    candidate = (backup / relative).resolve()
    if backup.resolve() not in candidate.parents:
        raise ValueError('升级备份路径无效')
    return candidate


def _write_manifest(backup, manifest):
    payload = json.dumps(manifest, ensure_ascii=True, indent=2).encode('utf-8')
    _atomic_write(backup / MANIFEST, payload)


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _tree_hashes(path, exclude=frozenset()):
    return {file.relative_to(path).as_posix(): _digest(file.read_bytes())
            for file in path.rglob('*') if file.is_file()
            and file.relative_to(path).as_posix() not in exclude}
