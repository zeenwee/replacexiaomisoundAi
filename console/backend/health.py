"""一键健康检查。

一次跑完，把「哪里坏了」直接说清楚，不用自己对着终端猜。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path

from console.backend import paths

ROOT = paths.BUNDLE_DIR
CONFIG = paths.CONFIG_PATH
ENVFILE = paths.ENV_PATH

OK = "ok"
WARN = "warn"
BAD = "bad"


def _item(name: str, level: str, detail: str, hint: str = "") -> dict:
    return {"name": name, "level": level, "detail": detail, "hint": hint}


async def _check_minimax() -> list[dict]:
    out = []
    from console.backend.agent import _api_base, _api_key, list_models

    base, key = _api_base(), _api_key()
    if not key:
        return [
            _item(
                "模型密钥",
                BAD,
                ".env 里没有 MINIMAX_API_KEY",
                "在「智能体」页填入，或手动写进 .env",
            )
        ]
    out.append(_item("模型密钥", OK, f"已配置（{key[:7]}...{key[-4:]}）"))

    try:
        models = await list_models()
        ids = [m["id"] for m in models]
        out.append(_item("模型接口", OK, f"连通，共 {len(ids)} 个模型可用"))
        from console.backend.agent import get_settings

        current = get_settings().get("model")
        if current in ids:
            out.append(_item("当前模型", OK, f"{current} 可用"))
        else:
            out.append(
                _item(
                    "当前模型",
                    BAD,
                    f"{current} 不在可用列表里",
                    f"可用：{', '.join(ids[:5])}",
                )
            )
    except Exception as e:
        out.append(
            _item(
                "模型接口",
                BAD,
                str(e)[:160],
                "检查 API 地址与密钥是否区域匹配（国内 minimaxi.com / 国际 minimax.io）",
            )
        )
    return out


async def _check_balance() -> list[dict]:
    import httpx

    from console.backend.agent import _api_base, _api_key

    key, base = _api_key(), _api_base()
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            resp = await c.get(
                base.replace("/v1", "") + "/v1/account/balance",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code == 200:
            data = resp.json()
            info = data.get("data") or {}
            bal = info.get("balance")
            if bal is None:
                return [_item("账户余额", WARN, "接口通但没返回余额字段")]
            return [
                _item(
                    "账户余额",
                    OK if bal > 0 else BAD,
                    f"{bal}",
                    "" if bal > 0 else "余额为 0，调用会报 402，去平台充值",
                )
            ]
        if resp.status_code in (401, 403):
            return [
                _item(
                    "账户余额",
                    WARN,
                    f"HTTP {resp.status_code}（余额接口要 admin key）",
                    "普通 key 看不到余额，去平台账户页查",
                )
            ]
        return [_item("账户余额", WARN, f"HTTP {resp.status_code}")]
    except Exception as e:
        return [_item("账户余额", WARN, f"查不到：{str(e)[:90]}")]


async def _check_xiaomi() -> list[dict]:
    out = []
    from console.backend.xiaomi import LINK

    token = Path.home() / ".mi.token"
    if not token.exists():
        return [
            _item(
                "小米登录态",
                BAD,
                f"没有 {token}",
                "跑 tools/xiaomi_verify_link.py --retry",
            )
        ]
    try:
        devices = await LINK.devices()
        out.append(_item("小米云登录", OK, f"连通，账号下 {len(devices)} 个设备"))
        speakers = [d for d in devices if d.get("is_speaker")]
        if speakers:
            out.append(
                _item("音箱识别", OK, f"找到 {speakers[0].get('name')}（{speakers[0].get('model')}）")
            )
        else:
            out.append(
                _item(
                    "音箱识别",
                    BAD,
                    "设备列表里没有 L16A",
                    "检查 config.yaml 的 mi_did，或跑 tools/list_devices.py",
                )
            )

        # 音箱系统音量：太小会听不清，太大反而吵
        try:
            vol = await LINK.get_volume()
            v = int(vol.get("volume") or 0)
            if v < 15:
                out.append(
                    _item(
                        "音箱音量",
                        WARN,
                        f"当前 {v}，偏小",
                        "在「设置」页调大（这是音箱系统音量，改增益不破音）",
                    )
                )
            else:
                out.append(_item("音箱音量", OK, f"当前 {v}"))
        except Exception as e:
            out.append(_item("音箱音量", WARN, f"读不到：{str(e)[:80]}"))
    except Exception as e:
        out.append(
            _item("小米云登录", BAD, str(e)[:160], "token 可能过期了，重新验证一次")
        )
    return out


def _ping(ip: str, timeout: int = 1) -> bool:
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout * 1000), ip],
            capture_output=True,
            timeout=timeout + 2,
        )
        return r.returncode == 0
    except Exception:
        return False


def _check_network() -> list[dict]:
    out = []
    ip = (os.getenv("SPEAKER_IP") or "").strip()
    if not ip:
        out.append(
            _item(
                "音箱局域网",
                WARN,
                "没设置 SPEAKER_IP，跳过",
                "查音箱 IP 后 export SPEAKER_IP=<你的音箱局域网IP>，再跑一次体检",
            )
        )
    elif _ping(ip):
        out.append(_item("音箱局域网", OK, f"{ip} 可达"))
    else:
        out.append(
            _item(
                "音箱局域网",
                WARN,
                f"{ip} ping 不通",
                "不影响云端链路；本机 HTTP 推音频可能受影响",
            )
        )
    try:
        s = socket.create_connection(("api.minimaxi.com", 443), timeout=6)
        s.close()
        out.append(_item("外网连通", OK, "MiniMax 接口可达"))
    except Exception as e:
        out.append(_item("外网连通", BAD, f"连不上：{str(e)[:80]}"))
    return out


def _check_config() -> list[dict]:
    from console.backend.config_io import ConfigEditor

    out = []
    editor = ConfigEditor(CONFIG)
    tts = editor.get("tts", "mi")
    out.append(_item("播报方式", OK, f"tts = {tts}"))
    if tts == "minimax":
        out.append(
            _item(
                "音色后端",
                OK,
                f"{editor.get('tts_options.voice', '未设')} @ "
                f"{editor.get('tts_options.model', '默认模型')}",
            )
        )
    kw = editor.get("keyword", []) or []
    ex = editor.get("exclude_keyword", []) or []
    out.append(
        _item(
            "触发规则",
            OK if kw else BAD,
            f"{len(kw)} 个触发词 / {len(ex)} 个排除词",
            "" if kw else "没有触发词，AI 永远不会被触发",
        )
    )
    env = ENVFILE
    if env.exists():
        mode = oct(env.stat().st_mode)[-3:]
        out.append(
            _item(
                ".env 权限",
                OK if mode == "600" else WARN,
                f"当前 {mode}",
                "" if mode == "600" else "建议 chmod 600 .env",
            )
        )
    return out


async def run_all() -> dict:
    from console.backend.engine import ENGINE

    items: list[dict] = []
    items += await _check_minimax()
    items += await _check_balance()
    items += await _check_xiaomi()
    items += _check_network()
    items += _check_config()

    status = ENGINE.status()
    items.append(
        _item(
            "引擎进程",
            OK if status["running"] else WARN,
            "运行中" if status["running"] else "没在跑",
            "" if status["running"] else "在「控制台」页点启动",
        )
    )

    counts = {
        "ok": sum(1 for i in items if i["level"] == OK),
        "warn": sum(1 for i in items if i["level"] == WARN),
        "bad": sum(1 for i in items if i["level"] == BAD),
    }
    overall = OK if counts["bad"] == 0 else (WARN if counts["warn"] == 0 else BAD)
    if counts["bad"] == 0 and counts["warn"] > 0:
        overall = WARN
    return {"items": items, "counts": counts, "overall": overall}
