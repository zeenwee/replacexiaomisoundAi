"""兼容性垫片：让缺依赖的机器也能跑起来。

打包成 .app 的时候我们把 langchain / groq 这些重依赖排掉了 ——
本项目用不上它们，而且 langchain_community 一收就是几百兆。

但 xiaogpt 的 `xiaogpt/bot/__init__.py` 是**模块级 import**：

    from xiaogpt.bot.langchain_bot import LangChainBot   # ← 缺了就整个包导不进来

所以在真正 import xiaogpt.bot 之前，先把用不到的模块换成空壳。
源码直跑时依赖都装得上，这段逻辑会自动跳过，行为完全不变。
"""

from __future__ import annotations

import sys
import types

# (模块名, 里面要提供的类名)
_STUBS = (
    ("xiaogpt.bot.langchain_bot", "LangChainBot"),
    ("xiaogpt.bot.llama_bot", "LlamaBot"),
)


def stub_unused_bots() -> list[str]:
    """给装不上的 bot 依赖塞空壳。返回被替换的模块名列表。"""
    replaced: list[str] = []

    def _dummy(name: str):
        class _UnusedBot:
            def __init__(self, *args, **kwargs):
                raise RuntimeError(
                    f"{name} 依赖在当前环境里没装 —— 本项目用不到它。"
                )

        _UnusedBot.__name__ = name
        _UnusedBot.__qualname__ = name
        return _UnusedBot

    for mod_name, attr in _STUBS:
        if mod_name in sys.modules:
            continue
        try:
            __import__(mod_name)
            continue
        except Exception:
            pass
        stub = types.ModuleType(mod_name)
        setattr(stub, attr, _dummy(attr))
        sys.modules[mod_name] = stub
        replaced.append(mod_name)
        print(f"已跳过用不到的依赖：{mod_name}（空壳替换）", flush=True)

    return replaced
