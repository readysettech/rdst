from importlib import import_module
import sys

_module = import_module("features.cache.performance_comparison")
globals().update(
    {
        k: v
        for k, v in _module.__dict__.items()
        if k not in {"__name__", "__loader__", "__package__", "__spec__"}
    }
)
sys.modules[__name__] = _module
