#!/usr/bin/env python3
"""MIoT 通道探测器：有了 sid=xiaomiio 登录态之后，把 L16A 能读写的属性全扫一遍。

背景
----
小米的 MIoT 云端接口在 api.io.mi.com，鉴权用的是 sid=xiaomiio 对应的 serviceToken；
小米云对话（micoapi）和 ubus（mina）用的都是另一套 token，所以之前 MIoT 一直打不开。

本脚本做四件事：
  1. device_list        —— 列出账号下设备，取 model / did / urn
  2. 旧版 miio 属性      —— home_get_props(did, [...])，试探 volume/mute/play 等老属性
  3. MIoT spec          —— 解析 L16A 的 siid/piid 全表
  4. MIoT 属性批量读     —— 把 spec 里所有只读属性一次读完，看哪些 code=0（真的支持）
     再把可写的属性列出来（--set 才能写，写操作单独确认）

用法：
    ./xiaogpt-env/bin/python tools/miot_probe.py
    ./xiaogpt-env/bin/python tools/miot_probe.py --props-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from aiohttp import ClientSession
from miservice.miaccount import MiAccount
from miservice.miioservice import MiIOService

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TOKEN_PATH = Path.home() / ".mi.token"
# 设备 miotDID：从环境变量读。跑 tools/list_devices.py 查你自己的设备。
MIOT_DID = (os.environ.get("MI_DID") or "").strip()

# 旧版 miio 属性名候选（L16A 可能一个都不支持，试一下成本很低）
LEGACY_PROPS = [
    "volume", "mute", "play", "play_mode", "status", "mode", "device_id",
    "current_url", "current_track", "nearend", "wifi_signal", "led",
]


def load_env() -> None:
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def get_service() -> MiIOService:
    if not TOKEN_PATH.exists():
        raise SystemExit(f"x 找不到 {TOKEN_PATH}")
    token = json.loads(TOKEN_PATH.read_text())
    if "xiaomiio" not in token:
        raise SystemExit(
            "x token 里没有 xiaomiio 字段，先跑 tools/miot_login_probe.py --save"
        )
    session = ClientSession()
    acct = MiAccount(session, "", "", token_store=None)
    acct.token = token
    return MiIOService(acct)


async def step_device_list(svc: MiIOService) -> dict:
    print("=" * 70)
    print("[1] device_list —— 账号下的设备")
    print("=" * 70)
    try:
        full = await svc.device_list(name="full")
    except Exception as e:
        print(f"  x device_list 失败: {e}")
        return {}
    found = {}
    for d in full:
        print(f"  - name={d.get('name')!r} model={d.get('model')!r} did={d.get('did')}")
        if str(d.get("did")) == MIOT_DID or "l16a" in str(d.get("model", "")).lower():
            found = d
    if found:
        print()
        print(f"  >> 命中 L16A: model={found.get('model')} did={found.get('did')}")
        print(f"     urn={found.get('urn')}")
        print(f"     token(本地miio用, 云端用不上) 长度={len(found.get('token') or '')}")
    else:
        print("  ! 没在列表里找到 L16A")
    return found


async def step_legacy_props(svc: MiIOService, did: str) -> None:
    print()
    print("=" * 70)
    print("[2] 旧版 miio 属性 home_get_props —— 试探老接口")
    print("=" * 70)
    try:
        vals = await svc.home_get_props(did, LEGACY_PROPS)
        for name, v in zip(LEGACY_PROPS, vals):
            print(f"  {name:16} = {v!r}")
    except Exception as e:
        print(f"  x 旧版属性读取失败: {e}")


async def step_spec(svc: MiIOService, model: str, versions: list[int]) -> dict | None:
    print()
    print("=" * 70)
    print("[3] MIoT spec —— 拿 siid/piid 全表")
    print("=" * 70)
    base = "urn:miot-spec-v2:device:speaker:0000A015:xiaomi-l16a"
    types = [f"{base}:{v}" for v in versions]
    for t in types:
        try:
            async with svc.account.session.get(
                "http://miot-spec.org/miot-spec-v2/instance?type=" + t
            ) as r:
                if r.status != 200:
                    print(f"  - {t} -> HTTP {r.status}")
                    continue
                spec = await r.json()
            if "services" in spec:
                print(f"  [OK] {t}  services={len(spec['services'])}")
                return spec
        except Exception as e:
            print(f"  - {t} -> {e}")
    print("  ! 没拿到 spec")
    return None


async def step_miot_props(svc: MiIOService, did: str, spec: dict | None) -> None:
    print()
    print("=" * 70)
    print("[4] MIoT 属性批量读 —— 哪些真的支持")
    print("=" * 70)
    if not spec:
        print("  ! 没有 spec，跳过")
        return
    readable = []
    writable = []
    for s in spec.get("services", []):
        siid = s["iid"]
        for p in s.get("properties", []):
            acc = p.get("access", [])
            desc = p.get("description", "")
            row = (siid, p["iid"], desc)
            if "read" in acc:
                readable.append(row)
            if "write" in acc:
                writable.append(row)

    print(f"  spec 里只读属性 {len(readable)} 个、可写属性 {len(writable)} 个，分批读……")
    ok = []
    unsupported = []
    B = 15
    for i in range(0, len(readable), B):
        batch = readable[i : i + B]
        try:
            vals = await svc.miot_get_props(did, [(a, b) for a, b, _ in batch])
        except Exception as e:
            print(f"    批次 {i} 失败: {e}")
            continue
        for (siid, piid, desc), v in zip(batch, vals):
            if v is not None:
                ok.append((siid, piid, desc, v))
            else:
                unsupported.append((siid, piid, desc))

    print()
    print(f"  >> 可读且有值的属性 {len(ok)} 个：")
    for siid, piid, desc, v in ok:
        sv = json.dumps(v, ensure_ascii=False)
        if len(sv) > 90:
            sv = sv[:90] + "..."
        print(f"     ({siid},{piid}) {desc:28} = {sv}")
    print()
    print(f"  >> 读不出值的属性 {len(unsupported)} 个（设备未实现/需授权）：")
    print("     " + ", ".join(f"({a},{b}){c}" for a, b, c in unsupported))

    print()
    print(f"  >> 可写属性 {len(writable)} 个（用 --set <siid>:<piid>=<值> 写）：")
    for siid, piid, desc in writable:
        print(f"     ({siid},{piid}) {desc}")


async def main() -> int:
    ap = argparse.ArgumentParser(description="MIoT 通道探测器")
    ap.add_argument("--props-only", action="store_true", help="只跑属性部分")
    ap.add_argument("--set", dest="set_op", default=None,
                    help="写属性，格式 siid:piid=value")
    ap.add_argument("--action", dest="action_op", default=None,
                    help="调 action，格式 siid:aiid[:入参JSON]")
    ap.add_argument("--spec-version", type=int, default=3,
                    help="用哪个版本的 MIoT spec（1/2/3/4，默认 3=最新 released）")
    args = ap.parse_args()

    load_env()
    svc = await get_service()

    did = MIOT_DID
    model = ""
    if not args.props_only:
        dev = await step_device_list(svc)
        model = dev.get("model", "")
        did = str(dev.get("did") or MIOT_DID)
        await step_legacy_props(svc, did)

    spec = await step_spec(svc, model, [args.spec_version])
    await step_miot_props(svc, did, spec)

    if args.action_op:
        parts = args.action_op.split(":", 2)
        siid, aiid = int(parts[0]), int(parts[1])
        aargs = []
        if len(parts) > 2 and parts[2]:
            try:
                aargs = json.loads(parts[2])
            except Exception:
                aargs = [parts[2]]
        try:
            code = await svc.miot_action(did, (siid, aiid), aargs)
            print(f"\n[action] ({siid},{aiid}) args={aargs!r} -> code={code}")
        except Exception as e:
            print(f"\n[action] ({siid},{aiid}) -> 失败: {e}")

    if args.set_op:
        siid, piid = args.set_op.split(":")[0:2]
        value = args.set_op.split("=", 1)[1]
        try:
            val = json.loads(value)
        except Exception:
            val = value
        code = await svc.miot_set_prop(did, (int(siid), int(piid)), val)
        print(f"\n[set] ({siid},{piid}) = {val!r} -> code={code}")

    await svc.account.session.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
