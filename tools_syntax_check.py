"""Compile the shipped Lua sources with the same Lua runtime the simulator uses."""
from pathlib import Path

import lupa

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat' / 'Scripts'


def compile_one(lua, path):
    source = path.read_text(encoding='utf-8')
    result = lua.eval('function(s, n) return load(s, n) end')(source, f'@{path.name}')
    if isinstance(result, tuple):
        loader, error = result[0], (result[1] if len(result) > 1 else None)
    else:
        loader, error = result, None
    if loader is None:
        raise SystemExit(f'{path.name}: {error}')
    print(f'{path.name}: OK ({len(source)} bytes)')


def main():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    for name in ('main.lua', 'weapon_switch.lua', 'community_defense.lua'):
        compile_one(lua, SCRIPTS / name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
