"""入口：把自己的智能体注册进 xiaogpt，然后按 xiaogpt 的方式跑起来。

用法（run.sh 里已经封装好）：

    ./xiaogpt-env/bin/python -m mybot.main --config config.yaml

为什么能这么干
--------------
xiaogpt 的 `xiaogpt/bot/__init__.py` 里：

    BOTS: dict[str, type[BaseBot]] = {"chatgptapi": ChatGPTBot, ...}

    def get_bot(config):
        return BOTS[config.bot].from_config(config)

`BOTS` 是个普通 dict，`get_bot()` 每次调用时现查。所以运行期往里插一项
就生效了 —— **不需要改 venv 里的 site-packages，也不需要打补丁**，
升级 xiaogpt 也不会被覆盖。

注意 `get_bot` 是在 `xiaogpt/xiaogpt.py` 里 import 的，但它拿到的是
`BOTS` 这个 dict 的**引用**，往 dict 里塞 key 对已经 import 过的模块
同样有效。所以这里注册完再进 xiaogpt 的 CLI 流程，顺序是安全的。
"""

from __future__ import annotations

import asyncio
import re
import sys

from mybot.compat import stub_unused_bots



def register() -> None:
    """把自有智能体塞进 xiaogpt 的 bot 注册表。"""
    # 用不到的 bot 依赖（langchain / groq）可能没被打包进来，
    # 先换空壳，否则 xiaogpt/bot/__init__.py 的模块级 import 会直接炸。
    stub_unused_bots()

    from mybot.minimax_agent import AGENT_KEY, MiniMaxAgent

    import xiaogpt.bot as bot_pkg

    bots = getattr(bot_pkg, "BOTS", None)
    if bots is None:
        raise RuntimeError(
            "在当前 xiaogpt 版本里找不到 xiaogpt.bot.BOTS 注册表，"
            "无法注册自有智能体。请确认 xiaogpt 版本 >= 3.0。"
        )
    bots[AGENT_KEY] = MiniMaxAgent
    print(f"已注册自有智能体：bot={AGENT_KEY} -> {MiniMaxAgent.__name__}")


def _quiet_exception_handler(loop, context) -> None:
    """把 asyncio 默认那一大坨 traceback 换成一行摘要。

    xiaogpt 会在后台任务里吞掉异常再抛出，asyncio 默认会把完整 traceback
    打到 stderr，和我们自己打印的中文提示重复，还把真正有用的信息淹掉。
    """
    exc = context.get("exception")
    if exc is not None:
        print(f"[后台任务异常] {type(exc).__name__}: {str(exc)[:200]}")
    elif context.get("message"):
        print(f"[后台任务异常] {context['message']}")


def patch_simulate(question: str) -> None:
    """把「轮询到的用户提问」替换成一条自己造的问题。

    用途：整条生产回路（认问题 → 匹配触发词 → 念「正在问小智请耐心等待」
    → 问模型 → 逐句播报）跑一次，但不用真人对着麦克风说话。

    做法是替换 ``MiGPT.get_latest_ask_from_xiaoai`` —— 这是轮询的**唯一**
    入口，换掉它之后，后面 ``run_forever`` 里的每一步都是原装的、
    跟真人说话时完全一致的代码路径。
    """
    import json
    import time

    from xiaogpt.xiaogpt import MiGPT

    state = {"fired": False}
    original = MiGPT.get_latest_ask_from_xiaoai

    async def fake_get_latest_ask(self, session):
        if state["fired"]:
            # 只放行一次，之后老老实实去轮询真实记录
            return await original(self, session)
        state["fired"] = True
        record = {
            "query": question,
            # 比实例启动时的 last_timestamp 大，才能被 _get_last_query 收下
            "time": int(time.time() * 1000) + 1000,
            "answers": [{"tts": {"text": "（这条是小爱的原声回答，自测模式）"}}],
            "requestId": "simulate",
        }
        payload = {
            "code": 0,
            "message": "Success",
            "data": json.dumps({"records": [record]}),
        }
        print(f"→ 自测模式：注入一条问题「{question}」")
        return self._get_last_query(payload)

    MiGPT.get_latest_ask_from_xiaoai = fake_get_latest_ask
    print(f"已开启自测模式：{question}")


def patch_agent_wakeup(words: list[str]) -> None:
    """给自有智能体加「专属唤醒词」——和小爱原生并存的双轨触发。

    背景
    ----
    小爱音箱的唤醒词「小爱同学」**会被音箱自己吃掉**，小米云对话记录里
    根本没有它（实测记录是「请推荐我中午今天中午吃什么」，开头就是「请」）。
    所以没法靠「小爱同学」这个词在记录里区分两条路。

    能区分的是**用户自己说的第二个词**：
        「小爱同学」→ 原生小爱（记录里是「帮我查天气」之类）
        「小智同学」→ 我们的智能体（记录里是「小智同学，帮我查天气」）

    小智同学既然是自己说的话，就一定留在记录里，所以拿它当唤醒词可行。

    这里做的事
    ----------
    在运行期接管 need_ask_gpt：
      1. 命中专属唤醒词 → 一定转给智能体（剥掉唤醒词再问模型）
      2. 没命中 → 走 xiaogpt 原逻辑（keyword 前缀 + exclude_keyword）

    为什么单独一个补丁而不是往 keyword 里加：
    keyword 里的词会被 xiaogpt 统一剥离，而「小智同学」要剥的是
    「小智同学，」整段（含逗号和后续空白），逻辑不一样。

    **不改 site-packages**，升级 xiaogpt 不会被覆盖。
    """
    from xiaogpt.xiaogpt import MiGPT

    original = MiGPT.need_ask_gpt

    def need_ask_gpt(self, record):
        if not record:
            return False
        query = (record.get("query") or "").strip()
        if not query:
            return False
        for w in words:
            if query.startswith(w):
                return True
        return original(self, record)

    MiGPT.need_ask_gpt = need_ask_gpt
    print(f"已加智能体专属唤醒词：{'、'.join(words)}")


def patch_strip_wakeup(words: list[str]) -> None:
    """把专属唤醒词从问题里剥掉（含后面的逗号和空格）。

    用户说「小智同学，帮我查天气」，问模型的应该是「帮我查天气」，
    而不是「小智同学，帮我查天气」—— 否则模型会以为你在跟它自我介绍。

    xiaogpt 自己剥 keyword 用的是 re.sub(rf"^({'|'.join(keyword)})", "", query)，
    这里在它之后再剥一层唤醒词，顺序：先剥唤醒词再剥 keyword。
    """
    from xiaogpt.xiaogpt import MiGPT

    orig_get = MiGPT.get_latest_ask_from_xiaoai

    async def get_latest_ask(self, session):
        record = await orig_get(self, session)
        if record:
            query = (record.get("query") or "").strip()
            for w in words:
                if query.startswith(w):
                    rest = query[len(w):]
                    # 剥掉唤醒词后面的逗号、顿号、冒号和空白
                    record["query"] = rest.lstrip("，,、：:  \t\n")
                    break
        return record

    MiGPT.get_latest_ask_from_xiaoai = get_latest_ask
    print(f"已加唤醒词剥离：{'、'.join(words)}")


def patch_need_ask_gpt(excludes: list[str]) -> None:
    """给「要不要转给 AI」的判断加一层排除规则。

    xiaogpt 原版的 need_ask_gpt 只看 keyword 前缀。放宽触发词之后有个
    新问题：「请打开客厅灯」会被「请」命中，结果灯没开，AI 还在那瞎说
    「好的，客厅灯已打开」。

    这里在运行期接管这个方法：命中排除词的一律还给小爱原生执行。
    **不改 site-packages**，升级 xiaogpt 不会被覆盖 —— 和注册 BOTS 同一套路。
    """
    from xiaogpt.config import WAKEUP_KEYWORD
    from xiaogpt.xiaogpt import MiGPT

    original = MiGPT.need_ask_gpt

    def need_ask_gpt(self, record):
        if not original(self, record):
            return False
        query = (record.get("query") or "").strip()
        # 连续对话模式下一律转给 AI，这里不做排除（保持 xiaogpt 原意）
        if self.in_conversation and not query.startswith(WAKEUP_KEYWORD):
            return True
        # 先剥掉触发词再判断排除词，顺序要和 xiaogpt 处理问题的顺序一致：
        # 「请打开客厅灯」剥掉「请」之后是「打开客厅灯」，才该被排除拦下
        stripped = re.sub(
            rf"^({'|'.join(re.escape(w) for w in self.config.keyword)})",
            "",
            query,
        )
        for word in excludes:
            if stripped.startswith(word):
                return False
        return True

    MiGPT.need_ask_gpt = need_ask_gpt
    print(f"已加上触发排除词：{'、'.join(excludes)}")



def patch_query_aliases(aliases: dict[str, str]) -> None:
    """把口语化的「连续对话」控制说法改写成 xiaogpt 认的标准词。

    xiaogpt 判断连续对话用的是**精确相等**（query == config.start_conversation），
    所以配置里写「开始持续对话」，用户说「开始对话」就进不去 ——
    实测小米云记下来的 query 就是「开始对话」（唤醒词被音箱吃掉了）。

    改写要发生在 run_forever 判断连续对话**之前**，所以挂在
    get_latest_ask_from_xiaoai 上（轮询的唯一入口）。
    """
    from xiaogpt.xiaogpt import MiGPT

    original = MiGPT.get_latest_ask_from_xiaoai

    async def wrapped(self, session):
        record = await original(self, session)
        if record:
            query = (record.get("query") or "").strip()
            if query in aliases:
                canonical = aliases[query]
                record["query"] = canonical
                print(f"→ 对话控制词改写：{query} → {canonical}", flush=True)
        return record

    MiGPT.get_latest_ask_from_xiaoai = wrapped
    print(f"已加上口语化对话控制词：{'、'.join(aliases)}")



def patch_speaker_volume() -> None:
    """启动时把音箱的系统音量设到 config 里的值。

    为什么用系统音量而不是把 MiniMax 的 vol 调大
    ------------------------------------------------
    - MiniMax 的 `vol` 改的是**合成出来的音频波形**：放大过头波形削顶，
      听起来是破音，不是"更响"。实测每翻一倍 +6dB，vol=6 就到峰值 0dBFS 了。
    - 音箱的**系统音量**改的是喇叭输出增益：波形不变，只放大输出，
      怎么调都不会破音。

    所以「回答不够响」应该调这个。config 里 speaker_volume 就是它
    （0-100，留空 = 不动音箱音量，交给用户在米家 App 里调）。

    这里包一层 try：设音量失败不该让引擎起不来 —— 大不了音量保持原样。
    """
    import asyncio

    from xiaogpt.xiaogpt import MiGPT

    volume = _load_speaker_volume()
    if volume is None:
        print("未配置音箱系统音量，保持音箱当前音量")
        return

    original = MiGPT.init_all_data

    async def init_all_data(self):
        await original(self)
        try:
            await self.mina_service.player_set_volume(self.device_id, volume)
            print(f"已把音箱系统音量设为 {volume}")
        except Exception as e:
            print(f"设音箱系统音量失败（不影响使用）：{e}")

    MiGPT.init_all_data = init_all_data
    print(f"已注册音箱系统音量设置：{volume}")


def _load_speaker_volume() -> int | None:
    """从 config.yaml 读 speaker_volume（0-100）。

    和 exclude_keyword 一样，xiaogpt 的 Config 没这个字段，自己读。
    """
    import yaml

    config_path = "config.yaml"
    argv = sys.argv[1:]
    if "--config" in argv:
        idx = argv.index("--config")
        if idx + 1 < len(argv):
            config_path = argv[idx + 1]
    try:
        with open(config_path, "rb") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return None
    raw = data.get("speaker_volume")
    if raw is None or raw == "":
        return None
    try:
        v = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, v))


def patch_minimax_tts() -> None:
    """让 config 里的 tts: minimax 走我们自己的 MiniMax TTS 后端。

    xiaogpt 自带的 TTS 只有三种：mi（小爱原生音色）/ fish / tetos。
    tetos 里那个 minimax speaker 打的是已废弃的 api.minimax.chat，还要
    group_id，用不了。这里在运行期接管，把 tts=minimax 指向我们自己实现
    的后端（国内新端点 + 音色可选 + 失败自动退回小爱原生音色）。

    同样**不改 site-packages**。
    """
    import functools

    from mybot.minimax_tts import MinimaxTTS
    from xiaogpt.xiaogpt import MiGPT

    original = MiGPT.tts

    @functools.cached_property
    def tts(self):
        if getattr(self.config, "tts", "mi") == "minimax":
            return MinimaxTTS(self.mina_service, self.device_id, self.config)
        return original.func(self)

    # Python 3.12 起 cached_property 必须走 __set_name__ 才会绑定属性名，
    # 这里是直接赋值给类，所以要手动补这一步。
    tts.__set_name__(MiGPT, "tts")
    MiGPT.tts = tts
    print("已注册 MiniMax 音色后端（tts: minimax）")


def _load_wakeups() -> list[str]:
    """从 config.yaml 读 agent_wakeup（智能体专属唤醒词）。

    和 exclude_keyword 一样，xiaogpt 的 Config 没这个字段，
    read_from_file 会丢掉，所以自己读。
    """
    import yaml

    config_path = "config.yaml"
    argv = sys.argv[1:]
    if "--config" in argv:
        idx = argv.index("--config")
        if idx + 1 < len(argv):
            config_path = argv[idx + 1]
    try:
        with open(config_path, "rb") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return []
    words = data.get("agent_wakeup") or []
    if isinstance(words, str):
        words = [words]
    return [str(w).strip() for w in words if str(w).strip()]


def _load_excludes() -> list[str]:
    """从 config.yaml 读 exclude_keyword。

    xiaogpt 的 Config 没这个字段，read_from_file 会把它丢掉，所以自己读。
    """
    import yaml

    config_path = "config.yaml"
    argv = sys.argv[1:]
    if "--config" in argv:
        idx = argv.index("--config")
        if idx + 1 < len(argv):
            config_path = argv[idx + 1]
    try:
        with open(config_path, "rb") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return []
    excludes = data.get('exclude_keyword') or []
    if isinstance(excludes, str):
        excludes = [excludes]
    return [w for w in excludes if w]


def main() -> None:
    # 先把自测模式的参数摘出来 —— xiaogpt 自己的 argparse 不认识它，
    # 留在 argv 里会直接报错退出。
    simulate = None
    argv = sys.argv[1:]
    if "--simulate" in argv:
        idx = argv.index("--simulate")
        if idx + 1 >= len(argv):
            print("--simulate 后面要跟一句问题", file=sys.stderr)
            raise SystemExit(2)
        simulate = argv[idx + 1]
        argv = argv[:idx] + argv[idx + 2:]
        sys.argv = [sys.argv[0]] + argv

    register()
    # 复用 xiaogpt 自己的 CLI（--config / --verbose / --proxy 等照常可用）
    from xiaogpt.cli import main as xiaogpt_main

    if simulate:
        patch_simulate(simulate)

    excludes = _load_excludes()
    if excludes:
        patch_need_ask_gpt(excludes)

    wakeups = _load_wakeups()
    if wakeups:
        patch_agent_wakeup(wakeups)
        patch_strip_wakeup(wakeups)

    patch_speaker_volume()

    patch_minimax_tts()

    patch_query_aliases(
        {
            "开始对话": "开始持续对话",
            "开启对话": "开始持续对话",
            "连续对话": "开始持续对话",
            "进入对话": "开始持续对话",
            "结束对话": "结束持续对话",
            "退出对话": "结束持续对话",
            "停止对话": "结束持续对话",
            "不说了": "结束持续对话",
        }
    )

    # 先把事件循环建好并挂上自己的异常处理器。
    #
    # 为什么要 new_event_loop 而不是 get_event_loop()：
    # Python 3.12 起，主线程里从未 set_event_loop() 过时，get_event_loop()
    # 会发 DeprecationWarning（3.14 直接抛 RuntimeError）。而 xiaogpt 的
    # cli.py 里就是 `loop = asyncio.get_event_loop()`，所以这里必须先
    # set_event_loop()，让 cli 拿到同一个 loop 且不再报警告。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_quiet_exception_handler)

    try:
        xiaogpt_main()
    except SystemExit:
        raise
    except Exception as e:
        text = str(e)
        if "Login failed" in text or "登录验证失败" in text or "70016" in text:
            print()
            print("=" * 60)
            print("✗ 小米云登录失败")
            print("=" * 60)
            print("  · .env 里的 MI_USER / MI_PASS 还是空的 → 跑一遍配置向导：")
            print("      ./xiaogpt-env/bin/python tools/setup_wizard.py")
            print("  · 小米ID 填错了（要用「米家 App → 我的 → 个人信息 → 小米ID」，不是手机号）")
            print("  · 海外 IP 登录大陆账号会被风控 → 回到国内网络再试")
            print("  · 已经登录过这台机器的话，~/.mi.token 可以直接拷过来用")
            raise SystemExit(1)
        raise
    finally:
        try:
            asyncio.set_event_loop(None)
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
