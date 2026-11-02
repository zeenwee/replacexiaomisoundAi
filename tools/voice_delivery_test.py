#!/usr/bin/env python3
"""验证：音箱到底吃哪种音频投递方式。

判据：play_by_url 之后 player_get_status 的 status 由 0 变 1 = 设备真的在播。
（status 播完不会自动回落，所以不用它计时。）
每轮之前强制 stop 并等 status 回到 0，避免上一轮的残留造成误判。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = 'https://api.minimaxi.com/v1/t2a_v2'
MODEL = 'speech-2.6-hd'
PORT = 8077


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


async def synth(key, text, voice):
    import httpx

    body = {
        'model': MODEL, 'text': text, 'stream': False,
        'voice_setting': {'voice_id': voice, 'speed': 1.0, 'vol': 1.0, 'pitch': 0},
        'audio_setting': {'sample_rate': 32000, 'bitrate': 128000, 'format': 'mp3'},
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(API, json=body, headers={
            'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        d = r.json()
    return bytes.fromhex(d['data']['audio'])


async def status_of(mina, did):
    try:
        info = await mina.player_get_status(did)
        return int(json.loads(info.get('data', {}).get('info', '{}')).get('status', -1))
    except Exception:
        return -1


async def measure(mina, did, url, label, ptype):
    for _ in range(3):
        try:
            await mina.player_stop(did)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        if await status_of(mina, did) == 0:
            break
    before = await status_of(mina, did)
    try:
        r = await mina.play_by_url(did, url, _type=ptype)
        code = r.get('code') if isinstance(r, dict) else '?'
    except Exception as e:
        print(f'  {label:34s} 调用异常 {type(e).__name__}: {str(e)[:60]}')
        return False
    saw = []
    for _ in range(8):
        await asyncio.sleep(0.8)
        saw.append(await status_of(mina, did))
    ok = 1 in saw
    print(f'  {label:34s} code={code} 采样={saw} -> '
          + ('✓ 播了' if ok else '✗ 没播'))
    return ok


async def main():
    from aiohttp import ClientSession
    from miservice import MiAccount, MiNAService

    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    key = os.environ.get('MINIMAX_API_KEY', '')

    tmp = Path(tempfile.mkdtemp()) / '.mi.token'
    shutil.copy2(Path.home() / '.mi.token', tmp)
    os.chmod(tmp, 0o600)

    work = Path(tempfile.mkdtemp())
    print('>>> 合成测试音频（男声）...')
    raw = await synth(key, '这是独立音色的测试，小智在用男声说话。', 'male-qn-qingse')
    (work / 'v.mp3').write_bytes(raw)
    print(f'  v.mp3 {len(raw)} bytes')

    os.chdir(work)
    httpd = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    await asyncio.sleep(0.5)

    async with ClientSession() as s:
        acc = MiAccount(s, '', '', str(tmp))
        acc.token = json.loads(tmp.read_text())
        mina = MiNAService(acc)
        devs = await mina.device_list()
        speakers = [d for d in devs if d.get("hardware") == "L16A"] or [
            d for d in devs if str(d.get("miotDID")) == os.environ.get("MI_DID", "")
        ]
        if not speakers:
            raise SystemExit("没找到 L16A 音箱；用 tools/list_devices.py 看看账号下的设备")
        did = speakers[0]["deviceID"]
        print()

        b64 = 'data:audio/mpeg;base64,' + base64.b64encode(raw).decode()
        # 本机所有局域网 IPv4（音箱要能访问到其中之一）
        import socket
        host_ips = set()
        for probe in ('8.8.8.8', '223.5.5.5'):
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sk.connect((probe, 80))
                host_ips.add(sk.getsockname()[0])
            except OSError:
                pass
            finally:
                sk.close()
        for ip in sorted(host_ips):
            for t in (1, 2):
                await measure(mina, did, f'http://{ip}:{PORT}/v.mp3',
                             f'局域网 {ip} type={t}', t)
        for t in (1, 2):
            await measure(mina, did, b64, f'data URI type={t}', t)
        await measure(mina, did,
                    'https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3',
                    '公网对照 type=2', 2)

        for _ in range(3):
            try:
                await mina.player_stop(did)
            except Exception:
                pass
            await asyncio.sleep(0.5)

    httpd.shutdown()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    asyncio.run(main())