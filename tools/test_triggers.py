"""触发词匹配自测：确认哪些句子会被转给 AI，哪些还给小爱原生。

用法：
    ./xiaogpt-env/bin/python tools/test_triggers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import yaml

    from mybot.main import (
        patch_agent_wakeup,
        patch_need_ask_gpt,
        patch_strip_wakeup,
    )
    from xiaogpt.xiaogpt import MiGPT

    data = yaml.safe_load((ROOT / "config.yaml").read_text())
    keywords = data.get('keyword') or []
    excludes = data.get('exclude_keyword') or []
    wakeups = data.get('agent_wakeup') or []

    patch_need_ask_gpt(excludes)
    if wakeups:
        patch_agent_wakeup(wakeups)
        patch_strip_wakeup(wakeups)

    class Fake:
        config = type('C', (), {'keyword': keywords})()
        in_conversation = False

    fake = Fake()

    # (句子, 期望是否转给 AI, 说明)
    cases = [
        ("帮我介绍一下量子计算", True, "经典触发词"),
        ("请问今天上海天气", True, "请问开头"),
        ("问一下量子计算是什么", True, "问一下开头"),
        ("智能助手讲个笑话", True, "智能助手开头"),
        ("告诉我量子计算原理", True, "告诉我开头"),
        ("我想知道黑洞是什么", True, "我想知道开头"),
        ("我想问一下黑洞", True, "我想问一下开头"),
        ("为什么天空是蓝色的", True, "疑问词开头"),
        ("怎么回事", True, "疑问词开头"),
        ("什么是量子纠缠", True, "疑问词开头"),
        ("怎么煮鸡蛋", True, "疑问词开头"),
        ("如何提高睡眠质量", True, "疑问词开头"),
        ("能不能讲讲黑洞", True, "疑问词开头"),
        ("可不可以推荐一部电影", True, "疑问词开头"),
        ("有没有关于黑洞的书", True, "疑问词开头"),
        ("介绍一下北京", True, "介绍开头"),
        ("解释一下相对论", True, "解释开头"),
        ("说说量子计算", True, "说说开头"),
        ("讲讲黑洞", True, "讲讲开头"),
        ("请打开客厅灯", False, "命中触发词但被排除词拦下"),
        ("帮我打开空调", False, "命中触发词但被排除词拦下"),
        ("打开客厅灯", False, "智能家居，原生执行"),
        ("关闭卧室灯", False, "智能家居，原生执行"),
        ("播放音乐", False, "智能家居，原生执行"),
        ("调大音量", False, "智能家居，原生执行"),
        ("提醒我喝水", False, "智能家居，原生执行"),
        ("我问你个问题", False, "开头没触发词"),
        ("中国最好看的城市是哪里", False, "开头没触发词"),
        ("小爱同学", False, "只是唤醒词"),
        # ---- 智能体专属唤醒词 ----
        ("小智同学，帮我查天气", True, "专属唤醒词 + 逗号"),
        ("小智同学帮我查天气", True, "专属唤醒词无逗号"),
        ("小智，帮我查天气", True, "短唤醒词"),
        ("小智同学介绍一下量子计算", True, "专属唤醒词 + 正式问题"),
        ("小智同学，打开客厅灯", True, "唤醒词优先于排除词（显式点名就听小智的）"),
        ("你好啊小智同学", False, "唤醒词不在开头不算命中"),
    ]

    ok = True
    for query, expect, note in cases:
        got = MiGPT.need_ask_gpt(fake, {'query': query})
        mark = '✓' if got == expect else '✗'
        if got != expect:
            ok = False
        print(f'  {mark} {"转AI" if got else "原生"}  {query}    ({note})')

    print()
    print('全部通过' if ok else '有用例没过')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())