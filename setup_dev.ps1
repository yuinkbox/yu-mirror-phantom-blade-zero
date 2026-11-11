<#
.SYNOPSIS
Create an isolated Windows Python 3.12 development environment, test, and build.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\setup_dev.ps1
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\setup_dev.ps1 -PythonExe C:\Python312\python.exe -Offline
.NOTES
Uses ../dependencies/wheels automatically when available. Does not install or
change game files. Use the desktop assistant to install the Mod after testing.
#>
[CmdletBinding()]
param(
    [string]$PythonExe,
    [string]$Wheelhouse,
    [switch]$Offline,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$priorPythonUtf8 = $env:PYTHONUTF8
$priorConsoleOutputEncoding = [Console]::OutputEncoding
$priorPipelineOutputEncoding = $OutputEncoding
$utf8OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8OutputEncoding
$OutputEncoding = $utf8OutputEncoding
$env:PYTHONUTF8 = '1'

function Step([string]$Text) {
    Write-Host "`n=== $Text ===" -ForegroundColor Cyan
}
function NeedFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "缺少 $Label : $Path"
    }
}
function CheckExit([string]$Label) {
    if ($LASTEXITCODE -ne 0) { throw "$Label 失败，退出码 $LASTEXITCODE" }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    Step '检查项目和依赖目录'
    NeedFile 'mod_app.py' '桌面源码'
    NeedFile 'requirements-dev.txt' '开发依赖清单'
    NeedFile 'mod-dev-v0.10\mod\PBZSmartCombat\Scripts\main.lua' 'Mod 源码'
    NeedFile 'mod-dev-v0.10\mod\PBZSmartCombat\Scripts\community_defense.lua' '防御模块'
    NeedFile 'mod-dev-v0.10\mod\PBZSmartCombat\Scripts\weapon_switch.lua' '切刀模块'
    $versionMatch = [regex]::Match(
        [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'mod_app.py')),
        "(?m)^VERSION\s*=\s*'([^']+)'")
    if (-not $versionMatch.Success) { throw '无法从 mod_app.py 读取版本号' }
    $appVersion = $versionMatch.Groups[1].Value

    $explicitWheelhouse = -not [string]::IsNullOrWhiteSpace($Wheelhouse)
    if (-not $explicitWheelhouse) {
        $Wheelhouse = Join-Path (Split-Path -Parent $PSScriptRoot) 'dependencies\wheels'
    }
    $hasWheelhouse = Test-Path -LiteralPath $Wheelhouse -PathType Container
    if ($hasWheelhouse) {
        $Wheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
        $hasWheelhouse = @(Get-ChildItem -LiteralPath $Wheelhouse -Filter '*.whl' -File).Count -gt 0
    }
    if (($Offline -or $explicitWheelhouse) -and -not $hasWheelhouse) {
        throw "离线依赖目录不存在或没有 wheel 文件：$Wheelhouse"
    }

    Step '检查 Python 3.12 x64'
    $bootstrapArgs = @()
    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        $pythonLauncher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($pythonLauncher) {
            $PythonExe = $pythonLauncher.Source
            $bootstrapArgs = @('-3.12')
        } else {
            $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
            if (-not $pythonCommand) {
                throw '找不到 Python。请安装 Python 3.12 x64，或用 -PythonExe 指定 python.exe。'
            }
            $PythonExe = $pythonCommand.Source
        }
    }
    $pythonCheck = "import sys, tkinter, venv; assert sys.platform == 'win32' and sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32, 'Requires Windows Python 3.12 x64 with tkinter and venv'; print(sys.executable, sys.version)"
    & $PythonExe @bootstrapArgs -c $pythonCheck
    CheckExit 'Python 检查'

    $venvDir = Join-Path $PSScriptRoot '.venv'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        & $PythonExe @bootstrapArgs -m venv $venvDir
        CheckExit '创建 .venv'
    }
    & $venvPython -c $pythonCheck
    CheckExit '.venv 检查；若旧 .venv 来自另一台电脑，请在新目录解压迁移包后重试'

    Step '安装固定版本的开发依赖'
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check')
    if ($hasWheelhouse) {
        Write-Host "使用离线依赖：$Wheelhouse"
        $pipArgs += @('--no-index', '--find-links', $Wheelhouse)
    } else {
        Write-Host '包内没有离线依赖，将通过 pip 联网下载。'
    }
    $pipArgs += @('-r', 'requirements-dev.txt')
    & $venvPython @pipArgs
    CheckExit '安装依赖'
    & $venvPython -m pip check
    CheckExit '依赖一致性检查'
    & $venvPython -c "import lupa; assert lupa.__version__ == '2.1', 'Expected lupa==2.1'; print('Lua runtime:', lupa.LuaRuntime().eval('_VERSION'))"
    CheckExit 'Lua 运行时检查'

    Step '运行完整单元测试'
    $testFiles = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'test_*.py' -File |
        Sort-Object Name | ForEach-Object { $_.FullName })
    if ($testFiles.Count -eq 0) { throw '项目没有 test_*.py 测试文件' }
    & $venvPython -m pytest -q @testFiles -p no:cacheprovider
    CheckExit '单元测试'

    Step '运行真实 Lua 源码仿真'
    & $venvPython sim_mod_lua.py
    CheckExit 'Lua 仿真'

    Step '桌面源码自检'
    & $venvPython check_mod_app.py
    CheckExit '桌面源码自检'

    if (-not $SkipBuild) {
        Step "构建 $appVersion"
        & $venvPython build_mod010.py
        CheckExit 'PyInstaller 构建'
        $releaseDir = Join-Path $PSScriptRoot "release-$appVersion"
        $releaseExe = Join-Path $releaseDir "PBZ战斗助手-$appVersion.exe"
        NeedFile $releaseExe '构建产物'
        Step '独立 EXE 启动自检'
        & $venvPython -c "import subprocess, sys; result = subprocess.run([sys.argv[1], '--smoke-test'], timeout=30); raise SystemExit(result.returncode)" $releaseExe
        CheckExit '独立 EXE 启动自检'
        Write-Host "输出：$releaseExe" -ForegroundColor Green
    }

    Step '完成'
    Write-Host "开发解释器：$venvPython" -ForegroundColor Green
    Write-Host '本脚本只配置开发环境和验证程序；游戏文件未安装或更新。'
    Write-Host '需要使用 Mod 时，先退出游戏，再打开助手选择游戏并点击“安装/升级 Mod”。'
} finally {
    Pop-Location
    $env:PYTHONUTF8 = $priorPythonUtf8
    [Console]::OutputEncoding = $priorConsoleOutputEncoding
    $OutputEncoding = $priorPipelineOutputEncoding
}
