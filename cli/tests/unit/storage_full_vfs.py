"""SQLite VFS fault seam for writes performed inside VACUUM.

SQLite applies max_page_count to ordinary writes and backup destinations, but VACUUM's
private database has its own page limit. This VFS returns SQLITE_FULL from its real xWrite
callback, so the production VACUUM executes its own error and rollback path. Install only
in a disposable subprocess using the same shared SQLite library as Python.
"""
from __future__ import annotations

import ctypes as c
import ctypes.util


def install():
    library = c.CDLL(ctypes.util.find_library('sqlite3'))

    class Vfs(c.Structure):
        _fields_ = [('version', c.c_int), ('szOsFile', c.c_int), ('mxPathname', c.c_int),
                    ('pNext', c.c_void_p), ('zName', c.c_char_p), ('pAppData', c.c_void_p)] + [
                        (name, c.c_void_p) for name in (
                            'xOpen', 'xDelete', 'xAccess', 'xFullPathname', 'xDlOpen', 'xDlError',
                            'xDlSym', 'xDlClose', 'xRandomness', 'xSleep', 'xCurrentTime',
                            'xGetLastError', 'xCurrentTimeInt64', 'xSetSystemCall',
                            'xGetSystemCall', 'xNextSystemCall')]

    class Methods(c.Structure):
        _fields_ = [('version', c.c_int)] + [(name, c.c_void_p) for name in (
            'xClose', 'xRead', 'xWrite', 'xTruncate', 'xSync', 'xFileSize', 'xLock', 'xUnlock',
            'xCheckReservedLock', 'xFileControl', 'xSectorSize', 'xDeviceCharacteristics',
            'xShmMap', 'xShmLock', 'xShmBarrier', 'xShmUnmap', 'xFetch', 'xUnfetch')]

    class File(c.Structure):
        _fields_ = [('methods', c.POINTER(Methods))]

    library.sqlite3_vfs_find.restype = c.POINTER(Vfs)
    original = library.sqlite3_vfs_find(None)
    vfs = Vfs()
    c.memmove(c.byref(vfs), original, c.sizeof(vfs))
    vfs.zName = b'rdst-fault-full'
    vfs.pNext = None
    opened = {}
    state = {'full': False, 'failures': 0}
    Open = c.CFUNCTYPE(c.c_int, c.POINTER(Vfs), c.c_void_p, c.POINTER(File), c.c_int,
                       c.POINTER(c.c_int))
    Write = c.CFUNCTYPE(c.c_int, c.POINTER(File), c.c_void_p, c.c_int, c.c_longlong)
    Close = c.CFUNCTYPE(c.c_int, c.POINTER(File))
    base_open = Open(original.contents.xOpen)

    @Write
    def write(file, buffer, amount, offset):
        base, methods = opened[c.addressof(file.contents)]
        if state['full']:
            state['failures'] += 1
            return 13  # SQLITE_FULL
        return Write(base.contents.xWrite)(file, buffer, amount, offset)

    @Close
    def close(file):
        base, methods = opened.pop(c.addressof(file.contents))
        file.contents.methods = base
        return Close(base.contents.xClose)(file)

    @Open
    def open_file(unused, name, file, flags, output):
        result = base_open(original, name, file, flags, output)
        if result == 0:
            base = c.cast(c.cast(file.contents.methods, c.c_void_p).value, c.POINTER(Methods))
            methods = Methods()
            c.memmove(c.byref(methods), base, c.sizeof(methods))
            methods.xWrite = c.cast(write, c.c_void_p).value
            methods.xClose = c.cast(close, c.c_void_p).value
            opened[c.addressof(file.contents)] = (base, methods)
            file.contents.methods = c.pointer(methods)
        return result

    vfs.xOpen = c.cast(open_file, c.c_void_p).value
    library.sqlite3_vfs_register.argtypes = [c.POINTER(Vfs), c.c_int]
    assert library.sqlite3_vfs_register(c.byref(vfs), 0) == 0
    # The callbacks and VFS must outlive every connection using them.
    return state, (library, vfs, open_file, write, close, opened)
