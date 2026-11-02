#!/usr/bin/env python3
"""扫描账号下**所有**米家设备的 MIoT 可读/可写属性。

为什么有这个东西
----------------
打通 sid=xiaomiio 之后发现：不只是音箱，账号下 24 个设备（空调、灯带、
温湿度计、空气净化器、摄像头、洗衣机…）全都能通过 MIoT 读属性，
大部分还能写。这等于给「小智」装上了全屋感知和控制能力。

跑法：
    ./xiaogpt-env/bin/python tools/miot_home_scan.py            # 全扫
    ./xiaogpt-env/bin/python tools/miot_home_scan.py --only 灯   # 只看名字含「灯」的
    ./xiaogpt-env/bin/python tools/miot_home_scan.py --json out.json

只读扫描，**不写任何属性**（写属性要用 --set 并自己确认）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

from aiohttp import ClientSession
from miservice.miaccount import MiAccount
from miservice.miioservice import MiIOService

TOKEN_PATH = Path.home() / ".mi.token"
INSTANCES = "http://miot-spec.org/miot-spec-v2/instances?status=all"
SPEC = "http://miot-spec.org/miot-spec-v2/instance?type="


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "miot-scan"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def instances_map() -> dict:
    data = http_json(INSTANCES)["instances"]
    out: dict[str, list] = {}
    for item in data:
        out.setdefault(item["model"], []).append(item)
    return out


async def scan(only: str = "") -> list[dict]:
    token = json.loads(TOKEN_PATH.read_text())
    if "xiaomiio" not in token:
        raise SystemExit("x token 里没有 xiaomiio，先跑 tools/miot_login_probe.py --save")
    session = ClientSession()
    account = MiAccount(session, "", "", token_store=None)
    account.token = token
    svc = MiIOService(account)
    devices = await svc.device_list(name="full")
    insts = instances_map()

    results = []
    for dev in devices:
        model = dev.get("model") or ""
        name = dev.get("name") or ""
        if only and only not in name and only not in model:
            continue
        entry = {
            "name": name,
            "model": model,
            "did": str(dev.get("did")),
            "spec": None,
            "readable": {},
            "writable": [],
        }
        cands = sorted(insts.get(model) or [], key=lambda x: x.get("version", 0))
        if not cands:
            entry["error"] = "miot-spec.org 没有这个型号"
            results.append(entry)
            continue
        typ = cands[-1].get("type")
        entry["spec"] = typ
        try:
            spec = http_json(SPEC + typ)
        except Exception as e:
            entry["error"] = f"spec 拉取失败 {e}"
            results.append(entry)
            continue

        readable = []
        writable = []
        for sv in spec.get("services", []):
            for p in sv.get("properties", []):
                item = {
                    "siid": sv["iid"],
                    "piid": p["iid"],
                    "service": sv.get("description", ""),
                    "name": p.get("description", ""),
                    "access": p.get("access", []),
                    "format": p.get("format", ""),
                }
                if "read" in item["access"]:
                    readable.append(item)
                if "write" in item["access"]:
                    writable.append(item)
        entry["writable"] = writable

        # 分批读
        values = {}
        B = 12
        for i in range(0, len(readable), B):
            chunk = readable[i : i + B]
            try:
                vals = await svc.miot_get_props(
                    entry["did"], [(x["siid"], x["piid"]) for x in chunk]
                )
            except Exception:
                vals = []
            for x, v in zip(chunk, vals):
                if v is not None:
                    values[f"{x['siid']},{x['piid']}"] = {
                        "name": x["name"],
                        "value": v,
                    }
        entry["readable"] = values
        results.append(entry)
        print(
            f"  {name:<22} {model:<28} 只读命中 {len(values):>2}/{len(readable):<2}"
            f"  可写 {len(writable):>2}",
            flush=True,
        )

    await session.close()
    return results


async def main() -> int:
    ap = argparse.ArgumentParser(description="扫描全屋设备的 MIoT 属性")
    ap.add_argument("--only", default="", help="只扫名字/型号里含这个词的设备")
    ap.add_argument("--json", dest="json_out", default="", help="把结果写成 JSON")
    args = ap.parse_args()

    print("扫描账号下的设备（只读，不改任何设置）…")
    results = await scan(args.only)
    print()
    total_r = sum(len(x["readable"]) for x in results)
    total_w = sum(len(x["writable"]) for x in results)
    print(f"共 {len(results)} 个设备，可读属性命中 {total_r} 个，可写属性 {total_w} 个")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
