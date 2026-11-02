"""音色与 TTS 模型清单。"""

from __future__ import annotations

from mybot.minimax_tts import AVAILABLE_VOICES

from console.backend.xiaomi import VOICE_LABELS

# 实测可用的 TTS 模型（2026-11-01）
TTS_MODELS = [
    {"id": "speech-2.6-turbo", "label": "speech-2.6-turbo（快，日常推荐）"},
    {"id": "speech-2.6-hd", "label": "speech-2.6-hd（音质最好，慢一点）"},
    {"id": "speech-2.5-turbo-preview", "label": "speech-2.5-turbo-preview"},
    {"id": "speech-2.5-hd-preview", "label": "speech-2.5-hd-preview"},
    {"id": "speech-02-hd", "label": "speech-02-hd"},
    {"id": "speech-01-turbo", "label": "speech-01-turbo"},
]


# 音色来源：智能体回答用什么声音念
#   minimax = MiniMax TTS 合成（音色可选，14 个系统音色 + 克隆音色）
#   mi      = 小爱原生音色（音箱自己发的声，见 xiaogpt/tts/mi.py）
TTS_SOURCES = [
    {
        "id": "minimax",
        "label": "MiniMax 音色（系统音色 + 克隆音色，能和小爱区分开）",
        "hint": "响度靠下面的「响度」滑块调；每翻一倍 +6dB，超过 5 会削波破音",
    },
    {
        "id": "mi",
        "label": "小爱原生音色（音箱自带，最大声、零延迟）",
        "hint": "声音是音箱自己发的，比 MiniMax 合成的声音大；代价是分不清哪句是小智答的",
    },
]


def list_tts_sources() -> list[dict]:
    return list(TTS_SOURCES)


def list_voices() -> list[dict]:
    """系统音色（本地白名单，不用联网）。"""
    return [
        {"id": v, "label": VOICE_LABELS.get(v, v), "kind": "system"}
        for v in AVAILABLE_VOICES
    ]


async def list_cloned_voices() -> dict:
    """拉账号下**已克隆**的音色（MiniMax /v1/get_voice）。

    为什么单独一个接口：克隆音色不在本地白名单里 —— 只有平台知道有哪些。
    账号没开通克隆权限时，voice_cloning 是空的列表，不会报错。
    """
    import httpx

    from console.backend.config_io import load_env
    from console.backend.paths import ENV_PATH

    load_env(ENV_PATH)
    import os

    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        return {"ok": False, "error": "没有配置 MiniMax API key", "voices": []}

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                "https://api.minimaxi.com/v1/get_voice",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"voice_type": "all"},
            )
        payload = r.json()
    except Exception as e:
        return {"ok": False, "error": f"拉音色列表失败：{e}", "voices": []}

    br = payload.get("base_resp") or {}
    if br.get("status_code") not in (0, None):
        return {
            "ok": False,
            "error": f"{br.get('status_msg','接口错误')}（{br.get('status_code')}）",
            "voices": [],
        }

    out = []
    for v in payload.get("voice_cloning") or []:
        vid = v.get("voice_id") or ""
        if not vid:
            continue
        out.append(
            {
                "id": vid,
                "label": v.get("voice_name") or vid,
                "kind": "cloned",
                "created": v.get("created_time", ""),
            }
        )
    for v in payload.get("voice_generation") or []:
        vid = v.get("voice_id") or ""
        if not vid:
            continue
        out.append(
            {
                "id": vid,
                "label": v.get("voice_name") or vid,
                "kind": "designed",
                "created": v.get("created_time", ""),
            }
        )
    return {"ok": True, "voices": out}


def list_tts_models() -> list[dict]:
    return list(TTS_MODELS)