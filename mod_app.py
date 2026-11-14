"""Standalone desktop companion for the UE4SS-based PBZ mod."""
import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

from coexistence import peer_processes
from environment import UE4SSPackage, inspect_environment, install_ue4ss, LoaderInstallError
from game_locator import locate as locate_game, source_label
from hold_combo import COMBO_KEYS, DEFAULT_COMBO_KEY, HoldGesture, combo_key
from mod_bridge import ModBridge
from mod_installer import ModInstaller
from ui_components import BG, BORDER, GREEN, MUTED, SURFACE, TEXT, AMBER
from ui_components import Button, Panel, Switch, label
from windows_io import Hotkeys, find_game_by_name, dpi_aware, is_down

VERSION = '0.18.7'
ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT / 'data-v0.10'
DEFAULT_SETTINGS = {
    'attack_enabled': True, 'guard_enabled': True, 'dodge_enabled': True,
    'heal_enabled': True, 'execution_enabled': True, 'skills_enabled': True,
    'shadow_enabled': False, 'auto_items': False, 'combo_enabled': True, 'combo_macro': True,
    'threshold': 0.4,
    'attack_distance': 3.5, 'attack_period': 0.35, 'combo_preset': 'balanced',
    'blue_response': 'deflect', 'red_response': 'dodge',
    'combo_speed': 1.0, 'shachi_reserve': 0.30, 'combo_key': DEFAULT_COMBO_KEY,
    'switch_enabled': True,
}
COMBO_LABELS = {'balanced': '均衡', 'pressure': '压制', 'swift': '疾风', 'light': '轻击'}
BLUE_LABELS = {'deflect': '蓝光→完美格挡', 'dodge': '蓝光→闪避', 'guard': '蓝光→架招'}
# combo_speed scales every move's input window: <1 presses earlier than the game
# accepts (more dropped inputs), >1 waits longer and never wastes a key.
SPEED_LABELS = {0.9: '激进', 1.0: '标准', 1.12: '稳健'}
# Sha-Chi pays for heavy chain steps and for guarding; the reserve keeps the
# defensive share of the gauge out of the damage rotation.
RESERVE_LABELS = {0.18: '低', 0.30: '中', 0.45: '高'}
COMBO_MODE_LABELS = {'tag': '招式', 'ability': '技能', 'phase': '阶段', 'timed': '定时'}


def label_for(mapping, value, fallback):
    best, distance = fallback, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return mapping.get(value, mapping[fallback])
    for option, title in mapping.items():
        gap = abs(option - number)
        if distance is None or gap < distance:
            best, distance = option, gap
    return mapping[best]


def bundled_mod_path():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    if getattr(sys, 'frozen', False):
        return base / 'PBZSmartCombat'
    return base / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat'


def load_path():
    try:
        value = (DATA / 'game-path.txt').read_text('utf-8').strip()
        if value:
            return value
    except OSError:
        pass
    return ''

def resolve_path(path=''):
    """Saved path first, then an automatic search (never raises)."""
    try:
        return locate_game(saved=path).path or path
    except OSError:
        return path


def save_path(value):
    if not value:
        return
    DATA.mkdir(parents=True, exist_ok=True)
    target = DATA / 'game-path.txt'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(str(Path(value).resolve()), 'utf-8')
    temporary.replace(target)


def load_settings():
    try:
        stored = json.loads((DATA / 'settings.json').read_text('utf-8'))
    except (OSError, ValueError):
        stored = {}
    result = DEFAULT_SETTINGS.copy()
    for key in result:
        if key in stored:
            result[key] = stored[key]
    result['combo_key'] = combo_key(result['combo_key'])
    # The single visible attack switch owns enabling; obsolete hidden combo
    # toggles from older settings must not leave the new mode silently disabled.
    result['combo_enabled'] = True
    return result


def save_settings(settings):
    DATA.mkdir(parents=True, exist_ok=True)
    target = DATA / 'settings.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), 'utf-8')
    temporary.replace(target)


class ModWorker(threading.Thread):
    def __init__(self, events, path=None, settings=None):
        super().__init__(daemon=True)
        self.events = events
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.path = str(path or load_path())
        self.settings = dict(settings or DEFAULT_SETTINGS)
        self.bridge = ModBridge(self.path or 'PBZ.exe')
        self.info = self.bridge.inspect()
        self.window = None
        self.hotkeys = None
        self.control_available = False
        self.control_reason = '正在检查控制权'
        self.run_requested = False
        self.last_path_check = -10
        self.last_peer_check = -10
        self.last_mod_check = -10
        self.last_env_check = -10
        self.last_pulse = -10
        self.last_publish = -10
        self.located = False
        self.locate_message = ''
        self.locate_source = ''
        self.manual_path = False
        self.env_report = None
        self.env_view = {}
        self.package = UE4SSPackage.discover()
        self.message = '等待游戏 Mod'
        self.gesture = HoldGesture()
        self.last_assist = -10
        self.last_assist_value = None

    def release_combo(self):
        owned = self.run_requested or self.last_assist_value is not None
        self.gesture.reset()
        self.last_assist_value = None
        if owned and self.info.get('installed'):
            try:
                self.bridge.write_assist(False, focused=False)
            except (OSError, RuntimeError, ValueError):
                pass

    def set_path(self, path):
        if not path:
            return
        self.release_combo()
        if self.run_requested:
            self.bridge.write_control(False)
        self.path = str(Path(path).resolve())
        self.bridge = ModBridge(self.path)
        self.info = self.bridge.inspect()
        self.run_requested = False
        self.window = None
        self.last_env_check = -10
        self.env_report = None
        save_path(self.path)

    def locate(self, force=False):
        """Find the game automatically; a saved or manual path still wins."""
        if self.manual_path and not force:
            return
        if not force and self.path and Path(self.path).is_file():
            self.located = True
            self.locate_source = self.locate_source or 'saved'
            self.locate_message = '使用上次保存的游戏路径'
            return
        try:
            found = locate_game(saved=self.path)
        except OSError as exc:
            self.message = '自动检测游戏失败：' + str(exc)
            return
        self.located = True
        self.locate_source = found.source
        if found.path:
            if str(Path(found.path)) != self.path:
                self.set_path(found.path)
            # Report the folder that was actually stored: resolve() can follow a
            # junction, and the entry would otherwise show a different path.
            self.locate_message = (f'自动检测到游戏（{source_label(found.source)}）'
                                   f' · {Path(self.path).parent}')
            self.message = self.locate_message
        else:
            self.locate_message = found.message
            if not (self.path and Path(self.path).is_file()):
                self.message = found.message

    def inspect_environment(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_env_check < 4:
            return
        self.last_env_check = now
        try:
            report = inspect_environment(self.path, VERSION, package=self.package)
        except OSError as exc:
            self.env_view = {'summary': '环境检查失败：' + str(exc), 'issues': [],
                             'can_install_loader': False, 'detail': ''}
            self.env_report = None
            return
        self.env_report = report
        if report.game_exists:
            self.locate_source = self.locate_source or 'saved'
        detail = ''
        if report.package:
            detail = '包内 UE4SS ' + (report.package.version or '安装包可用')
            if not report.ue4ss_installed:
                detail += ' · 可一键安装'
        elif not report.ue4ss_installed:
            detail = '未找到 UE4SS 安装包，需要手动安装'
        self.env_view = {
            'summary': report.summary,
            'issues': list(report.issues),
            'detail': detail,
            'can_install_loader': report.can_install_loader,
            'loader': report.ue4ss_installed,
            'loader_version': report.ue4ss_version,
            'mod_version': report.mod_version,
            'mod_enabled': report.mod_enabled,
            'conflicts': list(report.conflicts),
            'competing': list(report.competing),
            'game_writable': report.game_writable,
            'source': self.locate_source,
        }

    def install_loader(self):
        if not (self.path and Path(self.path).is_file()):
            self.message = '先确认游戏目录，再安装 UE4SS'
            return
        if find_game_by_name('PBZ.exe'):
            self.message = '请先退出游戏，再安装 UE4SS'
            return
        report = self.env_report
        try:
            result = install_ue4ss(self.path, report.package if report else None)
        except (LoaderInstallError, FileExistsError, OSError) as exc:
            self.message = str(exc)
        else:
            version = result.get('version') or ''
            self.message = ('已安装 UE4SS' + (f' {version}' if version else '')
                            + '；重启游戏后生效')
        self.inspect_environment(force=True)

    def ownership(self, now):
        if now - self.last_peer_check < 1:
            return
        self.last_peer_check = now
        try:
            peers = peer_processes()
            if peers:
                reason = '其他 PBZ 助手仍在运行'
            else:
                reason = ''
        except OSError as exc:
            reason = str(exc)
        if reason:
            self.release_combo()
            self.control_available = False
            self.control_reason = reason
            if self.run_requested:
                try:
                    self.bridge.write_control(False)
                except (OSError, RuntimeError, ValueError):
                    pass
                self.run_requested = False
                self.message = reason
            if self.hotkeys:
                self.hotkeys.close()
                self.hotkeys = None
            return
        if not self.hotkeys:
            try:
                # F10 belongs to the game's UE4SS console, so stop is F9.
                self.hotkeys = Hotkeys({1008: (0x77, 'toggle'), 1009: (0x78, 'stop')})
                self.control_available = True
                self.control_reason = ''
            except OSError as exc:
                self.control_available = False
                self.control_reason = str(exc)

    def request(self, run):
        self.info = self.bridge.inspect()
        if run:
            if not self.control_available:
                raise RuntimeError(self.control_reason or '桌面控制权不可用')
            if self.info['status'] == 'conflict':
                raise RuntimeError(self.info['message'])
            if not self.info['loaded']:
                raise RuntimeError(self.info['message'])
            if self.info.get('mod_version') != VERSION:
                raise RuntimeError(f'请先安装/升级 {VERSION} Mod，再启动一键连招')
            self.release_combo()
            self.bridge.write_control(True, self.settings)
            self.run_requested = True
            self.last_pulse = time.monotonic()
            self.message = '辅助已启动'
        else:
            self.release_combo()
            if self.info['installed'] and self.info['enabled']:
                self.bridge.write_control(False)
            self.run_requested = False
            self.message = '辅助已停止'

    def pulse(self, now):
        inspected = False
        if now - self.last_mod_check >= .4:
            self.last_mod_check = now
            try:
                self.info = self.bridge.inspect()
                inspected = True
            except (OSError, RuntimeError, ValueError) as exc:
                # A short read/permission hiccup must not cancel the user's run
                # request; the control lease below is what keeps the mod alive.
                self.message = '连接暂失，保持辅助请求：' + str(exc)
        if not self.run_requested:
            return
        if self.info.get('status') == 'conflict':
            try:
                self.bridge.write_control(False)
            except (OSError, RuntimeError, ValueError):
                pass
            self.run_requested = False
            self.message = self.info['message']
            return
        if not self.info.get('installed') or not self.info.get('enabled'):
            self.run_requested = False
            self.message = self.info.get('message', 'Mod 未启用，辅助已停止')
            return
        if inspected:
            if not self.info.get('loaded'):
                self.message = '连接暂失，辅助请求保持中'
            elif self.info.get('running'):
                self.message = '辅助运行中'
            else:
                self.message = '已发送启动请求，等待游戏响应'
        if now - self.last_pulse >= 1:
            try:
                self.bridge.write_control(True, self.settings)
                self.last_pulse = now
            except (OSError, RuntimeError, ValueError) as exc:
                if isinstance(exc, RuntimeError) and 'legacy combat mod conflict' in str(exc):
                    try:
                        self.bridge.write_control(False)
                    except (OSError, RuntimeError, ValueError):
                        pass
                self.run_requested = False
                self.message = str(exc)

    def assist_tick(self, now):
        """Only physical key state crosses the bridge; the Mod emits all inputs."""
        if not self.run_requested:
            if self.last_assist_value is not None:
                self.release_combo()
            return
        try:
            key = combo_key(self.settings.get('combo_key'))
            focused = bool(self.window and self.window.foreground())
            held = self.gesture.sample(
                enabled=self.run_requested and self.settings.get('attack_enabled', True)
                    and self.settings.get('combo_enabled', True),
                focused=focused, key=key, down=is_down(COMBO_KEYS[key][1]))
            value = (held, focused)
            if value == self.last_assist_value and now - self.last_assist < 0.08:
                return
            if self.info.get('installed') and self.info.get('enabled'):
                self.bridge.write_assist(held, focused=focused)
                self.last_assist, self.last_assist_value = now, value
        except (OSError, RuntimeError, ValueError) as exc:
            self.release_combo()
            self.message = '连招按键状态发送失败：' + str(exc)

    def publish(self, now, force=False):
        if not force and now - self.last_publish < .15:
            return
        self.last_publish = now
        self.events.put({
            'version': VERSION,
            'game_path': self.path,
            'message': self.message,
            'mod': self.info.copy(),
            'environment': dict(self.env_view),
            'locate_message': self.locate_message,
            'run_requested': self.run_requested,
            'control_available': self.control_available,
            'control_reason': self.control_reason,
        })

    def run(self):
        try:
            self.locate()
            self.inspect_environment(force=True)
            while not self.stop_event.is_set():
                now = time.monotonic()
                self.ownership(now)
                if self.hotkeys:
                    for key in self.hotkeys.poll():
                        if key == 'toggle':
                            try:
                                self.request(not self.run_requested)
                            except (OSError, RuntimeError, ValueError) as exc:
                                self.message = str(exc)
                        elif key == 'stop':
                            try:
                                self.request(False)
                            except (OSError, RuntimeError, ValueError) as exc:
                                self.message = str(exc)
                while True:
                    try:
                        command, value = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if command == 'run':
                            self.request(bool(value))
                        elif command == 'path':
                            self.manual_path = True
                            self.set_path(value)
                            self.inspect_environment(force=True)
                            self.message = '已更新游戏路径'
                        elif command == 'locate':
                            self.manual_path = False
                            self.locate(force=True)
                            self.inspect_environment(force=True)
                            self.message = self.locate_message or '已重新检测'
                        elif command == 'env':
                            self.inspect_environment(force=True)
                            self.message = '环境已重新检查'
                        elif command == 'install_ue4ss':
                            self.install_loader()
                        elif command == 'settings':
                            self.release_combo()
                            self.settings = dict(value)
                            if self.run_requested and self.info['installed'] and self.info['enabled']:
                                self.bridge.write_control(True, self.settings)
                            self.message = '设置已应用'
                        elif command == 'refresh':
                            self.info = self.bridge.inspect()
                            self.inspect_environment(force=True)
                            self.message = value
                    except (OSError, RuntimeError, ValueError) as exc:
                        self.message = str(exc)
                if now - self.last_path_check >= 2:
                    self.last_path_check = now
                    try:
                        window = find_game_by_name('PBZ.exe')
                        if window and str(Path(window.path).resolve()) != self.path:
                            self.set_path(window.path)
                            self.inspect_environment(force=True)
                            self.message = '已自动识别游戏路径'
                        self.window = window
                    except OSError as exc:
                        self.control_reason = str(exc)
                self.inspect_environment()
                self.pulse(now)
                self.assist_tick(now)
                self.publish(now)
                self.stop_event.wait(.02)
        except Exception as exc:
            self.message = '助手线程异常：' + str(exc)
            self.publish(time.monotonic(), True)
        finally:
            self.release_combo()
            if self.run_requested:
                try:
                    self.bridge.write_control(False)
                except (OSError, RuntimeError, ValueError):
                    pass
            if self.hotkeys:
                self.hotkeys.close()


class App(tk.Tk):
    def __init__(self, smoke=False):
        super().__init__()
        if smoke:
            self.withdraw()
        self.title('PBZ 战斗助手 ' + VERSION)
        self.configure(bg=BG)
        self.geometry('920x760')
        self.minsize(860, 640)
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _base = _Path(getattr(_sys, '_MEIPASS', _Path(__file__).resolve().parent))
            _ico = _base / 'app.ico'
            if _ico.is_file():
                self.iconbitmap(default=str(_ico))
        except Exception:
            pass
        self.events = queue.Queue()
        saved_path = load_path()
        self.game_path = tk.StringVar(
            value=saved_path if saved_path else '正在自动检测游戏目录…')
        self.mode = tk.StringVar(value='未连接')
        self.status = tk.StringVar(value='等待游戏 Mod')
        self.env_status = tk.StringVar(value='环境：正在检查 UE4SS 与 Mod…')
        self.env_detail = tk.StringVar(value='')
        self.health = tk.StringVar(value='—')
        self.target = tk.StringVar(value='—')
        self.enemies = tk.StringVar(value='—')
        self.weapon = tk.StringVar(value='—')
        self.mod_version = tk.StringVar(value='—')
        self.action = tk.StringVar(value='—')
        self.q_action = tk.StringVar(value='等待游戏映射')
        self.hit_rate = tk.StringVar(value='—')
        self.defense_rate = tk.StringVar(value='—')
        self.healing = tk.StringVar(value='—')
        self.skill = tk.StringVar(value='—')
        self.engine_status = tk.StringVar(value='防御引擎 · 等待连接')
        self.combo = tk.StringVar(value='—')
        self.settings = load_settings()
        self.setting_vars = {
            key: tk.BooleanVar(value=self.settings[key]) for key in (
                'attack_enabled', 'guard_enabled', 'dodge_enabled', 'heal_enabled',
                'execution_enabled', 'skills_enabled', 'shadow_enabled', 'switch_enabled',
                'combo_macro')
        }
        self.setting_traces = [
            variable.trace_add('write', lambda *_: self.save_settings())
            for variable in self.setting_vars.values()
        ]
        self.threshold = tk.DoubleVar(value=self.settings['threshold'] * 100)
        self.combo_preset = tk.StringVar(
            value=COMBO_LABELS.get(self.settings['combo_preset'], '均衡'))
        self.combo_key = tk.StringVar(value=COMBO_KEYS[combo_key(self.settings.get('combo_key'))][0])
        self.blue_response = tk.StringVar(
            value=BLUE_LABELS.get(self.settings['blue_response'], '蓝光→完美格挡'))
        self.combo_speed = tk.StringVar(
            value=label_for(SPEED_LABELS, self.settings.get('combo_speed'), 1.0))
        self.shachi_reserve = tk.StringVar(
            value=label_for(RESERVE_LABELS, self.settings.get('shachi_reserve'), 0.30))
        self.worker = ModWorker(self.events, saved_path, self.settings)
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.close)
        if not smoke:
            self.worker.start()
        self.after(120, self.poll)

    def _build(self):
        import tkinter.ttk as ttk
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TEntry', fieldbackground='#18181b', foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        insertcolor=TEXT, padding=8)
        header = tk.Frame(self, bg=BG, padx=28, pady=18)
        header.pack(fill='x')
        label(header, 'PBZ', 20, bold=True).pack(side='left')
        label(header, '战斗助手 · Mod', 14, MUTED).pack(side='left', padx=(12, 0))
        label(header, VERSION, 11, MUTED).pack(side='right')
        tk.Frame(self, bg=BORDER, height=1).pack(fill='x')

        viewport = tk.Frame(self, bg=BG)
        viewport.pack(fill='both', expand=True)
        self.content_canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient='vertical', command=self.content_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.content_canvas.pack(side='left', fill='both', expand=True)
        self.content_canvas.configure(yscrollcommand=scrollbar.set)
        body = tk.Frame(self.content_canvas, bg=BG, padx=24, pady=16)
        content_id = self.content_canvas.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda _event: self.content_canvas.configure(
            scrollregion=self.content_canvas.bbox('all')))
        self.content_canvas.bind('<Configure>', lambda event: self.content_canvas.itemconfigure(
            content_id, width=event.width))
        self.bind('<MouseWheel>', lambda event: self.content_canvas.yview_scroll(
            int(-event.delta / 120), 'units'))
        connection = Panel(body, padding=18)
        connection.pack(fill='x', pady=(0, 14))
        top = tk.Frame(connection.body, bg=SURFACE)
        top.pack(fill='x')
        label(top, '连接状态', 14, bold=True).pack(side='left')
        self.mode_label = label(top, '●  未连接', 12, MUTED)
        self.mode_label.pack(side='right')
        pathrow = tk.Frame(connection.body, bg=SURFACE)
        pathrow.pack(fill='x', pady=(14, 10))
        self.path_entry = ttk.Entry(pathrow, textvariable=self.game_path, state='readonly')
        self.path_entry.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self.detect_button = Button(pathrow, '自动检测', self.detect_game, width=94)
        self.detect_button.pack(side='right', padx=(0, 8))
        Button(pathrow, '选择游戏', self.choose_game, width=100).pack(side='right', padx=(0, 8))
        label(connection.body, size=12, color=MUTED, textvariable=self.status, anchor='w',
              wraplength=780, justify='left').pack(fill='x')
        label(connection.body, size=10, color=MUTED, textvariable=self.engine_status,
              anchor='w', wraplength=780, justify='left').pack(fill='x', pady=(5, 0))
        label(connection.body, size=10, color=TEXT, textvariable=self.env_status,
              anchor='w', wraplength=780, justify='left').pack(fill='x', pady=(5, 0))
        label(connection.body, size=10, color=MUTED, textvariable=self.env_detail,
              anchor='w', wraplength=780, justify='left').pack(fill='x', pady=(2, 0))

        stats = tk.Frame(connection.body, bg=SURFACE)
        stats.pack(fill='x', pady=(16, 0))
        stat_items = (('生命', self.health), ('目标距离', self.target),
                      ('附近敌人', self.enemies), ('治疗反馈', self.healing),
                      ('伤害反馈', self.hit_rate), ('防御后血量', self.defense_rate),
                      ('连招', self.combo), ('Q 技能', self.skill), ('当前动作', self.action))
        for index, (title, variable) in enumerate(stat_items):
            row, column = divmod(index, 4)
            cell = tk.Frame(stats, bg=SURFACE)
            cell.grid(row=row, column=column, sticky='ew', padx=(0, 12), pady=(0, 12 if row == 0 else 0))
            label(cell, title, 10, MUTED).pack(anchor='w')
            label(cell, size=12, color=TEXT, textvariable=variable, bold=True,
                  justify='left').pack(anchor='w', pady=(3, 0))
            stats.grid_columnconfigure(column, weight=1, uniform='stat')

        settings = Panel(body, padding=16)
        settings.pack(fill='x', pady=(0, 14))
        label(settings.body, '战斗设置', 14, bold=True).pack(anchor='w')
        options = tk.Frame(settings.body, bg=SURFACE)
        options.pack(fill='x', pady=(8, 6))
        setting_labels = (
            ('attack_enabled', '一键连招'), ('guard_enabled', '自动格挡'),
            ('dodge_enabled', '自动闪避'), ('heal_enabled', '自动喝药'),
            ('execution_enabled', '连招处决'), ('skills_enabled', '连招 Q 技能'),
            ('switch_enabled', '低杀气换刀'),
            ('combo_macro', '按住即出招'),
        )
        for index, (key, title) in enumerate(setting_labels):
            row, column = divmod(index, 4)
            cell = tk.Frame(options, bg=SURFACE)
            cell.grid(row=row, column=column, sticky='w', padx=(0, 14), pady=5)
            Switch(cell, self.setting_vars[key]).pack(side='left')
            label(cell, title, 10, TEXT).pack(side='left', padx=(7, 0))
            options.grid_columnconfigure(column, weight=1)
        lower = tk.Frame(settings.body, bg=SURFACE)
        lower.pack(fill='x', pady=(4, 0))
        label(lower, '喝药阈值', 11, MUTED).pack(side='left')
        scale = ttk.Scale(lower, from_=20, to=80, variable=self.threshold,
                          command=lambda _value: self.save_settings())
        scale.pack(side='left', fill='x', expand=True, padx=(10, 8))
        self.threshold_label = label(lower, size=11, color=TEXT, text=f'{self.threshold.get():.0f}%')
        self.threshold_label.pack(side='left', padx=(0, 20))
        label(lower, '连招', 11, MUTED).pack(side='left')
        self.combo_box = ttk.Combobox(lower, textvariable=self.combo_preset, state='readonly',
                                      values=tuple(COMBO_LABELS.values()), width=10)
        self.combo_box.pack(side='left', padx=(8, 14))
        self.combo_box.bind('<<ComboboxSelected>>', lambda _event: self.save_settings())
        label(lower, '蓝光', 11, MUTED).pack(side='left')
        self.blue_box = ttk.Combobox(lower, textvariable=self.blue_response, state='readonly',
                                     values=tuple(BLUE_LABELS.values()), width=13)
        self.blue_box.pack(side='left', padx=(8, 0))
        self.blue_box.bind('<<ComboboxSelected>>', lambda _event: self.save_settings())
        tuning = tk.Frame(settings.body, bg=SURFACE)
        tuning.pack(fill='x', pady=(10, 0))
        label(tuning, '连招键', 11, MUTED).pack(side='left')
        self.key_box = ttk.Combobox(tuning, textvariable=self.combo_key, state='readonly',
                                   values=tuple(row[0] for row in COMBO_KEYS.values()), width=12)
        self.key_box.pack(side='left', padx=(8, 16))
        self.key_box.bind('<<ComboboxSelected>>', lambda _event: self.save_settings())
        label(tuning, '连招节奏', 11, MUTED).pack(side='left')
        self.speed_box = ttk.Combobox(tuning, textvariable=self.combo_speed, state='readonly',
                                      values=tuple(SPEED_LABELS.values()), width=8)
        self.speed_box.pack(side='left', padx=(8, 16))
        self.speed_box.bind('<<ComboboxSelected>>', lambda _event: self.save_settings())
        label(tuning, '杀气保留', 11, MUTED).pack(side='left')
        self.reserve_box = ttk.Combobox(tuning, textvariable=self.shachi_reserve, state='readonly',
                                        values=tuple(RESERVE_LABELS.values()), width=8)
        self.reserve_box.pack(side='left', padx=(8, 16))
        self.reserve_box.bind('<<ComboboxSelected>>', lambda _event: self.save_settings())
        label(settings.body, '按住连招 · 松开停止 · 防御与喝药自动运行', 10, MUTED).pack(anchor='w', pady=(10, 0))

        control = Panel(self, padding=16)
        control.pack(side='bottom', fill='x', padx=24, pady=(0, 16), before=viewport)
        label(control.body, '控制', 14, bold=True).pack(anchor='w')
        buttons = tk.Frame(control.body, bg=SURFACE)
        buttons.pack(fill='x', pady=(10, 0))
        self.start_button = Button(buttons, '启动辅助', self.start,
                                   width=124, primary=True, shortcut='F8')
        self.start_button.pack(side='left', padx=(0, 8))
        self.stop_button = Button(buttons, '停止', lambda: self.worker.commands.put(('run', False)),
                                  width=100, shortcut='F9')
        self.stop_button.pack(side='left')
        self.start_button.set_enabled(False)
        self.stop_button.set_enabled(False)
        self.install_button = Button(buttons, '安装/升级 Mod', self.install_mod, width=112)
        self.install_button.pack(side='left', padx=(14, 8))
        self.rollback_button = Button(buttons, '回滚安装', self.rollback_mod, width=100)
        self.rollback_button.pack(side='left')
        self.loader_button = Button(buttons, '安装 UE4SS', self.install_loader, width=100)
        self.loader_button.pack(side='left', padx=(8, 0))
        self.install_button.set_enabled(False)
        self.rollback_button.set_enabled(False)
        self.loader_button.set_enabled(False)
        label(buttons, VERSION + ' · UE4SS', 11, MUTED).pack(side='right', padx=10)

    def save_settings(self):
        self.settings.update({key: variable.get() for key, variable in self.setting_vars.items()})
        self.settings['combo_enabled'] = True
        self.settings['combo_key'] = next(
            (key for key, row in COMBO_KEYS.items() if row[0] == self.combo_key.get()), DEFAULT_COMBO_KEY)
        self.settings['threshold'] = round(float(self.threshold.get()) / 100, 2)
        self.threshold_label.configure(text=f'{self.threshold.get():.0f}%')
        self.settings['combo_preset'] = next(
            (key for key, title in COMBO_LABELS.items() if title == self.combo_preset.get()),
            'balanced')
        self.settings['blue_response'] = next(
            (key for key, title in BLUE_LABELS.items() if title == self.blue_response.get()),
            'deflect')
        self.settings['combo_speed'] = next(
            (value for value, title in SPEED_LABELS.items() if title == self.combo_speed.get()),
            1.0)
        self.settings['shachi_reserve'] = next(
            (value for value, title in RESERVE_LABELS.items() if title == self.shachi_reserve.get()),
            0.30)
        try:
            save_settings(self.settings)
            self.worker.commands.put(('settings', self.settings.copy()))
        except OSError as exc:
            self.status.set('设置保存失败：' + str(exc))

    def start(self):
        self.save_settings()
        self.worker.commands.put(('run', True))

    def choose_game(self):
        selected = filedialog.askopenfilename(parent=self, title='选择影之刃零主程序',
            filetypes=[('影之刃零主程序', 'PBZ.exe'), ('所有文件', '*.*')])
        if selected:
            self.game_path.set(selected)
            self.worker.commands.put(('path', selected))

    def detect_game(self):
        self.status.set('正在自动检测游戏目录…')
        self.worker.commands.put(('locate', None))

    def install_loader(self):
        import tkinter.messagebox as messagebox
        if not messagebox.askyesno(
                '安装 UE4SS',
                '将把整合包内的 UE4SS（含 dwmapi.dll 注入代理）写入游戏目录。\n'
                '不会覆盖已有安装；安装后可点“安装/升级 Mod”。\n请先完全退出游戏。继续？',
                parent=self):
            return
        self.status.set('正在安装 UE4SS…')
        self.worker.commands.put(('install_ue4ss', None))

    def open_mod_manager(self):
        import os
        import webbrowser
        path = ROOT / 'community' / 'UE-MM' / 'UEModManager.exe'
        if path.is_file():
            os.startfile(path)
        else:
            webbrowser.open('https://github.com/KintaroModding/uemodmanager/releases/latest')

    def install_mod(self):
        import tkinter.messagebox as messagebox
        try:
            if find_game_by_name('PBZ.exe'):
                raise RuntimeError('请先退出游戏，再安装或切换 Mod')
            if self.worker.env_view.get('loader') is False:
                raise RuntimeError('未检测到 UE4SS：先点“安装 UE4SS”，再安装 Mod')
            installer = ModInstaller(self.game_path.get(), bundled_mod_path())
            if installer.target.exists():
                if not installer.can_upgrade():
                    raise RuntimeError('现有 Mod 不属于可安全升级的安装；未修改任何文件')
                if not messagebox.askyesno(
                        '升级 Mod',
                        '将先完整备份当前 Mod，再替换为此版本。重启游戏后生效，可通过“回滚安装”恢复当前版本。继续？',
                        parent=self):
                    return
                result = installer.upgrade()
                message = ('Mod 已是最新版本' if not result['changed']
                           else 'Mod 已升级，重启游戏后加载新版本')
            else:
                result = installer.install()
                disabled = sorted(set(result['disabled']))
                message = '安装完成，重启游戏后加载新 Mod'
                if disabled:
                    message += '；已停用旧插件：' + '、'.join(disabled)
            self.worker.commands.put(('refresh', message))
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.set(str(exc))

    def rollback_mod(self):
        import tkinter.messagebox as messagebox
        try:
            if find_game_by_name('PBZ.exe'):
                raise RuntimeError('请先退出游戏，再回滚 Mod')
            installer = ModInstaller(self.game_path.get(), bundled_mod_path())
            restores_previous = installer.rollback_restores_previous()
            prompt = ('将用升级前备份的 Mod 替换当前版本。游戏重启后生效。继续？'
                      if restores_previous else '将移除此 Mod 并恢复安装前的插件启用状态。继续？')
            if not messagebox.askyesno('回滚 Mod', prompt, parent=self):
                return
            installer.rollback()
            message = '已恢复升级前的 Mod' if restores_previous else '已移除 Mod 并恢复原插件状态'
            self.worker.commands.put(('refresh', message))
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.set(str(exc))

    def apply_environment(self, environment):
        """Show the UE4SS / Mod / conflict verdict and the one-click fixes."""
        if not environment:
            self.env_status.set('环境：等待检查…')
            self.env_detail.set('')
            self.loader_button.set_enabled(False)
            return
        summary = environment.get('summary') or '环境未知'
        issues = [issue for issue in environment.get('issues') or [] if issue]
        self.env_status.set('环境：' + summary
                            + ('　⚠ ' + '；'.join(issues[:2]) if issues else '　✓ 可直接使用'))
        detail = environment.get('detail') or ''
        source = environment.get('source') or ''
        source_text = {'saved': '上次保存的路径', 'running': '正在运行的游戏',
                       'env': '环境变量', 'steam:registry': 'Steam 注册表',
                       'steam:library': 'Steam 库', 'drive': '磁盘常见目录',
                       'scan': '磁盘扫描'}.get(source, '')
        if source_text:
            detail = (detail + '　' if detail else '') + '检测来源：' + source_text
        if environment.get('game_writable') is False:
            detail = (detail + '　' if detail else '') + '游戏目录不可写'
        self.env_detail.set(detail)
        self.loader_button.set_enabled(bool(environment.get('can_install_loader')))

    def poll(self):
        latest = None
        try:
            while True:
                latest = self.events.get_nowait()
        except queue.Empty:
            pass
        if latest:
            self.apply_status(latest)
        if self.winfo_exists():
            self.after(120, self.poll)

    def apply_status(self, event):
        path = event['game_path']
        self.game_path.set(path or '未检测到，请点“选择游戏”')
        environment = event.get('environment') or {}
        info = event['mod']
        connected = info.get('loaded', False)
        running = info.get('running', False)
        runtime_version = info.get('mod_version') or '未知'
        def _ver(text):
            parts = []
            for piece in str(text).split('.'):
                digits = ''.join(ch for ch in piece if ch.isdigit())
                parts.append(int(digits or 0))
            return tuple(parts)
        # Only a mod older than this panel needs an upgrade; a mod the panel
        # does not know about yet is merely new and must not be downgraded.
        newer = _ver(runtime_version) > _ver(VERSION)
        mismatch = connected and runtime_version != VERSION and not newer
        message = event['message'] if event['message'] != '等待游戏 Mod' else info['message']
        if mismatch:
            message = f'游戏加载 {runtime_version} · 桌面 {VERSION}；退出游戏后安装/升级 Mod'
        elif connected and newer:
            message = f'游戏内 Mod 较新（{runtime_version}），无需操作'
        self.status.set(message)
        self.apply_environment(environment)
        self.mode.set('版本不一致' if mismatch else
                      '辅助已开启' if running else '已连接' if connected else info['message'])
        self.mode_label.configure(text='●  ' + self.mode.get(),
                                  fg=AMBER if mismatch else GREEN if running else AMBER if connected else MUTED)
        engine = info.get('defense_engine')
        parry_ready = info.get('defense_parry_ready') == '1'
        dodge_ready = info.get('defense_dodge_ready') == '1'
        if not connected:
            engine_state = '等待连接'
        elif parry_ready and dodge_ready:
            engine_state = '格挡、闪避就绪'
        elif parry_ready:
            engine_state = '格挡就绪 · 闪避未就绪'
        elif dodge_ready:
            engine_state = '闪避就绪 · 格挡未就绪'
        else:
            engine_state = '未就绪'
        engine_text = f'Mod {runtime_version} · {engine or "防御引擎未识别"} · {engine_state}'
        tick_ms = _number(info.get('runtime_tick_ms'))
        tick_peak = _number(info.get('runtime_tick_ms_max'))
        if tick_ms is not None and tick_peak is not None:
            engine_text += f' · 脚本 {tick_ms:.1f}ms / 峰值 {tick_peak:.1f}ms'
        if info.get('defense_error'):
            engine_text += ' · ' + info['defense_error']
        if connected and (info.get('combo_confirm') or info.get('combo_reject')):
            engine_text += (f' · 连招 {info.get("combo_confirm", "0")} 成 / '
                            f'{info.get("combo_reject", "0")} 漏')
        self.engine_status.set(engine_text)
        hp, hp_max = _number(info.get('hp')), _number(info.get('hp_max'))
        self.health.set(f'{hp:.0f} / {hp_max:.0f}' if hp is not None and hp_max else '—')
        distance = _number(info.get('target_distance'))
        self.target.set(f'{distance:.1f} m' if distance is not None else '—')
        self.enemies.set(info.get('enemy_count') or '—')
        self.weapon.set(info.get('weapon') or '—')
        self.mod_version.set(info.get('mod_version') or '—')
        self.action.set(_action_label(info.get('action')))
        self.q_action.set(info.get('q_action') or '未识别')
        self.hit_rate.set(_attack_feedback(info))
        self.defense_rate.set(_defense_feedback(info))
        self.healing.set(_heal_feedback(info))
        self.skill.set(_skill_feedback(info))
        step = info.get('combo_step') or '0/0'
        chain = info.get('combo_state') or 'idle'
        preset = COMBO_LABELS.get(info.get('combo_plan'), '均衡')
        mode = COMBO_MODE_LABELS.get(info.get('combo_mode'), '')
        gate = {'waiting_key': '等待按住', 'held': '连招中', 'defense': '防御优先',
                'healing': '喝药优先', 'background': '已切出游戏', 'disabled': '已关闭',
                'input_expired': '按键连接超时', 'input_missing': '等待按键连接'}.get(info.get('combo_gate'))
        self.combo.set(f'{gate or "待机"} · {preset}'
                       + (f' {chain} {step}' if gate == '连招中' else ''))
        available = event.get('control_available', False)
        manageable = available and info.get('installed') and info.get('enabled')
        self.start_button.set_enabled(bool(manageable and connected and not running
                                           and runtime_version == VERSION and info.get('status') != 'conflict'))
        self.stop_button.set_enabled(bool(manageable and (running or event.get('run_requested'))))
        path = Path(event['game_path'])
        source = bundled_mod_path()
        installer = ModInstaller(path, source)
        can_install = (event.get('control_available', False) and path.is_file()
                       and (installer.ue4ss / 'UE4SS.dll').is_file()
                       and (source / 'Scripts' / 'main.lua').is_file()
                       and (installer.can_upgrade() or not installer.target.exists()))
        self.install_button.set_enabled(can_install)
        self.rollback_button.set_enabled(event.get('control_available', False) and installer.can_rollback())

    def close(self):
        if self.worker.is_alive():
            self.worker.stop_event.set()
            self.worker.join(timeout=.35)
        if self.worker.is_alive():
            self.after(100, self.close)
        else:
            self.destroy()


def _counter(info, key):
    try:
        value = int(info.get(key))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _defense_feedback(info):
    observed = _counter(info, 'defense_checks_no_loss')
    damaged = _counter(info, 'defense_checks_damage')
    unknown = _counter(info, 'defense_checks_unknown')
    if observed is None or damaged is None or unknown is None:
        return '结果未验证'
    total = observed + damaged + unknown
    if total == 0:
        return '还没有拦下过攻击'
    rate = 100.0 * observed / max(1, observed + damaged)
    lead = _number(info.get('parry_lead_ms'))
    text = (f'防住 {observed} / 被打 {damaged}（{rate:.0f}%）'
            f' · 未判定 {unknown}')
    if lead is not None:
        text += f' · 提前量 {lead:.0f}ms'
    failed = _counter(info, 'defense_api_fail')
    if failed:
        text += f' · 调用失败 {failed}'
    return text


def _attack_feedback(info):
    damage = _counter(info, 'attack_ok')
    unchanged = _counter(info, 'attack_no_damage')
    unverified = _counter(info, 'attack_unverified')
    if damage is None or unchanged is None:
        return '等待数据'
    return f'掉血 {damage} · 无变化 {unchanged}\n血量未知 {unverified if unverified is not None else "—"}'


def _heal_feedback(info):
    healed, failed = _counter(info, 'heal_ok'), _counter(info, 'heal_fail')
    if healed is None or failed is None:
        return '等待数据'
    source = {'heal': 'R 快捷药槽', 'heal_alt': '药葫芦（备用）'}.get(info.get('heal_last'), '尚未确认')
    # The resolved key and the exact reason it did nothing are what a "R 不喝药"
    # report needs: missing mapping, failed injection, or no HP change.
    state = info.get('heal_state_detail') or info.get('heal_state') or ''
    detail = ''
    if state.startswith('action_missing'):
        detail = '按键未识别'
    elif state.startswith('input_failed'):
        detail = '输入发送失败'
    elif state.startswith('no_hp_gain'):
        detail = '已按键但没回血'
    elif state.startswith('heal_brave_threat'):
        detail = '危险中强行喝药'
    elif state == 'waiting_defense':
        detail = '等待防御空档'
    action = info.get('heal_action') or ''
    support_state = {
        'sent_waiting_effect': 'T 已发送 · 观察效果',
        'hp_increased': 'T 已发送 · 观察到回血',
        'no_hp_increase_observed': 'T 已发送 · 未观察到回血',
        'support_action_missing': 'T 输入未识别',
    }.get(info.get('support_state'), 'T 尚未触发')
    line = source
    if detail:
        line += f' · {detail}'
    if action and detail:
        line += f'（{action}）'
    return f'回血 {healed} · 无效果 {failed}\n{line} · {support_state}'


def _skill_feedback(info):
    labels = {
        'ready': '可用', 'sent': '已释放', 'resource_empty': '资源不足',
        'resource_unknown': '资源未识别', 'input_missing': '按键未识别',
        'cooldown': '冷却中', 'disabled': '已关闭',
    }
    state = labels.get(info.get('skill_state'), '等待数据')
    charges = _counter(info, 'skill_charges')
    return f'{state} · {charges}' if charges is not None else state


def _action_label(action):
    labels = {
        'idle': '待机', 'waiting_player': '等待角色', 'game_paused': '游戏暂停',
        'wait_attack': '等待按住连招键', 'combo_disabled': '连招已关闭',
        'search_target': '寻找目标', 'approach': '接近目标', 'out_of_reach': '目标太远',
        'approach_input_missing': '移动输入未识别',
        'lock_on': '锁定目标', 'execution': '尝试处决', 'combo_ready': '等待连招',
        'weapon_skill': '释放 Q 技能', 'switch_weapon_low_shachi': '低杀气换刀',
        'skill_recovery': '技能收招', 'item_recovery': '道具收招',
        'threat_wait': '预留防御时机',
        'heal': '使用 R 快捷药槽', 'heal_retry': '重试快捷药槽', 'heal_alt': '使用药葫芦备用',
        'support_item': '使用 T 减伤恢复药',
        'heal_waiting': '等待回血', 'heal_wait_chain': '收招后喝药',
        'heal_wait_defense': '防御后喝药', 'heal_ok': '观察到回血',
        'heal_no_effect': '治疗未生效', 'heal_action_missing': '治疗输入未识别',
        'heal_unavailable': '治疗暂不可用', 'heal_backoff': '等待治疗重试',
        'attack_action_missing': '攻击输入未识别', 'shadow_charge': '影之武装蓄力',
        'shadow_used': '影之武装已用', 'shadow_charge_ended': '蓄力结束',
        'shield_broken': '护盾已破', 'projectile_dodge': '躲避投掷物',
        'community_parry': '社区引擎格挡', 'community_dodge': '社区引擎闪避',
        'community_hold': '维持防御', 'defense_unavailable': '防御引擎未就绪',
        'parry': '格挡', 'dodge': '闪避', 'hold': '维持防御',
    }
    if not action:
        return '—'
    if action in labels:
        return labels[action]
    if action.startswith('plan_'):
        return '重击' if action == 'plan_B' else '轻击'
    if action.startswith('boss_punish_'):
        return '抓破绽 · 重击'
    if action.startswith('combo_'):
        return '连招 ' + action.removeprefix('combo_')
    if action.startswith('defend_'):
        return '闪避' if action.endswith('_dodge') else '格挡'
    return action


def _number(value):
    try:
        number = float(value)
        if number == number and abs(number) != float('inf'):
            return number
    except (TypeError, ValueError):
        pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    dpi_aware()
    app = App(smoke=args.smoke_test)
    if args.smoke_test:
        app.after(300, app.close)
    app.mainloop()


if __name__ == '__main__':
    main()
