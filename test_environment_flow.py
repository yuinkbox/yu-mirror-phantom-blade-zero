"""Desktop flow for automatic game detection and the UE4SS self-check."""
import queue
from types import SimpleNamespace

import pytest

import mod_app
from environment import EnvironmentReport, UE4SSPackage
from game_locator import LocateResult


class Bridge:
    def __init__(self, path):
        self.path = path
        self.info = {'status': 'missing', 'installed': False, 'enabled': False,
                     'loaded': False, 'message': '未安装 PBZSmartCombat'}

    def inspect(self):
        return dict(self.info)

    def write_control(self, run, settings=None):
        return 'request'


class FakeHotkeys:
    def __init__(self, *args, **kwargs):
        self.closed = False

    def poll(self):
        return []

    def close(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Isolate the worker from the real machine: paths, registry and hotkeys."""
    monkeypatch.setattr(mod_app, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(mod_app, 'ModBridge', Bridge)
    monkeypatch.setattr(mod_app, 'Hotkeys', FakeHotkeys)
    monkeypatch.setattr(mod_app, 'find_game_by_name', lambda _name: None)
    monkeypatch.setattr(mod_app, 'UE4SSPackage', SimpleNamespace(discover=lambda: None))
    return monkeypatch


def make_worker(path=''):
    worker = mod_app.ModWorker(queue.Queue(), path, {})
    worker.env_view = {}
    return worker


def fake_report(**overrides):
    base = dict(game_path='C:/games/PBZ/platform/steam/PBZ.exe',
                game_dir='C:/games/PBZ/platform/steam', game_exists=True,
                game_writable=True, ue4ss_dir='C:/games/PBZ/platform/steam/ue4ss',
                ue4ss_installed=False, ue4ss_complete=False, ue4ss_proxy=False,
                ue4ss_settings=False, ue4ss_version='', mods_dir='', mods_txt=False,
                mod_installed=False, mod_enabled=False, mod_version='',
                mod_matches_app=False, mods=[], conflicts=[], competing=[],
                package=None, issues=['未安装 UE4SS'], summary='UE4SS 未安装 · Mod 未安装')
    base.update(overrides)
    return EnvironmentReport(**base)


def test_resolve_path_prefers_the_saved_file(env, tmp_path):
    exe = tmp_path / 'PBZ' / 'platform' / 'steam'
    exe.mkdir(parents=True)
    (exe / 'PBZ.exe').write_bytes(b'MZ' + b'\0' * (300 * 1024))
    called = []
    env.setattr(mod_app, 'locate_game', lambda **kwargs: called.append(kwargs)
                or LocateResult(path=kwargs.get('saved')))
    assert mod_app.resolve_path(str(exe / 'PBZ.exe')) == str(exe / 'PBZ.exe')
    assert called and called[0]['saved'] == str(exe / 'PBZ.exe')


def test_worker_locates_the_game_when_no_path_is_saved(env, tmp_path):
    target = tmp_path / 'steamapps' / 'common' / 'PBZ' / 'platform' / 'steam' / 'PBZ.exe'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'MZ' + b'\0' * (300 * 1024))
    env.setattr(mod_app, 'locate_game', lambda **kwargs: LocateResult(
        path=str(target), source='steam:library', score=7,
        message='自动检测到游戏（Steam 库）'))
    worker = make_worker()
    worker.locate()
    assert worker.path == str(target)
    assert 'Steam' in worker.message and worker.locate_source == 'steam:library'
    assert (tmp_path / 'data' / 'game-path.txt').read_text('utf-8') == str(target)


def test_saved_path_is_used_without_scanning(env, tmp_path):
    target = tmp_path / 'PBZ' / 'platform' / 'steam'
    target.mkdir(parents=True)
    exe = target / 'PBZ.exe'
    exe.write_bytes(b'MZ' + b'\0' * (300 * 1024))
    calls = []

    def boom(**kwargs):
        calls.append(kwargs)
        raise AssertionError('must not scan when the saved path exists')

    env.setattr(mod_app, 'locate_game', boom)
    worker = make_worker(str(exe))
    worker.locate()
    assert worker.path == str(exe)
    assert not calls


def test_locate_failure_keeps_the_message(env):
    env.setattr(mod_app, 'locate_game', lambda **kwargs: LocateResult(
        message='没有自动找到 PBZ.exe，请手动选择游戏目录'))
    worker = make_worker()
    worker.locate()
    assert worker.path == ''
    assert '手动' in worker.message


def test_manual_choice_is_not_overwritten_by_detection(env, tmp_path):
    target = tmp_path / 'PBZ' / 'platform' / 'steam'
    target.mkdir(parents=True)
    exe = target / 'PBZ.exe'
    exe.write_bytes(b'MZ' + b'\0' * (300 * 1024))
    calls = []
    env.setattr(mod_app, 'locate_game', lambda **kwargs: calls.append(kwargs)
                or LocateResult(path='other'))
    worker = make_worker(str(exe))
    worker.manual_path = True
    worker.locate()
    assert not calls and worker.path == str(exe)


def ready_package(tmp_path):
    archive = tmp_path / 'pkg.zip'
    archive.write_bytes(b'PK')
    return UE4SSPackage(zip_path=str(archive), version='v3.0.1')


def test_environment_view_exposes_issues_and_the_install_button(env, tmp_path):
    package = ready_package(tmp_path)
    env.setattr(mod_app, 'inspect_environment',
                lambda *args, **kwargs: fake_report(package=package))
    worker = make_worker()
    worker.inspect_environment(force=True)
    view = worker.env_view
    assert view['summary'].startswith('UE4SS 未安装')
    assert view['issues'] and view['can_install_loader']
    assert view['loader'] is False


def test_complete_environment_hides_the_install_button(env):
    env.setattr(mod_app, 'inspect_environment', lambda *args, **kwargs: fake_report(
        ue4ss_installed=True, ue4ss_complete=True, ue4ss_proxy=True,
        mod_installed=True, mod_enabled=True, mod_version='0.17.16',
        mod_matches_app=True, issues=[], summary='UE4SS v3.0.1 · Mod 0.17.16·已启用'))
    worker = make_worker()
    worker.inspect_environment(force=True)
    assert worker.env_view['can_install_loader'] is False
    assert '已启用' in worker.env_view['summary']


def test_install_loader_uses_the_package_and_reports_success(env, tmp_path):
    target = tmp_path / 'PBZ' / 'platform' / 'steam'
    target.mkdir(parents=True)
    exe = target / 'PBZ.exe'
    exe.write_bytes(b'MZ' + b'\0' * (300 * 1024))
    package = ready_package(tmp_path)
    env.setattr(mod_app, 'inspect_environment',
                lambda *args, **kwargs: fake_report(package=package))
    installed = {}

    def fake_install(path, pkg=None):
        installed['path'], installed['package'] = path, pkg
        return {'version': 'v3.0.1', 'files': 3}

    env.setattr(mod_app, 'install_ue4ss', fake_install)
    worker = make_worker(str(exe))
    worker.inspect_environment(force=True)
    worker.install_loader()
    assert installed['path'] == str(exe)
    assert installed['package'] is package
    assert '已安装 UE4SS' in worker.message and 'v3.0.1' in worker.message


def test_install_loader_refuses_while_the_game_runs(env, tmp_path):
    target = tmp_path / 'PBZ' / 'platform' / 'steam'
    target.mkdir(parents=True)
    exe = target / 'PBZ.exe'
    exe.write_bytes(b'MZ' + b'\0' * (300 * 1024))
    env.setattr(mod_app, 'find_game_by_name', lambda _name: SimpleNamespace(path=str(exe)))
    called = []
    env.setattr(mod_app, 'install_ue4ss', lambda *args, **kwargs: called.append(True))
    worker = make_worker(str(exe))
    worker.install_loader()
    assert not called
    assert '退出游戏' in worker.message


def test_install_loader_reports_a_refusal(env, tmp_path):
    from environment import LoaderInstallError
    target = tmp_path / 'PBZ' / 'platform' / 'steam'
    target.mkdir(parents=True)
    exe = target / 'PBZ.exe'
    exe.write_bytes(b'MZ' + b'\0' * (300 * 1024))

    def refuse(*_args, **_kwargs):
        raise LoaderInstallError('UE4SS 安装包校验失败，请重新解压整合包')

    env.setattr(mod_app, 'install_ue4ss', refuse)
    worker = make_worker(str(exe))
    worker.install_loader()
    assert '校验失败' in worker.message


def test_publish_carries_the_environment_view(env):
    worker = make_worker()
    worker.env_view = {'summary': 'UE4SS 未安装', 'issues': ['x'], 'can_install_loader': True}
    worker.publish(10.0, force=True)
    event = worker.events.get_nowait()
    assert event['environment']['summary'] == 'UE4SS 未安装'
    assert event['environment']['can_install_loader'] is True
    assert 'locate_message' in event
