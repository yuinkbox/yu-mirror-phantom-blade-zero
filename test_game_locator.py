"""Automatic game directory discovery tests (no real registry or drives)."""
from pathlib import Path

from game_locator import Candidate, is_game_exe, locate, score_exe, steam_libraries

EXE_BYTES = b'MZ' + b'\0' * (300 * 1024)


def make_install(root, name='PBZ', layout=('platform', 'steam')):
    """Create ``<root>/<name>/<layout>/PBZ.exe`` and return the launcher path."""
    directory = Path(root).joinpath(name, *layout)
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / 'PBZ.exe'
    exe.write_bytes(EXE_BYTES)
    return exe


def no_registry(*_args):
    return None


def test_env_override_is_used(tmp_path):
    exe = make_install(tmp_path / 'portable')
    result = locate(saved=None, env={'PBZ_GAME_PATH': str(exe)}, window=lambda: [],
                    registry=no_registry, drives=[], scan=False)
    assert result.path == str(exe) and result.found
    assert result.source == 'env'


def test_saved_path_wins_over_drive_scan(tmp_path):
    saved = make_install(tmp_path / 'saved')
    make_install(tmp_path / 'other')
    result = locate(saved=str(saved), env={}, window=lambda: [],
                    registry=no_registry, drives=[str(tmp_path)], scan=True)
    assert result.path == str(saved) and result.source == 'saved'
    assert result.candidates and result.candidates[0].path == str(saved)


def test_running_window_wins_when_nothing_is_saved(tmp_path):
    running = make_install(tmp_path / 'running')
    result = locate(saved='', env={}, window=lambda: [Candidate(str(running), 'running', 1)],
                    registry=no_registry, drives=[], scan=False)
    assert result.path == str(running) and result.source == 'running'


def test_steam_registry_and_library_are_searched(tmp_path):
    library = tmp_path / 'SteamLibrary'
    exe = make_install(library / 'steamapps' / 'common')
    result = locate(saved='', env={}, window=lambda: [],
                    registry=lambda base, key, name: str(library)
                    if name == 'SteamPath' else None,
                    drives=[], scan=False)
    assert result.path == str(exe), result
    assert result.source.startswith('steam')


def test_libraryfolders_vdf_adds_other_drives(tmp_path):
    steam = tmp_path / 'Steam'
    (steam / 'steamapps').mkdir(parents=True)
    extra = tmp_path / 'D-Drive-Library'
    exe = make_install(extra / 'steamapps' / 'common')
    (steam / 'steamapps' / 'libraryfolders.vdf').write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n'
        '\t\t"path"\t\t"%s"\n\t}\n}\n' % (str(steam).replace('\\', '\\\\'),
                                          str(extra).replace('\\', '\\\\')),
        encoding='utf-8')
    assert extra in steam_libraries(steam)
    result = locate(saved='', env={}, window=lambda: [],
                    registry=lambda base, key, name: str(steam) if name == 'SteamPath' else None,
                    drives=[], scan=False)
    assert result.path == str(exe), result


def test_appmanifest_installdir_finds_renamed_folder(tmp_path):
    library = tmp_path / 'SteamLibrary'
    exe = make_install(library / 'steamapps' / 'common', name='Phantom Blade Zero (PBZ)')
    (library / 'steamapps' / 'appmanifest_1234.acf').write_text(
        '"AppState"\n{\n\t"installdir"\t\t"Phantom Blade Zero (PBZ)"\n}\n', encoding='utf-8')
    result = locate(saved='', env={}, window=lambda: [],
                    registry=lambda base, key, name: str(library)
                    if name == 'SteamPath' else None,
                    drives=[], scan=False)
    assert result.path == str(exe), result


def test_bounded_scan_finds_a_copied_install(tmp_path):
    exe = make_install(tmp_path / 'Games')
    result = locate(saved='', env={}, window=lambda: [], registry=no_registry,
                    drives=[str(tmp_path)], scan=True)
    assert result.path == str(exe), result
    assert result.source in ('drive', 'scan', 'scan:library')


def test_deep_install_is_reached_through_a_steamapps_folder(tmp_path):
    exe = make_install(tmp_path / 'a' / 'steamapps' / 'common')
    result = locate(saved='', env={}, window=lambda: [], registry=no_registry,
                    drives=[str(tmp_path)], scan=True)
    assert result.path == str(exe), result


def test_nothing_found_explains_the_next_step(tmp_path):
    result = locate(saved='', env={}, window=lambda: [], registry=no_registry,
                    drives=[str(tmp_path)], scan=True)
    assert not result.found
    assert '手动' in result.message


def test_invalid_files_are_not_offered(tmp_path):
    directory = tmp_path / 'PBZ' / 'platform' / 'steam'
    directory.mkdir(parents=True)
    (directory / 'PBZ.exe').write_bytes(b'MZ')          # far too small
    assert not is_game_exe(directory / 'PBZ.exe')
    assert not is_game_exe(directory / 'notpbz.exe')
    result = locate(saved='', env={}, window=lambda: [], registry=no_registry,
                    drives=[str(tmp_path)], scan=True)
    assert not result.found


def test_loose_copy_is_reported_but_not_adopted(tmp_path):
    # A bare PBZ.exe with no recognisable layout must not silently become the
    # target; the user is asked to confirm.
    loose = tmp_path / 'downloads'
    loose.mkdir()
    (loose / 'PBZ.exe').write_bytes(EXE_BYTES)
    result = locate(saved='', env={}, window=lambda: [], registry=no_registry,
                    drives=[str(tmp_path)], scan=True)
    assert not result.found and '手动确认' in result.message


def test_score_prefers_the_canonical_steam_layout(tmp_path):
    canonical = make_install(tmp_path / 'a' / 'steamapps' / 'common')
    loose = make_install(tmp_path / 'b', layout=())
    assert score_exe(canonical) > score_exe(loose)
    result = locate(saved='', env={}, window=lambda: [], registry=no_registry,
                    drives=[str(tmp_path)], scan=True)
    assert result.path == str(canonical), result


def test_saved_path_that_disappeared_is_ignored(tmp_path):
    gone = tmp_path / 'old' / 'PBZ' / 'platform' / 'steam' / 'PBZ.exe'
    exe = make_install(tmp_path / 'new')
    result = locate(saved=str(gone), env={}, window=lambda: [],
                    registry=no_registry, drives=[str(tmp_path)], scan=True)
    assert result.path == str(exe), result


def test_ue4ss_next_to_the_launcher_raises_the_score(tmp_path):
    plain = make_install(tmp_path / 'a', layout=())
    with_loader = make_install(tmp_path / 'b', layout=())
    (with_loader.parent / 'ue4ss').mkdir()
    (with_loader.parent / 'ue4ss' / 'UE4SS.dll').write_bytes(b'dll')
    assert score_exe(with_loader) > score_exe(plain)
