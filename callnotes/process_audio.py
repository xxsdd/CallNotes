"""Capture one Windows process tree through the bundled WASAPI bridge.

Only process names, IDs and creation times are inspected. The user chooses the
application; failures never fall back to recording the whole computer.
"""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
from pathlib import Path
import sys

BRIDGE = Path(__file__).resolve().parent / 'native' / 'process_loopback.dll'
APPS = {'discord.exe': 'Discord', 'discordcanary.exe': 'Discord Canary',
        'discordptb.exe': 'Discord PTB', 'wechat.exe': '微信', 'weixin.exe': '微信'}


def support_error():
    if sys.platform != 'win32' or sys.getwindowsversion().build < 20348:
        return '只录通话软件需要 Windows 11 或 Windows build 20348 及以上版本。'
    if not BRIDGE.is_file():
        return '通话软件录音组件缺失，请重新安装或恢复 process_loopback.dll。'
    return ''


class PROCESSENTRY32W(ct.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ct.c_size_t), ('th32ModuleID', wt.DWORD),
                ('cntThreads', wt.DWORD), ('th32ParentProcessID', wt.DWORD),
                ('pcPriClassBase', wt.LONG), ('dwFlags', wt.DWORD), ('szExeFile', wt.WCHAR * 260)]


def _kernel():
    lib = ct.WinDLL('kernel32', use_last_error=True)
    lib.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    lib.CreateToolhelp32Snapshot.restype = wt.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        func = getattr(lib, name)
        func.argtypes = [wt.HANDLE, ct.POINTER(PROCESSENTRY32W)]
        func.restype = wt.BOOL
    lib.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    lib.OpenProcess.restype = wt.HANDLE
    lib.GetProcessTimes.argtypes = [wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4
    lib.GetProcessTimes.restype = wt.BOOL
    lib.CloseHandle.argtypes = [wt.HANDLE]
    lib.CloseHandle.restype = wt.BOOL
    lib.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    lib.WaitForSingleObject.restype = wt.DWORD
    return lib


def process_creation_time(pid):
    kernel = _kernel()
    process = kernel.OpenProcess(0x1000, False, int(pid))
    if not process:
        raise RuntimeError('所选通话软件已退出或无法访问，请刷新后重新选择。')
    try:
        start, end, system, user = (wt.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(process, ct.byref(start), ct.byref(end), ct.byref(system), ct.byref(user)):
            raise ct.WinError(ct.get_last_error())
        return (start.dwHighDateTime << 32) | start.dwLowDateTime
    finally:
        kernel.CloseHandle(process)


def _process_entries():
    if sys.platform != 'win32':
        return {}
    kernel = _kernel()
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ct.c_void_p(-1).value:
        raise ct.WinError(ct.get_last_error())
    entries = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ct.sizeof(entry)
    try:
        more = kernel.Process32FirstW(snapshot, ct.byref(entry))
        while more:
            entries[entry.th32ProcessID] = (entry.szExeFile.casefold(), entry.th32ParentProcessID)
            more = kernel.Process32NextW(snapshot, ct.byref(entry))
        if ct.get_last_error() not in (0, 18):  # ERROR_NO_MORE_FILES
            raise ct.WinError(ct.get_last_error())
    finally:
        kernel.CloseHandle(snapshot)
    return entries


def enumerate_voice_apps():
    """Return running Discord/WeChat roots; never inspect process arguments."""
    entries = _process_entries()
    roots = []
    for pid, (exe, parent) in entries.items():
        if exe not in APPS:
            continue
        # Electron's render/audio processes carry the same exe name. Selecting
        # their root also captures subprocesses created after recording starts.
        visited, ancestor = {pid}, parent
        has_root = False
        while ancestor in entries and ancestor not in visited:
            visited.add(ancestor)
            name, ancestor = entries[ancestor]
            if name in APPS and APPS[name] == APPS[exe]:
                has_root = True
                break
        if has_root:
            continue
        try:
            created = process_creation_time(pid)
        except (OSError, RuntimeError):
            continue
        roots.append(dict(kind='process', pid=pid, created=created, exe=exe, name=APPS[exe]))
    return sorted(roots, key=lambda value: (value['name'], value['pid']))


def _bridge():
    error = support_error()
    if error:
        raise RuntimeError(error)
    lib = ct.CDLL(str(BRIDGE))
    lib.cn_create.argtypes = [wt.DWORD, ct.c_ulonglong, ct.POINTER(ct.c_void_p)]
    lib.cn_create.restype = ct.c_long
    lib.cn_start.argtypes = [ct.c_void_p]
    lib.cn_start.restype = ct.c_long
    lib.cn_available.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32)]
    lib.cn_available.restype = ct.c_long
    lib.cn_read.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.POINTER(ct.c_uint32)]
    lib.cn_read.restype = ct.c_long
    lib.cn_stop.argtypes = lib.cn_close.argtypes = [ct.c_void_p]
    lib.cn_stop.restype = lib.cn_close.restype = None
    lib.cn_rendering_pids.argtypes = [ct.POINTER(wt.DWORD), ct.c_uint32, ct.POINTER(ct.c_uint32)]
    lib.cn_rendering_pids.restype = ct.c_long
    return lib


def _render_target(lib, target):
    """Choose the narrowest existing subtree containing this app's renderers."""
    root = int(target['pid'])
    if process_creation_time(root) != int(target['created']):
        raise RuntimeError('所选通话软件已重新启动，请刷新后选择。')
    pids, count = (wt.DWORD * 1024)(), ct.c_uint32()
    _check(lib.cn_rendering_pids(pids, len(pids), ct.byref(count)))
    entries = _process_entries()
    paths = []
    created = {root: int(target['created'])}
    for pid in pids[:count.value]:
        chain, current = [], int(pid)
        while current in entries and current not in chain:
            try:
                if current not in created:
                    created[current] = process_creation_time(current)
            except (OSError, RuntimeError):
                break
            # Toolhelp stores historical parent IDs. Do not mistake a reused
            # parent PID for membership of the newly selected application's tree.
            if created[current] < created[root] or (chain and created[current] > created[chain[-1]]):
                break
            chain.append(current)
            if current == root:
                paths.append(chain)
                break
            current = entries[current][1]
    if not paths:
        raise RuntimeError('还没检测到所选软件的通话声音。请先接通 Discord／微信语音，再开始录制。不会改录全部电脑声音。')
    common = set(paths[0]).intersection(*map(set, paths[1:]))
    # In ordinary voice apps there is one audio worker. Multiple sessions in
    # that same worker resolve to the same PID without duplicate capture.
    pid = next(pid for pid in paths[0] if pid in common)
    # A deep multi-branch layout cannot be flattened reliably on every Windows
    # build. Refuse it explicitly instead of silently losing one conversation.
    if len(paths) > 1 and any(path.index(pid) > 1 for path in paths):
        raise RuntimeError('所选软件同时使用多个深层声音进程，暂时无法完整分离。请关闭该软件内其他播放后重试，或自行选择“全部电脑声音”。')
    return dict(target, pid=pid, created=process_creation_time(pid))


def _check(result):
    if result < 0:
        code = result & 0xffffffff
        text = {0x800703e3: '录音启动已取消', 0x8007042b: '通话软件已退出，请重新打开并选择',
                0x80070005: '无法访问所选通话软件，请让两个软件使用相同的普通权限',
                0x8007006f: '音频读取积压过多，录音已停止并保留已录内容',
                0x800705b4: '通话软件录音启动超时，请刷新后重试'}.get(code, 'Windows 无法捕获所选通话软件的声音')
        raise RuntimeError(f'{text}（0x{code:08X}）。未切换为全部电脑声音。')


class ProcessLoopbackStream:
    rate, channels = 48000, 2

    def __init__(self, target):
        self.name = target.get('name', '通话软件')
        self.target = dict(target)
        self.lib = _bridge()
        self.handle = ct.c_void_p()
        self.root_handle = None
        self.started = False
        self.kernel = _kernel()
        self.root_handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, int(target['pid']))
        if not self.root_handle:
            raise RuntimeError('所选通话软件已退出或无法访问，请刷新后重新选择。')
        try:
            capture = _render_target(self.lib, target)
            self.capture_target = capture
            _check(self.lib.cn_create(int(capture['pid']), int(capture['created']), ct.byref(self.handle)))
        except Exception:
            self.close()
            raise

    def start_stream(self):
        _check(self.lib.cn_start(self.handle))
        self.started = True

    def get_read_available(self):
        if not self.root_handle or self.kernel.WaitForSingleObject(self.root_handle, 0) != 258:
            raise RuntimeError('所选通话软件已退出，录音已停止。未切换为全部电脑声音。')
        count = ct.c_uint32()
        _check(self.lib.cn_available(self.handle, ct.byref(count)))
        return count.value

    def read(self, frames, exception_on_overflow=True):
        buffer = ct.create_string_buffer(frames * self.channels * 2)
        count = ct.c_uint32()
        _check(self.lib.cn_read(self.handle, buffer, frames, ct.byref(count)))
        return buffer.raw[:count.value * self.channels * 2]

    def is_active(self):
        if self.started:
            self.get_read_available()
        return self.started

    def stop_stream(self):
        if self.handle:
            self.lib.cn_stop(self.handle)
        self.started = False

    def close(self):
        if self.handle:
            self.lib.cn_close(self.handle)
            self.handle = ct.c_void_p()
        if self.root_handle:
            self.kernel.CloseHandle(self.root_handle)
            self.root_handle = None
        self.started = False
