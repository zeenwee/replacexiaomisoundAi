#!/usr/bin/env python3
"""MIoT 单条属性读写（每次调用都是独立进程，避免 prop/get 缓存造成的假象）。

关键事实（实测）：
  * MIoT 的 prop/get 在**同一会话内会返回缓存值**——写入生效后，同一进程里读回可能还是旧值。
    所以校验写入必须换进程再读，或者用 ubus 交叉验证。
  * 写入本身是即时的，ubus（mediaplayer）能立刻读到硬件真实值。

用法：
    ./xiaogpt-env/bin/python tools/miot_rw.py get 9 1
    ./xiaogpt-env/bin/python tools/miot_rw.py set 9 1 true
    ./xiaogpt-env/bin/python tools/miot_rw.py set 2 1 70
    ./xiaogpt-env/bin/python tools/miot_rw.py action 7 1
    ./xiaogpt-env/bin/python tools/miot_rw.py action 7 3 '你好'
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from aiohttp import ClientSession
from miservice.miaccount import MiAccount
from miservice.miioservice import MiIOService

TOKEN_PATH = Path.home() / ".mi.token"
# 设备 miotDID：从环境变量读。跑 tools/list_devices.py 查你自己的设备。
DID = (os.environ.get("MI_DID") or "").strip()


def parse_value(s: str):
    low = s.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


async def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    op = sys.argv[1]
    siid, piid = int(sys.argv[2]), int(sys.argv[3])
    arg = sys.argv[4] if len(sys.argv) > 4 else None

    tok = json.loads(TOKEN_PATH.read_text())
    if "xiaomiio" not in tok:
        print(json.dumps({"ok": False, "error": "token 缺 xiaomiio"}, ensure_ascii=False))
        return 1

    session = ClientSession()
    acct = MiAccount(session, "", "", token_store=None)
    acct.token = tok
    svc = MiIOService(acct)
    out = {"ok": True, "op": op, "siid": siid, "piid": piid}
    try:
        if op == "get":
            out["value"] = await svc.miot_get_prop(DID, (siid, piid))
        elif op == "set":
            val = parse_value(arg)
            out["value"] = val
            out["code"] = await svc.miot_set_prop(DID, (siid, piid), val)
        elif op == "action":
            args = [arg] if arg else []
            out["args"] = args
            out["code"] = await svc.miot_action(DID, (siid, piid), args)
        else:
            out = {"ok": False, "error": f"未知操作 {op}"}
    except Exception as e:
        out = {"ok": False, "op": op, "error": str(e)}
    finally:
        await session.close()
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
