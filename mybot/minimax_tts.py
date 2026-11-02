"""MiniMax 音色播报后端 —— 让 AI 回答用**不同于小爱原生**的声音。

为什么需要它
------------
`tts: mi` 走的是小米云的 `text_to_speech`，音色由**音箱自己**决定，
小米云的 ubus 接口里没有音色参数，所以没法让 AI 回答用别的声音。

这里换一条路：模型回答 → MiniMax TTS 合成 mp3 → 本机 HTTP 服务 →
音箱来拉取播放。音色、音色融合、音色复刻都能用。

实测过的关键事实
----------------
1. MiniMax 国内新端点 `https://api.minimaxi.com/v1/t2a_v2` 可用（旧的
   `api.minimax.chat` 那个 tetos 自带的 speaker 用不了，它要 group_id）。
2. **音箱能访问本机的局域网地址**（实测同网段直连能播）。
   本机和音箱必须在同一个局域网（同一网段），音箱要能主动访问到本机 IP。
3. `data:` URI 内联**不行**（设备不收），必须给一个真实可访问的 URL。
4. 合成延迟：20 字左右的一句话约 0.4~0.8s。比 `tts: mi` 多这半秒，
   因为 `tts: mi` 是小米云边生成边说，这里是先合成完再推给音箱。
5. 音箱播 URL 音频时，播放器 status 会一直停在 1，**不会自动回落**。
   所以不能靠 status 判断播完，得按 mp3 真实时长等。

降级策略
--------
MiniMax 合成失败（余额不足、限流、网络抖动）时，自动退回 `tts: mi`，
用小爱原生音色把这句念出来 —— 宁可音色不对，也不能一声不吭。
"""

from __future__ import annotations

import asyncio
import functools
import os
import random
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import AsyncIterator

from xiaogpt.tts.base import TTS
from xiaogpt.utils import get_hostname

API_PATH = "/v1/t2a_v2"
DEFAULT_BASE_URL = "https://api.minimaxi.com"
DEFAULT_MODEL = "speech-2.6-turbo"
DEFAULT_VOICE = "male-qn-qingse"

# 实测可用的系统音色（2026-11-01，用本账号逐个试出来的）
AVAILABLE_VOICES = [
    "female-shaonv",       # 少女音色
    "female-chengshu",     # 成熟女声
    "female-tianmei",      # 甜美女声
    "presenter_female",    # 女性主持人
    "presenter_male",      # 男性主持人
    "male-qn-qingse",      # 青涩青年音色
    "male-qn-jingying",    # 精英青年音色
    "male-qn-badao",       # 霸道青年音色
    "male-qn-daxuesheng",  # 青年大学生音色
    "audiobook_male_1",    # 有声书男声
    "audiobook_female_1",  # 有声书女声
    "lovely_girl",         # 可爱女声
    "sweet_girl",          # 甜心女声
    "energetic_boy",       # 活力男声
]


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静音，别刷屏
        pass


class MinimaxTTS(TTS):
    """用 MiniMax TTS 合成，再推给音箱播。"""

    def __init__(self, mina_service, device_id, config) -> None:
        super().__init__(mina_service, device_id, config)
        opts = dict(config.tts_options or {})
        self.api_key = (
            opts.pop("api_key", None)
            or os.getenv("MINIMAX_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        self.base_url = str(opts.pop("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.model = opts.pop("model", DEFAULT_MODEL)
        self.voice = opts.pop("voice", DEFAULT_VOICE)
        self.speed = opts.pop("speed", 1.0)
        self.vol = opts.pop("vol", 1.0)
        self.pitch = opts.pop("pitch", 0)
        self.sample_rate = int(opts.pop("sample_rate", 32000))
        self.bitrate = int(opts.pop("bitrate", 128000))

        self.dirname = tempfile.TemporaryDirectory(prefix="xiaogpt-mm-tts-")
        self._start_http_server()

    # ---------------- HTTP 服务（音箱来拉 mp3） ----------------

    def _start_http_server(self) -> None:
        port_range = range(8050, 8090)
        self.port = int(os.getenv("XIAOGPT_PORT", random.choice(port_range)))
        handler = functools.partial(_Handler, directory=self.dirname.name)
        httpd = ThreadingHTTPServer(("", self.port), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.hostname = get_hostname()

    def configure(
        self,
        voice: str | None = None,
        model: str | None = None,
        speed: float | None = None,
        vol: float | None = None,
        pitch: int | None = None,
    ) -> None:
        # 运行时改参数。桌面控制台切音色时用得上 —— 不用重建对象，
        # 也就不会重起一遍本地 HTTP 服务、换端口。
        if voice:
            self.voice = voice
        if model:
            self.model = model
        if speed is not None:
            self.speed = speed
        if vol is not None:
            self.vol = vol
        if pitch is not None:
            self.pitch = pitch

    # ---------------- 合成 ----------------

    async def _synth_bytes(self, text: str) -> tuple[bytes, float]:
        import httpx

        body = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": self.voice,
                "speed": self.speed,
                "vol": self.vol,
                "pitch": self.pitch,
            },
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "bitrate": self.bitrate,
                "format": "mp3",
            },
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                self.base_url + API_PATH,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            payload = resp.json()

        base_resp = payload.get("base_resp") or {}
        if base_resp.get("status_code") not in (0, None):
            raise RuntimeError(
                "MiniMax TTS 失败："
                + str(base_resp.get("status_msg") or payload.get("message"))[:120]
            )
        hex_audio = (payload.get("data") or {}).get("audio") or ""
        if not hex_audio:
            raise RuntimeError("MiniMax TTS 没有返回音频")
        raw = bytes.fromhex(hex_audio)
        ms = (payload.get("extra_info") or {}).get("audio_length", 0)
        return raw, float(ms) / 1000.0

    async def _make_audio_file(self, text: str) -> tuple[Path, float]:
        raw, duration = await self._synth_bytes(text)
        out = Path(self.dirname.name) / f"{abs(hash(text)) % 10 ** 10}.mp3"
        out.write_bytes(raw)
        return out, duration

    # ---------------- 播报 ----------------

    async def _speak_native(self, text: str) -> None:
        """降级：用小爱原生音色念。"""
        try:
            await self.mina_service.text_to_speech(self.device_id, text)
        except Exception:
            pass

    async def _wait_play(self, duration: float) -> None:
        # 不能用基类的 wait_for_duration：它靠播放器 status 回到 0 判断播完，
        # 但音箱播 URL 音频时 status 会一直停在 1（实测 30 秒不回落），会死等。
        # 这里按 mp3 的真实时长等 —— 时长是合成接口给的 audio_length，够准。
        await asyncio.sleep(duration + 0.3)

    async def synthesize(self, lang: str, text_stream: AsyncIterator[str]) -> None:
        async for text in text_stream:
            text = (text or "").strip()
            if not text:
                continue
            try:
                path, duration = await self._make_audio_file(text)
            except Exception as e:
                print(f"[MiniMax TTS] 合成失败，退回小爱原生音色：{e}")
                await self._speak_native(text)
                continue
            url = f"http://{self.hostname}:{self.port}/{path.name}"
            try:
                await self.mina_service.play_by_url(self.device_id, url, _type=1)
                print(
                    f"[MiniMax TTS] {self.voice} 推送 {duration:.1f}s 音频：{text[:30]}",
                    flush=True,
                )
                await self._wait_play(duration)
            except Exception as e:
                print(f"[MiniMax TTS] 播放失败，退回小爱原生音色：{e}")
                await self._speak_native(text)
