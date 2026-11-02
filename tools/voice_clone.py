#!/usr/bin/env python3
"""音色克隆：检查录音 → 上传 → 克隆 → 试听 → 写进配置。

用法
----
    # 1) 先检查录音素材合不合格（不花钱、不上传）
    ./xiaogpt-env/bin/python tools/voice_clone.py check ~/Downloads/voice.m4a

    # 2) 克隆（会真的调 MiniMax 接口）
    ./xiaogpt-env/bin/python tools/voice_clone.py clone ~/Downloads/voice.m4a \
        --voice-id xiaozhi_myvoice --name "小智的声音"

    # 3) 列出这个账号下已有的音色（系统音色 + 克隆音色）
    ./xiaogpt-env/bin/python tools/voice_clone.py list

    # 4) 试听某个音色（推给音箱说一句话）
    ./xiaogpt-env/bin/python tools/voice_clone.py try xiaozhi_myvoice

    # 5) 把克隆音色设成控制台默认
    ./xiaogpt-env/bin/python tools/voice_clone.py use xiaozhi_myvoice

为什么单独写这个
--------------------
MiniMax 的音色克隆要三步（上传文件 → 用 file_id 建音色 → 用 voice_id 合成），
中间有六个坑全是实测踩出来的：

1. 端点是 **POST /v1/voice_clone**，但**不能直接传音频**。必须先
   POST /v1/files/upload（purpose=voice_clone）拿 file_id，再拿 file_id 去
   建音色。直接 multipart 传文件 + voice_id 会被拒。
2. **voice_id 有命名规则**，实测：不合法时报的错很具体 ——
   - 长度必须在 8~256 之间（短了报 "voice_id length"）
   - 首字符必须是字母（数字开头报 "voice_id first character"）
   - 只能含字母/数字/下划线/连字符（有空格报 "voice_id invalid character"）
   - 不能以下划线或连字符结尾（报 "voice_id last character"）
3. **voice_id 在账号内唯一**，重名会失败 —— 所以默认带时间戳。
4. **克隆音色 7 天不用会被平台自动删**。所以克隆完必须马上合成一句，
   工具会自动做这一步（试合成）。
5. **2038 = 账号没开通克隆权限**（需实名认证 + 平台开通）；
   **2061 = 套餐不含 voice_design**（音色设计要另外的套餐）。
   这两个不是参数错，改参数没用。
6. 音频要求：**mp3 / m4a / wav**，**10 秒 ~ 5 分钟**，**≤ 20MB**，
   采样率 ≥16kHz，单人说话。

权限
----
克隆需要平台侧开通。本工具会把平台返回的原始错误原样打出来，
不猜、不粉饰 —— 该开通就去开通。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

BASE = "https://api.minimaxi.com/v1"
ALLOWED_EXT = {".mp3", ".m4a", ".wav"}
MAX_BYTES = 20 * 1024 * 1024
MIN_SEC = 10.0
MAX_SEC = 300.0

# voice_id 规则（实测错误码反推，见文件头）
VOICE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]$")


def key() -> str:
    k = os.environ.get("MINIMAX_API_KEY", "")
    if not k:
        raise SystemExit("x .env 里没有 MINIMAX_API_KEY")
    return k


def headers() -> dict:
    return {"Authorization": "Bearer " + key()}


def probe_duration(path: Path) -> float:
    """读音频时长（秒）。用 macOS 自带的 afinfo，不用装 ffmpeg。"""
    tool = shutil.which("afinfo")
    if not tool:
        return -1.0
    try:
        out = subprocess.run([tool, str(path)], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return -1.0
    m = re.search(r"estimated duration:\s*([0-9.]+)\s*sec", out)
    return float(m.group(1)) if m else -1.0


def probe_samplerate(path: Path) -> int:
    tool = shutil.which("afinfo")
    if not tool:
        return 0
    try:
        out = subprocess.run([tool, str(path)], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return 0
    m = re.search(r"(\d+)\s*Hz", out)
    return int(m.group(1)) if m else 0


def check_file(path: Path) -> dict:
    """检查录音素材合不合格。只读，不上传。"""
    problems: list[str] = []
    warns: list[str] = []
    if not path.exists():
        return {"ok": False, "problems": [f"文件不存在：{path}"], "warns": []}

    size = path.stat().st_size
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXT:
        problems.append(f"格式 {ext or '（无后缀）'} 不支持，只能用 mp3 / m4a / wav")
    if size > MAX_BYTES:
        problems.append(f"文件 {size/1024/1024:.1f}MB，超过 20MB 上限")
    if size == 0:
        problems.append("文件是空的")

    dur = probe_duration(path)
    if dur < 0:
        warns.append("读不出时长（afinfo 不可用或格式怪），请自己确认在 10 秒 ~ 5 分钟之间")
    else:
        if dur < MIN_SEC:
            problems.append(f"只有 {dur:.1f} 秒，太短，至少要 10 秒")
        if dur > MAX_SEC:
            problems.append(f"有 {dur:.1f} 秒，超过 5 分钟上限")
        if 20 <= dur <= 60:
            warns.append(f"{dur:.1f} 秒偏短，30~60 秒的干净人声效果更稳")

    sr = probe_samplerate(path)
    if sr and sr < 16000:
        problems.append(f"采样率 {sr}Hz 低于 16kHz，建议换 16kHz 以上的")

    return {
        "ok": not problems,
        "size": size,
        "duration": dur,
        "sample_rate": sr,
        "problems": problems,
        "warns": warns,
    }


def check_voice_id(vid: str) -> list[str]:
    problems = []
    if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", vid):
        problems.append("voice_id 只能含字母、数字、下划线、连字符，且必须字母开头")
    elif len(vid) < 8:
        problems.append(f"voice_id 只有 {len(vid)} 个字符，至少要 8 个")
    elif len(vid) > 256:
        problems.append(f"voice_id 有 {len(vid)} 个字符，最多 256 个")
    elif vid.endswith("_") or vid.endswith("-"):
        problems.append("voice_id 不能以下划线或连字符结尾")
    return problems


async def upload(path: Path) -> int:
    """上传音频拿 file_id。"""
    import httpx

    data = path.read_bytes()
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            BASE + "/files/upload",
            headers=headers(),
            data={"purpose": "voice_clone"},
            files={"file": (path.name, data, "application/octet-stream")},
        )
    payload = r.json()
    br = payload.get("base_resp") or {}
    if br.get("status_code") not in (0, None):
        raise RuntimeError("上传失败：" + str(br.get("status_msg")))
    fid = (payload.get("file") or {}).get("file_id")
    if not fid:
        raise RuntimeError("上传没有返回 file_id：" + json.dumps(payload, ensure_ascii=False)[:200])
    return int(fid)


async def clone(file_id: int, voice_id: str, text: str | None, model: str) -> dict:
    """用 file_id 建音色。返回平台原始响应。"""
    import httpx

    body: dict = {"file_id": file_id, "voice_id": voice_id}
    if text:
        body["text"] = text
        body["model"] = model
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            BASE + "/voice_clone",
            headers={**headers(), "Content-Type": "application/json"},
            json=body,
        )
    return r.json()


async def list_voices() -> dict:
    """拉账号下的全部音色（系统 + 克隆 + 设计）。"""
    import httpx

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            BASE + "/get_voice",
            headers={**headers(), "Content-Type": "application/json"},
            json={"voice_type": "all"},
        )
    return r.json()


async def synth(voice_id: str, text: str, model: str = "speech-2.6-turbo") -> bytes:
    """用指定音色合成一句，返回 mp3 字节。"""
    import httpx

    body = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": 1.0, "vol": 4.0, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"},
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(BASE + "/t2a_v2", json=body, headers={**headers(), "Content-Type": "application/json"})
    p = r.json()
    br = p.get("base_resp") or {}
    if br.get("status_code") not in (0, None):
        raise RuntimeError("合成失败：" + str(br.get("status_msg")))
    raw = bytes.fromhex((p.get("data") or {}).get("audio") or "")
    if not raw:
        raise RuntimeError("合成没有返回音频")
    return raw


def explain(code: int, msg: str) -> str:
    """把平台错误码翻译成人话。"""
    known = {
        2013: "参数不合法（voice_id 命名规则没过）",
        2038: "账号没有音色克隆权限 —— 要去 platform.minimaxi.com 实名认证并申请开通",
        2061: "当前套餐不支持这个模型（音色设计要另外的套餐）",
        1043: "克隆音色的 ASR 相似度不达标 —— 录音里有多人说话或背景噪声太重",
        1004: "鉴权失败 —— .env 里的 key 不对",
        1008: "余额不足",
    }
    head = known.get(code, "平台拒绝了这次请求")
    return f"{head}（{code}: {msg}）"


async def cmd_clone(args) -> int:
    path = Path(args.file).expanduser()
    vid = args.voice_id
    problems = check_voice_id(vid)
    if problems:
        print("x voice_id 不合法：")
        for p in problems:
            print("   - " + p)
        return 2

    chk = check_file(path)
    print("检查录音素材：")
    if chk.get("duration", 0) and chk.get("duration", 0) > 0:
        print(f"  时长 {chk['duration']:.1f}s / {chk['size']/1024:.0f}KB / {chk.get('sample_rate',0)}Hz")
    for w in chk["warns"]:
        print("  ! " + w)
    for p in chk["problems"]:
        print("  x " + p)
    if not chk["ok"]:
        print("\n素材不合格，先改好再传。")
        return 2

    print("\n上传音频…")
    file_id = await upload(path)
    print(f"  file_id = {file_id}")

    print(f"创建音色 {vid}…")
    payload = await clone(file_id, vid, args.text, args.model)
    br = payload.get("base_resp") or {}
    code = br.get("status_code")
    if code not in (0, None):
        print("\nx 克隆失败：" + explain(int(code), str(br.get("status_msg"))))
        if int(code) == 2038:
            print("\n这是账号权限问题，不是参数问题 —— 参数已经过了校验。")
            print("要去 platform.minimaxi.com 完成实名认证，并申请开通「音色克隆」权限。")
            print("开通后重跑同一条命令即可，file_id 还在。")
        return 3

    print("  克隆成功")

    # 7 天不用的克隆音色会被平台删掉 —— 立刻合成一句把它「激活」
    print("\n试合成一句（激活音色，防止 7 天不用被清掉）…")
    try:
        raw = await synth(vid, args.text or "你好，我是小智，这是我的克隆音色。")
        out = Path("/tmp") / f"{vid}_preview.mp3"
        out.write_bytes(raw)
        print(f"  成功，{len(raw)/1024:.0f}KB -> {out}")
    except Exception as e:
        print("  ! 试合成失败：" + str(e)[:160])

    if args.use:
        return await _set_default(vid)
    print(f"\n下一步：tools/voice_clone.py use {vid} 把它设成控制台默认音色")
    return 0


async def _set_default(voice_id: str) -> int:
    """把 voice_id 写进 config.yaml 的 tts_options.voice。"""
    sys.path.insert(0, str(ROOT))
    from console.backend.config_io import ConfigEditor

    cfg = ROOT / "config.yaml"
    editor = ConfigEditor(cfg)
    ok = editor.set_scalar("tts_options.voice", voice_id)
    if not ok:
        print("x 没找到 config.yaml 里的 tts_options.voice 字段")
        return 2
    editor.save()
    print(f"\n已把 config.yaml 的 tts_options.voice 设成 {voice_id}")
    print("提醒：控制台里音色来源要选「MiniMax 音色」才用得到它。")
    return 0


async def cmd_list(args) -> int:
    p = await list_voices()
    br = p.get("base_resp") or {}
    if br.get("status_code") not in (0, None):
        print("x " + explain(int(br.get("status_code")), str(br.get("status_msg"))))
        return 3
    sysv = p.get("system_voice") or []
    cloning = p.get("voice_cloning") or []
    gen = p.get("voice_generation") or []
    print(f"系统音色 {len(sysv)} 个 / 克隆音色 {len(cloning)} 个 / 设计音色 {len(gen)} 个")
    if cloning:
        print("\n克隆音色：")
        for v in cloning:
            print(f"  {v.get('voice_id')!r:38s} {v.get('voice_name','')}")
    if gen:
        print("\n设计音色：")
        for v in gen:
            print(f"  {v.get('voice_id')!r:38s} {v.get('voice_name','')}")
    return 0


async def cmd_try(args) -> int:
    raw = await synth(args.voice_id, args.text)
    out = Path("/tmp") / f"{args.voice_id}_preview.mp3"
    out.write_bytes(raw)
    print(f"合成成功 -> {out}")
    if args.speak:
        print("推给音箱…")
        import httpx

        from aiohttp import ClientSession
        from miservice import MiAccount, MiNAService

        token = json.loads((Path.home() / ".mi.token").read_text())
        tmpd = Path("/tmp") / ".mi.token.clonetest"
        shutil.copy2(Path.home() / ".mi.token", tmpd)
        os.chmod(tmpd, 0o600)
        async with ClientSession() as s:
            acc = MiAccount(s, "", "", str(tmpd))
            acc.token = token
            mina = MiNAService(acc)
            devs = await mina.device_list()
            speakers = [d for d in devs if d.get("hardware") == "L16A"] or [
                d for d in devs if str(d.get("miotDID")) == os.environ.get("MI_DID", "")
            ]
            if not speakers:
                raise SystemExit("没找到 L16A 音箱；用 tools/list_devices.py 看看账号下的设备")
            did = speakers[0]["deviceID"]
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            sock.close()
            import functools
            import tempfile
            import threading
            from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

            work = Path(tempfile.mkdtemp())
            (work / "t.mp3").write_bytes(raw)

            class _H(SimpleHTTPRequestHandler):
                def log_message(self, *a):
                    pass

            httpd = ThreadingHTTPServer(("0.0.0.0", 0), functools.partial(_H, directory=str(work)))
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            await asyncio.sleep(0.3)
            await mina.play_by_url(did, f"http://{host}:{port}/t.mp3", _type=1)
            print(f"  正在播：http://{host}:{port}/t.mp3")
            await asyncio.sleep(len(raw) / 32000 / 8 * 1.2 + 1)
            try:
                await mina.player_stop(did)
            except Exception:
                pass
            httpd.shutdown()
            shutil.rmtree(work, ignore_errors=True)
        tmpd.unlink(missing_ok=True)
    return 0


async def cmd_use(args) -> int:
    return await _set_default(args.voice_id)


async def main() -> int:
    ap = argparse.ArgumentParser(description="MiniMax 音色克隆工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="检查录音素材合不合格（不联网）")
    p.add_argument("file")

    p = sub.add_parser("clone", help="上传 + 克隆")
    p.add_argument("file")
    p.add_argument("--voice-id", required=True, help="音色 id（字母开头，8~256 位，只能字母数字_-）")
    p.add_argument("--name", default="", help="音色名字（仅记录用）")
    p.add_argument("--text", default="", help="顺便合成这句（建议填，能激活音色）")
    p.add_argument("--model", default="speech-2.6-turbo")
    p.add_argument("--use", action="store_true", help="克隆完直接设为默认音色")

    sub.add_parser("list", help="列出账号下的音色")

    p = sub.add_parser("try", help="用某个音色合成一句（可选推给音箱）")
    p.add_argument("voice_id")
    p.add_argument("--text", default="你好，我是小智。")
    p.add_argument("--speak", action="store_true", help="顺便推给音箱播")

    p = sub.add_parser("use", help="把音色设成控制台默认")
    p.add_argument("voice_id")

    args = ap.parse_args()
    if args.cmd == "check":
        path = Path(args.file).expanduser()
        chk = check_file(path)
        if chk.get("duration", 0) and chk.get("duration", 0) > 0:
            print(f"时长 {chk['duration']:.1f}s / {chk['size']/1024:.0f}KB / {chk.get('sample_rate',0)}Hz")
        for w in chk["warns"]:
            print("! " + w)
        for p_ in chk["problems"]:
            print("x " + p_)
        print("\n" + ("素材合格" if chk["ok"] else "素材不合格"))
        return 0 if chk["ok"] else 2
    if args.cmd == "clone":
        return await cmd_clone(args)
    if args.cmd == "list":
        return await cmd_list(args)
    if args.cmd == "try":
        return await cmd_try(args)
    if args.cmd == "use":
        return await cmd_use(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))