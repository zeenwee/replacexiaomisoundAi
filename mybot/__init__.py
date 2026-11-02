"""自有智能体包：接管小爱音箱的「回答大脑」。

导入顺序有讲究
--------------
先用空壳换掉用不到的 bot（langchain / groq），**再**导入自有智能体。

因为 minimax_agent 要 import xiaogpt.bot.base_bot，而 xiaogpt/bot/__init__.py
是模块级 import，会连带把 langchain_bot 一起拉进来 —— 打包成 .app 时那些
重依赖被排除掉了，不先换空壳就会直接 ModuleNotFoundError。

源码直跑时依赖都装得上，stub_unused_bots() 会自动跳过，行为完全不变。
"""

from mybot.compat import stub_unused_bots

stub_unused_bots()

from mybot.minimax_agent import AGENT_KEY, MiniMaxAgent  # noqa: E402

__all__ = ["AGENT_KEY", "MiniMaxAgent"]
