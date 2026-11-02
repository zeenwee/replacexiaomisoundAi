#!/usr/bin/env python3
"""音色试听：用 MiniMax TTS 合成，直接推给音箱播出来。

用法
----
    # 列出全部可用音色
    ./xiaogpt-env/bin/python tools/tts_preview.py --list

    # 逐个试听所有音色（每种念一句自己的名字）
    ./xiaogpt-env/bin/python tools/tts_preview.py --audition

    # 只试听某几个
    ./xiaogpt-env/bin/python tools/tts_preview.py --voice male-qn-qingse --voice presenter_female

    # 试听一段自定义文字
    ./xiaogpt-env/bin/python tools/tts_preview.py --voice sweet_girl --text "这是甜心女声的试听"

注意：它直接占用音箱出声，和常驻实例别同时说话。
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VOICE_LABELS = {
    "female-shaonv": "少女音色",
    "female-chengshu": "成熟女声",
    "female-tianmei": "甜美女声",
    "presenter_female": "女性主持人",
    "presenter_male": "男性主持人",
    "male-qn-qingse": "青涩青年音色",
    "male-qn-jingying": "精英青年音色",
    "male-qn-badao": "霸道青年音色",
    "male-qn-daxuesheng": "青年大学生音色",
    "audiobook_male_1": "有声书男声",
    "audiobook_female_1": "有声书女声",
    "lovely_girl": "可爱女声",
    "sweet_girl": "甜心女声",
    "energetic_boy": "活力男声",
}


class _H(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


async def _synth(key: str, text: str, voice: str, model: str) -> tuple[bytes, float]:
    import httpx

    # 音量/语速/音调跟 config.yaml 走，别写死 —— 试听要和正式播报一致，
    # 否则「试听觉得够响，正式播报却小声」这种事会一直有。
    import yaml

    opts = {}
    try:
        cfg = yaml.safe_load(Path(ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        opts = cfg.get("tts_options") or {}
    except Exception:
        pass

    body = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice,
            "speed": opts.get("speed", 1.0),
            "vol": opts.get("vol", 1.0),
            "pitch": opts.get("pitch", 0),
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
        },
    }
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(
            "https://api.minimaxi.com/v1/t2a_v2",
            json=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        payload = resp.json()
    base_resp = payload.get("base_resp") or {}
    if base_resp.get("status_code") not in (0, None):
        raise RuntimeError(str(base_resp.get("status_msg") or "TTS 失败")[:100])
    raw = bytes.fromhex((payload.get("data") or {}).get("audio") or "")
    ms = (payload.get("extra_info") or {}).get("audio_length", 0) / 1000
    return raw, ms


async def main() -> int:
    parser = argparse.ArgumentParser(description="MiniMax 音色试听")
    parser.add_argument("--list", action="store_true", help="只列出音色")
    parser.add_argument("--audition", action="store_true", help="逐个试听全部音色")
    parser.add_argument("--voice", action="append", help="指定要试听的音色，可重复")
    parser.add_argument("--text", help="自定义试听文案")
    parser.add_argument("--model", default="speech-2.6-turbo")
    args = parser.parse_args()

    from mybot.minimax_tts import AVAILABLE_VOICES

    if args.list:
        print("可用音色：")
        for v in AVAILABLE_VOICES:
            print(f"  {v:22s} {VOICE_LABELS.get(v, '')}")
        return 0

    if not (args.audition or args.voice):
        parser.error("给 --audition 试听全部，或用 --voice 指定（--list 看清单）")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        print("✗ .env 里没有 MINIMAX_API_KEY")
        return 1

    voices = args.voice or AVAILABLE_VOICES

    from aiohttp import ClientSession
    from miservice import MiAccount, MiNAService

    tmp = Path(tempfile.mkdtemp()) / ".mi.token"
    shutil.copy2(Path.home() / ".mi.token", tmp)
    os.chmod(tmp, 0o600)

    work = Path(tempfile.mkdtemp())
    handler = functools.partial(_H, directory=str(work))
    httpd = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host = _local_ip()
    await asyncio.sleep(0.3)

    async with ClientSession() as s:
        acc = MiAccount(s, "", "", str(tmp))
        acc.token = json.loads(tmp.read_text())
        mina = MiNAService(acc)
        devs = await mina.device_list()
        speakers = [d for d in devs if d.get("hardware") == "L16A"] or [
            d for d in devs if str(d.get("miotDID")) == os.environ.get("MI_DID", "")
        ]
        if not speakers:
            raise SystemExit("没找到 L16A 音箱；用 tools/list_devices.py 看看账号下的设备")
        did = speakers[0]["deviceID"]

        for idx, voice in enumerate(voices):
            label = VOICE_LABELS.get(voice, voice)
            text = args.text or f"我是{label}。"
            try:
                raw, ms = await _synth(key, text, voice, args.model)
            except Exception as e:
                print(f"  ✗ {voice:22s} 合成失败: {e}")
                continue
            name = f"v{idx}_{voice}.mp3"
            (work / name).write_bytes(raw)
            url = f"http://{host}:{port}/{name}"
            try:
                await mina.play_by_url(did, url, _type=1)
                print(f"  ✓ {voice:22s} {label:12s} {ms:.1f}s  正在播…", flush=True)
                await asyncio.sleep(ms + 0.5)
            except Exception as e:
                print(f"  ✗ {voice:22s} 播放失败: {type(e).__name__} {str(e)[:60]}")
            await asyncio.sleep(0.3)

        try:
            await mina.player_stop(did)
        except Exception:
            pass

    httpd.shutdown()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    print("\n试听结束。把喜欢的音色填到 config.yaml 的 tts_options.voice 即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
