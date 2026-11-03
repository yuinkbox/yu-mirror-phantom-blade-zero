"""Read-only peer detection; never close or reconfigure another assistant."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path


class ProcessEntry(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('pid', W.DWORD),
                ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                ('name', W.WCHAR*260)]


def peer_processes():
    k = C.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [W.HANDLE,C.POINTER(ProcessEntry)]
    k.CloseHandle.argtypes = [W.HANDLE]
    handle = k.CreateToolhelp32Snapshot(2,0)
    if handle == C.c_void_p(-1).value:
        raise OSError('无法确认其他助手是否运行')
    try:
        entry = ProcessEntry()
        entry.dwSize = C.sizeof(entry)
        rows = []
        ok = k.Process32FirstW(handle,C.byref(entry))
        if not ok:
            raise OSError('无法读取运行中的助手列表')
        while ok:
            rows.append(dict(pid=entry.pid,parent=entry.parent,name=entry.name))
            ok = k.Process32NextW(handle,C.byref(entry))
    finally:
        k.CloseHandle(handle)
    return select_peers(rows,os.getpid())


def select_peers(rows,current_pid):
    # The one-file packager has a parent bootloader with the same executable name.
    by_pid={p['pid']:p for p in rows}
    ancestors={current_pid}
    pid=current_pid
    while pid in by_pid:
        pid=by_pid[pid]['parent']
        if pid in ancestors or not pid:
            break
        ancestors.add(pid)
    return [p for p in rows if p['pid'] not in ancestors
            and Path(p['name']).stem.replace(' ','').startswith('PBZ战斗助手')]


class ObserverSink:
    """A second barrier for --observe: this object has no input-injection access."""
    def __init__(self):
        self.held=set()
        self.deadlines={}

    def pump(self,*args,**kwargs):
        pass

    def release_all(self):
        pass

    def release_keys(self,*args):
        pass

    def dispatch(self,*args,**kwargs):
        raise RuntimeError('只读模式禁止发送按键')
