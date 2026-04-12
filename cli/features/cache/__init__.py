"""Cache feature slice."""

from .events import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDeployCompleteEvent,
    CacheDropAllEvent,
    CacheEvent,
    CacheListEvent,
    CacheRunCompleteEvent,
    CacheStatusEvent,
)
from .models import CacheInput, CacheOptions
from .service import CacheService

__all__ = [
    "CacheAddEvent",
    "CacheDeleteEvent",
    "CacheDeployCompleteEvent",
    "CacheDropAllEvent",
    "CacheEvent",
    "CacheInput",
    "CacheListEvent",
    "CacheOptions",
    "CacheRunCompleteEvent",
    "CacheService",
    "CacheStatusEvent",
]


def __getattr__(name: str):
    if name in {"CacheManager", "CacheOperation", "CacheQuery", "ThreadSafeSet"}:
        from .cache_manager import (
            CacheManager,
            CacheOperation,
            CacheQuery,
            ThreadSafeSet,
        )

        return {
            "CacheManager": CacheManager,
            "CacheOperation": CacheOperation,
            "CacheQuery": CacheQuery,
            "ThreadSafeSet": ThreadSafeSet,
        }[name]
    raise AttributeError(name)
