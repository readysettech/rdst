"""Allocator settings for the long-running web server process."""

from __future__ import annotations

import ctypes
import os
import sys

# glibc mallopt parameter number for the arena cap (malloc.h).
M_ARENA_MAX = -8
# The server's Python threads take turns under the GIL, so extra arenas buy little
# allocation concurrency; each one retains its own freed high-water memory.
WEB_MALLOC_ARENAS = 2


def limit_malloc_arenas(count: int = WEB_MALLOC_ARENAS) -> bool:
    """Cap glibc malloc arenas unless MALLOC_ARENA_MAX already chose a cap.

    glibc otherwise grows to eight arenas per core as worker threads allocate, and
    freed memory stays inside whichever arena served it. Returns whether the cap
    was applied; other platforms and allocators are left alone.
    """
    if not sys.platform.startswith("linux") or "MALLOC_ARENA_MAX" in os.environ:
        return False
    try:
        mallopt = ctypes.CDLL(None).mallopt
    except (OSError, AttributeError):
        return False
    mallopt.argtypes = (ctypes.c_int, ctypes.c_int)
    mallopt.restype = ctypes.c_int
    return mallopt(M_ARENA_MAX, count) == 1
