#!/usr/bin/env python3
"""小米账号二次验证：生成验证链接 / 重试登录。

为什么需要这个
--------------
小米账号 13418088810 属于「需要二次身份验证」的账号。调用
`serviceLoginAuth2` 时密码校验其实**已经通过**（返回 code=0 描述「成功」），
但小米不会下发 token，而是返回 `securityStatus: 16` 和一个
`notificationUrl`，必须由真人在浏览器里过一次图形验证码 + 短信验证。

这一步自动化做不了（验证码必须人工过），所以这里提供两个动作：
    1) 打印一个新鲜的验证链接 → 在 Safari/Chrome 里打开并完成验证
    2) 验证完再跑一次 → 这次应该直接拿到 token

用法：
    ./xiaogpt-env/bin/python tools/xiaomi_verify_link.py            # 打印新链接
    ./xiaogpt-env/bin/python tools/xiaomi_verify_link.py --retry    # 验证后重试登录
    ./xiaogpt-env/bin/python tools/xiaomi_verify_link.py --link-only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

from aiohttp import ClientSession
from miservice.miaccount import MiAccount, MiTokenStore, get_random

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TOKEN_PATH = Path.home() / ".mi.token"


def load_env() -> tuple[str, str]:
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.getenv("MI_USER", ""), os.getenv("MI_PASS", "")


async def attempt_login(user: str, password: str, save: bool = True):
    """直接调小米云的登录接口。

    返回 (status, payload)：
      status = "ok"    → 已登录，token 已存到 ~/.mi.token
      status = "verify"→ 需要二次身份验证，payload 是验证链接
      status = "fail"  → 登录失败，payload 是原始响应

    ⚠️ 小米的风控是**按设备**的：同一个 deviceId 过了验证码之后，
    用这个 deviceId 就能直接登录成功。所以 .env 里可以写死 MI_DEVICE_ID，
    验证过一次之后就不用再反复过验证码了。
    """
    device_id = os.getenv("MI_DEVICE_ID", "").strip()
    async with ClientSession() as session:
        acct = MiAccount(session, user, password, str(TOKEN_PATH) if save else None)
        acct.token = {"deviceId": device_id or get_random(16).upper()}
        r1 = await acct._serviceLogin("serviceLogin?sid=micoapi&_json=true")
        if "qs" not in r1:
            return "fail", r1
        data = {
            "_json": "true",
            "qs": r1["qs"],
            "sid": r1["sid"],
            "_sign": r1["_sign"],
            "callback": r1["callback"],
            "user": user,
            "hash": hashlib.md5(password.encode()).hexdigest().upper(),
        }
        r2 = await acct._serviceLogin("serviceLoginAuth2", data)
        if "userId" not in r2:
            if r2.get("notificationUrl"):
                return "verify", r2["notificationUrl"]
            return "fail", r2

        if save:
            # serviceToken 要单独去 sts 换，xiaogpt 的 cookie 就是靠它拼的
            service_token = await acct._securityTokenService(
                r2["location"], r2["nonce"], r2["ssecurity"]
            )
            acct.token["userId"] = r2["userId"]
            acct.token["passToken"] = r2["passToken"]
            acct.token["micoapi"] = (r2["ssecurity"], service_token)
            MiTokenStore(str(TOKEN_PATH)).save_token(acct.token)
        return "ok", r2


def print_verify_instructions(url: str) -> None:
    print("=" * 64)
    print(" 需要二次身份验证（密码是对的，只是小米要额外确认本人）")
    print("=" * 64)
    print()
    print("请在 **Safari 或 Chrome（你自己的浏览器）** 里打开下面这个链接，")
    print("按提示完成「图形验证码 + 短信验证」：")
    print()
    print(url)
    print()
    print("完成后回到这里跑：")
    print("    ./xiaogpt-env/bin/python tools/xiaomi_verify_link.py --retry")
    print()
    print("（链接有时效，过期了就重新跑一次本命令拿新的）")
    print()
    print("另一个更省事的办法：如果你之前在别的电脑上跑通过配置向导，")
    print(f"把那边的 {TOKEN_PATH} 直接拷过来放到本机 {TOKEN_PATH} 即可，")
    print("这样完全跳过验证码。")


def main() -> int:
    ap = argparse.ArgumentParser(description="小米账号二次验证")
    ap.add_argument("--retry", action="store_true", help="只重试登录（验证完之后用）")
    ap.add_argument("--link-only", action="store_true", help="只打印链接，不保存 token")
    args = ap.parse_args()

    user, password = load_env()
    if not user or not password:
        print("✗ .env 里没有 MI_USER / MI_PASS，先跑 tools/setup_wizard.py")
        return 1

    status, payload = asyncio.run(
        attempt_login(user, password, save=not args.link_only)
    )

    if status == "ok":
        print("✓ 登录成功，token 已保存到", TOKEN_PATH)
        print("  下一步：./xiaogpt-env/bin/python tools/setup_wizard.py")
        return 0

    if status == "verify":
        print_verify_instructions(payload)
        return 2

    print("✗ 登录失败：", payload)
    return 1


if __name__ == "__main__":
    sys.exit(main())
