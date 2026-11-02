#!/usr/bin/env python3
"""模型延迟基准：帮你在「快」和「便宜」之间做取舍。

音箱场景最关键的是 **TTFT（开口延迟）** —— 从请求发出到第一个正文 token
出来的时间。音箱要等这个 token 才会开始念，延迟直接决定「秒回」还是「卡一下」。

用法：
    ./xiaogpt-env/bin/python tools/bench_model.py
    ./xiaogpt-env/bin/python tools/bench_model.py --rounds 15
    ./xiaogpt-env/bin/python tools/bench_model.py --models MiniMax-M3 MiniMax-M3.1-Flash

默认对比的几组参数：
    MiniMax-M3              + thinking.disabled   （关思考，直接作答）
    MiniMax-M3.1-Flash      + reasoning_effort=low（强制思考，思考深度调低）
    MiniMax-M3.1-Flash      + 默认 effort
    MiniMax-M2.7            + thinking.disabled
    MiniMax-M2.7-highspeed  + thinking.disabled

注意：会真的调 API 并消耗额度，每次调用按平台价计费。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
BASE = "https://api.minimaxi.com/v1"

DEFAULT_CANDIDATES = [
    ("M3 @ thinking.disabled", "MiniMax-M3", {"thinking": {"type": "disabled"}}),
    ("M3.1-Flash @ effort=low", "MiniMax-M3.1-Flash", {"reasoning_effort": "low"}),
    ("M3.1-Flash @ effort=default", "MiniMax-M3.1-Flash", None),
    ("M2.7 @ thinking.disabled", "MiniMax-M2.7", {"thinking": {"type": "disabled"}}),
    ("M2.7-highspeed @ thinking.disabled", "MiniMax-M2.7-highspeed", {"thinking": {"type": "disabled"}}),
]

QUESTIONS = [
    ("自我介绍", "用一句话介绍你自己。"),
    ("知识问答", "简单说说量子计算是什么。"),
    ("算术", "17乘以23等于多少？"),
]

SYSTEM = (
    "你是音箱上的语音助手，你的回答会被直接朗读出来。"
    "要求：1）用中文口语回答，控制在 100 字以内；"
    "2）只讲结论，不要用 Markdown 列表符、编号、标题、代码块或链接；"
    "3）不要复述问题；4）不知道就直说不知道，不要编造。"
)


def load_key() -> str:
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("MINIMAX_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return os.getenv("MINIMAX_API_KEY", "")


def one_call(headers, model, extra, question) -> tuple[float | None, float, str]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
        ],
        "max_tokens": 500,
        "stream": True,
    }
    if extra:
        body.update(extra)

    t0 = time.perf_counter()
    ttft = None
    chunks: list[str] = []
    with httpx.stream("POST", f"{BASE}/chat/completions", headers=headers,
                      json=body, timeout=120) as resp:
        if resp.status_code != 200:
            resp.read()
            raise RuntimeError(f"HTTP {resp.status_code} {resp.text[:160]}")
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            delta = chunk["choices"][0].get("delta") or {}
            if delta.get("content"):
                if ttft is None:
                    ttft = time.perf_counter() - t0
                chunks.append(delta["content"])
    return ttft, time.perf_counter() - t0, "".join(chunks)


def main() -> int:
    ap = argparse.ArgumentParser(description="MiniMax 模型延迟基准")
    ap.add_argument("--rounds", type=int, default=10, help="每类问题跑几轮")
    ap.add_argument("--models", nargs="*", help="只测这些模型名（默认测预置组合）")
    args = ap.parse_args()

    key = load_key()
    if not key:
        print("✗ 没有 MINIMAX_API_KEY，先跑 tools/setup_wizard.py 或写进 .env")
        return 1
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if args.models:
        candidates = [
            (m, m, {"thinking": {"type": "disabled"}} if m != "MiniMax-M3.1-Flash"
                   else {"reasoning_effort": "low"})
            for m in args.models
        ]
    else:
        candidates = DEFAULT_CANDIDATES

    print(f"每类问题 {args.rounds} 轮，交错采样，共 {args.rounds * len(QUESTIONS)} 次/候选")
    print("（会真的调 API 消耗额度）\n")

    results = {name: [] for name, _, _ in candidates}
    for _, question in QUESTIONS:
        for _ in range(args.rounds):
            for name, model, extra in candidates:
                try:
                    ttft, total, text = one_call(headers, model, extra, question)
                except Exception as e:
                    print(f"  [{name}] 请求失败：{e}")
                    results[name].append((None, None, ""))
                    continue
                results[name].append((ttft, total, text))

    print()
    for name, model, extra in candidates:
        rows = [r for r in results[name] if r[0] is not None]
        if not rows:
            print(f"── {name}\n   全部失败\n")
            continue
        ttfts = [r[0] * 1000 for r in rows]
        totals = [r[1] * 1000 for r in rows]
        leaks = sum(1 for r in rows if "<think>" in r[2])
        print(f"── {name}")
        print(f"   TTFT 中位 {statistics.median(ttfts):6.0f}ms   "
              f"p25 {statistics.quantiles(ttfts, n=4)[0]:6.0f}ms   "
              f"p75 {statistics.quantiles(ttfts, n=4)[2]:6.0f}ms   "
              f"最慢 {max(ttfts):6.0f}ms")
        print(f"   总耗时 中位 {statistics.median(totals):6.0f}ms")
        print(f"   正文含 <think> : {leaks}/{len(rows)} 次"
              f"{'  ← 需要 ThinkFilter 兜底' if leaks else ''}")
        print()

    print("=" * 66)
    print("选型参考：TTFT 越低音箱开口越快；M3.1-Flash 单价约为 M3 的一半")
    print("（官方 ¥1/¥4 vs M3 的两倍以上），但 M3.1-Flash 强制思考会拖慢首字。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
