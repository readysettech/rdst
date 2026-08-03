"""Small asyncio helpers shared by long-running service tracks."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable


def start_blocking(
    callback: Callable, *args: Any, **kwargs: Any
) -> asyncio.Future:
    """Start a blocking call on a one-shot daemon worker."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future = loop.create_future()

    def settle_value(value: Any) -> None:
        if not result.done():
            result.set_result(value)

    def settle_error(error: BaseException) -> None:
        if not result.done():
            result.set_exception(error)

    def invoke() -> None:
        try:
            value = callback(*args, **kwargs)
        except BaseException as exc:
            try:
                loop.call_soon_threadsafe(settle_error, exc)
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(settle_value, value)
            except RuntimeError:
                pass

    threading.Thread(target=invoke, daemon=True).start()
    return result


async def run_blocking(callback: Callable, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking call on a cancellable one-shot daemon worker."""
    result = start_blocking(callback, *args, **kwargs)
    return await result
