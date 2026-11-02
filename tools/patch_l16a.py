#!/usr/bin/env python3
"""确保 xiaogpt 的硬件指令表里有 L16A。

背景
----
xiaogpt 的 `HARDWARE_COMMAND_DICT`（xiaogpt/config.py）里原本**没有**
L16A（Xiaomi Sound by HARMAN），不补的话 `get()` 会回落到
`DEFAULT_COMMAND = ("5-1", "5-5")`，而 L16A 和同代的 L17A 一样要用
`("7-3", "7-4")`。

已用 MIoT spec 独立验证（不是靠"和 L17A 同代"推测）：

    urn:miot-spec-v2:device:speaker:0000A015:xiaomi-l16a:4
      siid=7  Intelligent Speaker
        aiid=3  Play Text                in=[1]  -> tts_command
        aiid=4  Execute Text Directive   in=[1,2] -> wakeup_command

实测 `7-4 小爱同学 0` 和 `7-3 文本` 都返回 0（成功）。
注意 aiid=4 只接受 **2 个** in 参数，多传一个就返回 -704220025。

影响范围
--------
- `tts: mi`（默认，走小米云 ubus）：不受影响。
- `use_command` 路径的 TTS：受影响。
- **持续对话的唤醒指令** `wakeup_xiaoai()`：受影响 —— 说「开始持续对话」
  之前会下发 wakeup_command，填错的话小爱不会接着听你说话。

用法
----
    ./xiaogpt-env/bin/python tools/patch_l16a.py

幂等：已经打过就只做校验，不会重复写。改动前会自动备份成 config.py.bak。
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
from pathlib import Path

ANCHOR = '    "L15A": ("7-3", "7-4"),\n'
PATCH = '    "L16A": ("7-3", "7-4"),  # Xiaomi Sound by HARMAN，与 L17A 同代 MiOS\n'
EXPECTED = ("7-3", "7-4")


def locate_config_py() -> Path | None:
    spec = importlib.util.find_spec("xiaogpt.config")
    if spec and spec.origin and spec.origin != "built-in":
        return Path(spec.origin)
    return None


def current_commands() -> tuple[str, str] | None:
    """读回实际生效的指令。

    注意：xiaogpt 的 Config 默认 bot=chatgptapi，而 __post_init__ 里有一条
    「用 chatgptapi 就必须给 openai_key」的校验，所以这里要补一个占位 key，
    否则构造 Config 就会抛异常。
    """
    try:
        cfg = importlib.import_module("xiaogpt.config")
        config = cfg.Config(hardware="L16A", openai_key="patch-check")
        return config.tts_command, config.wakeup_command
    except Exception as e:
        print(f"  读取时出错：{e!r}")
        return None


def main() -> int:
    path = locate_config_py()
    if path is None:
        print("✗ 在当前环境里找不到 xiaogpt.config 模块。")
        print("  先装好环境：uv pip install 'xiaogpt[locked]' miservice_fork")
        return 1

    print(f"配置文件：{path}")

    source = path.read_text(encoding="utf-8")

    if '"L16A"' in source:
        print("✓ 已经打过 L16A 补丁，跳过写入。")
    else:
        if ANCHOR not in source:
            print("✗ 没能在 HARDWARE_COMMAND_DICT 里找到锚点行，手动改一下：")
            print('     "L16A": ("7-3", "7-4"),  # Xiaomi Sound by HARMAN')
            return 1
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        print(f"✓ 已备份原文件：{backup}")
        path.write_text(source.replace(ANCHOR, ANCHOR + PATCH, 1), encoding="utf-8")
        print("✓ 已写入 L16A 硬件指令。")

    # 重新加载模块再校验
    importlib.invalidate_caches()
    try:
        importlib.reload(importlib.import_module("xiaogpt.config"))
    except Exception:
        pass

    commands = current_commands()
    if commands is None:
        print("✗ 校验失败：无法导入 xiaogpt.config")
        return 1

    tts_cmd, wakeup_cmd = commands
    print(f"  tts_command     = {tts_cmd}")
    print(f"  wakeup_command  = {wakeup_cmd}")
    if (tts_cmd, wakeup_cmd) == EXPECTED:
        print("✓ 校验通过，L16A 指令正确。")
        return 0
    print(f"✗ 校验不通过，期望 {EXPECTED}，实际 {(tts_cmd, wakeup_cmd)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
