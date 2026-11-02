#!/usr/bin/env python3
"""控制台全功能自检：把界面上每个功能都真跑一遍。

    ./xiaogpt-env/bin/python -m console.selftest

会真的让音箱说一句话（speak 那一步），先确认家里没人睡觉。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = "✓"
FAIL = "✗"


def main() -> int:
    from console.app import Api
    from console.backend.loop import start as start_loop

    start_loop()
    api = Api()
    results: list[tuple[str, bool, str]] = []

    def call(name: str, payload: dict | None = None, timeout: float = 90):
        r = api.call(name, payload or {})
        if r.get("immediate"):
            return r["data"]
        tid = r["task_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = api.task_status(tid)
            if st.get("done"):
                if st.get("error"):
                    raise RuntimeError(st["error"])
                return st["result"]
            time.sleep(0.3)
        raise TimeoutError(name)

    def check(label, fn):
        try:
            detail = fn()
            results.append((label, True, str(detail)))
            print(f"  {PASS} {label}: {detail}", flush=True)
        except Exception as e:
            results.append((label, False, f"{type(e).__name__}: {e}"))
            print(f"  {FAIL} {label}: {type(e).__name__}: {e}", flush=True)

    print("=== 界面数据 ===")
    check("概览", lambda: json.dumps(call("get_overview")["settings"], ensure_ascii=False)[:110])
    check("音色列表", lambda: f"{len(call('list_voices')['voices'])} 个音色 / "
                               f"{len(call('list_voices')['models'])} 个 TTS 模型")
    check("模型列表(真实API)", lambda: f"{len(call('list_models')['models'])} 个模型")
    check("读设置", lambda: f"model={call('get_settings')['model']} "
                             f"voice={(call('get_settings')['tts_options'] or {}).get('voice')}")

    print()
    print("=== 触发词 ===")
    check("读规则", lambda: f"{len(call('get_rules')['keyword'])} 触发 / "
                            f"{len(call('get_rules')['exclude_keyword'])} 排除")
    check("判断:帮我查天气", lambda: call("judge", {"query": "帮我查天气"})["detail"])
    check("判断:请打开客厅灯", lambda: call("judge", {"query": "请打开客厅灯"})["detail"])
    check("判断:开始对话", lambda: call("judge", {"query": "开始对话"})["detail"])
    check("判断:中国哪个省人美", lambda: call("judge", {"query": "中国哪个省人美"})["detail"])

    print()
    print("=== 引擎 ===")
    # 控制台只允许存在一个引擎（两个会同时轮询小米云，音箱答两遍）。
    # 所以先把别的进程里跑着的停掉，保证这一步测的是「本进程起引擎」这条路径。
    ext = (api.engine_status() or {}).get("external") or []
    if ext:
        print(f"  （先停掉外部引擎 pid {ext}，避免两个引擎同时轮询）")
        api.engine_stop()
    check("查状态", lambda: json.dumps(call("engine_status"), ensure_ascii=False))
    check("启动引擎", lambda: json.dumps(call("engine_start"), ensure_ascii=False))
    time.sleep(3)
    check("日志有内容", lambda: f"{len(call('engine_logs', {'offset': 0})['lines'])} 行")
    check("停止引擎", lambda: json.dumps(call("engine_stop"), ensure_ascii=False))

    print()
    print("=== 真实能力 ===")
    check("健康检查", lambda: json.dumps(call("health")["counts"], ensure_ascii=False))
    check("对话记录", lambda: f"{len(call('history', {'limit': 10}))} 条")
    check("设备列表", lambda: f"{len(call('devices'))} 个设备")
    check("本地试听合成", lambda: f"{call('preview', {'text': '这是一段本地试听测试。'})['duration']:.1f}s 音频")
    check("本地对话测试台", lambda: (call("ask", {"question": "用一句话介绍你自己"})["answer"] or "")[:60])
    check("让音箱说话", lambda: json.dumps(
        call("speak", {"text": "小智控制台自检通过，这是一段测试语音。"}), ensure_ascii=False))

    print()
    bad = [r for r in results if not r[1]]
    print(f"总计 {len(results)} 项，通过 {len(results) - len(bad)}，失败 {len(bad)}")
    for label, _, detail in bad:
        print(f"  {FAIL} {label}: {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
