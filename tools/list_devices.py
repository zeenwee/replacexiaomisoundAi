#!/usr/bin/env python3
"""列出小米账号下的所有设备，并标出音箱。

用法：
    ./xiaogpt-env/bin/python tools/list_devices.py
    ./xiaogpt-env/bin/python tools/list_devices.py --hardware L16A

会优先从 .env 里读 MI_USER / MI_PASS；没有就交互式问。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from aiohttp import ClientSession
from miservice import MiAccount, MiNAService, MiIOService

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TOKEN_PATH = Path.home() / ".mi.token"


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ask_credentials() -> tuple[str, str]:
    load_env()
    user = os.getenv("MI_USER", "").strip()
    password = os.getenv("MI_PASS", "")
    if not user:
        print("小米ID（在「米家 App → 我的 → 个人信息 → 小米ID」看，不是手机号）")
        try:
            user = input("小米ID: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            user = ""
    if not password:
        try:
            password = getpass.getpass("小米账号密码（不回显）: ")
        except (EOFError, KeyboardInterrupt):
            print()
            password = ""
    if not user or not password:
        print("✗ 缺少小米账号或密码。", file=sys.stderr)
        sys.exit(1)
    return user, password


async def fetch(user: str, password: str):
    async with ClientSession() as session:
        account = MiAccount(session, user, password, str(TOKEN_PATH))
        await account.login("micoapi")
        mina = MiNAService(account)
        miao = MiIOService(account)
        devices = await mina.device_list()
        miot_devices = await miao.device_list()
    return devices or [], miot_devices or []


def main() -> int:
    ap = argparse.ArgumentParser(description="列出小米账号下的设备")
    ap.add_argument("--hardware", default="L16A", help="要标出的音箱型号")
    args = ap.parse_args()

    user, password = ask_credentials()
    print(f"\n正在登录小米云（账号 {user}）…")
    try:
        devices, miot_devices = asyncio.run(fetch(user, password))
    except Exception as e:
        print(f"\n✗ 登录失败：{e}")
        print("  常见原因：")
        print("   · 海外 IP 登录大陆账号会被风控 → 回到国内网络再试")
        print("   · 小米ID 填成了手机号/邮箱 → 用「米家 App → 我的 → 个人信息 → 小米ID」")
        print("   · 密码错 → 重跑一次")
        return 1

    print(f"\n共 {len(devices)} 个设备：\n")
    print(f"{'型号':<12}{'名称':<22}{'miotDID':<26}{'deviceID'}")
    print("-" * 96)
    speaker = None
    for d in devices:
        hardware = d.get("hardware", "?")
        name = d.get("name", "?")
        miot_did = d.get("miotDID", "")
        device_id = d.get("deviceID", "")
        mark = "  ← 音箱" if hardware == args.hardware else ""
        print(f"{hardware:<12}{name:<22}{miot_did:<26}{device_id}{mark}")
        if hardware == args.hardware:
            speaker = d

    print()
    if speaker:
        did = speaker.get("miotDID", "")
        print(f"✓ 找到 {args.hardware}（{speaker.get('name')}）")
        print(f"  miotDID = {did}")
        print(f"  把它填进 config.yaml 的 mi_did，或者写进 .env 的 MI_DID")
    else:
        print(f"⚠️ 没找到型号 {args.hardware} 的设备。")
        print("   看上面列表里的型号对不对，然后用 --hardware 指定。")

    if miot_devices:
        matching = [
            d for d in miot_devices
            if str(d.get("model", "")).endswith(args.hardware.lower())
        ]
        if matching:
            print(f"\n  MIoT 列表里的 did: {[d.get('did') for d in matching]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
