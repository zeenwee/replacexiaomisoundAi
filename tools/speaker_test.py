#!/usr/bin/env python3
"""音箱出声自检：不依赖真人语音，把「模型 → 文字 → 音箱」这条链路跑一遍。

用法
----
    # 1) 只让音箱念一句话（验证 TTS 链路）
    ./xiaogpt-env/bin/python tools/speaker_test.py --say "小智在这儿，链路正常"

    # 2) 走完整链路：问 MiniMax → 拿到答案 → 让音箱念出来
    ./xiaogpt-env/bin/python tools/speaker_test.py --question "用一句话介绍量子计算"

    # 3) 只测模型，不出声
    ./xiaogpt-env/bin/python tools/speaker_test.py --question "你好" --no-tts

为什么单独写这个
----------------
真机验证需要人对着音箱说话，反馈慢。而链路里最容易断的两点其实可以
自己测：MiniMax 能不能出话、小米云能不能让音箱开口。这个脚本把中间
那段补上。

安全注意
--------
``miservice`` 的 ``MiAccount.login()`` 在登录失败时会把 token 文件**删掉**
（``save_token()`` 不带参数 → ``os.remove``）。而我们的 ``~/.mi.token``
是费了很大劲才拿到的，所以这里：

* 只读不写 —— 用一个临时副本当 token store，脚本退出就删；
* 不调用 ``login()`` —— 直接用 token 里的 serviceToken 发请求；
* 真出 401 也不自动重登。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402


def _load_env() -> None:
    """载入项目根目录的 .env（不覆盖已有的环境变量）。"""
    env_file = ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def _read_config():
    """读 config.yaml，返回真正的 Config 对象（不是 dict）。

    ``Config.read_from_file()`` 返回的是 dict，xiaogpt 的 CLI 是用
    ``Config.from_options()`` 才变成对象的；这里直接补上最后一步。
    """
    from xiaogpt.config import Config

    return Config(**Config.read_from_file(str(ROOT / "config.yaml")))


async def _speak(session, account_token_path: Path, mi_did: str, text: str) -> bool:
    """把一段文字推给音箱播报。"""
    mina, device_id = await _mina(session, account_token_path, mi_did)
    result = await mina.text_to_speech(device_id, text)
    ok = bool(result) and result.get("code") == 0
    return ok


async def _mina(session, account_token_path: Path, mi_did: str):
    """建好 MiNAService，并定位到目标音箱的 deviceID。"""
    from miservice import MiAccount, MiNAService

    account = MiAccount(session, "", "", str(account_token_path))
    # 直接塞 token，不走 login()：login 失败会删 token 文件，不能冒险
    account.token = json.loads(account_token_path.read_text())
    if "micoapi" not in (account.token or {}):
        raise RuntimeError("token 里没有 micoapi 字段，无法调用小米云")

    mina = MiNAService(account)
    devices = await mina.device_list()
    device_id = None
    for dev in devices or []:
        if str(dev.get("miotDID", "")) == str(mi_did):
            device_id = dev.get("deviceID")
            break
    if not device_id:
        names = [f"{d.get('name')} ({d.get('miotDID')})" for d in devices or []]
        raise RuntimeError(f"设备列表里没有 miotDID={mi_did}；当前有：{names}")
    return mina, device_id


async def _verify_playback(
    session, account_token_path: Path, mi_did: str, text: str, watch_seconds: float = 12.0
) -> bool:
    """播一段长句子，同时轮询音箱播放状态，确认它真的在出声。

    text_to_speech 返回 code=0 只说明「指令被接受」，不代表喇叭响了。
    真正能证明出声的是 mediaplayer 的 status 变成 1（播放中）。
    """
    mina, device_id = await _mina(session, account_token_path, mi_did)

    async def _status() -> int:
        try:
            info = await mina.player_get_status(device_id)
            return int(
                json.loads(info.get("data", {}).get("info", "{}")).get("status", -1)
            )
        except Exception:
            return -1

    before = await _status()
    await mina.text_to_speech(device_id, text)

    seen_playing = False
    samples: list[int] = []
    deadline = time.monotonic() + watch_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(1)
        st = await _status()
        samples.append(st)
        print(f"    播放状态 status={st}")
        if st == 1:
            seen_playing = True
    print(f"  播报前 status={before}，采样={samples}")
    return seen_playing


async def _ask_model(config: dict, question: str) -> list[str]:
    """问自有智能体，返回逐句答案。"""
    from mybot.minimax_agent import MiniMaxAgent

    agent = MiniMaxAgent.from_config(config)
    sentences: list[str] = []
    async for sentence in agent.ask_stream(question):
        sentences.append(sentence)
    return sentences


async def _fetch_records(session, account_token_path: Path, mi_did: str) -> list[dict]:
    """拉取音箱的对话记录（输入侧的接口）。

    xiaogpt 的轮询走的是
    ``userprofile.mina.mi.com/device_profile/v2/conversation``，
    音箱把识别到的语音上传到小米云，这里就能读到。
    读得到 = 「用户说话 → 小爱听到 → 记录上传」这半条链路是通的。
    """
    from aiohttp import ClientTimeout
    from xiaogpt.config import COOKIE_TEMPLATE, LATEST_ASK_API
    from xiaogpt.utils import parse_cookie_string

    mina, device_id = await _mina(session, account_token_path, mi_did)
    token = json.loads(account_token_path.read_text())
    cookie = COOKIE_TEMPLATE.format(
        device_id=device_id,
        service_token=token["micoapi"][1],
        user_id=token["userId"],
    )
    url = LATEST_ASK_API.format(
        hardware="L16A", timestamp=str(int(time.time() * 1000))
    )
    async with session.get(
        url, cookies=parse_cookie_string(cookie), timeout=ClientTimeout(total=15)
    ) as resp:
        payload = await resp.json(content_type=None)
    data = json.loads(payload.get("data") or "{}")
    return data.get("records") or []


async def main() -> int:
    parser = argparse.ArgumentParser(description="音箱出声 / 端到端自检")
    parser.add_argument("--question", help="要问 MiniMax 的问题")
    parser.add_argument("--say", help="不调模型，直接让音箱念这句")
    parser.add_argument("--no-tts", action="store_true", help="只测模型，不出声")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="播一段长句子并轮询播放状态，确认音箱真的在出声",
    )
    parser.add_argument(
        "--records",
        action="store_true",
        help="拉取音箱的对话记录，验证输入侧通道是否通",
    )
    args = parser.parse_args()

    if not args.question and not args.say and not args.verify and not args.records:
        parser.error("至少给一个 --question、--say、--verify 或 --records")

    _load_env()
    config = _read_config()
    mi_did = str(getattr(config, "mi_did", "") or os.getenv("MI_DID") or "")

    token_src = Path.home() / ".mi.token"
    if not token_src.exists():
        print(f"✗ 找不到 {token_src}，先跑 tools/xiaomi_verify_link.py --retry")
        return 1

    # 用临时副本，避免脚本里的任何写操作碰到真 token
    tmp_dir = Path(tempfile.mkdtemp(prefix="mimi_tts_"))
    token_copy = tmp_dir / ".mi.token"
    shutil.copy2(token_src, token_copy)
    os.chmod(token_copy, 0o600)

    rc = 0
    from aiohttp import ClientSession, ClientTimeout

    try:
        async with ClientSession() as session:
            if args.records:
                records = await _fetch_records(session, token_copy, mi_did)
                print(f"→ 音箱对话记录接口连通，取到 {len(records)} 条")
                for rec in records:
                    answers = rec.get("answers") or []
                    said = ""
                    for a in answers:
                        said = (a.get("tts") or {}).get("text", "")
                        if said:
                            break
                    print(f"  · 时间={rec.get('time')} 问={rec.get('query')!r}")
                    print(f"    小爱答={said!r}")
                return 0 if records else 1

            if args.verify:
                long_text = (
                    "这是一段用来验证音箱播放状态的较长语音。"
                    "小智已经接通了小爱音箱的回答大脑，"
                    "接下来会念完这一整段话，请你听一下音箱有没有真的响起来。"
                    "如果听到了，说明模型到音箱的整条链路是通的。"
                )
                print(f"→ 播放状态校验：{long_text}")
                ok = await _verify_playback(session, token_copy, mi_did, long_text)
                print(
                    "✓ 检测到音箱正在播放（status=1）" if ok else "✗ 没检测到播放状态"
                )
                return 0 if ok else 1

            if args.say:
                print(f"→ 让音箱念：{args.say}")
                ok = await _speak(session, token_copy, mi_did, args.say)
                print("✓ 音箱已接受播报请求" if ok else "✗ 播报请求失败")
                rc = 0 if ok else 1
            else:
                print(f"→ 问 MiniMax：{args.question}")
                t0 = time.perf_counter()
                sentences = await _ask_model(config, args.question)
                elapsed = time.perf_counter() - t0
                text = "".join(sentences).strip()
                if not text:
                    print("✗ 模型没有返回内容")
                    return 1
                print(f"✓ 模型回答（{elapsed:.2f}s，{len(sentences)} 句）：{text}")

                if args.no_tts:
                    return 0

                print(f"→ 让音箱念出来（{len(sentences)} 句）……")
                for idx, sentence in enumerate(sentences, 1):
                    ok = await _speak(session, token_copy, mi_did, sentence)
                    mark = "✓" if ok else "✗"
                    print(
                        f"  {mark} 第 {idx}/{len(sentences)} 句已推送：{sentence.strip()}"
                    )
                    if not ok:
                        rc = 1
                        break
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
