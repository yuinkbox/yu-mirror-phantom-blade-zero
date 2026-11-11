"""Environment inspection and one-click UE4SS installation tests."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from environment import (
    EnvironmentReport, LoaderInstallError, ModEntry, UE4SSPackage, inspect_environment,
    install_ue4ss, read_mod_version,
)

EXE_BYTES = b'MZ' + b'\0' * (300 * 1024)
LOADER_BYTES = b'UE4SS-DLL' * 1024


def game_dir(root, name='PBZ'):
    directory = Path(root) / 'steamapps' / 'common' / name / 'platform' / 'steam'
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / 'PBZ.exe'
    exe.write_bytes(EXE_BYTES)
    return exe


def write_loader(exe, version='0.17.16', mods=None, enabled=True):
    """Create an installed UE4SS plus optional mods next to the launcher."""
    base = exe.parent
    (base / 'ue4ss' / 'Mods').mkdir(parents=True, exist_ok=True)
    (base / 'ue4ss' / 'UE4SS.dll').write_bytes(LOADER_BYTES)
    (base / 'ue4ss' / 'UE4SS-settings.ini').write_text('[General]\n', encoding='utf-8')
    (base / 'dwmapi.dll').write_bytes(b'proxy')
    for name, wanted in (mods or {}).items():
        directory = base / 'ue4ss' / 'Mods' / name
        (directory / 'Scripts').mkdir(parents=True, exist_ok=True)
        (directory / 'Scripts' / 'main.lua').write_text(
            f"-- mod\nlocal VERSION = '{version}'\n", encoding='utf-8')
        if wanted and enabled:
            (directory / 'enabled.txt').touch()
    return base / 'ue4ss'


def make_package(root, *, version='v3.0.1-1195-g1ceefac8', bad_hash=False):
    """Build the same layout as dependencies/ue4ss, hash file included."""
    folder = Path(root) / 'dependencies' / 'ue4ss'
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'phantombladezero_ue4ss.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('dwmapi.dll', 'proxy-bytes')
        bundle.writestr('ue4ss/UE4SS.dll', LOADER_BYTES)
        bundle.writestr('ue4ss/UE4SS-settings.ini', '[General]\n')
        bundle.writestr('ue4ss/Mods/mods.txt', 'ConsoleEnablerMod : 1\n')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if bad_hash:
        digest = '0' * 64
    (folder / 'SHA256.txt').write_text(
        f'{digest}  phantombladezero_ue4ss_{version}.zip\n', encoding='utf-8')
    return folder


def test_missing_loader_is_reported_with_an_installable_package(tmp_path):
    exe = game_dir(tmp_path)
    package = UE4SSPackage.discover(bases=[tmp_path])
    report = inspect_environment(exe, '0.17.16', package=package)
    assert not report.ue4ss_installed
    assert report.package is not None and report.package.ready
    assert report.can_install_loader
    assert any('未安装 UE4SS' in issue for issue in report.issues)
    assert report.summary.startswith('UE4SS 未安装')


def test_installed_loader_and_mod_are_reported(tmp_path):
    exe = game_dir(tmp_path)
    write_loader(exe, version='0.17.16', mods={'PBZSmartCombat': True})
    report = inspect_environment(exe, '0.17.16')
    assert report.ue4ss_installed and report.ue4ss_complete and report.ue4ss_proxy
    assert report.mod_installed and report.mod_enabled
    assert report.mod_version == '0.17.16' and report.mod_matches_app
    assert report.ok, report.issues
    assert 'PBZSmartCombat' not in report.summary or '已启用' in report.summary


def test_disabled_and_outdated_mod_are_reported(tmp_path):
    exe = game_dir(tmp_path)
    write_loader(exe, version='0.17.15', mods={'PBZSmartCombat': False})
    report = inspect_environment(exe, '0.17.16')
    assert report.mod_installed and not report.mod_enabled
    assert any('未启用' in issue for issue in report.issues)
    write_loader(exe, version='0.17.15', mods={'PBZSmartCombat': True})
    report = inspect_environment(exe, '0.17.16')
    assert report.mod_version == '0.17.15' and not report.mod_matches_app
    assert any('0.17.15' in issue for issue in report.issues)


def test_legacy_and_competing_mods_are_separated(tmp_path):
    exe = game_dir(tmp_path)
    write_loader(exe, mods={'PBZSmartCombat': True, 'PBZAutoGuard': True,
                            'PBZ-AutoParryDodge': True, 'SomeVisualMod': True})
    report = inspect_environment(exe, '0.17.16')
    assert report.conflicts == ['PBZAutoGuard']
    assert report.competing == ['PBZ-AutoParryDodge']
    assert any('旧战斗' in issue for issue in report.issues)
    assert any('其他自动防御' in issue for issue in report.issues)


def test_disabled_competing_mod_is_only_listed(tmp_path):
    exe = game_dir(tmp_path)
    write_loader(exe, mods={'PBZSmartCombat': True, 'PBZAutoGuard': False})
    report = inspect_environment(exe, '0.17.16')
    assert report.conflicts == []
    assert any(mod.name == 'PBZAutoGuard' and not mod.enabled for mod in report.mods)


def test_incomplete_loader_is_not_treated_as_working(tmp_path):
    exe = game_dir(tmp_path)
    (exe.parent / 'ue4ss').mkdir()
    (exe.parent / 'ue4ss' / 'UE4SS.dll').write_bytes(LOADER_BYTES)
    report = inspect_environment(exe, '0.17.16')
    assert report.ue4ss_installed and not report.ue4ss_complete
    assert any('不完整' in issue for issue in report.issues)


def test_missing_game_is_reported(tmp_path):
    report = inspect_environment(tmp_path / 'nope' / 'PBZ.exe', '0.17.16')
    assert not report.game_exists and not report.ok
    assert 'PBZ.exe' in report.issues[0]


def test_chinese_install_path_works(tmp_path):
    exe = game_dir(tmp_path, name='影之刃零 拷贝')
    write_loader(exe, mods={'PBZSmartCombat': True})
    report = inspect_environment(exe, '0.17.16')
    assert report.game_exists and report.mod_installed and report.ok, report.issues


def test_install_ue4ss_extracts_the_bundled_layout(tmp_path):
    exe = game_dir(tmp_path)
    make_package(tmp_path)
    package = UE4SSPackage.discover(bases=[tmp_path])
    assert package is not None and package.ready and package.version.startswith('v3.0.1')
    result = install_ue4ss(exe, package)
    assert result['files'] >= 3
    assert (exe.parent / 'ue4ss' / 'UE4SS.dll').is_file()
    assert (exe.parent / 'dwmapi.dll').is_file()
    marker = json.loads((exe.parent / 'ue4ss' / '.PBZ-loader.json').read_text('utf-8'))
    assert marker['version'] == 'v3.0.1-1195-g1ceefac8'
    report = inspect_environment(exe, '0.17.16', package=package)
    assert report.ue4ss_installed and report.ue4ss_complete
    assert not report.can_install_loader
    with pytest.raises(FileExistsError):
        install_ue4ss(exe, package)


def test_install_refuses_a_corrupted_package(tmp_path):
    exe = game_dir(tmp_path)
    make_package(tmp_path, bad_hash=True)
    package = UE4SSPackage.discover(bases=[tmp_path])
    assert package is not None and package.sha256 == '0' * 64
    with pytest.raises(LoaderInstallError, match='校验失败'):
        install_ue4ss(exe, package)
    assert not (exe.parent / 'ue4ss').exists()


def test_install_refuses_when_a_proxy_dll_already_exists(tmp_path):
    exe = game_dir(tmp_path)
    (exe.parent / 'dwmapi.dll').write_bytes(b'other-injector')
    make_package(tmp_path)
    package = UE4SSPackage.discover(bases=[tmp_path])
    assert package is not None and package.ready
    with pytest.raises(LoaderInstallError, match='dwmapi'):
        install_ue4ss(exe, package)


def test_install_is_not_offered_without_a_package(tmp_path):
    exe = game_dir(tmp_path)
    report = inspect_environment(exe, '0.17.16', package=UE4SSPackage(''))
    assert report.package is None and not report.can_install_loader
    assert any('手动安装' in issue for issue in report.issues)


def test_mod_version_reader_handles_missing_files(tmp_path):
    assert read_mod_version(tmp_path) == ''
    (tmp_path / 'Scripts').mkdir()
    (tmp_path / 'Scripts' / 'main.lua').write_text("local VERSION = '9.9.9'\n", encoding='utf-8')
    assert read_mod_version(tmp_path) == '9.9.9'


def test_report_defaults_are_inert():
    report = EnvironmentReport()
    assert not report.ok or report.issues == []
    assert not report.can_install_loader
    assert isinstance(ModEntry('x', True), ModEntry)
