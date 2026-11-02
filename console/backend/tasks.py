"""后台任务。

pywebview 的桥接方法会阻塞到 Python 返回。慢操作（播报 10 秒、问模型）
直接调会把界面卡住。所以走「提交任务 + 轮询状态」：

    task_id = api.submit("speak", {text: "..."})   # 立刻返回
    api.task_status(task_id)                        # {done, result, error}
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from typing import Any, Callable

from console.backend.loop import submit as _submit

_TASKS: dict[str, dict] = {}


def submit(fn: Callable, *args, **kwargs) -> str:
    """提交一个异步任务，返回 task_id。"""

    async def runner():
        task = _TASKS[task_id]
        try:
            task["result"] = await fn(*args, **kwargs)
        except Exception as e:
            task["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            task["done"] = True

    task_id = uuid.uuid4().hex[:12]
    _TASKS[task_id] = {"done": False, "result": None, "error": None}
    if len(_TASKS) > 100:
        for old in list(_TASKS)[: len(_TASKS) - 100]:
            _TASKS.pop(old, None)
    _submit(runner())
    return task_id


def status(task_id: str) -> dict:
    task = _TASKS.get(task_id)
    if task is None:
        return {"done": True, "result": None, "error": "任务不存在"}
    return dict(task)
