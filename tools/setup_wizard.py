#!/usr/bin/env python3
"""一站式配置向导：登录小米云 → 找到音箱 → 回填 config.yaml 和 .env。

用法：
    ./xiaogpt-env/bin/python tools/setup_wizard.py

向导做四件事：
    1. 问你要小米ID + 密码（密码不回显），登录小米云
    2. 拉设备列表，自动认出音箱型号并打印 miotDID
    3. 把 hardware / mi_did 回填进 config.yaml（保留注释和 MiniMax 配置）
    4. 把 MI_USER / MI_PASS / MI_DID 写进 .env（权限 600）

⚠️ 它 **不会** 动 AI 后端那几行 —— 自有 MiniMax 智能体的配置
   （bot / api_base / gpt_options）原样保留。
"""

from __future__ import annotations

import asyncio
import getpass
import os
import re
import sys
from pathlib import Path

from aiohttp import ClientSession
from miservice import MiAccount, MiNAService

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
TOKEN_PATH = Path.home() / ".mi.token"

DEFAULT_HARDWARE = "L16A"


def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value or default


def ask_password(text: str) -> str:
    try:
        return getpass.getpass(text)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def update_yaml_scalar(path: Path, key: str, value: str) -> bool:
    """就地替换 config.yaml 里某个顶层标量，保留注释和其余内容。"""
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        indent = m.group(1)
        # 只改顶层键（缩进为空），避免误伤嵌套的同名键
        if indent:
            continue
        lines[i] = f'{indent}{key}: "{value}"\n'
        path.write_text("".join(lines), encoding="utf-8")
        return True
    return False


def write_env(user: str, password: str, did: str) -> None:
    """把凭据写进 .env（保留已有的 MiniMax key 等其它行），权限 600。"""
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = existing.splitlines()

    def upsert(name: str, value: str) -> None:
        nonlocal lines
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith(f"{name}="):
                lines[i] = f"{name}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{name}={value}")

    upsert("MI_USER", user)
    upsert("MI_PASS", password)
    if did:
        upsert("MI_DID", did)

    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)


async def fetch_devices(user: str, password: str):
    """先做一次原始登录拿到 token，再拉设备列表。

    小米账号可能需要二次身份验证，这时登录接口不给 token 只给验证链接，
    这种情况要把链接直接打给用户，别让他对着一堆 stacktrace 发呆。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from xiaomi_verify_link import attempt_login, print_verify_instructions

    status, payload = await attempt_login(user, password, save=True)
    if status == "verify":
        print_verify_instructions(payload)
        return None
    if status == "fail":
        raise RuntimeError(f"小米云登录失败：{payload}")

    async with ClientSession() as session:
        account = MiAccount(session, user, password, str(TOKEN_PATH))
        mina = MiNAService(account)
        devices = await mina.device_list()
    return devices or []


def main() -> int:
    print("=" * 56)
    print("  xiaogpt 配置向导（小米 Sound / L16A → 自有 MiniMax 智能体）")
    print("=" * 56)

    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    print("\n小米ID 在「米家 App → 我的 → 个人信息 → 小米ID」，不是手机号。")
    user = prompt("小米ID", os.getenv("MI_USER", ""))
    if not user:
        print("✗ 没有小米ID。")
        return 1
    # .env 里已经有 MI_PASS 就不用再问
    password = os.getenv("MI_PASS", "") or ask_password("小米账号密码（不回显）: ")
    if not password:
        print("✗ 没有密码。")
        return 1

    print("\n正在登录小米云…")
    try:
        devices = asyncio.run(fetch_devices(user, password))
    except Exception as e:
        print(f"\n✗ 登录失败：{e}")
        print("  · 海外 IP 登录大陆账号会被风控 → 回国内网络再跑")
        print("  · 小米ID / 密码不对")
        return 1

    if devices is None:
        # 上面已经把验证链接和说明打印出来了
        print()
        print("完成验证后，重新跑一次本命令即可。")
        return 2

    print(f"\n账号下共 {len(devices)} 个设备：\n")
    for d in devices:
        print(f"  {d.get('hardware', '?'):<12}{d.get('name', '?'):<24}{d.get('miotDID', '')}")

    speaker = next(
        (d for d in devices if d.get("hardware") == DEFAULT_HARDWARE), None
    )
    if speaker is None:
        print(f"\n⚠️ 没找到 {DEFAULT_HARDWARE}。请确认音箱型号（看音箱底部标签）。")
        hardware = prompt("音箱型号", DEFAULT_HARDWARE)
        speaker = next((d for d in devices if d.get("hardware") == hardware), None)

    if speaker is None:
        print("✗ 找不到音箱，先用 tools/list_devices.py 看看设备列表。")
        return 1

    did = speaker.get("miotDID", "")
    print(f"\n✓ 找到音箱：{speaker.get('name')}（{speaker.get('hardware')}）")
    print(f"  miotDID = {did}")

    if update_yaml_scalar(CONFIG_PATH, "hardware", speaker.get("hardware", DEFAULT_HARDWARE)):
        print(f"  ✓ 已回填 config.yaml 的 hardware")
    if did and update_yaml_scalar(CONFIG_PATH, "mi_did", did):
        print(f"  ✓ 已回填 config.yaml 的 mi_did")

    write_env(user, password, did)
    print(f"  ✓ 已写入 .env（权限 600）")

    print("\n" + "=" * 56)
    print("还差一步：把 MiniMax 密钥填进 .env")
    print("  echo 'MINIMAX_API_KEY=你的key' >> .env && chmod 600 .env")
    print("  密钥地址：https://platform.minimaxi.com/user-center/basic-information/interface-key")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    sys.exit(main())
