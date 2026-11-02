#!/usr/bin/env python3
"""打包入口。

同一个二进制，两种角色：

    小智控制台              → 打开桌面界面（pywebview / WKWebView）
    小智控制台 --engine     → 跑 xiaogpt 引擎（对话回路）

为什么合二为一
--------------
打包成 .app 之后，App 包里**没有**项目的 venv 了，引擎没法再
「xiaogpt-env/bin/python -m mybot.main」。让同一个可执行文件换个角色
当引擎，是最省事、也最不容易出错的做法 —— 不用管相对路径、
不用管第二份 Python 环境。

用法（源码态）：
    ./xiaogpt-env/bin/python -m console.launcher
    ./xiaogpt-env/bin/python -m console.launcher --engine --config config.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    """源码态：把项目根塞进 sys.path。打包态不需要（模块都打进包里了）。"""
    if getattr(sys, "frozen", False):
        return
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _redirect_output() -> None:
    """打包后没有终端，输出写进 ~/.xiaozhi/console.log，方便排查。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return
    except Exception:
        pass
    try:
        from console.backend import paths

        paths.ensure_ready()
        log_path = paths.USER_HOME / "console.log"
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        # 打包成 --windowed 时 sys.stdout 可能是 None，print() 会直接炸，
        # 这里补一个真实文件对象。
        if sys.stdout is None or sys.stderr is None:
            fh = open(log_path, "a", encoding="utf-8", errors="replace")
            if sys.stdout is None:
                sys.stdout = fh
            if sys.stderr is None:
                sys.stderr = fh
    except Exception:
        pass


def main() -> int:
    _bootstrap()
    _redirect_output()

    argv = sys.argv[1:]

    if "--serve" in argv:
        # 浏览器版服务模式
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--serve"]
        from console.server import main as server_main

        return server_main()

    if "--engine" in argv:
        # 引擎模式：把 --engine 摘掉，剩下的是 xiaogpt 自己的参数
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--engine"]
        from mybot.main import main as engine_main

        engine_main()
        return 0

    from console.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
