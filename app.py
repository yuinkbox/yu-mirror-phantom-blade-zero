"""PBZ keyboard/mouse assistant: calibrated vision, healing and basic combat."""
import argparse
from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageGrab, ImageTk
import numpy as np

from core import Config, Observation, Scheduler, crop_rect, fit_color, read_health, anchor_score
from windows_io import GameWindow, InputSink, Hotkeys, find_game, dpi_aware, is_down, CHOICES

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT/'data'
CONFIG_PATH = DATA/'settings.json'
ANCHOR_PATH = DATA/'hud-anchor.png'
VERSION = '0.1.0'


class Worker(threading.Thread):
    def __init__(self, config, outgoing):
        super().__init__(daemon=True)
        self.config = config
        self.outgoing = outgoing
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.scheduler = Scheduler(config)
        self.sink = InputSink()
        self.window = None
        self.anchor = None
        self.capture_at = None
        self.previous = {}
        self.last_find = 0
        self.last_status = ''
        self.reload_anchor()

    def reload_anchor(self):
        try:
            with Image.open(ANCHOR_PATH) as im:
                self.anchor = im.convert('RGB').copy()
        except (OSError, ValueError):
            self.anchor = None

    def notify(self, status, hp=None, hud=None):
        self.outgoing.put(('status', dict(status=status, hp=hp, hud=hud,
                                         armed=self.scheduler.armed, connected=self.window is not None)))
        if status != self.last_status:
            logging.info(status)
            self.last_status = status

    def pause(self, reason='已暂停'):
        self.scheduler.pause(reason)
        try:
            self.sink.release_all()
        except OSError as exc:
            logging.exception('按键释放失败')
            reason = '按键释放失败，请手动松开按键：'+str(exc)
        self.notify(reason)

    def edge(self, vk):
        down = is_down(vk)
        edge = down and not self.previous.get(vk, False)
        self.previous[vk] = down
        return edge

    def calibrated(self):
        cfg = self.config
        return bool(cfg.hp_rect and cfg.anchor_rect and cfg.color and self.anchor is not None)

    def toggle(self):
        if self.scheduler.armed:
            self.pause()
        elif not self.calibrated():
            self.notify('请先按 F6 抓取满血画面，并完成两步校准')
        elif not self.window or not self.window.foreground():
            self.notify('切回游戏画面后按 F8 开始')
        elif not any((self.config.heal_enabled, self.config.guard_enabled, self.config.attack_enabled)):
            self.notify('请至少选择一个辅助功能并保存')
        else:
            self.scheduler.arm(time.monotonic())
            self.notify('运行中')

    def capture(self):
        self.pause('已暂停，准备校准')
        if not self.window or not self.window.foreground():
            self.notify('抓取取消：请切回游戏，并在游戏中按 F6')
            return
        image = ImageGrab.grab(bbox=self.window.box(), all_screens=True).convert('RGB')
        if not self.window.foreground():
            self.notify('抓取期间窗口变化，请重新按 F6')
            return
        if np.asarray(image).std() < 4:
            self.notify('画面抓取为空，请将游戏改为无边框或窗口模式后重试')
            return
        self.outgoing.put(('capture', image))

    def observe(self, now):
        if not self.window or not self.window.foreground():
            return Observation(now, False, False, None)
        cfg = self.config
        box = self.window.box()
        w, h = box[2]-box[0], box[3]-box[1]
        if abs(w/h-cfg.calibration_aspect) > .025:
            return Observation(now, True, False, None)
        a, b = cfg.hp_rect, cfg.anchor_rect
        union = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
        left, top = round(union[0]*w), round(union[1]*h)
        right, bottom = round(union[2]*w), round(union[3]*h)
        frame = ImageGrab.grab(bbox=(box[0]+left, box[1]+top, box[0]+right, box[1]+bottom), all_screens=True).convert('RGB')
        def region(r):
            return frame.crop((round(r[0]*w)-left, round(r[1]*h)-top,
                               round(r[2]*w)-left, round(r[3]*h)-top))
        score = anchor_score(region(b), self.anchor)
        hp = read_health(region(a), cfg.color) if score >= .68 else None
        return Observation(time.monotonic(), self.window.foreground(), score >= .68, hp)

    def run(self):
        hotkeys = None
        try:
            hotkeys = Hotkeys()
            while not self.stop_event.is_set():
                tick_start = time.monotonic()
                try:
                    while True:
                        command, value = self.commands.get_nowait()
                        if command == 'config':
                            self.pause('设置已更新，回到游戏按 F8 开始')
                            self.config = value
                            self.scheduler = Scheduler(value)
                            self.reload_anchor()
                            self.window = None
                        elif command == 'pause':
                            self.pause()
                        elif command == 'capture':
                            self.pause('3 秒后抓取，请切回游戏')
                            self.capture_at = time.monotonic()+3
                        elif command == 'toggle':
                            self.toggle()
                except queue.Empty:
                    pass
                try:
                    now = time.monotonic()
                    if now-self.last_find > 1:
                        self.window = find_game(self.config.game_exe)
                        self.last_find = now
                    pressed = hotkeys.poll()
                    if 'stop' in pressed:
                        self.capture_at = None
                        self.pause('F10 已停止，并松开辅助按键')
                    for key in ([] if 'stop' in pressed else pressed):
                        if key == 'capture':
                            self.capture_at = None
                            self.capture()
                        elif key == 'toggle':
                            self.toggle()
                    if self.edge(0x1b) and self.scheduler.armed:
                        self.pause('按下 Esc，已暂停；关闭菜单后按 F8')
                    if self.capture_at and now >= self.capture_at:
                        self.capture_at = None
                        self.capture()
                    if self.scheduler.armed:
                        obs = self.observe(now)
                        for action in self.scheduler.tick(obs):
                            late_keys = hotkeys.poll()
                            if late_keys:
                                if 'stop' in late_keys:
                                    self.capture_at = None
                                    self.pause('F10 已停止，并松开辅助按键')
                                elif 'capture' in late_keys:
                                    self.capture_at = None
                                    self.capture()
                                else:
                                    self.pause('F8 已暂停')
                                break
                            # Recheck stop and foreground immediately before every input.
                            if is_down(0x79) or is_down(0x1b):
                                self.pause('热键已停止辅助')
                                break
                            self.sink.dispatch(action, self.window,
                                               lambda: self.stop_event.is_set() or is_down(0x79) or is_down(0x1b))
                        if not self.scheduler.armed:
                            self.sink.release_all()
                        self.notify(self.scheduler.status, obs.hp, obs.hud)
                    elif self.calibrated() and self.window and self.window.foreground():
                        obs = self.observe(now)
                        self.notify(self.scheduler.status, obs.hp, obs.hud)
                    elif self.last_status == '' or not self.window:
                        self.notify('等待游戏窗口' if not self.window else '已暂停')
                except Exception as exc:
                    logging.exception('运行中断')
                    try:
                        self.pause('已暂停：'+str(exc))
                    except OSError:
                        logging.exception('按键释放失败')
                        self.notify('按键释放失败，请手动松开按键并退出辅助')
                if not self.scheduler.armed and self.sink.held:
                    self.pause('已暂停，正在释放辅助按键')
                elapsed = time.monotonic()-tick_start
                self.stop_event.wait(max(.01, .10-elapsed))
        except Exception as exc:
            logging.exception('辅助线程无法启动')
            self.notify('辅助未启动：'+str(exc))
        finally:
            if hotkeys:
                hotkeys.close()
            try:
                self.sink.release_all()
            except OSError:
                logging.exception('退出时释放按键失败')


class Calibration(tk.Toplevel):
    def __init__(self, parent, image, done):
        super().__init__(parent)
        self.title('两步校准 · 在满血画面上框选')
        self.image = image
        self.done = done
        self.rects = []
        self.start = None
        self.rectangle_id = None
        max_w, max_h = self.winfo_screenwidth()-100, self.winfo_screenheight()-220
        self.scale = min(1., max_w/image.width, max_h/image.height)
        self.preview = image.resize((round(image.width*self.scale), round(image.height*self.scale)))
        self.photo = ImageTk.PhotoImage(self.preview)
        self.label = ttk.Label(self, text='第 1 步：拖框选中完整血条内部的细横条。必须满血，避开边框、数值和图案。', wraplength=self.preview.width)
        self.label.pack(padx=14, pady=12, anchor='w')
        self.canvas = tk.Canvas(self, width=self.preview.width, height=self.preview.height, highlightthickness=0, cursor='crosshair')
        self.canvas.pack(padx=12)
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw')
        self.canvas.bind('<ButtonPress-1>', self.begin)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.finish)
        buttons = ttk.Frame(self)
        buttons.pack(fill='x', padx=14, pady=12)
        ttk.Button(buttons, text='重新框选', command=self.reset).pack(side='left')
        self.confirm = ttk.Button(buttons, text='保存校准', command=self.commit, state='disabled')
        self.confirm.pack(side='right')
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='right', padx=10)
        self.transient(parent)
        self.grab_set()
        self.focus_set()

    def begin(self, event):
        if len(self.rects) >= 2:
            return
        self.start = (event.x, event.y)
        self.rectangle_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline='#45e5bd', width=2)

    def drag(self, event):
        if self.start:
            self.canvas.coords(self.rectangle_id, *self.start, event.x, event.y)

    def finish(self, event):
        if self.start is None:
            return
        x, y = self.start
        self.start = None
        w, h = self.preview.size
        rect = [max(0, min(x, event.x))/w, max(0, min(y, event.y))/h,
                min(w, max(x, event.x))/w, min(h, max(y, event.y))/h]
        try:
            selected = crop_rect(self.image, rect)
            if not self.rects:
                self.profile = fit_color(selected)
            elif selected.width < 12 or selected.height < 8 or np.asarray(selected).std() < 5:
                raise ValueError('请选择至少 12×8 像素、有图案的固定界面标识')
        except ValueError as exc:
            self.canvas.delete(self.rectangle_id)
            messagebox.showerror('请重新框选', str(exc), parent=self)
            return
        self.rects.append(rect)
        if len(self.rects) == 1:
            self.label.configure(text='第 2 步：框选血条旁固定不变的图标或边框。避开血量填充、数字、动画和场景背景，用于识别界面是否可见。')
        else:
            self.label.configure(text='校准完成。保存后先观察血量是否正确，再回到游戏按 F8。')
            self.confirm.configure(state='normal')

    def reset(self):
        self.rects.clear()
        self.start = None
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw')
        self.confirm.configure(state='disabled')
        self.label.configure(text='第 1 步：满血时框选完整血条内部的细横条，避开边框、数字和图案。')

    def commit(self):
        if len(self.rects) != 2:
            return
        self.done(self.rects, self.profile, crop_rect(self.image, self.rects[1]), self.image.width/self.image.height)
        self.destroy()


class App(tk.Tk):
    def __init__(self, config, smoke=False):
        super().__init__()
        self.title('PBZ 战斗助手 · '+VERSION)
        self.geometry(f'760x{min(810, self.winfo_screenheight()-90)}')
        self.minsize(700, min(700, self.winfo_screenheight()-90))
        self.config_obj = config
        self.events = queue.Queue()
        self.worker = Worker(config, self.events)
        self.smoke = smoke
        self.build_ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        if not smoke:
            self.worker.start()
        self.after(100, self.poll)

    def build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'))
        style.configure('Note.TLabel', foreground='#596878')
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 12, 'bold'), foreground='#176759')
        outer = ttk.Frame(self)
        outer.pack(fill='both', expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, background=style.lookup('TFrame', 'background'))
        scroll = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        frame = ttk.Frame(canvas, padding=22)
        content = canvas.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content, width=event.width))
        self.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-int(event.delta/120), 'units'))
        ttk.Label(frame, text='PBZ 战斗助手', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='自动喝药 · 基础格挡 · 轻攻击循环', style='Note.TLabel').pack(anchor='w', pady=(3, 14))
        self.status_var = tk.StringVar(value='等待游戏窗口；请先完成一次满血校准')
        ttk.Label(frame, textvariable=self.status_var, style='Status.TLabel', wraplength=670).pack(anchor='w', pady=6)
        health_row = ttk.Frame(frame)
        health_row.pack(fill='x', pady=(8, 14))
        self.hp_var = tk.StringVar(value='血量：—')
        ttk.Label(health_row, textvariable=self.hp_var, width=15).pack(side='left')
        self.health_bar = ttk.Progressbar(health_row, maximum=100)
        self.health_bar.pack(side='left', fill='x', expand=True)
        self.hud_var = tk.StringVar(value='界面：未校准')
        ttk.Label(health_row, textvariable=self.hud_var, width=19, anchor='e').pack(side='left')

        features = ttk.LabelFrame(frame, text='辅助功能', padding=12)
        features.pack(fill='x', pady=5)
        cfg = self.config_obj
        self.heal = tk.BooleanVar(value=cfg.heal_enabled)
        self.guard = tk.BooleanVar(value=cfg.guard_enabled)
        self.attack = tk.BooleanVar(value=cfg.attack_enabled)
        ttk.Checkbutton(features, text='自动喝药：血量低于阈值时使用恢复药', variable=self.heal).grid(row=0, column=0, sticky='w', pady=3)
        self.threshold = tk.StringVar(value=str(round(cfg.threshold*100)))
        ttk.Spinbox(features, from_=5, to=90, textvariable=self.threshold, width=5).grid(row=0, column=1, padx=8)
        ttk.Label(features, text='%').grid(row=0, column=2)
        ttk.Checkbutton(features, text='基础格挡：空闲时持续按住格挡键', variable=self.guard).grid(row=1, column=0, sticky='w', pady=3)
        ttk.Checkbutton(features, text='轻攻击循环：按设定节奏攻击', variable=self.attack).grid(row=2, column=0, sticky='w', pady=3)
        ttk.Label(features, text='基础版本由你负责走位与目标；格挡不识别红眼招式，攻击不识别敌人。', style='Note.TLabel', wraplength=620).grid(row=3, column=0, columnspan=3, sticky='w', pady=(7, 0))

        keys = ttk.LabelFrame(frame, text='按键与节奏', padding=12)
        keys.pack(fill='x', pady=8)
        self.bind_vars = {}
        for column, (label, attr) in enumerate([('喝药', 'heal_key'), ('格挡', 'guard_key'), ('轻攻击', 'attack_key')]):
            ttk.Label(keys, text=label).grid(row=0, column=column*2, sticky='w', padx=(0, 6))
            var = tk.StringVar(value=getattr(cfg, attr))
            self.bind_vars[attr] = var
            ttk.Combobox(keys, textvariable=var, values=CHOICES, width=11, state='readonly').grid(row=0, column=column*2+1, padx=(0, 12))
        ttk.Label(keys, text='恢复药放在 R 对应药槽；如装在上方药槽，将喝药键改为 T。以游戏设置为准。', style='Note.TLabel', wraplength=620).grid(row=1, column=0, columnspan=6, sticky='w', pady=(8, 10))
        self.period = tk.StringVar(value=str(cfg.attack_period))
        self.attack_lock = tk.StringVar(value=str(cfg.attack_lock))
        ttk.Label(keys, text='攻击间隔').grid(row=2, column=0)
        ttk.Spinbox(keys, from_=.8, to=10, increment=.1, textvariable=self.period, width=8).grid(row=2, column=1, sticky='w')
        ttk.Label(keys, text='攻击后等待').grid(row=2, column=2)
        ttk.Spinbox(keys, from_=.15, to=5, increment=.05, textvariable=self.attack_lock, width=8).grid(row=2, column=3, sticky='w')
        ttk.Label(keys, text='秒', style='Note.TLabel').grid(row=2, column=4)

        target = ttk.Frame(frame)
        target.pack(fill='x', pady=6)
        ttk.Label(target, text='游戏主程序').pack(side='left')
        self.game_path = tk.StringVar(value=cfg.game_exe)
        ttk.Entry(target, textvariable=self.game_path).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(target, text='选择', command=self.choose_game).pack(side='right')
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(12, 8))
        ttk.Button(buttons, text='保存设置', command=self.save).pack(side='left')
        ttk.Button(buttons, text='3 秒后抓取校准', command=self.capture).pack(side='left', padx=10)
        ttk.Button(buttons, text='暂停并松键', command=lambda: self.worker.commands.put(('pause', None))).pack(side='right')
        ttk.Label(frame, text='F6  满血画面校准     F8  开始 / 暂停     F10  立即停止\nEsc、切出游戏或界面标识消失时会暂停；返回游戏后需再按 F8。', wraplength=670).pack(anchor='w', pady=10)
        self.calibration_var = tk.StringVar(value='已保存校准' if cfg.color and ANCHOR_PATH.exists() else '未校准：满血时切回游戏按 F6，然后依次框选血条和固定图标。')
        ttk.Label(frame, textvariable=self.calibration_var, style='Note.TLabel', wraplength=670).pack(anchor='w')
        ttk.Label(frame, text='首次只开自动喝药，确认血量读数正确后再开启格挡和攻击。', style='Note.TLabel', wraplength=670).pack(anchor='w', pady=(8, 0))

    def choose_game(self):
        value = filedialog.askopenfilename(title='选择 platform/steam 下的 PBZ.exe', filetypes=[('游戏主程序', '*.exe')])
        if value:
            self.game_path.set(value)

    def read_config(self):
        cfg = replace(self.config_obj, game_exe=self.game_path.get().strip(), heal_enabled=self.heal.get(),
                      guard_enabled=self.guard.get(), attack_enabled=self.attack.get(),
                      threshold=float(self.threshold.get())/100, attack_period=float(self.period.get()),
                      attack_lock=float(self.attack_lock.get()), **{k: v.get() for k, v in self.bind_vars.items()})
        cfg.validate()
        if not Path(cfg.game_exe).is_file() or Path(cfg.game_exe).name.casefold() != 'pbz.exe':
            raise ValueError('请选择游戏实际主程序 platform/steam/PBZ.exe')
        return cfg

    def save(self):
        self.worker.commands.put(('pause', None))
        try:
            cfg = self.read_config()
            cfg.save(CONFIG_PATH)
            self.config_obj = cfg
            self.worker.commands.put(('config', cfg))
            self.status_var.set('已保存，切回游戏后按 F8')
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror('设置未保存', str(exc), parent=self)
            return False

    def capture(self):
        if self.save():
            self.worker.commands.put(('capture', None))

    def calibrated(self, rects, profile, anchor, aspect):
        cfg = replace(self.config_obj, hp_rect=rects[0], anchor_rect=rects[1], color=profile, calibration_aspect=aspect)
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            temporary = ANCHOR_PATH.with_suffix('.tmp.png')
            anchor.save(temporary)
            temporary.replace(ANCHOR_PATH)
            cfg.save(CONFIG_PATH)
            self.config_obj = cfg
            self.worker.commands.put(('config', cfg))
            self.calibration_var.set('校准已保存。回到游戏观察读数；确认恢复药槽后按 F8 开始。')
        except OSError as exc:
            messagebox.showerror('校准未保存', str(exc), parent=self)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'capture':
                    self.deiconify()
                    self.lift()
                    Calibration(self, value, self.calibrated)
                elif kind == 'status':
                    self.status_var.set(value['status'])
                    hp = value['hp']
                    self.hp_var.set('血量：—' if hp is None else f'血量：{hp:.0%}')
                    self.health_bar['value'] = 0 if hp is None else hp*100
                    self.hud_var.set('界面：可见' if value['hud'] else '界面：未确认')
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def close(self):
        self.worker.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=3)
        if self.worker.is_alive():
            self.status_var.set('正在停止，请稍候…')
            self.after(250, self.close)
            return
        self.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    dpi_aware()
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA/'assistant.log', level=logging.INFO, encoding='utf-8',
                        format='%(asctime)s %(levelname)s %(message)s')
    config_error = None
    try:
        config = Config.load(CONFIG_PATH)
    except (ValueError, OSError, TypeError) as exc:
        config_error = str(exc)
        config = Config()
    if args.diagnose:
        from windows_io import INPUT, KEYBDINPUT, MOUSEINPUT
        import ctypes
        win = find_game(config.game_exe)
        print(json.dumps({'version': VERSION, 'game_window_found': bool(win),
                          'input_struct_size': ctypes.sizeof(INPUT), 'keyboard_struct_size': ctypes.sizeof(KEYBDINPUT),
                          'mouse_struct_size': ctypes.sizeof(MOUSEINPUT), 'config_error': config_error,
                          'calibrated': bool(config.color and ANCHOR_PATH.exists())}, ensure_ascii=False))
        return
    mutex = None
    if not args.smoke_test:
        import ctypes as C
        from windows_io import k
        k.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        k.CreateMutexW.restype = C.c_void_p
        mutex = k.CreateMutexW(None, False, 'Local\\PBZAssistantKeyboardMouse')
        if not mutex:
            raise OSError('无法创建辅助实例')
        if C.get_last_error() == 183:
            k.CloseHandle(mutex)
            prompt = tk.Tk(); prompt.withdraw()
            messagebox.showinfo('PBZ 战斗助手', '辅助已经在运行，请使用已有窗口。', parent=prompt)
            prompt.destroy()
            return
    try:
        app = App(config, smoke=args.smoke_test)
        if args.smoke_test:
            app.update()
            result = {'version': VERSION, 'window_created': app.winfo_exists() == 1,
                      'window_size': [app.winfo_width(), app.winfo_height()], 'worker_started': app.worker.is_alive()}
            (DATA/'ui-smoke-test.json').write_text(json.dumps(result, indent=2), 'utf-8')
            app.destroy()
        else:
            if config_error:
                messagebox.showwarning('配置需要重新保存', '原配置无法读取，已使用默认值。\n'+config_error, parent=app)
            app.mainloop()
    finally:
        if mutex:
            k.CloseHandle(mutex)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('程序异常')
        raise
