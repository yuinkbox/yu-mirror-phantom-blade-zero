# 影之刃零 · 镜（yu-mirror-phantom-blade）

《影之刃零》（Phantom Blade Zero）的辅助工具：游戏内 UE4SS Lua Mod + 桌面遥控面板。

**一键连招 + 自动防御 + 自动喝药**，主控是键盘上的一个键：按住左键，角色按武器对应的连招串自动出招；松手立即停。

## 当前状态

- 版本：**0.18.4**（2026-11-11）
- 面板：Python + Tkinter，PyInstaller onefile 打包
- Mod：UE4SS Lua（`PBZSmartCombat`），逻辑全部在游戏线程内执行，不经外挂输入
- 离线验证：`sim_mod_lua.py` 35 个仿真场景、`pytest` 538 项

## 功能

| 模块 | 说明 |
|---|---|
| 一键连招 | 按住左键 → 按武器串出招（黑伤 `AAAB`、双刃 `AAB`、长刀 `AAAAB`…）；不看目标/距离/招式标签；重击杀气不足自动降级轻击；松手 0.0 释放 |
| 自动处决 | 目标 ≤3.2 m 且血量 ≤25% 时自动处决，2 秒冷却 |
| 自动防御 | 读游戏 `DeflectComponent` 的来招帧，按帧类型走格挡/闪避；防御时暂停攻击线 |
| 自动喝药 | 血量低于阈值时按药葫芦，逐步切备用道具槽 |
| 影之武装 | **已移除自动释放**，破盾由玩家手动操作 |

## 目录

```
mod-dev-v0.10/            UE4SS Mod 源码（PBZSmartCombat）
  Scripts/main.lua        战斗逻辑主文件（版本号在文件头）
  Scripts/combo_input.lua 按键租约（面板写 assist.ini，mod 侧限时读取）
  Scripts/weapon_switch.lua
mod_app.py                桌面面板
mod_bridge.py             control.ini / status-a.ini / assist.ini 协议
mod_installer.py          安装、增量升级、回滚（带备份）
sim_mod_lua.py            lupa 离线仿真器（35 场景）
test_*.py                 单元与场景测试
build_mod010.py           PyInstaller 构建
check_mod_app.py          打包后自检
```

## 开发

本机约定（新机器见 `开发环境与迁移说明.md`）：

```powershell
$py = "C:\Users\彧\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = "<离线轮子目录>\_pydeps"
python sim_mod_lua.py                 # 35 场景
$files = (Get-ChildItem -Filter 'test_*.py').Name
python -m pytest -q @files -p no:cacheprovider   # 538 项
python build_mod010.py                # release-<ver>/PBZ战斗助手-<ver>.exe
```

注意：

- lupa 必须 **2.1**（2.8 会导致 combo 假失败）
- Lua 顶层 `local` 已接近 200 上限，新增状态挂到 `Cache.*` / `Inj.*` 等表里
- 写文件必须 UTF-8 无 BOM（PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会加 BOM）

## 安装

1. 游戏装好 UE4SS（`PBZ/platform/steam/dwmapi.dll` + `ue4ss/UE4SS.dll`）
2. 把 `mod-dev-v0.10/mod/PBZSmartCombat` 复制到 `ue4ss/Mods/`
3. 启动游戏 → 打开面板 → F8 启动 → 按住左键
4. **改完 Mod 必须完全退出游戏再进**才加载新版本

## 许可

个人自用工具，未发布二进制到商店。第三方社区 Mod（`PBZ-AutoParryDodge` 等）遵循其 MIT 许可，见 `third-party/`。
