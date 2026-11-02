#!/usr/bin/env python3
"""MiniMax 接入自检：列模型 + 按 config.yaml 的真实参数打一次问答。

用法（项目根目录执行）：

    ./xiaogpt-env/bin/python tools/check_minimax.py
    ./xiaogpt-env/bin/python tools/check_minimax.py --model MiniMax-M3.1-Flash
    ./xiaogpt-env/bin/python tools/check_minimax.py --no-stream

自检做三件事：
    1. GET  {base_url}/models           —— 确认 key 有效、区域对得上、模型名没写错
    2. POST {base_url}/chat/completions —— 用和智能体完全一样的参数真打一次
    3. 打印首字延迟 / 总耗时 / 回答正文

自检不碰小米云、不需要音箱在线，所以在换 key、换模型、换区域的时候
先跑它，能把「是模型侧的问题」和「是小米链路的问题」切开。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"

RESERVED_OPTIONS = {"system_prompt", "agent_name", "stream"}


def load_env(path: Path) -> None:
    """极简 .env 解析：KEY=VALUE，'#' 开头是注释。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def mask(key: str) -> str:
    if not key:
        return "(空)"
    if len(key) <= 10:
        return key[:3] + "****"
    return key[:6] + "..." + key[-4:]


def die(msg: str, code: int = 1) -> None:
    print(f"\n✗ {msg}")
    sys.exit(code)


def diagnose(status: int, body: str) -> str:
    """把 HTTP 错误翻译成可执行的建议。"""
    low = body.lower()
    if status == 401:
        return (
            "401：密钥无效或区域不匹配。\n"
            "  · 国内区 key → base_url 必须是 https://api.minimaxi.com/v1\n"
            "  · 国际区 key → base_url 必须是 https://api.minimax.io/v1\n"
            "  · key 在 https://platform.minimaxi.com/user-center/basic-information/interface-key"
        )
    if status == 404:
        return "404：地址或模型不存在。检查 base_url 有没有多写 /v1/chat/completions。"
    if status == 429:
        return "429：限流或额度不足。去平台看余额和速率限制。"
    if status == 400 and "thinking" in low:
        return (
            "400 且提到 thinking：这个模型关不掉思考。\n"
            "  · MiniMax-M3.1-Flash 强制思考 → 把 extra_body 里的 thinking 整段删掉，\n"
            "    改成 reasoning_effort: low\n"
            "  · MiniMax-M2.x 系列 → thinking 写了也忽略，无所谓"
        )
    if status == 400:
        return "400：请求参数不合法。最常见是 extra_body 里写了模型不认的字段。"
    if status >= 500:
        return f"{status}：MiniMax 服务端异常，稍后重试。"
    return f"{status}：{body[:500]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="MiniMax 接入自检")
    ap.add_argument("--model", help="临时覆盖 config.yaml 里的模型名")
    ap.add_argument("--base-url", help="临时覆盖 config.yaml 里的 base_url")
    ap.add_argument("--no-stream", action="store_true", help="只测非流式")
    ap.add_argument("--timeout", type=float, default=60.0, help="单次请求超时秒数")
    args = ap.parse_args()

    load_env(ENV_PATH)

    if not CONFIG_PATH.exists():
        die(f"找不到配置文件：{CONFIG_PATH}")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    gpt_options = dict(config.get("gpt_options") or {})

    api_base = (
        args.base_url
        or config.get("api_base")
        or os.getenv("MINIMAX_BASE_URL")
        or "https://api.minimaxi.com/v1"
    ).rstrip("/")
    api_key = (
        config.get("openai_key")
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    model = args.model or gpt_options.get("model") or "MiniMax-M3"

    if not api_key:
        die(
            "没有模型密钥。写进 .env：\n"
            "    echo 'MINIMAX_API_KEY=你的key' >> .env && chmod 600 .env"
        )

    body_options = {k: v for k, v in gpt_options.items() if k not in RESERVED_OPTIONS}
    # ⚠️ extra_body 是 openai SDK 的概念：SDK 会把它「摊平」到请求体顶层再发出去。
    # 这里直接用 httpx 发原始 JSON，所以必须手动摊平，
    # 否则 MiniMax 收到的是 {"extra_body": {...}} 这种它不认的结构，
    # thinking 设置会静默失效，模型的思考过程就混进正文里被音箱念出来了。
    extra = body_options.pop("extra_body", None) or {}
    if not isinstance(extra, dict):
        extra = {}
    body_options = {**body_options, **extra}
    body_options["model"] = model

    print("=" * 60)
    print("MiniMax 接入自检")
    print("=" * 60)
    print(f"base_url : {api_base}")
    print(f"api_key  : {mask(api_key)}")
    print(f"model    : {model}")
    print(f"参数     : {json.dumps(body_options, ensure_ascii=False)}")
    print()

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # ---- 1. 列模型 ----
    print("── 1/2 拉取模型列表 ──")
    try:
        with httpx.Client(timeout=args.timeout) as client:
            resp = client.get(f"{api_base}/models", headers=headers)
    except Exception as e:
        die(f"连不上 {api_base}：{e}")

    if resp.status_code != 200:
        die(diagnose(resp.status_code, resp.text))
    try:
        models = [m.get("id") for m in resp.json().get("data", [])]
    except Exception:
        models = []
    print(f"✓ key 有效，区域匹配（共 {len(models)} 个模型）")
    print(f"  {', '.join(models) if models else '(接口没返回模型列表)'}")
    if model not in models and models:
        print(f"  ⚠️  配置里的 {model} 不在列表里，可能是名字写错了")
    print()

    # ---- 2. 真打一次 ----
    print("── 2/2 实测一次问答 ──")
    messages = [
        {
            "role": "system",
            "content": gpt_options.get("system_prompt")
            or config.get("prompt")
            or "你是一个语音助手，简洁地回答。",
        },
        {"role": "user", "content": "用一句话介绍你自己。"},
    ]
    payload = {**body_options, "messages": messages}

    try:
        with httpx.Client(timeout=args.timeout) as client:
            if args.no_stream:
                t0 = time.perf_counter()
                resp = client.post(
                    f"{api_base}/chat/completions", headers=headers, json=payload
                )
                elapsed = time.perf_counter() - t0
                if resp.status_code != 200:
                    die(diagnose(resp.status_code, resp.text))
                content = resp.json()["choices"][0]["message"].get("content") or ""
                print(f"✓ 非流式 OK，耗时 {elapsed:.2f}s")
                print(f"  回答：{content}")
            else:
                t0 = time.perf_counter()
                t_first = None
                chunks: list[str] = []
                with client.stream(
                    "POST",
                    f"{api_base}/chat/completions",
                    headers=headers,
                    json={**payload, "stream": True},
                ) as resp:
                    if resp.status_code != 200:
                        resp.read()
                        die(diagnose(resp.status_code, resp.text))
                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = (
                                json.loads(data)["choices"][0].get("delta") or {}
                            ).get("content")
                        except Exception:
                            continue
                        if not delta:
                            continue
                        if t_first is None:
                            t_first = time.perf_counter() - t0
                        chunks.append(delta)
                total = time.perf_counter() - t0
                content = "".join(chunks)
                print(
                    f"✓ 流式 OK，首字 {t_first:.2f}s / 总耗时 {total:.2f}s"
                    if t_first is not None
                    else f"✓ 流式 OK，总耗时 {total:.2f}s（没有拿到正文）"
                )
                print(f"  回答：{content}")
    except httpx.HTTPError as e:
        die(f"请求失败：{e}")

    print()
    print("=" * 60)
    print("✓ 模型侧全部通过。音箱侧再跑：./run.sh")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
