"""Compact neutral dashboard. Rendering only; combat remains in the worker."""
import tkinter as tk
from tkinter import ttk
import webbrowser
from combat import COMBO_NAMES, COMBOS
from ui_components import BG, SURFACE, INPUT, BORDER, TEXT, MUTED, DIM, GREEN, AMBER
from ui_components import label, font, Panel, Button, Switch, Meter, Tabs, Scroller, Tooltip


def build_dashboard(app):
    app.configure(bg=BG)
    app.geometry(f'1000x{min(800,app.winfo_screenheight()-90)}')
    app.minsize(860,650)
    style=ttk.Style(app);style.theme_use('clam')
    style.configure('.',font=font(13),background=SURFACE,foreground=TEXT)
    for kind in ('TCombobox','TSpinbox'):
        style.configure(kind,fieldbackground=INPUT,background=INPUT,foreground=TEXT,
                        arrowcolor=MUTED,bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,
                        borderwidth=1,padding=8,selectbackground=BORDER,selectforeground=TEXT)
        style.map(kind,fieldbackground=[('readonly',INPUT),('disabled',SURFACE)],
                  foreground=[('disabled',DIM),('readonly',TEXT)],
                  selectbackground=[('readonly',INPUT)],selectforeground=[('readonly',TEXT)])
    app.option_add('*TCombobox*Listbox.background',INPUT)
    app.option_add('*TCombobox*Listbox.foreground',TEXT)
    app.option_add('*TCombobox*Listbox.selectBackground',BORDER)
    app.option_add('*TCombobox*Listbox.font',font(13))

    header=tk.Frame(app,bg=BG,padx=30,pady=22);header.pack(fill='x')
    brand=tk.Frame(header,bg=BG);brand.pack(side='left')
    mark=tk.Canvas(brand,width=32,height=32,bg=BG,highlightthickness=0);mark.pack(side='left',padx=(0,12))
    mark.create_line(8,5,25,22,fill=TEXT,width=2)
    mark.create_line(24,5,7,22,fill=TEXT,width=2)
    mark.create_line(4,19,12,27,fill=TEXT,width=2)
    mark.create_line(20,27,28,19,fill=TEXT,width=2)
    label(brand,'PBZ',21,bold=True).pack(side='left')
    label(brand,'战斗助手 · 开发版',15,MUTED).pack(side='left',padx=(12,0))
    app.mode_badge=label(header,'●  未连接',12,MUTED,padx=12,pady=5)
    app.mode_badge.pack(side='right')
    tk.Frame(app,bg=BORDER,height=1).pack(fill='x')

    footer=tk.Frame(app,bg=BG,padx=28,pady=18);footer.pack(side='bottom',fill='x')
    separator=tk.Frame(app,bg=BORDER,height=1);separator.pack(side='bottom',fill='x')
    app.ui_dirty=tk.StringVar(value='')
    Button(footer,'保存设置',app.save,width=106).pack(side='left')
    label(footer,textvariable=app.ui_dirty,size=12,color=AMBER).pack(side='left',padx=12)
    app.start_button=Button(footer,'3 秒后开始',app.start,width=166,primary=True,shortcut='F8')
    app.start_button.pack(side='right')
    app.stop_button=Button(footer,'停止',lambda:app.worker.commands.put(('pause',None)),width=116,shortcut='F10')
    app.stop_button.pack(side='right',padx=(0,10))

    navigation=tk.Frame(app,bg=BG,padx=28,pady=18);navigation.pack(fill='x')
    pages=tk.Frame(app,bg=BG);pages.pack(fill='both',expand=True)
    app.pages={};app.active_page='战斗'
    for name in ('战斗','键位','高级'):
        app.pages[name]=Scroller(pages)
    def show(name):
        for page in app.pages.values():page.pack_forget()
        app.pages[name].pack(fill='both',expand=True);app.active_page=name
    app.tabs=Tabs(navigation,('战斗','键位','高级'),show);app.tabs.pack(side='left')
    app.weapon_text=tk.StringVar(value='等待角色')
    label(navigation,textvariable=app.weapon_text,size=13,color=MUTED).pack(side='right')
    app.bind('<MouseWheel>',lambda e:app.pages[app.active_page].scroll(e))
    show('战斗')

    cfg=app.config_obj
    for attr,field in [('heal','heal_enabled'),('guard','guard_enabled'),('attack','attack_enabled'),
            ('execution','execution_enabled'),('blue_dodge','blue_dodge_enabled'),('red_dodge','red_dodge_enabled'),
            ('adaptive','adaptive_enabled'),('skills','skills_enabled'),('projectile_dodge','projectile_dodge_enabled'),
            ('switch','switch_enabled'),('autolock','autolock_enabled'),('shadow','shadow_enabled')]:
        setattr(app,attr,tk.BooleanVar(value=getattr(cfg,field)))
    for attr,value in [('dodge_lead',round(cfg.colored_dodge_lead*1000)),('period',cfg.attack_period),
                       ('distance',cfg.attack_distance),('threshold',round(cfg.threshold*100))]:
        setattr(app,attr,tk.StringVar(value=str(value)))
    app.combo=tk.StringVar(value=COMBO_NAMES[cfg.combo_preset])
    app.switch_widgets={}

    def panel(parent,title):
        p=Panel(parent)
        label(p.body,title,14,bold=True).pack(anchor='w',pady=(0,12))
        return p

    def toggle(parent,title,attr,hint=''):
        row=tk.Frame(parent,bg=SURFACE,height=42);row.pack(fill='x');row.pack_propagate(False)
        name=label(row,title,13);name.pack(side='left')
        control=Switch(row,getattr(app,attr));control.pack(side='right')
        app.switch_widgets[attr]=control
        name.bind('<Button-1>',control.toggle)
        if hint:Tooltip(control,hint)
        return control

    def divider(parent):tk.Frame(parent,bg=BORDER,height=1).pack(fill='x',pady=8)
    def field(parent,title,var,lo,hi,inc,unit):
        row=tk.Frame(parent,bg=SURFACE);row.pack(fill='x',pady=7)
        label(row,title,13).pack(side='left')
        label(row,unit,12,MUTED,width=4,anchor='e').pack(side='right',padx=(6,0))
        box=ttk.Spinbox(row,textvariable=var,from_=lo,to=hi,increment=inc,width=6,justify='center')
        box.pack(side='right');return box

    main=app.pages['战斗'].body
    metrics=tk.Frame(main,bg=BG);metrics.pack(fill='x',pady=(0,14))
    app.health_text=tk.StringVar(value='— / —');app.shachi_text=tk.StringVar(value='— / —')
    app.medicine_text=tk.StringVar(value='—');app.charge_text=tk.StringVar(value='—')
    for i,(title,var,weight) in enumerate([('生命',app.health_text,2),('杀气',app.shachi_text,2),
                                         ('恢复药',app.medicine_text,1),('武技充能',app.charge_text,1)]):
        metrics.columnconfigure(i,weight=weight,uniform='metrics')
        p=Panel(metrics,padding=16);p.grid(row=0,column=i,sticky='nsew',padx=(0,12) if i<3 else 0)
        label(p.body,title,12,MUTED).pack(anchor='w')
        label(p.body,textvariable=var,size=25,bold=True).pack(anchor='w',pady=(9,10))
        if i<2:
            meter=Meter(p.body);meter.pack(fill='x')
            if i==0:app.hp_bar=meter
            else:app.shachi_bar=meter
        else:tk.Frame(p.body,bg=SURFACE,height=4).pack(fill='x')
    target_line=tk.Frame(main,bg=BG);target_line.pack(fill='x',pady=(0,14))
    app.target=tk.StringVar(value='未锁定')
    app.tactical_text=tk.StringVar(value='附近敌人 —')
    label(target_line,textvariable=app.target,size=13,color=MUTED).pack(side='left')
    label(target_line,textvariable=app.tactical_text,size=13,color=MUTED).pack(side='right')

    controls=tk.Frame(main,bg=BG);controls.pack(fill='x')
    controls.columnconfigure(0,weight=1,uniform='features');controls.columnconfigure(1,weight=1,uniform='features')
    offense=panel(controls,'自动战斗');offense.grid(row=0,column=0,sticky='nsew',padx=(0,7))
    defense=panel(controls,'防御与恢复');defense.grid(row=0,column=1,sticky='nsew',padx=(7,0))
    for title,attr in [('自动连招','attack'),('战况自适应','adaptive'),('智能武技','skills'),
                       ('影之武装 · 破盾','shadow'),
                       ('低杀气换刀','switch'),('自动锁敌 / 转火','autolock'),('自动处决','execution')]:
        toggle(offense.body,title,attr,'需要自动连招与战况自适应' if attr in ('skills','shadow','switch','autolock') else '')
    for title,attr in [('自动防御','guard'),('蓝光应对','blue_dodge'),('红光闪避','red_dodge'),
                       ('投掷物闪避','projectile_dodge'),('自动喝药','heal')]:
        toggle(defense.body,title,attr,'需要开启自动防御' if attr in ('blue_dodge','red_dodge','projectile_dodge') else '')
    divider(offense.body);field(offense.body,'攻击距离',app.distance,1,8,.5,'米')
    divider(defense.body);field(defense.body,'喝药阈值',app.threshold,5,90,5,'%')

    app.status=tk.StringVar(value='正在连接游戏…')
    app.strategy_text=tk.StringVar(value='')
    statusbox=tk.Frame(main,bg=BG,pady=16);statusbox.pack(fill='x')
    label(statusbox,textvariable=app.status,size=13,color=MUTED,anchor='w',justify='left',wraplength=830).pack(fill='x')
    app.status_label=statusbox.winfo_children()[-1]
    statusbox.bind('<Configure>',lambda e:app.status_label.configure(wraplength=max(100,e.width-10)))

    keys=app.pages['键位'].body
    label(keys,'键位设置',19,bold=True).pack(anchor='w',pady=(4,18))
    key_panel=Panel(keys,padding=20);key_panel.pack(fill='x')
    grid=key_panel.body;app.key_vars={}
    for i,(caption,attr) in enumerate([('轻击','attack_key'),('杀气攻击','heavy_key'),('格挡','guard_key'),
            ('闪避','dodge_key'),('恢复药','heal_key'),('处决','execution_key'),('武技','skill_key'),('影之武装','shadow_key'),('换刀','switch_key'),('锁定','lock_key')]):
        r,c=divmod(i,2);cell=tk.Frame(grid,bg=SURFACE)
        cell.grid(row=r,column=c,sticky='ew',padx=(0,24) if c==0 else (24,0),pady=12)
        label(cell,caption,13).pack(side='left')
        var=tk.StringVar(value=getattr(cfg,attr));app.key_vars[attr]=var
        ttk.Combobox(cell,textvariable=var,values=app.key_choices,state='readonly',width=12).pack(side='right')
    grid.columnconfigure(0,weight=1,uniform='keys');grid.columnconfigure(1,weight=1,uniform='keys')
    shortcuts=tk.Frame(keys,bg=BG,pady=20);shortcuts.pack(fill='x')
    label(shortcuts,'F8  开始 / 暂停',13,MUTED).pack(side='left')
    label(shortcuts,'F10  停止',13,MUTED).pack(side='left',padx=30)

    advanced=app.pages['高级'].body
    label(advanced,'高级设置',19,bold=True).pack(anchor='w',pady=(4,18))
    connection=panel(advanced,'游戏连接');connection.pack(fill='x',pady=(0,14))
    path_row=tk.Frame(connection.body,bg=SURFACE);path_row.pack(fill='x')
    ttk.Entry(path_row,textvariable=app.game_path,state='readonly').pack(side='left',fill='x',expand=True,padx=(0,10))
    Button(path_row,'选择游戏',app.choose_game,width=106).pack(side='right')
    timing=panel(advanced,'节奏与连招');timing.pack(fill='x',pady=(0,14))
    field(timing.body,'蓝红光提前量',app.dodge_lead,40,220,10,'ms')
    period=field(timing.body,'固定连招间隔',app.period,.25,3,.05,'秒')
    row=tk.Frame(timing.body,bg=SURFACE);row.pack(fill='x',pady=7)
    label(row,'备用连招',13).pack(side='left')
    combo=ttk.Combobox(row,textvariable=app.combo,values=list(COMBO_NAMES.values()),state='readonly',width=14)
    combo.pack(side='right')
    Tooltip(combo,'关闭战况自适应时使用');Tooltip(period,'仅用于固定连招')
    app.combo_preview=tk.StringVar(value='')
    def preview(*_):
        key=next((k for k,v in COMBO_NAMES.items() if v==app.combo.get()),'balanced')
        app.combo_preview.set(' → '.join('重' if k=='heavy' else '轻' for k,_ in COMBOS[key]))
    app.combo.trace_add('write',preview);preview()
    label(timing.body,textvariable=app.combo_preview,size=12,color=DIM).pack(anchor='e',pady=(6,0))

    community=panel(advanced,'社区插件');community.pack(fill='x',pady=(0,14))
    row=tk.Frame(community.body,bg=SURFACE);row.pack(fill='x')
    label(row,'PBZAutoGuard',13).pack(side='left')
    app.community_status=tk.StringVar(value='未启用')
    label(row,textvariable=app.community_status,size=12,color=MUTED).pack(side='right')
    links=tk.Frame(community.body,bg=SURFACE);links.pack(anchor='w',pady=(14,0))
    from community_mods import AUTOGUARD_URL
    Button(links,'作者页面',lambda:webbrowser.open(AUTOGUARD_URL),width=106).pack(side='left',padx=(0,8))
    Button(links,'模组管理器',app.open_mod_manager,width=116).pack(side='left')
    Tooltip(links,'插件启用时接管防御；插件开关在游戏内 F5')

    diagnostics=panel(advanced,'本次运行');diagnostics.pack(fill='x',pady=(0,18))
    app.details=tk.StringVar(value='');app.skill_text=tk.StringVar(value='武技未就绪')
    app.shadow_text=tk.StringVar(value='影之武装未就绪')
    app.combo_status=tk.StringVar(value='连段 0 · 校正 0')
    app.catalog_text=tk.StringVar(value='');app.enhancement_text=tk.StringVar(value='')
    app.counts=tk.StringVar(value='暂无动作')
    app.raw_status=tk.StringVar(value='')
    for var in (app.skill_text,app.shadow_text,app.catalog_text,app.enhancement_text,app.combo_status,app.counts,app.strategy_text,app.raw_status):
        l=label(diagnostics.body,textvariable=var,size=12,color=MUTED,wraplength=790,justify='left',anchor='w')
        l.pack(fill='x',pady=4)
        l.bind('<Configure>',lambda e:e.widget.configure(wraplength=max(80,e.width-8)))

    def dependencies(*_):
        for attr in ('skills','shadow','switch','autolock'):
            app.switch_widgets[attr].set_enabled(app.attack.get() and app.adaptive.get())
        for attr in ('blue_dodge','red_dodge','projectile_dodge'):
            app.switch_widgets[attr].set_enabled(app.guard.get())
        combo.configure(state='disabled' if app.adaptive.get() else 'readonly')
        period.configure(state='disabled' if app.adaptive.get() else 'normal')
    for attr in ('attack','adaptive','guard'):getattr(app,attr).trace_add('write',dependencies)
    dependencies()
    settings=[getattr(app,a) for a in ('heal','guard','attack','execution','blue_dodge','red_dodge','adaptive',
        'skills','shadow','projectile_dodge','switch','autolock','dodge_lead','period','distance','threshold','combo')]+list(app.key_vars.values())
    for var in settings:var.trace_add('write',lambda *_:app.ui_dirty.set('未保存'))


def update_dashboard(app,latest):
    s=latest['state'];raw=latest['status'];app.raw_status.set(raw)
    if not app.game_path_pending or latest.get('game_path')==app.game_path.get():
        app.game_path.set(latest.get('game_path',app.game_path.get()))
        app.game_path_pending=False
    status={'已连接，返回游戏按 F8 开始':'等待开始','设置已保存，返回游戏按 F8':'设置已保存',
            '等待锁定目标':'等待锁定','运行中，等待战斗信号':'等待战斗','等待最新战况，防御与恢复继续监测':'正在读取战况',
            '确认实际招式，防御继续监测':'连招进行中','等待上一招生效，防御继续监测':'连招进行中',
            '目标超出攻击距离':'目标距离较远','已自动恢复，监测战斗状态':'已恢复'}.get(raw,raw)
    app.status.set(status)
    enabled=latest.get('enabled',False);armed=latest.get('armed',False)
    mode='运行中' if armed else '已挂起' if enabled else '已暂停' if s.valid else '未连接'
    available=latest.get('control_available',True)
    if not available:mode='只读观察'
    app.start_button.set_enabled(available)
    app.mode_badge.configure(text='●  '+mode,fg=GREEN if armed else AMBER if enabled else MUTED)
    app.health_text.set(f'{s.hp:.0f} / {s.hp_max:.0f}' if s.valid else '— / —')
    app.shachi_text.set(f'{s.shachi:.0f} / {s.shachi_max:.0f}' if s.valid else '— / —')
    app.medicine_text.set(str(s.medicine) if s.valid and s.medicine is not None else '—')
    app.hp_bar.set(100*s.hp/s.hp_max if s.valid and s.hp_max else 0)
    app.shachi_bar.set(100*s.shachi/s.shachi_max if s.valid and s.shachi_max else 0)
    tactical=s.tactics;enemies=tactical.get('enemies',[])
    close=[e for e in enemies if e['distance']<=7]
    target=next((e for e in enemies if e['actor']==s.target),None)
    weapon=tactical.get('weapon_label',tactical.get('weapon',''))
    known={'Weapon_Hun_Long':'长刀','Weapon_Hun_Tang':'黑伤','Weapon_Hun_Double':'黑伤双刃'}
    level=tactical.get('weapon_level')
    usable=[k for k,v in tactical.get('moves',{}).items() if v.get('available')]
    app.weapon_text.set((known.get(weapon,weapon.removeprefix('Weapon_Hun_')) if weapon else '等待角色')
                        +(f' · {level} 级' if level is not None else '')
                        +(f' · {len(usable)} 招' if usable else ''))
    from combo_planner import label as combo_label
    endpoints=[p for p in usable if not any(q.startswith(p) and len(q)>len(p) for q in usable)]
    app.catalog_text.set('可用派生 · '+' / '.join(combo_label(p) for p in sorted(endpoints)))
    upgrade=tactical.get('enhancement',{})
    app.enhancement_text.set(('黑伤强化 · '+('生效中' if upgrade.get('buff_active') else '已解锁 · 格挡 + 重击'))
                            if upgrade.get('unlocked') else '')
    app.target.set((('Boss' if target and target['boss'] else '锁定目标')+f'  ·  {s.target_distance:.1f} m'
                    +(f"  ·  {target['hp']:.0f} / {target['hp_max']:.0f}" if target else ''))
                   if s.target and s.target_distance is not None else '未锁定')
    app.tactical_text.set(f'附近敌人  {len(close)}' if tactical else '附近敌人  —')
    art=tactical.get('skills',{}).get('combat_art',{});charge=art.get('charge');cost=art.get('cost')
    app.charge_text.set(str(charge) if charge is not None else '—')
    from weapon_policy import combat_art_decision
    reason=combat_art_decision(s)[1] if s.valid else '等待角色'
    progress=art.get('charge_progress')
    app.skill_text.set(f'{app.config_obj.skill_key} · {reason} · 充能 {charge if charge is not None else "—"}/{cost if cost is not None else "—"}'
                       +(f' · 进度 {progress:.0%}' if progress is not None else ''))
    shadow=tactical.get('shadow',{});ammo=shadow.get('ammo')
    app.shadow_text.set((f"{app.config_obj.shadow_key} · {shadow.get('label','影之武装')} · 资源 {ammo:g}" if ammo is not None else '影之武装未就绪')
        +(' · 目标持盾' if target and target.get('shield_present') else '')
        +(' · 暂未适配' if shadow and not shadow.get('supported') else ''))
    if target and target.get('shield_present'):
        from shadow_controller import ShadowController
        app.shadow_text.set(app.shadow_text.get()+' · '+ShadowController().decision(0,s,app.config_obj)[1])
    combo=latest.get('combo',{})
    app.combo_status.set(f"已确认 {combo.get('confirmed',0)} 招 · 连段 {combo.get('completed',0)} · 校正 {combo.get('repaired',0)}")
    app.strategy_text.set(latest.get('strategy',''))
    c=latest['counts']
    app.counts.set(f"轻击 {c['attack']-c['heavy']}   重击 {c['heavy']}   格挡 {c['guard']}   闪避 {c['dodge']}   处决 {c['execution']}   喝药 {c['heal']}\n"
                   f"武技确认 {c['skill_confirmed']}/{c['skill']}   武装确认 {c.get('shadow_confirmed',0)}/{c.get('shadow',0)}   换刀确认 {c['switch_confirmed']}/{c['switch']}   锁定确认 {c.get('lock_confirmed',0)}/{c.get('lock',0)}   原生完美确认 {c['native_perfect']}")
    info=latest.get('community',{})
    app.community_status.set({'missing':'未安装','disabled':'未启用','started':'已接管防御 · F5',
                              'pending':'等待加载','error':'加载异常','unreadable':'状态未知'}.get(info.get('status'),'未启用'))
