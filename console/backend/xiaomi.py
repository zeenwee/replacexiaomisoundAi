"""小米云连接封装。

三条铁律
--------
1. **只读 token**：用 ~/.mi.token 的临时副本，绝不碰原文件。
   miservice 的 login() 失败时会把 token 文件删掉 —— 那份 token 很贵。
2. **不调 login()**：直接用 token 里的 serviceToken 发请求。
3. **复用连接**：一次连上，播报/试听/记录都复用，省掉重复握手。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from console.backend import paths

ROOT = paths.BUNDLE_DIR
CONFIG = paths.CONFIG_PATH
ENVFILE = paths.ENV_PATH
TOKEN_PATH = paths.MI_TOKEN_PATH

# 音色 id -> 中文名（与 mybot/minimax_tts.py 的 AVAILABLE_VOICES 对应）
VOICE_LABELS: dict[str, str] = {
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


class XiaomiLink:
    """连着音箱的那条线。"""

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._mina = None
        self._device_id: str | None = None
        self._tts = None
        self._tmp_dir: Path | None = None

    # ---------------- 连接 ----------------

    async def connect(self):
        from miservice import MiAccount, MiNAService

        if self._mina is not None:
            return self._mina, self._device_id

        if not TOKEN_PATH.exists():
            raise RuntimeError(
                f"找不到 {TOKEN_PATH}，先跑 tools/xiaomi_verify_link.py --retry"
            )

        self._tmp_dir = Path(tempfile.mkdtemp(prefix="console_mina_"))
        token_copy = self._tmp_dir / ".mi.token"
        shutil.copy2(TOKEN_PATH, token_copy)
        os.chmod(token_copy, 0o600)

        self._session = ClientSession()
        account = MiAccount(self._session, "", "", str(token_copy))
        # 直接塞 token，不走 login()
        account.token = json.loads(token_copy.read_text())
        if "micoapi" not in (account.token or {}):
            raise RuntimeError("token 里没有 micoapi 字段")

        self._mina = MiNAService(account)
        devices = await self._mina.device_list()
        mi_did = os.getenv("MI_DID") or self._mi_did_from_config()
        for dev in devices or []:
            if str(dev.get("miotDID")) == str(mi_did):
                self._device_id = dev.get("deviceID")
                break
        if not self._device_id:
            names = [
                f"{d.get('name')}({d.get('miotDID')})" for d in devices or []
            ]
            raise RuntimeError(f"设备列表里没有 miotDID={mi_did}；当前有：{names}")
        return self._mina, self._device_id

    def _mi_did_from_config(self) -> str:
        try:
            from console.backend.config_io import ConfigEditor

            return str(ConfigEditor(CONFIG).get("mi_did", ""))
        except Exception:
            return ""

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        self._mina = None
        self._tts = None

    # ---------------- TTS ----------------

    async def _get_tts(self):
        """拿（或建）MinimaxTTS 实例。"""
        if self._tts is not None:
            return self._tts

        from console.backend.config_io import ConfigEditor, load_env
        from mybot.minimax_tts import MinimaxTTS
        from xiaogpt.config import Config

        # 密钥在 .env 里，不在 config.yaml —— 先载入环境变量
        load_env(ENVFILE)

        editor = ConfigEditor(CONFIG)
        raw = editor.load()
        config = Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})

        mina, device_id = await self.connect()
        self._tts = MinimaxTTS(mina, device_id, config)
        return self._tts

    async def speak(
        self,
        text: str,
        voice: str | None = None,
        speed: float | None = None,
        vol: float | None = None,
        pitch: int | None = None,
        use_minimax: bool = True,
    ) -> dict:
        """让音箱说话。

        use_minimax=True 用 MiniMax 音色；False 用小爱原生音色。
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("内容为空")

        if not use_minimax:
            mina, device_id = await self.connect()
            await mina.text_to_speech(device_id, text)
            return {"ok": True, "engine": "mi", "text": text}

        tts = await self._get_tts()
        tts.configure(voice=voice, speed=speed, vol=vol, pitch=pitch)

        async def one():
            yield text

        await tts.synthesize("zh-", one())
        return {"ok": True, "engine": "minimax", "voice": tts.voice, "text": text}

    async def preview(self, text: str, **kwargs) -> dict:
        """只合成 mp3 交给本机播放，不推音箱。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("内容为空")
        tts = await self._get_tts()
        tts.configure(
            voice=kwargs.get("voice"),
            speed=kwargs.get("speed"),
            vol=kwargs.get("vol"),
            pitch=kwargs.get("pitch"),
        )
        raw, duration = await tts._synth_bytes(text)
        return {
            "ok": True,
            "duration": duration,
            "voice": tts.voice,
            "audio": base64.b64encode(raw).decode(),
        }

    # ---------------- 记录 ----------------

    async def records(self, limit: int = 20) -> list[dict]:
        """拉音箱的对话记录。"""
        from xiaogpt.config import COOKIE_TEMPLATE, LATEST_ASK_API
        from xiaogpt.utils import parse_cookie_string

        mina, device_id = await self.connect()
        token = json.loads((self._tmp_dir / ".mi.token").read_text())
        cookie = COOKIE_TEMPLATE.format(
            device_id=device_id,
            service_token=token["micoapi"][1],
            user_id=token["userId"],
        )
        url = LATEST_ASK_API.format(
            hardware="L16A", timestamp=str(int(__import__("time").time() * 1000))
        )
        async with self._session.get(
            url,
            cookies=parse_cookie_string(cookie),
            timeout=__import__("aiohttp").ClientTimeout(total=15),
        ) as resp:
            payload = await resp.json(content_type=None)
        data = json.loads(payload.get("data") or "{}")
        records = data.get("records") or []
        out = []
        for rec in records[:limit]:
            said = ""
            for ans in rec.get("answers") or []:
                said = (ans.get("tts") or {}).get("text", "")
                if said:
                    break
            out.append(
                {
                    "time": rec.get("time"),
                    "query": rec.get("query", ""),
                    "xiaoai": said,
                }
            )
        return out

    async def devices(self) -> list[dict]:
        mina, _ = await self.connect()
        devices = await mina.device_list()
        return [
            {
                "name": d.get("name") or d.get("alias"),
                # 小米云设备列表里没有 model 字段，型号在 hardware 里
                # （L16A = Xiaomi Sound）
                "model": d.get("model") or d.get("hardware") or "",
                "firmware": d.get("romVersion") or "",
                "presence": d.get("presence") or "",
                "miot_did": d.get("miotDID"),
                "device_id": d.get("deviceID"),
                "mac": d.get("mac"),
                "is_speaker": d.get("hardware") == "L16A",
            }
            for d in devices or []
        ]


    # ---------------- 音箱系统音量 ----------------

    async def get_volume(self) -> dict:
        """读音箱的系统音量（0-100）。

        这是**音箱自己的**音量旋钮，和 MiniMax TTS 的 vol 是两回事：
        - vol  改的是合成出来的音频波形，放大过头会削波失真
        - 系统音量改的是喇叭的输出增益，怎么调都不会破音

        所以「让声音大一点」应该用这个，而不是靠 vol 堆。
        """
        import json as _json

        mina, device_id = await self.connect()
        st = await mina.player_get_status(device_id)
        info = _json.loads(st["data"]["info"])
        return {
            "ok": True,
            "volume": int(info.get("volume") or 0),
            "status": info.get("status"),
            "device_id": device_id,
        }

    async def set_volume(self, volume: int | None = None) -> dict:
        """设置音箱系统音量（0-100，超出范围会被设备钳制）。

        实测：设为 0 → 设备实际存 8（最小非零）；设 100 → 100；设 150 → 钳到 100。
        """
        import json as _json

        mina, device_id = await self.connect()
        if volume is None:
            cur = await self.get_volume()
            return cur
        try:
            want = int(round(float(volume)))
        except (TypeError, ValueError):
            raise ValueError("音量必须是数字")

        await mina.player_set_volume(device_id, max(0, min(100, want)))
        await asyncio.sleep(1.0)
        # 读回实际值（设备可能钳制）
        st = await mina.player_get_status(device_id)
        info = _json.loads(st["data"]["info"])
        return {
            "ok": True,
            "volume": int(info.get("volume") or 0),
            "requested": want,
            "device_id": device_id,
        }


# 全局单例：桌面控制台进程内共用一条连接
LINK = XiaomiLink()
