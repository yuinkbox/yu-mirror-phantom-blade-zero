"""Build the desktop companion with PyInstaller.

The version comes from mod_app.py, so a version bump does not need this file
to be edited.  Run with the project root as the working directory.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'mod_app.py'
MOD = ROOT / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat'
sys.dont_write_bytecode = True

VERSION = re.search(r"^VERSION\s*=\s*'([^']+)'", APP.read_text('utf-8'), re.M).group(1)
BUILD = ROOT / f'build-{VERSION}'
OUT = ROOT / f'release-{VERSION}'
SPEC = BUILD / 'spec'
EXE = OUT / f'PBZ战斗助手-{VERSION}.exe'

import PyInstaller.__main__

SPEC.mkdir(parents=True, exist_ok=True)
PyInstaller.__main__.run([
    '--noconfirm', '--onefile', '--windowed',
    '--name', f'PBZ战斗助手-{VERSION}',
    '--icon', str(ROOT / 'app.ico'),
    '--add-data', str(ROOT / 'app.ico') + ';.',
    '--add-data', str(MOD) + ';PBZSmartCombat',
    '--distpath', str(OUT), '--workpath', str(BUILD),
    '--specpath', str(SPEC), '--paths', str(ROOT), str(APP),
])
print(f'built {EXE}  exists={EXE.is_file()}')
