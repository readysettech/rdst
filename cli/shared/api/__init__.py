"""Shared API infrastructure.

Submodules like ``ssh_errors`` are imported from CLI paths that must not
require FastAPI, so the app/guard exports resolve lazily on access.
"""

__all__ = [
    "is_loopback_request",
    "require_local_request",
    "same_host_from_headers",
    "create_app",
]

_GUARD_EXPORTS = {
    "is_loopback_request",
    "require_local_request",
    "same_host_from_headers",
}


def __getattr__(name):
    if name in _GUARD_EXPORTS:
        from . import guards

        return getattr(guards, name)
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
