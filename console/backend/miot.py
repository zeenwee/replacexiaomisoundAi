"""MIoT 属性通道（api.io.mi.com）—— 设备能力的第二条腿。

和小米云对话那条线是**两套独立登录态**
--------------------------------------
  小米云对话 / ubus  ->  sid=micoapi   （~/.mi.token 里的 micoapi 字段）
  MIoT 属性 / 动作   ->  sid=xiaomiio   （~/.mi.token 里的 xiaomiio 字段）

xiaomiio 这套要单独过一次小米账号安全验证（图形验证码 + 短信），
没补过的话这里会直接报错并告诉你怎么补。

实测能用的（2026-11-01 真机验证）
--------------------------------
  可写：(2,1) 音量 / (4,1) 麦克风静音 / (9,1) 勿扰开关
  动作：(7,1) 主动唤醒 / (7,3) 云端播报 / (6,1) 停闹钟 / (3,3) 暂停
  只读：设备信息 / 播放状态 / 环境分贝 / 扬声器静音

三个坑
------
1. prop/get 在同一会话里有缓存 —— 写完同一会话读可能还是旧值。
   所以这里**每次操作都开一个新会话**，别复用。
2. 写入生效有 10-20 秒延迟，界面要如实告诉用户，别假装秒生效。
3. code=0 只代表云端收下了，不代表设备照做了 —— 必须读回验证。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from aiohttp import ClientSession

from console.backend import paths

TOKEN_PATH = paths.MI_TOKEN_PATH

# L16A（Xiaomi Sound）的 MIoT 属性表。
# 来源：miot-spec.org 的 urn:miot-spec-v2:device:speaker:0000A015:xiaomi-l16a:3
# 最后一列是**实测**结论（真机验过），不是照抄 spec。
#   rw = 真能写；r = 只能读（spec 里有，但设备不认写入）
PROPS: dict[str, tuple[int, int, str, str]] = {
    "volume":        (2, 1,  "音量",       "rw"),
    "speaker_mute":  (2, 2,  "扬声器静音",  "r"),
    "playing_state": (3, 1,  "播放状态",    "r"),
    "mic_mute":      (4, 1,  "麦克风静音",  "rw"),
    "sleep_mode":    (7, 3,  "休眠模式",    "r"),
    "no_disturb":    (9, 1,  "勿扰",       "rw"),
    "period":        (9, 2,  "勿扰时段",    "r"),
    "decibel":       (10, 2, "环境分贝",    "r"),
    "sound_detect":  (10, 3, "声音检测",    "r"),
    "noise_detect":  (10, 4, "噪音检测",    "r"),
    "manufacturer":  (1, 1,  "厂商",       "r"),
    "model":         (1, 2,  "型号",       "r"),
    "device_id":     (1, 3,  "设备ID",     "r"),
    "firmware":      (1, 4,  "固件版本",    "r"),
    "serial":        (1, 5,  "序列号",     "r"),
}

# 动作：(siid, aiid, 名字)
ACTIONS: dict[str, tuple[int, int, str]] = {
    "wake_up":    (7, 1, "主动唤醒"),
    "play_text":  (7, 3, "云端播报"),
    "stop_alarm": (6, 1, "停闹钟"),
    "pause":      (3, 3, "暂停"),
}

# 写完之后等多久读回验证（设备生效延迟 10-20 秒）
VERIFY_DELAY = 3.0


class MiOTLink:
    """MIoT 属性 / 动作的调用封装。"""

    def __init__(self) -> None:
        self._last_error: str = ""

    # ---------------- 底层 ----------------

    def _token(self) -> dict:
        if not TOKEN_PATH.exists():
            raise RuntimeError(
                f"找不到小米登录态 {TOKEN_PATH}，先跑 tools/xiaomi_verify_link.py --retry"
            )
        try:
            token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"小米登录态读不出来：{e}") from e
        if "xiaomiio" not in token:
            raise RuntimeError(
                "小米登录态里没有 MIoT 专用通道（xiaomiio）。"
                "它要单独过一次账号安全验证，跑一次 "
                "./xiaogpt-env/bin/python tools/miot_login_probe.py --save 补上。"
            )
        return token

    def _did(self) -> str:
        """设备 miotDID：环境变量 > config.yaml > 空。

        不要在这里写死某个设备的 DID —— 一台机器一份配置才是对的。
        跑 `tools/list_devices.py` 查自己设备的 miotDID。
        """
        did = (os.getenv("MI_DID") or "").strip()
        if not did:
            try:
                from console.backend.config_io import ConfigEditor

                did = str(ConfigEditor(paths.CONFIG_PATH).get("mi_did", "") or "")
            except Exception:
                did = ""
        return did

    async def _run(self, fn):
        """开一个全新的会话跑一次操作。

        每次都新建会话是有意的：同一个会话里 prop/get 会返回缓存值，
        写完立刻读会拿到旧值，看起来像「没生效」。
        """
        from miservice.miaccount import MiAccount
        from miservice.miioservice import MiIOService

        token = self._token()
        async with ClientSession() as session:
            account = MiAccount(session, "", "", token_store=None)
            # 直接塞 token，绝不走 login() —— 失败时它会把 token 文件删掉
            account.token = token
            svc = MiIOService(account)
            return await fn(svc)

    # ---------------- 读 ----------------

    async def get_prop(self, siid: int, piid: int) -> Any:
        did = self._did()

        async def _fn(svc):
            return await svc.miot_get_prop(did, (siid, piid))

        return await self._run(_fn)

    async def state(self) -> dict:
        """一次读回所有关心的属性。"""
        did = self._did()
        items = list(PROPS.items())

        async def _fn(svc):
            out: dict[str, Any] = {"ok": True, "did": did}
            batch = 10
            for i in range(0, len(items), batch):
                chunk = items[i : i + batch]
                vals = await svc.miot_get_props(
                    did, [(siid, piid) for _, (siid, piid, _, _) in chunk]
                )
                for (key, (siid, piid, label, acc)), v in zip(chunk, vals):
                    out[key] = v
                    out["labels"] = out.get("labels", {})
                    out["labels"][key] = {"label": label, "writable": acc == "rw"}
            return out

        return await self._run(_fn)

    # ---------------- 写 ----------------

    async def set_prop(self, siid: int, piid: int, value: Any) -> dict:
        did = self._did()

        async def _fn(svc):
            code = await svc.miot_set_prop(did, (siid, piid), value)
            return code

        code = await self._run(_fn)
        return {"ok": code == 0, "code": code, "siid": siid, "piid": piid, "value": value}

    async def _set_bool(self, key: str, on: bool) -> dict:
        siid, piid, label, _acc = PROPS[key]

        async def _fn(svc):
            did = self._did()
            code = await svc.miot_set_prop(did, (siid, piid), on)
            await asyncio.sleep(VERIFY_DELAY)
            got = await svc.miot_get_prop(did, (siid, piid))
            return code, got

        code, got = await self._run(_fn)
        applied = got is on
        return {
            "ok": code == 0,
            "code": code,
            "key": key,
            "label": label,
            "value": on,
            "current": got,
            "applied": applied,
            "note": (
                "已生效"
                if applied
                else "已下发，设备约 30 秒后上报生效"
            ),
            "effective_hint": "设备属性上报有周期，实测约 30 秒后才在云端看到新值",
        }

    async def set_mic_mute(self, on: bool) -> dict:
        """麦克风静音 —— 让音箱听不见唤醒词。

        这是 MIoT 独占的能力，小米云 ubus 那条通道做不到。
        小爱同学、小智同学都会失去响应，直到你把它关掉。
        """
        return await self._set_bool("mic_mute", bool(on))

    async def set_no_disturb(self, on: bool) -> dict:
        """勿扰开关。

        实测：写 false 立刻生效；写 true 有时会被设备端的「勿扰时段」挡住，
        需要先把时段放开才收。
        """
        return await self._set_bool("no_disturb", bool(on))

    # ---------------- 动作 ----------------

    async def action(self, key: str, args: list | None = None) -> dict:
        siid, aiid, label = ACTIONS[key]
        did = self._did()
        args = args or []

        async def _fn(svc):
            return await svc.miot_action(did, (siid, aiid), args)

        code = await self._run(_fn)
        return {
            "ok": code == 0,
            "code": code,
            "key": key,
            "label": label,
            "args": args,
        }

    async def play_text(self, text: str) -> dict:
        """云端直推播报：让小米云念一段文字。

        和控制台现有的「说话」按钮的区别：
          - 现有那个：MiniMax 合成 mp3 -> 本机 HTTP 服务 -> 音箱（音色是小智的）
          - 这个：小米云自己合成并推给音箱（**不依赖本机在线**，音色是小米的）
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("内容为空")
        return await self.action("play_text", [text])

    async def wake_up(self) -> dict:
        """远程唤醒音箱。"""
        return await self.action("wake_up", [])

    async def close(self) -> None:
        # 每次操作都自建自销会话，这里没有要关的东西
        return None


# 全局单例
MIOT = MiOTLink()
