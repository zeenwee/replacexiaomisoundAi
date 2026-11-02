"""触发词规则：读改 + 实时判断一句话会走哪条路。"""

from __future__ import annotations

import re
from pathlib import Path

from console.backend import paths

ROOT = paths.BUNDLE_DIR
CONFIG = paths.CONFIG_PATH

# mybot/main.py 里那套口语化对话控制词（保持一致）
ALIASES = {
    "开始对话": "开始持续对话",
    "开启对话": "开始持续对话",
    "连续对话": "开始持续对话",
    "进入对话": "开始持续对话",
    "结束对话": "结束持续对话",
    "退出对话": "结束持续对话",
    "停止对话": "结束持续对话",
    "不说了": "结束持续对话",
}


def _editor():
    from console.backend.config_io import ConfigEditor

    return ConfigEditor(CONFIG)


def get_rules() -> dict:
    editor = _editor()
    return {
        "agent_wakeup": list(editor.get("agent_wakeup", []) or []),
        "keyword": list(editor.get("keyword", []) or []),
        "exclude_keyword": list(editor.get("exclude_keyword", []) or []),
        "start_conversation": editor.get("start_conversation", "开始持续对话"),
        "end_conversation": editor.get("end_conversation", "结束持续对话"),
        "aliases": ALIASES,
    }


def set_rules(patch: dict) -> dict:
    editor = _editor()
    changed = []
    if isinstance(patch.get("agent_wakeup"), list):
        items = [str(x).strip() for x in patch["agent_wakeup"] if str(x).strip()]
        if editor.set_list("agent_wakeup", items):
            changed.append("agent_wakeup")
    if isinstance(patch.get("keyword"), list):
        items = [str(x).strip() for x in patch["keyword"] if str(x).strip()]
        if editor.set_list("keyword", items):
            changed.append("keyword")
    if isinstance(patch.get("exclude_keyword"), list):
        items = [str(x).strip() for x in patch["exclude_keyword"] if str(x).strip()]
        if editor.set_list("exclude_keyword", items):
            changed.append("exclude_keyword")
    if patch.get("start_conversation"):
        if editor.set_scalar("start_conversation", str(patch["start_conversation"]).strip()):
            changed.append("start_conversation")
    if patch.get("end_conversation"):
        if editor.set_scalar("end_conversation", str(patch["end_conversation"]).strip()):
            changed.append("end_conversation")
    editor.save()
    return {"changed": changed, "rules": get_rules()}


def judge(query: str) -> dict:
    """判断一句话会怎么处理。

    规则和 xiaogpt 运行时完全一致：
    1. 命中触发词前缀 -> 转给 AI（先剥掉触发词）
    2. 剥完之后命中排除词 -> 还给小爱原生
    3. 连续对话模式下，什么都转给 AI
    """
    rules = get_rules()
    wakeups = rules.get("agent_wakeup") or []
    keywords = rules["keyword"]
    excludes = rules["exclude_keyword"]
    query = (query or "").strip()

    # 专属唤醒词：命中就一定走智能体（小爱原生的「小爱同学」被音箱吃掉，
    # 记录里不存在，所以只能用用户自己说的「小智同学」来区分）
    for w in wakeups:
        if query.startswith(w):
            rest = query[len(w):].lstrip("，,、：:  \t\n")
            return {
                "route": "ai",
                "detail": f"命中智能体专属唤醒词「{w}」，会转给智能体",
                "query": query,
                "stripped": rest,
            }

    # 对话控制词（别名改写）
    canonical = ALIASES.get(query)
    if canonical:
        return {
            "route": "control",
            "detail": f"对话控制词，会被改写成「{canonical}」",
            "query": canonical,
            "stripped": canonical,
        }

    lowered = query.lower()
    matched = next((k for k in keywords if lowered.startswith(k.lower())), None)
    if not matched:
        return {
            "route": "xiaoi",
            "detail": "没命中触发词，交给小爱原生",
            "query": query,
            "stripped": query,
        }

    stripped = re.sub(
        r"^(" + "|".join(re.escape(k) for k in keywords) + ")", "", query
    )
    hit = next((e for e in excludes if stripped.startswith(e)), None)
    if hit:
        return {
            "route": "xiaoi",
            "detail": f"命中触发词「{matched}」，但排除词「{hit}」优先，还给小爱原生",
            "query": query,
            "stripped": stripped,
        }
    return {
        "route": "ai",
        "detail": f"命中触发词「{matched}」，会转给智能体",
        "query": query,
        "stripped": stripped,
    }
