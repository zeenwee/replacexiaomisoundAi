"""自有智能体 —— 用 MiniMax 接管小爱音箱的「回答大脑」。

为什么不用 xiaogpt 自带的 `bot: chatgptapi`
------------------------------------------------
自带的那个 bot 是照着 OpenAI 写的，有三个对音箱场景很致命的问题：

1. 智能体名字写死成 "ChatGPT"。xiaogpt 会把它念出来：
   `正在问{chatbot.name}请耐心等待` → 音箱会冒出"正在问 ChatGPT 请耐心等待"。
2. 没有 system 角色。人设只能靠 `config.prompt` 拼到用户问题后面
   （xiaogpt 的做法：`query = f"{query},{prompt}"`），不是正经的系统提示。
3. 出错时 `return ""`。音箱一声不吭，你根本不知道是 key 错了还是网络断了。

下面这个把三件事都做掉了：名字自己定、人设走 system、报错说人话。

关键事实（都对着 xiaogpt v3.23 源码 + MiniMax 官方文档核对过）
------------------------------------------------------------
1. xiaogpt 把 `config.gpt_options` 原样展开成 **kwargs 传给 openai 的
   `chat.completions.create()`。MiniMax 的私有字段（thinking /
   reasoning_effort / service_tier）OpenAI SDK 不认识，直接传会 TypeError，
   **必须走 `extra_body`** —— 这样在 `xiaogpt[locked]` 锁定的
   `openai==2.6.1` 上也能用，不挑 SDK 版本。

2. MiniMax-M3 / M3.1-Flash **不接受 temperature / top_p 调参**：
   官方文档写明「Not supported yet; any value passed has no effect」，
   M3 固定 temperature=1、top_p=0.95。所以配置里不要写这两项。

3. MiniMax-M3 默认走 adaptive thinking（会先想再答，首字延迟高）。
   要"秒回"必须显式 `thinking.type = disabled`。
   ⚠️ MiniMax-M3.1-Flash 是 **always thinks**：`thinking.type=disabled`
   会直接返回 HTTP 400，换成它只能改用 `reasoning_effort: low`。

4. thinking 内容走 `reasoning_content` 字段返回，和正文 `content` 分开，
   不会被朗读出来，不用自己解析 <think> 标签。

5. xiaogpt 只认 `config` 里的顶层字段，`gpt_options` 是唯一的自由字典，
   所以智能体的人设 / 名字都放在 `gpt_options` 里。
"""

from __future__ import annotations

import inspect
import os
import re
from typing import Any, AsyncGenerator

import httpx
from rich import print

from xiaogpt.bot.base_bot import BaseBot, ChatHistoryMixin
from xiaogpt.utils import split_sentences

# 在 xiaogpt 的 BOTS 注册表里占的键名，对应 config.yaml 里的 `bot:`
AGENT_KEY = "minimax"

# 中国大陆区 OpenAI 兼容接口；国际区换成 https://api.minimax.io/v1
DEFAULT_API_BASE = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_AGENT_NAME = "小智"

DEFAULT_SYSTEM_PROMPT = (
    "你是音箱上的语音助手，你的回答会被直接朗读出来。"
    "要求："
    "1）用中文口语回答，控制在 100 字以内；"
    "2）只讲结论，不要用 Markdown 列表符、编号、标题、代码块或链接；"
    "3）不要复述问题，不要说“好的，我来帮你查一下”这类废话；"
    "4）不知道就直说不知道，不要编造。"
)

# 这些键是我们自己的，不该传给 openai SDK
RESERVED_OPTIONS = {"system_prompt", "agent_name", "stream"}

# 兜底：滤掉 MiniMax 偶尔混进正文的思考块
_THINK_BLOCK = re.compile(r"(?is)<think>.*?</think>")
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def strip_think(text: str) -> str:
    """去掉正文里残留的 <think>…</think> 思考块。"""
    if "think" not in text.lower():
        return text.strip()
    text = _THINK_BLOCK.sub("", text)
    # 没闭合的残块（流被截断时可能出现）
    text = re.sub(r"(?is)<think>.*$", "", text)
    return text.strip()


class ThinkFilter:
    """流式场景下滤掉 <think>…</think>。

    音箱场景这个兜底很要紧：模型的英文思考过程一旦被念出来就非常出戏。
    做法是先缓冲开头几个字，确认「不是思考块」之后才放行，正常回答几乎
    没有延迟；真遇到思考块就整段丢掉。
    """

    MAX_BUFFER = 4000

    def __init__(self) -> None:
        self.buffer = ""
        self.done = False  # True 表示后面确定都是正文

    def feed(self, chunk: str) -> str:
        if self.done:
            return chunk
        self.buffer += chunk
        text = self.buffer.lstrip()
        if not text:
            return ""
        if text.startswith(_THINK_OPEN):
            idx = self.buffer.find(_THINK_CLOSE)
            if idx == -1:
                if len(self.buffer) > self.MAX_BUFFER:
                    # 一直等不到结束标签，当正文放出去（总比一声不吭强）
                    out, self.buffer = self.buffer, ""
                    self.done = True
                    return out
                return ""
            out = self.buffer[idx + len(_THINK_CLOSE):]
            self.buffer = ""
            self.done = True
            return out
        if text.startswith("<") and _THINK_OPEN.startswith(text):
            # 还不确定是不是 <think>，等下一片
            return ""
        out = self.buffer
        self.buffer = ""
        self.done = True
        return out

    def flush(self) -> str:
        # 收尾：缓冲区里可能还留着一个没闭合的思考块（流被截断时会出现），
        # 一并滤掉，不能让思考过程被念出来。
        out = strip_think(self.buffer)
        self.buffer = ""
        self.done = True
        return out


class MiniMaxAgent(ChatHistoryMixin, BaseBot):
    """自有智能体：MiniMax（OpenAI 兼容接口）。"""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str = DEFAULT_MODEL,
        agent_name: str = DEFAULT_AGENT_NAME,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        proxy: str | None = None,
        gpt_options: dict[str, Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        # xiaogpt 会念出这个名字：正在问{self.name}请耐心等待
        self.name = agent_name
        self.system_prompt = system_prompt
        self.proxy = proxy
        # 存一份自己的模型参数，这样即使调用方没传 gpt_options 也不会丢参数
        self.gpt_options = dict(gpt_options or {})
        self.history: list[tuple[str, str]] = []

    # ---------------- xiaogpt bot 接口 ----------------

    @classmethod
    def from_config(cls, config) -> "MiniMaxAgent":
        gpt_options = dict(config.gpt_options or {})
        # 优先级：config.yaml 里的 openai_key > 环境变量 MINIMAX_API_KEY > OPENAI_API_KEY
        # （config.yaml 里留空时 xiaogpt 不会用环境变量兜底，所以这里自己兜）
        api_key = (
            config.openai_key
            or os.getenv("MINIMAX_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        api_base = (
            config.api_base or os.getenv("MINIMAX_BASE_URL") or DEFAULT_API_BASE
        ).rstrip("/")
        agent_name = (
            gpt_options.pop("agent_name", None)
            or os.getenv("AGENT_NAME")
            or DEFAULT_AGENT_NAME
        )
        system_prompt = (
            gpt_options.pop("system_prompt", None)
            or config.prompt
            or DEFAULT_SYSTEM_PROMPT
        )
        return cls(
            api_key=api_key,
            api_base=api_base,
            model=gpt_options.get("model") or DEFAULT_MODEL,
            agent_name=agent_name,
            system_prompt=system_prompt,
            proxy=config.proxy,
            gpt_options=gpt_options,
        )

    def has_history(self) -> bool:
        """恒为 True。

        xiaogpt 用它决定要不要把 `config.prompt` 拼到用户问题后面
        （`if not self.chatbot.has_history(): query = f"{query},{prompt}"`）。
        我们有自己的 system prompt，不需要那套拼接，所以永远返回 True。
        """
        return True

    def change_prompt(self, new_prompt: str) -> None:
        """对音箱说「更改提示词 …」时生效：换掉 system 人设。"""
        self.system_prompt = new_prompt
        print(f"[{self.name}] 人设已更新：{new_prompt}")

    # ---------------- 内部实现 ----------------

    def _build_messages(self, query: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        messages.extend(self.get_messages())  # 历史多轮
        messages.append({"role": "user", "content": query})
        return messages

    def _api_kwargs(self, options: dict[str, Any]) -> dict[str, Any]:
        """把 xiaogpt 传进来的 gpt_options 洗成 openai SDK 认识的参数。

        以自己存的 gpt_options 为底，调用方传的同名参数优先。
        """
        merged = {**self.gpt_options, **options}
        kwargs = {k: v for k, v in merged.items() if k not in RESERVED_OPTIONS}
        # stream 由 xiaogpt 在 ask_stream 里自己传，重复传会 TypeError
        kwargs.pop("stream", None)
        kwargs["model"] = kwargs.get("model") or self.model
        kwargs.setdefault("max_tokens", 500)
        return kwargs

    def _httpx_kwargs(self) -> dict[str, Any]:
        """httpx >= 0.28 用 proxy=，< 0.28 用 proxies=。"""
        if not self.proxy:
            return {}
        try:
            params = inspect.signature(httpx.AsyncClient.__init__).parameters
            return {"proxy": self.proxy} if "proxy" in params else {"proxies": self.proxy}
        except Exception:
            return {"proxy": self.proxy}

    def _make_client(self, sess: httpx.AsyncClient):
        import openai

        return openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            http_client=sess,
        )

    def _error_text(self, exc: Exception) -> str:
        """把异常翻译成音箱能念的一句话。"""
        text = str(exc)
        low = text.lower()
        if "401" in text or "incorrect api key" in low or "invalid_api_key" in low:
            return "抱歉，模型密钥无效，或者区域选错了，请检查配置。"
        if "404" in text or "model_not_found" in low or "not exist" in low:
            return "抱歉，没有找到这个模型，请检查模型名称。"
        if "429" in text or "rate limit" in low or "quota" in low or "insufficient" in low:
            return "抱歉，模型调用超出限流或者额度了，请稍后再试。"
        if "thinking" in low and "400" in text:
            return "抱歉，这个模型关不掉思考，请换一个模型或者去掉思考设置。"
        if "connect" in low or "timeout" in low or "timed out" in low:
            return "抱歉，连不上模型服务，请检查网络。"
        return "抱歉，模型回答出错了，请稍后再试。"

    # ---------------- 问答 ----------------

    async def ask(self, query: str, **options: Any) -> str:
        messages = self._build_messages(query)
        kwargs = self._api_kwargs(options)
        try:
            async with httpx.AsyncClient(trust_env=True, **self._httpx_kwargs()) as sess:
                client = self._make_client(sess)
                completion = await client.chat.completions.create(
                    messages=messages, **kwargs
                )
        except Exception as e:
            print(f"[{self.name}] 请求失败：{e}")
            return self._error_text(e)

        message = completion.choices[0].message.content or ""
        message = strip_think(message)
        self.add_message(query, message)
        print(message)
        return message

    async def ask_stream(
        self, query: str, **options: Any
    ) -> AsyncGenerator[str, None]:
        messages = self._build_messages(query)
        kwargs = self._api_kwargs(options)
        message = ""
        try:
            async with httpx.AsyncClient(trust_env=True, **self._httpx_kwargs()) as sess:
                client = self._make_client(sess)
                try:
                    completion = await client.chat.completions.create(
                        messages=messages, stream=True, **kwargs
                    )
                except Exception as e:
                    print(f"[{self.name}] 请求失败：{e}")
                    yield self._error_text(e)
                    return

                think = ThinkFilter()

                async def text_gen():
                    async for event in completion:
                        if not event.choices:
                            continue
                        delta = event.choices[0].delta
                        if delta.content is None:
                            continue
                        for piece in think.feed(delta.content):
                            print(piece, end="")
                            yield piece
                    for piece in think.flush():
                        print(piece, end="")
                        yield piece

                try:
                    # 按句切分后逐句吐出去，xiaogpt 的 MiTTS 会一句一句念，
                    # 所以音箱是"边生成边说"，不是等整段生成完才开口。
                    async for sentence in split_sentences(text_gen()):
                        message += sentence
                        yield sentence
                finally:
                    print()
        except Exception as e:
            print(f"[{self.name}] 流式调用失败：{e}")
            yield self._error_text(e)
        finally:
            if message:
                self.add_message(query, message)
