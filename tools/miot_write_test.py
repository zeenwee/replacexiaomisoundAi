#!/usr/bin/env python3
"""MIoT 写入深挖：搞清「code=0 但没生效」到底是哪一类。

第一轮实测发现：
  (9,1) No Disturb  写 false -> code 0，读回仍是 true
  (4,1) Mic Mute    写 true  -> code 0，读回仍是 false
  (2,1) Volume / (9,2) 勿扰时段 / (7,3) Sleep Mode  同值写入正常

三种可能：
  A. 云端规格版本对不上（设备注册的是 v1，v2/v3 才有的属性被收下但忽略）
  B. 设备端有定时/物理逻辑保护（勿扰按时段自己算、麦克风静音靠实体键）
  C. 写入有延迟或需要唤醒/在线状态

这一版用三招区分：
  1. 写**不同的真实值**（不是同值），排除「同值写入看起来成功」的假象
  2. 写完**连续轮询 10 秒**，排除延迟
  3. 用 ubus（另一条通道）**交叉读回**音量，验证 MIoT 写是否真落到硬件

安全：每个测试都写回原值并复核；不动 (2,2) Speaker Mute。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import string
import sys
from pathlib import Path

import httpx
from aiohttp import ClientSession
from miservice.miaccount import MiAccount
from miservice.miioservice import MiIOService

TOKEN_PATH = Path.home() / ".mi.token"
# 设备 miotDID：从环境变量读。跑 tools/list_devices.py 查你自己的设备。
DID = (os.environ.get("MI_DID") or "").strip()


async def get_svc() -> MiIOService:
    token = json.loads(TOKEN_PATH.read_text())
    session = ClientSession()
    acct = MiAccount(session, "", "", token_store=None)
    acct.token = token
    return MiIOService(acct)


async def read(svc, siid, piid):
    try:
        return await svc.miot_get_prop(DID, (siid, piid))
    except Exception as e:
        return f"<ERR {e}>"


async def write(svc, siid, piid, value):
    try:
        return await svc.miot_set_prop(DID, (siid, piid), value)
    except Exception as e:
        return f"<ERR {e}>"


def ubus_volume(service_token, user_id) -> str:
    """用 ubus 通道读硬件真实音量（独立于 MIoT 的交叉验证）"""
    url = "https://api2.mina.mi.com/remote/ubus"
    p = {
        "deviceId": DEVICE_ID,
        "method": "player_get_play_status",
        "path": "mediaplayer",
        "message": json.dumps({"media": "app_ios"}),
        "requestId": "app_ios_" + "".join(random.choice(string.ascii_letters) for _ in range(30)),
    }
    r = httpx.post(url, params=p,
                   cookies={"userId": str(user_id), "serviceToken": service_token},
                   timeout=20)
    j = r.json()
    info = j.get("data", {}).get("info", "")
    try:
        d = json.loads(info)
        return f"volume={d.get('volume')} status={d.get('status')}"
    except Exception:
        return str(info)[:120]


async def poll(svc, siid, piid, want, rounds=5, gap=2.0):
    """连续轮询，直到读到 want 或轮完"""
    seen = []
    for i in range(rounds):
        v = await read(svc, siid, piid)
        seen.append(v)
        if v == want:
            return True, seen
        await asyncio.sleep(gap)
    return False, seen


async def main() -> int:
    svc = await get_svc()
    tok = json.loads(TOKEN_PATH.read_text())
    st = tok["micoapi"][1]
    uid = tok["userId"]

    print("=" * 72)
    print(" MIoT 写入深挖 —— did=%s" % DID)
    print("=" * 72)

    # ---------- A. Volume：写不同值 + ubus 交叉验证 ----------
    print()
    print("【A. Volume (2,1) —— 写不同真实值 + ubus 交叉验证】")
    orig = await read(svc, 2, 1)
    print(f"  原值={orig!r}   ubus 侧: {ubus_volume(st, uid)}")
    target = 70 if orig != 70 else 60
    code = await write(svc, 2, 1, target)
    ok, seen = await poll(svc, 2, 1, target, rounds=3, gap=2.0)
    print(f"  写 {target} -> code={code}; MIoT 轮询 {seen}; ubus 侧: {ubus_volume(st, uid)}")
    print(f"  判定: {'MIoT 写入已生效' if ok else 'MIoT 写入未生效'}")
    code = await write(svc, 2, 1, orig)
    await asyncio.sleep(2)
    back = await read(svc, 2, 1)
    print(f"  复原 {orig!r} -> code={code} 读回 {back!r}  ubus 侧: {ubus_volume(st, uid)}")

    # ---------- B. 麦克风静音 ----------
    print()
    print("【B. Microphone Mute (4,1) —— 写不同值 + 10 秒轮询】")
    orig = await read(svc, 4, 1)
    target = not orig
    code = await write(svc, 4, 1, target)
    ok, seen = await poll(svc, 4, 1, target, rounds=5, gap=2.0)
    print(f"  原值={orig!r} -> 写 {target!r} code={code}")
    print(f"  轮询 10 秒: {seen}")
    print(f"  判定: {'已生效' if ok else '未生效（写被忽略）'}")
    code = await write(svc, 4, 1, orig)
    await asyncio.sleep(1)
    print(f"  复原 {orig!r} -> code={code} 读回 {await read(svc, 4, 1)!r}")

    # ---------- C. 勿扰开关 ----------
    print()
    print("【C. No Disturb (9,1) —— 写不同值 + 10 秒轮询】")
    orig = await read(svc, 9, 1)
    target = not orig
    code = await write(svc, 9, 1, target)
    ok, seen = await poll(svc, 9, 1, target, rounds=5, gap=2.0)
    print(f"  原值={orig!r} -> 写 {target!r} code={code}")
    print(f"  轮询 10 秒: {seen}")
    print(f"  判定: {'已生效' if ok else '未生效（写被忽略）'}")
    code = await write(svc, 9, 1, orig)
    await asyncio.sleep(1)
    print(f"  复原 {orig!r} -> code={code} 读回 {await read(svc, 9, 1)!r}")

    # ---------- D. 勿扰时段（字符串属性，验证字符串写是否真落盘） ----------
    print()
    print("【D. Enable Time Period (9,2) —— 写不同字符串 + 轮询】")
    orig = await read(svc, 9, 2)
    target = "23:30:00-08:30:00"
    code = await write(svc, 9, 2, target)
    ok, seen = await poll(svc, 9, 2, target, rounds=3, gap=2.0)
    print(f"  原值={orig!r} -> 写 {target!r} code={code}")
    print(f"  轮询: {seen}")
    print(f"  判定: {'已生效' if ok else '未生效（写被忽略）'}")
    code = await write(svc, 9, 2, orig if orig != "<ERR>" else ORIG_PERIOD)
    await asyncio.sleep(1)
    print(f"  复原 -> code={code} 读回 {await read(svc, 9, 2)!r}")

    # ---------- E. 动作复查 ----------
    print()
    print("【E. 动作复查（无副作用的那几个）】")
    for siid, aiid, args, label in [(7, 1, [], "Wake Up"), (6, 1, [], "Stop Alarm"),
                                    (3, 4, [], "Stop"), (3, 3, [], "Pause")]:
        try:
            code = await svc.miot_action(DID, (siid, aiid), args)
        except Exception as e:
            code = f"<ERR {e}>"
        await asyncio.sleep(1)
        print(f"  ({siid},{aiid}) {label:12} -> code={code}")

    print()
    print("【F. 只读快照】")
    for siid, piid, name in [(1, 4, "固件版本"), (1, 5, "序列号"), (2, 1, "Volume"),
                             (2, 2, "Speaker Mute"), (3, 1, "Playing State"),
                             (4, 1, "Mic Mute"), (7, 3, "Sleep Mode"),
                             (9, 1, "No Disturb"), (9, 2, "勿扰时段"),
                             (10, 2, "Noise Decibel"), (10, 3, "Sound Detection"),
                             (10, 4, "Noise Detection")]:
        print(f"  ({siid},{piid}) {name:16} = {await read(svc, siid, piid)!r}")

    await svc.account.session.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
