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
from mod_bridge import ModBridge
from mod_installer import ModInstaller
from ui_components import BG, BORDER, GREEN, MUTED, SURFACE, TEXT, AMBER
from ui_components import Button, Panel, label
from windows_io import Hotkeys, find_game_by_name, dpi_aware

VERSION = '0.14.1'
ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT / 'data-v0.10'
GAME_DEFAULT = r'E:\Steam\steamapps\common\PBZ\platform\steam\PBZ.exe'
DEFAULT_SETTINGS = {
    'attack_enabled': True, 'guard_enabled': True, 'dodge_enabled': True,
    'heal_enabled': True, 'execution_enabled': True, 'skills_enabled': True,
    'shadow_enabled': True, 'combo_enabled': True, 'threshold': 0.4,
    'attack_distance': 3.5, 'attack_period': 0.46, 'combo_preset': 'balanced',
    'blue_response': 'deflect', 'red_response': 'dodge',
}
COMBO_LABELS = {'balanced': '均衡', 'pressure': '压制', 'swift': '疾风', 'light': '轻击'}
BLUE_LABELS = {'deflect': '蓝光→完美格挡', 'dodge': '蓝光→闪避', 'guard': '蓝光→架招'}


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
    return GAME_DEFAULT


def save_path(value):
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
        self.bridge = ModBridge(self.path)
        self.info = self.bridge.inspect()
        self.window = None
        self.hotkeys = None
        self.control_available = False
        self.control_reason = '正在检查控制权'
        self.run_requested = False
        self.last_path_check = -10
        self.last_peer_check = -10
        self.last_mod_check = -10
        self.last_pulse = -10
        self.last_publish = -10
        self.message = '等待游戏 Mod'

    def set_path(self, path):
        self.path = str(Path(path).resolve())
        self.bridge = ModBridge(self.path)
        self.info = self.bridge.inspect()
        self.run_requested = False
        save_path(self.path)

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
            self.bridge.write_control(True, self.settings)
            self.run_requested = True
            self.last_pulse = time.monotonic()
            self.message = '只读观测已启动'
        else:
            if self.info['installed'] and self.info['enabled'] and self.info['status'] != 'conflict':
                self.bridge.write_control(False)
            self.run_requested = False
            self.message = '只读观测已停止'

    def pulse(self, now):
        if now - self.last_mod_check >= .4:
            self.info = self.bridge.inspect()
            self.last_mod_check = now
        if not self.run_requested:
            return
        if self.info['status'] == 'conflict' or not self.info['loaded']:
            self.run_requested = False
            self.message = self.info['message']
            return
        if now - self.last_pulse >= 1:
            try:
                self.bridge.write_control(True, self.settings)
                self.last_pulse = now
            except (OSError, RuntimeError, ValueError) as exc:
                self.run_requested = False
                self.message = str(exc)

    def publish(self, now, force=False):
        if not force and now - self.last_publish < .15:
            return
        self.last_publish = now
        self.events.put({
            'version': VERSION,
            'game_path': self.path,
            'message': self.message,
            'mod': self.info.copy(),
            'run_requested': self.run_requested,
            'control_available': self.control_available,
            'control_reason': self.control_reason,
        })

    def run(self):
        try:
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
                            self.set_path(value)
                            self.message = '已更新游戏路径'
                        elif command == 'settings':
                            self.settings = dict(value)
                            if self.run_requested and self.info['installed'] and self.info['enabled']:
                                self.bridge.write_control(True, self.settings)
                            self.message = '设置已应用'
                        elif command == 'refresh':
                            self.info = self.bridge.inspect()
                            self.message = value
                    except (OSError, RuntimeError, ValueError) as exc:
                        self.message = str(exc)
                if now - self.last_path_check >= 2:
                    self.last_path_check = now
                    try:
                        window = find_game_by_name('PBZ.exe')
                        if window and str(Path(window.path).resolve()) != self.path:
                            self.set_path(window.path)
                            self.message = '已自动识别游戏路径'
                    except OSError as exc:
                        self.control_reason = str(exc)
                self.pulse(now)
                self.publish(now)
                self.stop_event.wait(.05)
        except Exception as exc:
            self.message = '助手线程异常：' + str(exc)
            self.publish(time.monotonic(), True)
        finally:
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
        self.geometry('900x620')
        self.minsize(820, 580)
        self.events = queue.Queue()
        self.game_path = tk.StringVar(value=load_path())
        self.mode = tk.StringVar(value='未连接')
        self.status = tk.StringVar(value='等待游戏 Mod')
        self.health = tk.StringVar(value='—')
        self.target = tk.StringVar(value='—')
        self.enemies = tk.StringVar(value='—')
        self.weapon = tk.StringVar(value='—')
        self.mod_version = tk.StringVar(value='—')
        self.action = tk.StringVar(value='—')
        self.q_action = tk.StringVar(value='等待游戏映射')
        self.hit_rate = tk.StringVar(value='—')
        self.defense_rate = tk.StringVar(value='—')
        self.combo = tk.StringVar(value='—')
        self.settings = load_settings()
        self.setting_vars = {
            key: tk.BooleanVar(value=self.settings[key]) for key in (
                'attack_enabled', 'guard_enabled', 'dodge_enabled', 'heal_enabled',
                'execution_enabled', 'skills_enabled', 'shadow_enabled', 'combo_enabled')
        }
        self.threshold = tk.DoubleVar(value=self.settings['threshold'] * 100)
        self.combo_preset = tk.StringVar(
            value=COMBO_LABELS.get(self.settings['combo_preset'], '均衡'))
        self.blue_response = tk.StringVar(
            value=BLUE_LABELS.get(self.settings['blue_response'], '蓝光→完美格挡'))
        self.worker = ModWorker(self.events, self.game_path.get(), self.settings)
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

        body = tk.Frame(self, bg=BG, padx=24, pady=16)
        body.pack(fill='both', expand=True)
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
        Button(pathrow, '选择游戏', self.choose_game, width=100).pack(side='right')
        label(connection.body, size=12, color=MUTED, textvariable=self.status, anchor='w').pack(fill='x')

        stats = tk.Frame(connection.body, bg=SURFACE)
        stats.pack(fill='x', pady=(18, 0))
        for title, variable in (('生命', self.health), ('目标距离', self.target),
                                ('附近敌人', self.enemies), ('武器', self.weapon),
                                ('命中率', self.hit_rate), ('防御率', self.defense_rate),
                                ('连招', self.combo), ('动作', self.action)):
            column = tk.Frame(stats, bg=SURFACE)
            column.pack(side='left', fill='x', expand=True)
            label(column, title, 11, MUTED).pack(anchor='w')
            label(column, size=13, color=TEXT, textvariable=variable, bold=True).pack(anchor='w', pady=(5, 0))

        settings = Panel(body, padding=16)
        settings.pack(fill='x', pady=(0, 14))
        label(settings.body, '战斗设置', 14, bold=True).pack(anchor='w')
        options = tk.Frame(settings.body, bg=SURFACE)
        options.pack(fill='x', pady=(8, 6))
        setting_labels = (
            ('attack_enabled', '自动攻击'), ('guard_enabled', '自动格挡'),
            ('dodge_enabled', '自动闪避'), ('heal_enabled', '自动喝药'),
            ('execution_enabled', '自动处决'), ('skills_enabled', '自动 Q 技能'),
            ('shadow_enabled', '破盾蓄力'), ('combo_enabled', '智能连招'),
        )
        for index, (key, title) in enumerate(setting_labels):
            row, column = divmod(index, 4)
            tk.Checkbutton(
                options, text=title, variable=self.setting_vars[key], command=self.save_settings,
                bg=SURFACE, fg=TEXT, activebackground=SURFACE, activeforeground=TEXT,
                selectcolor='#27272a', font=('Segoe UI', 10), relief='flat',
            ).grid(row=row, column=column, sticky='w', padx=(0, 14), pady=5)
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

        control = Panel(body, padding=16)
        control.pack(fill='x')
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
        self.install_button.set_enabled(False)
        self.rollback_button.set_enabled(False)
        label(buttons, VERSION + ' · UE4SS', 11, MUTED).pack(side='right', padx=10)

    def save_settings(self):
        self.settings.update({key: variable.get() for key, variable in self.setting_vars.items()})
        self.settings['threshold'] = round(float(self.threshold.get()) / 100, 2)
        self.threshold_label.configure(text=f'{self.threshold.get():.0f}%')
        self.settings['combo_preset'] = next(
            (key for key, title in COMBO_LABELS.items() if title == self.combo_preset.get()),
            'balanced')
        self.settings['blue_response'] = next(
            (key for key, title in BLUE_LABELS.items() if title == self.blue_response.get()),
            'deflect')
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

    def open_mod_manager(self):
        import os
        import webbrowser
        path = ROOT / 'community' / 'UE-MM' / 'UEModManager.exe'
        if path.is_file():
            os.startfile(path)
        else:
            webbrowser.open('https://github.com/KintaroModding/uemodmanager/releases/latest')

    def install_mod(self):
        try:
            if find_game_by_name('PBZ.exe'):
                raise RuntimeError('请先退出游戏，再安装或切换 Mod')
            installer = ModInstaller(self.game_path.get(), bundled_mod_path())
            if installer.target.exists():
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
        if not messagebox.askyesno('回滚 Mod', '移除新 Mod 并恢复安装前的插件启用状态？', parent=self):
            return
        try:
            if find_game_by_name('PBZ.exe'):
                raise RuntimeError('请先退出游戏，再回滚 Mod')
            ModInstaller(self.game_path.get(), bundled_mod_path()).rollback()
            self.worker.commands.put(('refresh', '已回滚到安装前状态'))
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.set(str(exc))

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
        self.game_path.set(event['game_path'])
        info = event['mod']
        self.status.set(event['message'] if event['message'] != '等待游戏 Mod' else info['message'])
        connected = info.get('loaded', False)
        running = info.get('running', False)
        self.mode.set('战斗运行中' if running else '已连接' if connected else info['message'])
        self.mode_label.configure(text='●  ' + self.mode.get(),
                                  fg=GREEN if running else AMBER if connected else MUTED)
        hp, hp_max = _number(info.get('hp')), _number(info.get('hp_max'))
        self.health.set(f'{hp:.0f} / {hp_max:.0f}' if hp is not None and hp_max else '—')
        distance = _number(info.get('target_distance'))
        self.target.set(f'{distance:.1f} m' if distance is not None else '—')
        self.enemies.set(info.get('enemy_count') or '—')
        self.weapon.set(info.get('weapon') or '—')
        self.mod_version.set(info.get('mod_version') or '—')
        self.action.set(info.get('action') or '—')
        self.q_action.set(info.get('q_action') or '未识别')
        self.hit_rate.set(_rate(info.get('attack_ok'), info.get('attack_no_damage')))
        self.defense_rate.set(_rate(info.get('def_ok'), info.get('def_fail')))
        step = info.get('combo_step') or '0/0'
        chain = info.get('combo_state') or 'idle'
        self.combo.set(f'{chain} {step}')
        available = event.get('control_available', False)
        manageable = available and info.get('installed') and info.get('enabled')
        self.start_button.set_enabled(bool(manageable and connected and not running
                                           and info.get('status') != 'conflict'))
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


def _rate(good, bad):
    try:
        good, bad = int(good or 0), int(bad or 0)
    except (TypeError, ValueError):
        return '—'
    total = good + bad
    if total <= 0:
        return '—'
    return f'{good * 100 // total}% ({good}/{total})'


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
