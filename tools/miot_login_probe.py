#!/usr/bin/env python3
"""探测 sid=xiaomiio 登录态是否可获取（MIoT 通道前置条件）。

为什么需要这个
--------------
miservice 的 MiIOService 走 api.io.mi.com，鉴权用的是 **xiaomiio 这个 sid**
对应的 serviceToken；而我们当前 ~/.mi.token 里只有 micoapi 那一套
（xiaogpt 对话用的就是它），所以 MIoT 的 prop/get、prop/set、action 全都打不开。

小米账号 13418088810 需要二次身份验证。验证过一次之后，**同一个 deviceId**
再登录新 sid 通常可以直接放行——所以这里用 .env 里的 MI_DEVICE_ID 原样试。

安全性
------
  * 只读探测，不改 ~/.mi.token（除非显式 --save）
  * 不打印密码 / passToken / serviceToken 明文
  * 失败时不动任何现有凭据

用法：
    ./xiaogpt-env/bin/python tools/miot_login_probe.py
    ./xiaogpt-env/bin/python tools/miot_login_probe.py --save   # 成功后写回 token
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from aiohttp import ClientSession
from miservice.miaccount import MiAccount, MiTokenStore, get_random

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TOKEN_PATH = Path.home() / ".mi.token"
SID = "xiaomiio"


def load_env() -> tuple[str, str, str]:
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return (
        os.getenv("MI_USER", ""),
        os.getenv("MI_PASS", ""),
        os.getenv("MI_DEVICE_ID", "").strip(),
    )


def mask(resp: dict) -> dict:
    """把敏感字段打码，方便贴日志。"""
    hide = {"passToken", "serviceToken", "ssecurity", "nonce", "cUserId", "location"}
    out = {}
    for k, v in resp.items():
        if k in hide:
            out[k] = f"<len {len(str(v))}>"
        elif k == "notificationUrl":
            out[k] = v
        else:
            out[k] = v
    return out


async def probe(user: str, password: str, device_id: str, save: bool) -> int:
    # 已有 token 作为 cookies 复用（userId/passToken），但绝不覆盖写回
    existing = {}
    if TOKEN_PATH.exists():
        try:
            existing = json.loads(TOKEN_PATH.read_text())
        except Exception:
            existing = {}

    async with ClientSession() as session:
        acct = MiAccount(session, user, password, token_store=None)
        acct.token = dict(existing)
        acct.token.setdefault("deviceId", device_id or get_random(16).upper())

        print(f"[*] deviceId = {acct.token['deviceId']}")
        print(f"[*] 复用的 cookie: userId={acct.token.get('userId')} "
              f"passToken={'有' if acct.token.get('passToken') else '无'}")

        # 1) 第一步：拿 qs
        try:
            r1 = await acct._serviceLogin(f"serviceLogin?sid={SID}&_json=true")
        except Exception as e:
            print(f"[!] serviceLogin 抛异常: {e!r}")
            return 1
        print(f"[*] serviceLogin?sid={SID} -> {json.dumps(mask(r1), ensure_ascii=False)}")
        if "qs" not in r1:
            print("[x] 没有 qs，说明 sid 不被接受或账号被风控拦在第一步")
            return 1

        # 2) 第二步：密码校验
        data = {
            "_json": "true",
            "qs": r1["qs"],
            "sid": r1["sid"],
            "_sign": r1["_sign"],
            "callback": r1["callback"],
            "user": user,
            "hash": hashlib.md5(password.encode()).hexdigest().upper(),
        }
        try:
            r2 = await acct._serviceLogin("serviceLoginAuth2", data)
        except Exception as e:
            print(f"[!] serviceLoginAuth2 抛异常: {e!r}")
            return 1
        print(f"[*] serviceLoginAuth2 -> {json.dumps(mask(r2), ensure_ascii=False)}")

        if "userId" not in r2:
            if r2.get("notificationUrl"):
                url_file = Path.home() / ".xiaozhi" / "miot_verify_url.txt"
                url_file.parent.mkdir(parents=True, exist_ok=True)
                url_file.write_text(r2["notificationUrl"], encoding="utf-8")
                print(f"[*] 验证链接已存到 {url_file}")
                print()
                print("=" * 64)
                print(" 需要二次身份验证（password 已通过，但这个 sid 要再确认一次本人）")
                print("=" * 64)
                print(r2["notificationUrl"])
                print()
                print("在浏览器里过完图形验证码+短信后，重跑本命令即可。")
                return 2
            print("[x] 登录失败")
            return 1

        # 3) 换 serviceToken
        service_token = await acct._securityTokenService(
            r2["location"], r2["nonce"], r2["ssecurity"]
        )
        print(f"[✓] 拿到 {SID} 的 serviceToken（长度 {len(service_token)}）")

        acct.token["userId"] = r2["userId"]
        acct.token["passToken"] = r2["passToken"]
        acct.token[SID] = (r2["ssecurity"], service_token)

        if save:
            if TOKEN_PATH.exists():
                shutil.copy2(TOKEN_PATH, TOKEN_PATH.with_suffix(".token.bak"))
            MiTokenStore(str(TOKEN_PATH)).save_token(acct.token)
            os.chmod(TOKEN_PATH, 0o600)
            print(f"[✓] 已写回 {TOKEN_PATH}（备份在 {TOKEN_PATH}.bak，权限 600）")
            print(f"[i] token 字段: {sorted(acct.token.keys())}")
        else:
            print("[i] 未写回（加 --save 才落盘）")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="探测 sid=xiaomiio 登录态")
    ap.add_argument("--save", action="store_true", help="成功后写回 ~/.mi.token")
    args = ap.parse_args()

    user, password, device_id = load_env()
    if not user or not password:
        print("x .env 里没有 MI_USER / MI_PASS")
        return 1
    return asyncio.run(probe(user, password, device_id, args.save))


if __name__ == "__main__":
    sys.exit(main())
