<#
  setup_dev.ps1 -- bootstrap the PBZ combat assistant project on a fresh Windows machine.

  Usage:
    powershell -ExecutionPolicy Bypass -File setup_dev.ps1                 # test + simulate + build
    powershell -ExecutionPolicy Bypass -File setup_dev.ps1 -InstallMod    # also install the mod into the game
    powershell -ExecutionPolicy Bypass -File setup_dev.ps1 -GameExe "E:\Steam\steamapps\common\PBZ\platform\steam\PBZ.exe" -InstallMod

  Run it from the project root (the folder that contains mod_app.py).
#>
[CmdletBinding()]
param(
  [string]$GameExe,
  [switch]$InstallMod,
  [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Need($path, $label) {
  if (-not (Test-Path -LiteralPath $path)) { throw "缺少 $label : $path" }
}

Step '0. 检查目录'
Need 'mod_app.py' '项目根（mod_app.py）'
Need 'mod-dev-v0.10\mod\PBZSmartCombat\Scripts\main.lua' 'mod 源码'
Write-Host "项目根: $PSScriptRoot"

Step '1. 检查 Python'
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw '找不到 python。请安装 Python 3.12 64 位并勾选 Add to PATH。' }
& python -c "import sys; assert sys.version_info[:2] >= (3,10); assert sys.maxsize > 2**32, '需要 64 位'; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Python 版本/位数不满足要求（需 3.10+ 64 位）。' }

Step '2. 安装依赖'
& python -m pip install --upgrade pip
& python -m pip install -r requirements.txt
& python -m pip install pytest lupa pyinstaller imageio-ffmpeg
if ($LASTEXITCODE -ne 0) { throw 'pip 安装失败' }

Step '3. 单元测试（期望 382 passed）'
$files = (Get-ChildItem -Filter 'test_*.py').Name
& python -m pytest -q @files -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw '单元测试失败，先修测试再继续。' }

Step '4. 离线仿真 mod（期望 simulated 7 scenarios from main.lua）'
& python sim_mod_lua.py
if ($LASTEXITCODE -ne 0) { throw '仿真失败，先修 main.lua。' }

if (-not $SkipBuild) {
  Step '5. 打包 exe'
  & python build_mod010.py
  if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败（先确认 build_mod010.py 里没有这台机器专有的 build_deps 路径）。' }
  $exe = Get-ChildItem -Path 'release-0.12.0' -Filter '*.exe' | Select-Object -First 1
  if (-not $exe) { throw '没找到构建出的 exe' }
  Step '6. exe 冒烟检查'
  & python check_mod_app.py $exe.FullName
  if ($LASTEXITCODE -ne 0) { throw 'exe 冒烟检查失败' }
}

if ($InstallMod) {
  Step '7. 安装/升级 mod 到游戏目录'
  if (-not $GameExe) {
    $GameExe = Read-Host '请输入 PBZ.exe 的完整路径'
  }
  Need $GameExe 'PBZ.exe'
  & python -c "from pathlib import Path; from mod_installer import ModInstaller; i = ModInstaller(Path(r'$GameExe'), Path(r'mod-dev-v0.10\mod\PBZSmartCombat')); print(i.upgrade()); print('installed ->', i.target)"
  if ($LASTEXITCODE -ne 0) { throw 'mod 安装失败（游戏是否在运行？先退出游戏）' }
  Write-Host '装完要重启游戏才会加载新 mod。' -ForegroundColor Yellow
}

Step '完成'
Write-Host @'
下一步：
  1) 开游戏 -> 开 release-0.12.0\PBZ战斗助手-0.12.0.exe -> 选游戏 -> F8
  2) 打完一局把 ue4ss\Mods\PBZSmartCombat\diag.txt 和 status-a.ini 回传
  3) 看 开发环境与迁移说明.md 第 5、6 节排查
'@ -ForegroundColor Green
