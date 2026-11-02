"""智能体：模型列表、切换、对话测试。"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from console.backend import paths

ROOT = paths.BUNDLE_DIR
CONFIG = paths.CONFIG_PATH
ENVFILE = paths.ENV_PATH

# 实测参考值（2026-11-01，本机 MiniMax 国内区）
MODEL_HINTS = {
    "MiniMax-M3": "TTFT 中位 0.79s · 思考关闭后直答 · 建议用这个",
    "MiniMax-M3.1-Flash": "TTFT 中位 1.18s（比 M3 慢）· 但便宜一半以上",
    "MiniMax-M2.7": "思考 100% 泄漏 · 不建议",
    "MiniMax-M2.7-highspeed": "最慢（2.8s）· 不建议",
    "MiniMax-M2.5": "可用",
    "MiniMax-M2.1": "404 已下线",
    "MiniMax-M2": "404 已下线",
}


def _api_base() -> str:
    from console.backend.config_io import ConfigEditor

    base = ConfigEditor(CONFIG).get("api_base") or ""
    return base.rstrip("/")


def _api_key() -> str:
    from console.backend.config_io import ConfigEditor, EnvFile

    key = ConfigEditor(CONFIG).get("openai_key") or ""
    if key:
        return str(key).strip()
    env = EnvFile(ENVFILE)
    return (env.get("MINIMAX_API_KEY") or env.get("OPENAI_API_KEY") or "").strip()


async def list_models() -> list[dict]:
    """拉 /v1/models，带实测参考值。"""
    base = _api_base()
    key = _api_key()
    if not base or not key:
        raise RuntimeError("API 地址或密钥没配")
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.get(
            base + "/models", headers={"Authorization": f"Bearer {key}"}
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        data = resp.json()
    out = []
    for item in data.get("data", []):
        mid = item.get("id")
        out.append({"id": mid, "hint": MODEL_HINTS.get(mid, "")})
    return out


async def ask(question: str) -> dict:
    """本地对话测试台：不经过音箱，直接问智能体。"""
    from console.backend.config_io import ConfigEditor, load_env
    from mybot.compat import stub_unused_bots

    # minimax_agent 会 import xiaogpt.bot（连带一堆用不到的 bot 依赖），
    # 打包态下那些依赖可能不存在 —— 先换空壳再 import。
    stub_unused_bots()

    from mybot.minimax_agent import MiniMaxAgent
    from xiaogpt.config import Config

    # 密钥在 .env 里，不在 config.yaml —— 先载入环境变量
    load_env(ENVFILE)

    editor = ConfigEditor(CONFIG)
    raw = editor.load()
    fields = Config.__dataclass_fields__
    config = Config(**{k: v for k, v in raw.items() if k in fields})
    agent = MiniMaxAgent.from_config(config)

    chunks: list[str] = []
    async for sentence in agent.ask_stream(question):
        chunks.append(sentence)
    return {"answer": "".join(chunks), "sentences": len(chunks)}


def get_settings() -> dict:
    from console.backend.config_io import ConfigEditor, EnvFile

    editor = ConfigEditor(CONFIG)
    env = EnvFile(ENVFILE)
    key = env.get("MINIMAX_API_KEY") or env.get("OPENAI_API_KEY")
    return {
        "api_base": editor.get("api_base", ""),
        "model": editor.get("gpt_options.model", ""),
        "agent_name": editor.get("gpt_options.agent_name", ""),
        "system_prompt": editor.get("gpt_options.system_prompt", ""),
        "max_tokens": editor.get("gpt_options.max_tokens", 500),
        "thinking_disabled": bool(
            (editor.get("gpt_options.extra_body.thinking") or {}).get("type")
            == "disabled"
        ),
        "api_key_set": bool(key),
        "api_key_masked": (key[:7] + "..." + key[-4:]) if key else "",
        "tts": editor.get("tts", "mi"),
        "tts_options": editor.get("tts_options", {}) or {},
        "stream": editor.get("stream", True),
        "mute_xiaoai": editor.get("mute_xiaoai", True),
        "mi_did": editor.get("mi_did", ""),
    }


def update_settings(patch: dict) -> dict:
    """改配置（写回 config.yaml / .env，注释保留）。"""
    from console.backend.config_io import ConfigEditor, EnvFile

    editor = ConfigEditor(CONFIG)
    changed: list[str] = []

    scalar_map = {
        "model": "gpt_options.model",
        "agent_name": "gpt_options.agent_name",
        "system_prompt": "gpt_options.system_prompt",
        "max_tokens": "gpt_options.max_tokens",
        "api_base": "api_base",
        "mi_did": "mi_did",
        "tts": "tts",
        "stream": "stream",
        "mute_xiaoai": "mute_xiaoai",
    }
    for key, path in scalar_map.items():
        if key in patch and patch[key] is not None:
            if editor.set_scalar(path, patch[key]):
                changed.append(path)

    if "thinking_disabled" in patch:
        path = "gpt_options.extra_body.thinking.type"
        editor.set_scalar(path, "disabled" if patch["thinking_disabled"] else "adaptive")
        changed.append(path)

    if isinstance(patch.get("tts_options"), dict):
        for k, v in patch["tts_options"].items():
            if editor.set_scalar(f"tts_options.{k}", v):
                changed.append(f"tts_options.{k}")

    if patch.get("api_key"):
        env = EnvFile(ENVFILE)
        env.set("MINIMAX_API_KEY", str(patch["api_key"]).strip())
        changed.append("MINIMAX_API_KEY")

    editor.save()
    return {"changed": changed, "settings": get_settings()}
