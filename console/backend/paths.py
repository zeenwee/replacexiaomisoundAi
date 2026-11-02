"""路径解析 —— 源码直跑和打包成 .app 两种形态都能找到该找的东西。

三类东西，分三类处理
--------------------
1. **只读资源**（界面 HTML）：源码态在项目里，打包态在 App 包内（只读）。
2. **可写配置**（config.yaml）：源码态用项目里的；打包态放 `~/.xiaozhi/`。
   **绝不写进 App 包** —— App 包在 macOS 上会被 codesign 校验，
   改一个字节签名就废了，下次启动还会被 Gatekeeper 拦。
3. **机密**（.env 里的 MiniMax key）：只放 `~/.xiaozhi/.env`，永远不进包。

三种形态
--------
源码直跑：
    可执行   ./xiaogpt-env/bin/python -m console.app
    资源     <项目根>/console/web/
    配置     <项目根>/config.yaml
    引擎     <项目根>/xiaogpt-env/bin/python -m mybot.main

打包成 .app：
    可执行   dist/小智控制台.app/Contents/MacOS/小智控制台
    资源     sys._MEIPASS/console/web/
    配置     ~/.xiaozhi/config.yaml（首次运行从包内模板播种）
    引擎     同一个可执行文件 + `--engine` 参数（见 console/launcher.py）

环境变量可覆盖（方便调试和自动化测试）：
    XIAOZHI_HOME    用户数据目录
    XIAOZHI_CONFIG  配置文件路径
    XIAOZHI_ENV     .env 路径
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller 冻结后会设 sys.frozen
FROZEN = bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    """只读资源根目录。

    打包态：sys._MEIPASS（PyInstaller 解包出来的资源目录）。
    源码态：项目根（console/backend/paths.py 往上三层）。
    """
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent.parent


BUNDLE_DIR = _bundle_dir()

# 用户数据目录（打包态用 ~/.xiaozhi，源码态直接用项目根）
USER_HOME = Path(
    os.getenv("XIAOZHI_HOME") or (Path.home() / ".xiaozhi")
).expanduser()


def _config_path() -> Path:
    override = os.getenv("XIAOZHI_CONFIG")
    if override:
        return Path(override).expanduser()
    if FROZEN:
        return USER_HOME / "config.yaml"
    return BUNDLE_DIR / "config.yaml"


def _env_path() -> Path:
    override = os.getenv("XIAOZHI_ENV")
    if override:
        return Path(override).expanduser()
    if FROZEN:
        return USER_HOME / ".env"
    return BUNDLE_DIR / ".env"


CONFIG_PATH = _config_path()
ENV_PATH = _env_path()

# 只读资源
WEB_DIR = BUNDLE_DIR / "console" / "web"
CONFIG_TEMPLATE = BUNDLE_DIR / "config.template.yaml"

# 引擎：打包态复用同一个可执行文件（--engine 模式），源码态用项目 venv
if FROZEN:
    ENGINE_PYTHON = sys.executable
    ENGINE_ARGS = ["--engine"]
else:
    ENGINE_PYTHON = str(BUNDLE_DIR / "xiaogpt-env" / "bin" / "python")
    ENGINE_ARGS = ["-m", "mybot.main"]

# 小米登录态：一直在用户主目录，不受打包影响
MI_TOKEN_PATH = Path.home() / ".mi.token"


def ensure_ready() -> dict:
    """首次运行时把用户数据目录铺好。

    打包态才需要 —— 源码态配置和 .env 本来就在项目里。

    做的三件事：
    1. 建 ~/.xiaozhi（700）
    2. 没有 config.yaml 就从包内模板复制一份
    3. 没有 .env 就建一个空的（600），让界面提示用户填 key

    返回一份说明，给界面「首次运行」提示用。
    """
    info: dict = {"frozen": FROZEN, "created": [], "user_home": str(USER_HOME)}
    if not FROZEN:
        info["config"] = str(CONFIG_PATH)
        info["env"] = str(ENV_PATH)
        return info

    USER_HOME.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(USER_HOME, 0o700)
    except OSError:
        pass

    if not CONFIG_PATH.exists():
        if CONFIG_TEMPLATE.exists():
            CONFIG_PATH.write_text(
                CONFIG_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
            )
            info["created"].append(str(CONFIG_PATH))
        else:
            info["error"] = f"包里没有配置模板：{CONFIG_TEMPLATE}"

    if not ENV_PATH.exists():
        ENV_PATH.write_text(
            "# MiniMax API key（界面「智能体」页也能直接填）\n"
            "MINIMAX_API_KEY=\n",
            encoding="utf-8",
        )
        info["created"].append(str(ENV_PATH))

    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass

    info["config"] = str(CONFIG_PATH)
    info["env"] = str(ENV_PATH)
    return info
