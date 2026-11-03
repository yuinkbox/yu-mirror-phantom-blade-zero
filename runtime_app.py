"""Chinese desktop UI for the build-specific memory-backed battle assistant."""
import argparse
from dataclasses import asdict
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from combat import CombatSettings, CombatScheduler, COMBO_NAMES
from runtime import RuntimeReader, RuntimeState, verify_build
from windows_io import InputSink, InputSuspended, Hotkeys, find_game, find_game_by_name, dpi_aware, is_down, CHOICES, MOUSE, KEYS
from coexistence import peer_processes, ObserverSink
from defense_record import DefenseRecorder
from community_mods import inspect_autoguard
from tactics_runtime import TacticalMonitor
from session_control import SessionControl

VERSION = '0.9.5-preview.1'
ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT/'data-next'
SETTINGS = DATA/'combat-settings.json'
GAME = r'E:\Steam\steamapps\common\PBZ\platform\steam\PBZ.exe'


def load_game_path():
    try:
        value=(DATA/'game-path.txt').read_text('utf-8').strip()
        if value:return value
    except OSError:
        pass
    return GAME


def save_game_path(path):
    DATA.mkdir(parents=True,exist_ok=True)
    target=DATA/'game-path.txt'
    tmp=target.with_suffix('.tmp')
    tmp.write_text(str(Path(path).resolve()),'utf-8')
    tmp.replace(target)


def load_settings():
    if not SETTINGS.exists():
        return CombatSettings()
    data = json.loads(SETTINGS.read_text('utf-8'))
    if 'combo_preset' not in data:
        data['attack_period'] = .48
    result = CombatSettings(**{k: v for k, v in data.items() if k in CombatSettings.__dataclass_fields__})
    result.validate()
    if any(getattr(result, key) not in CHOICES for key in ('heal_key', 'guard_key', 'attack_key', 'dodge_key','heavy_key','execution_key','skill_key','shadow_key','switch_key','lock_key')):
        raise ValueError('设置中存在不支持的按键')
    return result


class Worker(threading.Thread):
    def __init__(self, config, events, path=None, observe=False):
        super().__init__(daemon=True)
        self.events = events
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.scheduler = CombatScheduler(config)
        self.path = str(path or load_game_path())
        self.window = None
        self.runtime = None
        self.state = RuntimeState()
        self.observe_only = observe
        self.control_available = False
        self.control_reason = '正在检查运行中的助手'
        self.sink = ObserverSink() if observe else InputSink()
        self.last_peer_check = -10
        self.lock_was_down = False
        self.status = '正在连接游戏'
        self.last_notify = 0
        self.last_find = -10
        self.next_discover = 0
        self.verified_signature = None
        self.start_at = None
        self.last_status = ''
        self.last_attributes = None
        self.heartbeat_at = 0
        self.defense_recorder = DefenseRecorder(DATA/'defense-capture.jsonl')
        self.plugin_info = inspect_autoguard(self.path)
        self.last_plugin_check = -10
        self.tactical = None
        self.session = SessionControl()
        self.last_combo_counts=(0,0,0)
        self.last_move_catalog=None

    def pause(self, reason='已暂停'):
        self.session.stop(reason)
        self.scheduler.pause(reason)
        self.sink.release_all()
        self.status = reason
        self.start_at = None

    def suspend(self, reason):
        self.scheduler.pause(reason)
        self.sink.release_all()
        self.status=reason

    def update_session(self, now):
        if not self.control_available:
            if self.session.enabled:self.pause(self.control_reason)
            return
        foreground=bool(self.window and self.window.foreground())
        was_enabled=self.session.enabled
        ready=self.session.update(now,foreground,self.state,is_down(0x1b) or is_down(9))
        if not ready:
            if self.scheduler.armed:self.suspend(self.session.reason)
            if was_enabled:self.status=self.session.reason
        elif not self.scheduler.armed:
            self.scheduler.arm(now,self.state)
            self.status='已自动恢复，监测战斗状态'
            logging.info('RESUME hp=%s game_time=%s',self.state.hp,self.state.game_time)

    def toggle(self):
        if not self.control_available:
            self.status=self.control_reason
            return
        if self.session.enabled:
            self.pause()
        elif not self.window or not self.window.foreground():
            self.status = '请回到游戏画面后按 F8'
        elif not any((self.scheduler.config.heal_enabled, self.scheduler.config.guard_enabled, self.scheduler.config.attack_enabled,self.scheduler.config.execution_enabled)):
            self.status = '请先勾选辅助功能并保存'
        elif not self.state.valid:
            self.status = self.state.reason
        elif self.plugin_info['enabled'] and self.plugin_info['status'] != 'started':
            self.status = self.plugin_info['message']+'；确认加载后再开始助手'
        else:
            self.session.start(time.monotonic(), self.state)
            self.status = self.session.reason
            logging.info('ARM hp=%s/%s medicine=%s target=%s', self.state.hp, self.state.hp_max, self.state.medicine, hex(self.state.target))

    def notify(self, now, force=False):
        if not force and now-self.last_notify < .15:
            return
        self.last_notify = now
        if self.status != self.last_status:
            logging.info(self.status)
            if self.scheduler.armed:
                from weapon_policy import combat_art_decision
                data=self.state.tactics
                logging.info('DECISION target=%s distance=%s weapon=%s phase=%s active_moves=%s Q=%s shadow=%s',
                    hex(self.state.target),self.state.target_distance,data.get('weapon'),data.get('phase'),
                    data.get('active_moves'),combat_art_decision(self.state)[1],data.get('shadow'))
                logging.info('SHIELD %s',self.scheduler.shadow.decision(now,self.state,self.scheduler.config,
                             now<self.scheduler.director.hurt_until)[1])
            self.last_status = self.status
        data=self.state.tactics
        catalog=(data.get('weapon'),data.get('weapon_level'),
                 tuple(sorted(k for k,v in data.get('moves',{}).items() if v.get('available'))),
                 data.get('enhancement',{}).get('unlocked'))
        if data and catalog!=self.last_move_catalog:
            logging.info('MOVE_CATALOG weapon=%s level=%s available=%s enhancement=%s',*catalog)
            self.last_move_catalog=catalog
        self.events.put({'status': self.status, 'state': self.state, 'armed': self.scheduler.armed,
                         'game_path':self.path,
                         'control_available':self.control_available,'control_reason':self.control_reason,
                         'enabled': self.session.enabled,
                         'counts': self.scheduler.actions_count.copy(), 'combo_index':self.scheduler.combo_index,
                         'strategy': self.scheduler.strategy,
                         'combo':dict(plan=self.scheduler.director.plan.label if self.scheduler.director.plan else '',
                                      completed=self.scheduler.director.completed,repaired=self.scheduler.director.repairs,
                                      confirmed=self.scheduler.director.confirmed),
                         'community': self.plugin_info.copy()})

    def sync_control(self,now,hotkeys):
        """Yield exclusively to existing assistants, including the unmodified 0.8.1."""
        if now-self.last_peer_check<1:
            return hotkeys
        self.last_peer_check=now
        reason='只读观察模式' if self.observe_only else ''
        if not reason:
            try:
                peers=peer_processes()
                if peers:reason='现用助手仍在运行 · 新版只读观察'
            except OSError as exc:
                reason=str(exc)
        if reason:
            if self.control_available or self.session.enabled:
                self.pause(reason)
            self.control_available=False
            self.control_reason=reason
            if hotkeys:hotkeys.close()
            return None
        if hotkeys is None:
            try:
                hotkeys=Hotkeys({1008:(0x77,'toggle'),1010:(0x79,'stop')})
            except OSError:
                self.control_available=False
                self.control_reason='快捷键由其他程序使用 · 新版只读观察'
                return None
        self.control_available=True
        self.control_reason=''
        return hotkeys

    def connect(self, now):
        self.last_find = now
        window = find_game(self.path)
        if not window:
            candidate=find_game_by_name('PBZ.exe')
            if candidate:
                candidate_path=Path(candidate.path)
                try:
                    compatible=verify_build(candidate_path)
                except OSError:
                    compatible=False
                if not compatible:
                    if self.runtime:
                        self.pause('游戏版本不兼容')
                        if self.tactical:
                            self.tactical.stop_event.set()
                            self.tactical.join(timeout=.3)
                            self.tactical=None
                        self.runtime.close()
                        self.runtime=None
                    self.window=None
                    self.state=RuntimeState(reason='检测到 PBZ.exe，但此游戏版本尚未适配')
                    self.status=self.state.reason
                    return
                if str(candidate_path.resolve())!=str(Path(self.path).resolve()):
                    self.path=str(candidate_path.resolve())
                    save_game_path(self.path)
                    stat=candidate_path.stat()
                    self.verified_signature=(self.path,stat.st_size,stat.st_mtime_ns)
                    self.plugin_info=inspect_autoguard(self.path)
                    self.last_plugin_check=now
                window=candidate
        if not window and self.window and self.window.valid():
            window=self.window
        pid = window.pid() if window else None
        if self.runtime and self.runtime.memory.pid != pid:
            self.pause('游戏已关闭或进程已变化')
            if self.tactical:
                self.tactical.stop_event.set()
                self.tactical.join(timeout=.3)
                self.tactical = None
            self.runtime.close()
            self.runtime = None
            self.state = RuntimeState(reason='等待游戏')
        self.window = window
        if window and not self.runtime:
            file = Path(self.path)
            st = file.stat()
            signature = (str(file.resolve()), st.st_size, st.st_mtime_ns)
            if signature != self.verified_signature:
                self.status = '正在核对游戏版本'
                self.notify(now, True)
                if not verify_build(file):
                    raise ValueError('当前游戏版本与已分析版本不同，需要重新适配')
                self.verified_signature = signature
            self.runtime = RuntimeReader(pid, self.path)
            self.next_discover = 0

    def run(self):
        hotkeys = None
        try:
            while not self.stop_event.is_set():
                tick = time.monotonic()
                try:
                    hotkeys=self.sync_control(tick,hotkeys)
                    pressed = hotkeys.poll() if hotkeys else []
                    if self.control_available and ('stop' in pressed or is_down(0x79)):
                        self.pause('F10 已停止')
                        pressed = []
                    while True:
                        try:
                            command, value = self.commands.get_nowait()
                        except queue.Empty:
                            break
                        if command == 'pause':
                            self.pause()
                        elif command == 'settings':
                            self.pause('设置已保存，返回游戏按 F8')
                            self.scheduler = CombatScheduler(value)
                        elif command == 'game-path':
                            path=Path(value)
                            if path.name.casefold()!='pbz.exe' or not path.is_file():
                                self.status='请选择实际的 PBZ.exe'
                                continue
                            self.pause('正在切换游戏连接')
                            if self.tactical:
                                self.tactical.stop_event.set()
                                self.tactical.join(timeout=.3)
                                self.tactical=None
                            if self.runtime:
                                self.runtime.close()
                                self.runtime=None
                            self.window=None
                            self.state=RuntimeState(reason='等待游戏')
                            self.path=str(path.resolve())
                            self.verified_signature=None
                            save_game_path(self.path)
                            self.plugin_info=inspect_autoguard(self.path)
                            self.last_plugin_check=time.monotonic()
                            self.status='正在连接所选游戏'
                        elif command == 'start':
                            if self.control_available:
                                self.pause('3 秒后开始，请切回游戏')
                                self.start_at = time.monotonic()+3
                            else:self.status=self.control_reason
                    now = time.monotonic()
                    if now-self.last_find >= 1:
                        self.connect(now)
                    if self.runtime:
                        if not self.runtime.component and now >= self.next_discover:
                            self.status = '正在寻找当前角色'
                            self.notify(now, True)
                            self.runtime.discover(self.stop_event.is_set)
                            self.next_discover = time.monotonic()+3
                        self.state = self.runtime.sample()
                        if self.state.valid:
                            if not self.tactical or not self.tactical.is_alive():
                                self.tactical = TacticalMonitor(self.runtime.memory.pid,self.path,self.runtime.npcs)
                                self.tactical.start()
                            self.tactical.request = (self.state.player,self.state.world,self.state.target,self.runtime.backpack)
                            observed = self.tactical.latest
                            if (observed.get('player')==self.state.player and observed.get('world')==self.state.world
                                    and 0 <= time.monotonic()-observed.get('at',0) <= .3):
                                self.state.tactics = observed
                    else:
                        self.state = RuntimeState(reason='等待游戏，请先启动影之刃零')
                        self.status = self.state.reason
                    if self.state.valid:
                        attributes = (self.state.player, self.state.hp, self.state.hp_max, self.state.medicine)
                        if attributes != self.last_attributes:
                            logging.info('STATE player=%s hp=%.1f/%.1f medicine=%s',hex(self.state.player),self.state.hp,self.state.hp_max,self.state.medicine)
                            self.last_attributes = attributes
                        if self.status in ('正在寻找当前角色','正在核对游戏版本','正在连接游戏'):
                            self.status = '已连接，返回游戏按 F8 开始'
                    now = time.monotonic()
                    sampled_at = now
                    if now-self.last_plugin_check > 2:
                        self.plugin_info = inspect_autoguard(self.path,
                            self.runtime.memory.started_at if self.runtime else None)
                        self.last_plugin_check = now
                    self.state.community_defense = self.plugin_info['enabled']
                    if self.session.enabled and self.plugin_info['enabled'] and self.plugin_info['status'] != 'started':
                        self.pause(self.plugin_info['message'])
                    self.defense_recorder.observe(now, self.state, self.scheduler.armed)
                    if 'toggle' in pressed:
                        self.toggle()
                    if self.start_at and now >= self.start_at:
                        self.start_at = None
                        self.toggle()
                    self.update_session(now)
                    lock=self.scheduler.config.lock_key
                    lock_vk=MOUSE[lock][2] if lock in MOUSE else KEYS.get(lock)
                    lock_down=bool(lock_vk and is_down(lock_vk))
                    if (self.control_available and lock_down and not self.lock_was_down
                            and lock not in self.sink.held):
                        self.scheduler.targeting.manual(now)
                    self.lock_was_down=lock_down
                    self.sink.pump(self.window,lambda: self.stop_event.is_set() or is_down(0x79) or is_down(0x1b) or is_down(9))
                    if self.scheduler.armed:
                        actions = self.scheduler.tick(now, bool(self.window and self.window.foreground()), self.state)
                        director=self.scheduler.director
                        combo_counts=(director.confirmed,director.completed,director.repairs)
                        if combo_counts!=self.last_combo_counts:
                            logging.info('COMBO confirmed=%s completed=%s repaired=%s actual=%s next=%s',
                                         *combo_counts,director.last['path'] if director.last else None,
                                         director.pending['expected'] if director.pending else None)
                            self.last_combo_counts=combo_counts
                        scheduler_stopped = not self.scheduler.armed
                        for action in actions:
                            if not self.control_available or not hotkeys or hotkeys.poll() or is_down(0x79) or self.stop_event.is_set():
                                self.pause('热键已暂停辅助')
                                break
                            if is_down(0x1b) or is_down(9):
                                self.session.menu_until=time.monotonic()+.35
                                self.suspend('菜单已打开，关闭后自动恢复')
                                break
                            input_started = time.monotonic()
                            if action in (('tap',self.scheduler.config.dodge_key),('down',self.scheduler.config.guard_key)):
                                cfg=self.scheduler.config
                                self.sink.release_keys((cfg.attack_key,cfg.heavy_key,cfg.skill_key,cfg.shadow_key,cfg.switch_key,cfg.lock_key))
                            self.sink.dispatch(action, self.window,
                                lambda: self.stop_event.is_set() or is_down(0x79) or is_down(0x1b) or is_down(9),nonblocking=True)
                            if action in (('tap',self.scheduler.config.dodge_key),('down',self.scheduler.config.guard_key)):
                                self.defense_recorder.input(input_started, self.state, action,
                                    time.monotonic()-input_started, input_started-sampled_at,
                                    self.scheduler.config.colored_dodge_lead)
                            logging.info('INPUT %s hp=%.1f/%.1f med=%s time=%.4f threats=%s',
                                         action, self.state.hp, self.state.hp_max, self.state.medicine,
                                         self.state.game_time, self.state.threats)
                            if action[0]=='tap' and self.scheduler.config.adaptive_enabled:
                                td=self.state.tactics
                                enemy=next((e for e in td.get('enemies',[]) if e['actor']==self.state.target),None)
                                logging.info('TACTICS strategy=%s nearby=%s target=%s art=%s',self.scheduler.strategy,
                                             sum(e['distance']<=7 for e in td.get('enemies',[])),enemy,td.get('skills',{}))
                        self.status = self.scheduler.status
                        if not self.scheduler.armed:
                            self.sink.release_all()
                            if self.session.enabled and scheduler_stopped:
                                if not self.window.foreground():
                                    self.session.clear_since=None
                                    self.status='已切出游戏，返回后自动恢复'
                                else:
                                    self.session.stop(self.scheduler.status)
                    elif not self.state.valid and self.start_at is None:
                        self.status = self.state.reason
                    if not self.control_available:self.status=self.control_reason
                    self.notify(now)
                    if now-self.heartbeat_at>10:
                        logging.info('HEARTBEAT armed=%s player=%s hp=%s medicine=%s',self.scheduler.armed,hex(self.state.player),self.state.hp,self.state.medicine)
                        self.heartbeat_at=now
                except InputSuspended:
                    if is_down(0x79) or self.stop_event.is_set():self.pause('已停止')
                    else:
                        self.session.clear_since=None
                        self.session.menu_until=time.monotonic()+.35
                        self.suspend('暂时挂起，回到游戏后自动恢复')
                except Exception as exc:
                    logging.exception('辅助已暂停')
                    self.pause('已暂停：'+str(exc))
                    self.notify(time.monotonic(), True)
                    self.stop_event.wait(1)
                interval = .01 if self.scheduler.armed and self.state.threats else .02
                self.stop_event.wait(max(.001, interval-(time.monotonic()-tick)))
        except Exception as exc:
            logging.exception('辅助线程退出')
            self.status = '辅助未启动：'+str(exc)
            self.notify(time.monotonic(), True)
        finally:
            if hotkeys:
                hotkeys.close()
            try:
                self.sink.release_all()
            finally:
                self.defense_recorder.close()
                if self.tactical:
                    self.tactical.stop_event.set()
                    self.tactical.join(timeout=.5)
                if self.runtime:
                    self.runtime.close()


class App(tk.Tk):
    def __init__(self, config, smoke=False, observe=False):
        super().__init__()
        if smoke:self.withdraw()
        self.title('PBZ 战斗助手 · 开发版 '+VERSION)
        icon=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'app.ico'
        if icon.exists():self.iconbitmap(str(icon))
        self.geometry('720x680')
        self.minsize(680, 640)
        self.config_obj = config
        self.events = queue.Queue()
        self.game_path=tk.StringVar(value=load_game_path())
        self.game_path_pending=False
        self.worker = Worker(config, self.events,path=self.game_path.get(),observe=observe)
        self.build_ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        if not smoke:
            self.worker.start()
        self.after(100, self.poll)

    def build_ui(self):
        from dashboard import build_dashboard
        self.key_choices = CHOICES
        build_dashboard(self)

    def save(self):
        try:
            config=CombatSettings(heal_enabled=self.heal.get(),guard_enabled=self.guard.get(),attack_enabled=self.attack.get(),execution_enabled=self.execution.get(),
                                  adaptive_enabled=self.adaptive.get(),skills_enabled=self.skills.get(),shadow_enabled=self.shadow.get(),
                                  projectile_dodge_enabled=self.projectile_dodge.get(),switch_enabled=self.switch.get(),
                                  autolock_enabled=self.autolock.get(),
                                  blue_dodge_enabled=self.blue_dodge.get(),red_dodge_enabled=self.red_dodge.get(),
                                  colored_dodge_lead=float(self.dodge_lead.get())/1000,
                                  threshold=float(self.threshold.get())/100,attack_period=float(self.period.get()),
                                  attack_distance=float(self.distance.get()),combo_preset=next(k for k,v in COMBO_NAMES.items() if v==self.combo.get()),**{k:v.get() for k,v in self.key_vars.items()})
            config.validate()
            DATA.mkdir(parents=True,exist_ok=True)
            tmp=SETTINGS.with_suffix('.tmp');tmp.write_text(json.dumps(asdict(config),ensure_ascii=False,indent=2),'utf-8');tmp.replace(SETTINGS)
            self.config_obj=config
            self.worker.commands.put(('settings',config))
            self.status.set('设置已保存')
            self.ui_dirty.set('')
            return True
        except (ValueError,OSError) as exc:
            messagebox.showerror('设置未保存',str(exc),parent=self)
            return False

    def choose_game(self):
        selected=filedialog.askopenfilename(parent=self,title='选择影之刃零主程序',
            filetypes=[('影之刃零主程序','PBZ.exe'),('所有文件','*.*')])
        if selected:
            self.game_path.set(selected)
            self.game_path_pending=True
            self.worker.commands.put(('game-path',selected))

    def start(self):
        if not self.worker.control_available:
            self.status.set(self.worker.control_reason)
            return
        if self.save():
            self.worker.commands.put(('start',None))

    def open_mod_manager(self):
        import os
        import webbrowser
        from community_mods import MANAGER_URL
        path = ROOT/'community'/'UE-MM'/'UEModManager.exe'
        if path.is_file():
            try:
                os.startfile(path)
            except OSError as exc:
                messagebox.showerror('模组管理器未能打开',str(exc),parent=self)
        else:
            webbrowser.open(MANAGER_URL)

    def poll(self):
        latest=None
        try:
            while True:latest=self.events.get_nowait()
        except queue.Empty:pass
        if latest:
            from dashboard import update_dashboard
            update_dashboard(self,latest)
        self.after(100,self.poll)

    def close(self):
        self.worker.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=.25)
        if self.worker.is_alive():
            self.status.set('正在停止并松开按键…');self.after(200,self.close)
        else:self.destroy()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--observe',action='store_true')
    args=parser.parse_args()
    dpi_aware();DATA.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(DATA/'combat.log',maxBytes=2_000_000,backupCount=2,encoding='utf-8')
    logging.basicConfig(handlers=[handler],level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    try:config=load_settings()
    except Exception as exc:
        config=CombatSettings();logging.exception('设置读取失败，使用默认值')
        messagebox.showwarning('设置读取失败',str(exc)+'；已恢复默认设置')
    app=App(config,smoke=args.smoke_test,observe=args.observe)
    if args.smoke_test:
        app.after(400,app.close)
    app.mainloop()


if __name__=='__main__':main()
