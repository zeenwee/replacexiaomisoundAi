"""常驻 asyncio 事件循环。

为什么需要它
------------
pywebview 的桥接方法在**独立线程**执行（源码里明说「executed in a
separate thread to prevent blocking the UI thread」）。而我们的操作全是
异步的（小米云、MiniMax 都是 httpx/aiohttp）。

如果每个桥接方法各自 asyncio.run()，aiohttp 的 session 会跨事件循环报错。
所以这里起**一个常驻 loop**，所有异步操作都通过
run_coroutine_threadsafe 丢进去 —— 同步的桥接方法 + 异步的底层，
两边都不打架。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def start() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever, daemon=True, name="console-asyncio"
        ).start()
        _loop = loop
        return loop


def run(coro: Coroutine[Any, Any, Any], timeout: float = 180.0) -> Any:
    """在常驻 loop 里跑一个协程并同步等结果（给桥接方法用）。"""
    loop = start()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def submit(coro: Coroutine[Any, Any, Any]):
    """丢进常驻 loop 就走，不等结果（给后台任务用）。"""
    loop = start()
    return asyncio.run_coroutine_threadsafe(coro, loop)


def stop() -> None:
    global _loop
    with _lock:
        if _loop is not None:
            try:
                _loop.call_soon_threadsafe(_loop.stop)
            except Exception:
                pass
            _loop = None
