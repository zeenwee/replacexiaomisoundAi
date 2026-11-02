# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 「小智控制台.app」。

用法：
    ./console/build_app.sh
或者：
    ./xiaogpt-env/bin/python -m PyInstaller --noconfirm --clean console/console.spec

几个关键取舍
------------
1. **配置和密钥不进包**。
   config.yaml 只作为「模板」打进去，运行时复制到 ~/.xiaozhi/；
   .env 根本不进包。理由：App 包在 macOS 上会被 codesign 校验，
   改一个字节签名就废；而把密钥打进包里等于明文分发。
2. **界面 HTML 进包**（只读资源），运行时从 sys._MEIPASS 读。
3. **langchain / groq 排除**：本项目用不上，langchain_community 一收
   就是几百兆。mybot/compat.py 给它们准备了空壳兜底。
4. **同一个二进制两个角色**：带 --engine 参数就是 xiaogpt 引擎
   （见 console/launcher.py），不用再找项目的 venv。
"""

import sys
from pathlib import Path

def _find_root() -> Path:
    """找到项目根（同时含 console/ 和 config.yaml 的那一层）。

    PyInstaller 不同版本里 SPECPATH 的基准不一样（有的是 spec 所在目录，
    有的是它跑起来时的 cwd），所以这里直接往上找，找到为止。
    """
    here = Path(SPECPATH).resolve()
    for cand in (here, here.parent, here.parent.parent):
        if (cand / "console" / "launcher.py").exists() and (cand / "config.yaml").exists():
            return cand
    return here.parent


ROOT = _find_root()
APP_NAME = "小智控制台"

# ---- 配置模板 ----
# PyInstaller 的 datas 目标名是**目录名**，想让文件落在指定名字下，
# 得先把它复制到一个暂存目录，再用 "." 作为目标目录。
STAGE = ROOT / ".pyi_stage"
STAGE.mkdir(parents=True, exist_ok=True)
TEMPLATE = STAGE / "config.template.yaml"
TEMPLATE.write_text(
    (ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8"
)

# 源码里就有的依赖（打包后依然要能 import）
hiddenimports = [
    # pywebview 的 WKWebView 后端
    "webview.platforms.cocoa",
    "AppKit",
    "Foundation",
    "WebKit",
    "Quartz",
    "UniformTypeIdentifiers",
    "objc",
    "PyObjCTools",
    "PyObjCTools.AppHelper",
    # 业务依赖（动态 import，静态分析扫不到的补上）
    "miservice",
    # MIoT 通道用到的子模块（console/backend/miot.py 里是函数内 import）
    "miservice.miaccount",
    "miservice.miioservice",
    "openai",
    "httpx",
    "aiohttp",
    "yaml",
    "rich",
    # xiaogpt 里被动态引用的模块
    "xiaogpt.bot.base_bot",
    "xiaogpt.bot.chatgptapi_bot",
    "xiaogpt.tts.base",
    "xiaogpt.tts.mi",
]

# 明确不要的：用到就会炸的重量级依赖
excludes = [
    "langchain",
    "langchain_community",
    "langchain_openai",
    "langchain_core",
    "langchain_text_splitters",
    "langchain_schema",
    "langchain_agents",
    "langchain_chains",
    "langchain_memory",
    "langchain_callbacks",
    "langchain_text_splitters",
    "groq",
    "tkinter",
    "matplotlib",
    "pandas",
    "IPython",
    "notebook",
    "pytest",
]

a = Analysis(
    [str(ROOT / "console" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 界面资源（只读）
        (str(ROOT / "console" / "web"), "console/web"),
        # 配置模板：首次运行复制到 ~/.xiaozhi/config.yaml
        (str(TEMPLATE), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

ICON = ROOT / "console" / "assets" / "icon.icns"

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="com.xiaozhi.console",
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleLocalizations": ["zh_CN", "en"],
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        # 本机 HTTP 推音频给音箱用（MiniMax TTS 的 mp3 走局域网）
        "NSAppTransportSecurity": {
            "NSAllowsLocalNetworking": True,
        },
    },
)
