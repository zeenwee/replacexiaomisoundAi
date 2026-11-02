"""对话历史：从小米云拉。"""

from __future__ import annotations

from console.backend.xiaomi import LINK


async def list_records(limit: int = 20) -> list[dict]:
    return await LINK.records(limit)


async def list_devices() -> list[dict]:
    return await LINK.devices()
