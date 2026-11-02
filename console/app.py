"""小智控制台 —— 入口。

跑起来：
    ./xiaogpt-env/bin/python -m console.app

或者用打包好的 App：dist/小智控制台.app

架构
----
界面是 pywebview（WKWebView），后端就是本机 Python。
所有业务逻辑都直接 import 现有的 mybot/ 模块 —— 界面里改的触发词、
音色、模型，和命令行跑的是同一套代码，不存在「两套逻辑对不上」。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from console.backend import agent as agent_mod
from console.backend import history as history_mod
from console.backend import health as health_mod
from console.backend import tasks as tasks_mod
from console.backend import triggers as triggers_mod
from console.backend import voices as voices_mod
from console.backend import paths
from console.backend.config_io import load_env
from console.backend.engine import ENGINE
from console.backend.loop import run as loop_run
from console.backend.miot import MIOT
from console.backend.xiaomi import LINK

# 密钥只存在 .env 里（不写进 config.yaml），这里先载入环境变量，
# 后面 TTS / 对话测试台才有 key 可用。外部已导出的变量优先，不覆盖。
load_env(paths.ENV_PATH)


class Api:
    """暴露给前端的方法。全部同步返回（慢操作走任务 + 轮询）。"""

    # ---------- 快速方法：直接给结果 ----------

    def get_overview(self) -> dict:
        settings = agent_mod.get_settings()
        return {
            "engine": ENGINE.status(),
            "settings": {
                "model": settings.get("model"),
                "voice": (settings.get("tts_options") or {}).get("voice"),
                "agent_name": settings.get("agent_name"),
                "tts": settings.get("tts"),
                "api_key_set": settings.get("api_key_set"),
                "api_key_masked": settings.get("api_key_masked"),
                "api_base": settings.get("api_base"),
                "mi_did": settings.get("mi_did"),
            },
            "keyword_count": len(triggers_mod.get_rules()["keyword"]),
            "exclude_count": len(triggers_mod.get_rules()["exclude_keyword"]),
        }

    def list_voices(self) -> dict:
        return {
            "voices": voices_mod.list_voices(),
            "models": voices_mod.list_tts_models(),
            "sources": voices_mod.list_tts_sources(),
        }

    async def list_cloned_voices(self) -> dict:
        """账号下已克隆 / 已设计的音色（要联网，所以是慢操作）。"""
        return await voices_mod.list_cloned_voices()

    def list_models(self) -> dict:
        return {"models": loop_run(agent_mod.list_models())}

    def get_settings(self) -> dict:
        return agent_mod.get_settings()

    def get_paths(self) -> dict:
        """界面「路径」卡片显示的真实位置（打包后会变，不能写死）。"""
        return {
            "config": str(paths.CONFIG_PATH),
            "env": str(paths.ENV_PATH),
            "token": str(paths.MI_TOKEN_PATH),
            "user_home": str(paths.USER_HOME),
            "web": str(paths.WEB_DIR),
            "frozen": paths.FROZEN,
        }

    def update_settings(self, patch: dict) -> dict:
        return agent_mod.update_settings(patch or {})

    def get_rules(self) -> dict:
        return triggers_mod.get_rules()

    def set_rules(self, patch: dict) -> dict:
        return triggers_mod.set_rules(patch or {})

    def judge(self, query: str) -> dict:
        return triggers_mod.judge(query)

    def engine_status(self) -> dict:
        return ENGINE.status()

    def engine_start(self) -> dict:
        return ENGINE.start()

    def engine_stop(self) -> dict:
        return ENGINE.stop()

    def engine_restart(self) -> dict:
        return ENGINE.restart()

    def engine_logs(self, offset: int = 0) -> dict:
        lines, total = ENGINE.since(int(offset or 0))
        return {"lines": lines, "total": total}

    def task_status(self, task_id: str) -> dict:
        return tasks_mod.status(task_id)

    def diagnostics(self, payload: dict) -> dict:
        """前端自检数据。

        界面空白 / 某个模块没加载时，靠这个定位问题。
        设置 CONSOLE_DEBUG=1 时会打到 stdout。
        """
        import os as _os

        if _os.getenv("CONSOLE_DEBUG"):
            print("[前端自检]", payload, flush=True)
        return {"ok": True}

    # ---------- 分发器 ----------

    def call(self, name: str, payload: dict | None = None):
        """前端统一入口。

        返回两种形态：
            {"immediate": True,  "data": ...}
            {"immediate": False, "task_id": "..."}
        前端自己判断要不要轮询。
        """
        payload = payload or {}
        handler = self._handlers().get(name)
        if handler is None:
            raise ValueError(f"未知调用：{name}")
        slow = name in self._slow()
        if not slow:
            return {"immediate": True, "data": handler(**payload)}
        task_id = tasks_mod.submit(handler, **payload)
        return {"immediate": False, "task_id": task_id}

    def _slow(self) -> set:
        # 这些是协程，必须走「提交任务 + 轮询」，不能同步调。
        # 音量操作也算 —— 它要走小米云（要网络往返 + 等 1 秒读回实际值）。
        return {
            "speak",
            "preview",
            "ask",
            "history",
            "devices",
            "health",
            "get_volume",
            "set_volume",
            # MIoT 通道：都要走小米云网络往返
            "miot_state",
            "set_mic_mute",
            "set_no_disturb",
            "miot_play_text",
            "miot_wake",
            # 音色克隆：拉账号音色列表要走 MiniMax 接口
            "list_cloned_voices",
        }

    # ---------- 慢操作：真正的协程 ----------

    # 这些函数直接跑在常驻 asyncio loop 里（tasks.submit 会 await 它们）。
    # 注意：**不要**在这里再套 loop_run —— 那会在 loop 线程里同步等自己，
    # 直接死锁。底层模块（xiaomi/history/health/agent）本来就都是 async，
    # 直接 await 即可。

    async def _op_speak(self, **kw):
        return await LINK.speak(
            kw.get("text", ""),
            voice=kw.get("voice"),
            speed=kw.get("speed"),
            vol=kw.get("vol"),
            pitch=kw.get("pitch"),
            use_minimax=kw.get("use_minimax", True),
        )

    async def _op_preview(self, **kw):
        return await LINK.preview(
            kw.get("text", ""),
            voice=kw.get("voice"),
            speed=kw.get("speed"),
            vol=kw.get("vol"),
            pitch=kw.get("pitch"),
        )

    async def _op_ask(self, **kw):
        return await agent_mod.ask(kw.get("question", ""))

    async def _op_history(self, **kw):
        return await history_mod.list_records(int(kw.get("limit", 20)))

    async def _op_devices(self, **kw):
        return await history_mod.list_devices()

    async def _op_get_volume(self, **kw):
        return await LINK.get_volume()

    async def _op_set_volume(self, **kw):
        return await LINK.set_volume(kw.get("volume"))

    async def _op_health(self, **kw):
        return await health_mod.run_all()

    # ---------- MIoT 通道（api.io.mi.com）----------

    async def _op_miot_state(self, **kw):
        """读回 L16A 的 MIoT 属性（音量/静音/勿扰/分贝/设备信息…）。"""
        return await MIOT.state()

    async def _op_set_mic_mute(self, **kw):
        """麦克风静音：让音箱听不见唤醒词。"""
        return await MIOT.set_mic_mute(bool(kw.get("on")))

    async def _op_set_no_disturb(self, **kw):
        """勿扰开关。"""
        return await MIOT.set_no_disturb(bool(kw.get("on")))

    async def _op_miot_play_text(self, **kw):
        """云端直推播报（小米音色，不依赖本机在线）。"""
        return await MIOT.play_text(kw.get("text", ""))

    async def _op_miot_wake(self, **kw):
        """远程唤醒音箱。"""
        return await MIOT.wake_up()

    def _handlers(self) -> dict:
        """name -> handler。

        约定：**所有** handler 都只接受关键字参数 **kw。
        快操作是同步函数，慢操作是协程函数（见上面的 _op_* ）。
        """
        return {
            "get_overview": lambda **kw: self.get_overview(),
            "list_voices": lambda **kw: self.list_voices(),
            "list_cloned_voices": lambda **kw: self.list_cloned_voices(),
            "list_models": lambda **kw: self.list_models(),
            "get_settings": lambda **kw: self.get_settings(),
            "get_paths": lambda **kw: self.get_paths(),
            "update_settings": lambda **kw: self.update_settings(kw.get("patch") or {}),
            "get_rules": lambda **kw: self.get_rules(),
            "set_rules": lambda **kw: self.set_rules(kw.get("patch") or {}),
            "judge": lambda **kw: self.judge(kw.get("query", "")),
            "engine_status": lambda **kw: self.engine_status(),
            "engine_start": lambda **kw: self.engine_start(),
            "engine_stop": lambda **kw: self.engine_stop(),
            "engine_restart": lambda **kw: self.engine_restart(),
            "engine_logs": lambda **kw: self.engine_logs(kw.get("offset", 0)),
            "task_status": lambda **kw: self.task_status(kw.get("task_id", "")),
            "diagnostics": lambda **kw: self.diagnostics(kw.get("payload") or {}),
            # 慢的
            "speak": self._op_speak,
            "preview": self._op_preview,
            "ask": self._op_ask,
            "history": self._op_history,
            "devices": self._op_devices,
            "health": self._op_health,
            "get_volume": self._op_get_volume,
            "set_volume": self._op_set_volume,
            # MIoT
            "miot_state": self._op_miot_state,
            "set_mic_mute": self._op_set_mic_mute,
            "set_no_disturb": self._op_set_no_disturb,
            "miot_play_text": self._op_miot_play_text,
            "miot_wake": self._op_miot_wake,
        }


def main() -> int:
    import webview

    from console.backend.loop import start as start_loop

    # 首次运行：把用户数据目录（~/.xiaozhi）铺好，播种配置模板
    ready = paths.ensure_ready()
    if ready.get("created"):
        print("首次运行，已创建：", "、".join(ready["created"]), flush=True)
    print(f"配置：{ready.get('config')}", flush=True)
    print(f"密钥：{ready.get('env')}", flush=True)

    load_env(paths.ENV_PATH)
    start_loop()

    index = paths.WEB_DIR / "index.html"
    window = webview.create_window(
        "小智控制台",
        url=str(index),
        js_api=Api(),
        width=1180,
        height=780,
        min_size=(1000, 660),
        background_color="#F2F2F7",
        vibrancy=True,
        text_select=True,
    )

    # 菜单栏常驻
    try:
        from console.menubar import Menubar

        Menubar(window).install()
    except Exception as e:  # 菜单栏失败不影响主功能
        print(f"菜单栏安装失败（不影响主窗口）：{e}")

    try:
        webview.start()
    finally:
        # 关掉小米云的 aiohttp 连接，别留 "Unclosed client session" 噪音
        try:
            loop_run(LINK.close(), timeout=10)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
