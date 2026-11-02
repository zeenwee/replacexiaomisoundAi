#!/usr/bin/env python3
"""浏览器版服务 —— 在这台 mac 上跑，局域网的设备用浏览器就能访问。

用法
----
    ./xiaogpt-env/bin/python -m console.server
    ./xiaogpt-env/bin/python -m console.server --port 8848 --open
    ./xiaogpt-env/bin/python -m console.server --no-auth
    ./xiaogpt-env/bin/python -m console.server --host 127.0.0.1

或者用打包好的 App：
    "小智控制台.app/Contents/MacOS/小智控制台" --serve

和桌面版的关系
--------------
**同一套后端**（console/app.py 的 Api 类）、**同一份前端**
（console/web/*，浏览器版多加载一个 bridge-http.js 垫片把 pywebview 的
桥换成 HTTP）。界面里改触发词、模型、音色，改的还是引擎读的那份配置。

引擎也是同一个 xiaogpt 进程（`--engine` 模式），
所以**桌面 App 和这个服务别同时开** —— 两个引擎会同时轮询小米云，
音箱会答两遍。

访问保护
--------
默认要令牌。第一次运行自动生成，存到 `~/.xiaozhi/access_token`（600），
启动时会把带令牌的完整地址打印出来：

    http://<你的局域网IP>:8848/?token=xxxxxxxx

点一下就能进，不用输密码；令牌会存进浏览器的 localStorage。
同一个 Wi-Fi 下的陌生人猜不到令牌就打不开接口。

完全可信的网络里可以用 `--no-auth` 关掉。
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from console.backend import paths
from console.backend.config_io import load_env

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8848
COOKIE = "xiaozhi_token"
WEB_DIR = paths.WEB_DIR

STATIC_FILES = (
    "app.css",
    "app.js",
    "bridge-http.js",
    "manifest.webmanifest",
    "icon-192.png",
    "icon-512.png",
    "icon-1024.png",
)

# 静态资源不设防：里面只有界面和垫片，没有任何机密。
# 真正要保护的是 /api/*（能改配置、能让音箱说话、能读密钥掩码）。
PUBLIC_PATHS = {"/" + n for n in STATIC_FILES} | {"/favicon.ico", "/api/ping"}

LOCKED_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小智控制台 · 需要访问令牌</title><style>
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#F2F2F7;color:#1D1D1F;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:24px}
.box{background:#fff;border-radius:14px;padding:26px 28px;max-width:460px;
box-shadow:0 8px 30px rgba(0,0,0,.08);line-height:1.7}
h1{font-size:18px;margin:0 0 10px}code{background:#F2F2F7;padding:2px 6px;border-radius:5px}
@media (prefers-color-scheme:dark){body{background:#1C1C1E;color:#F2F2F7}
.box{background:#2C2C2E;box-shadow:none}code{background:#3A3A3C}}
</style></head><body><div class="box">
<h1>需要访问令牌</h1>
<p>这个服务只接受带令牌的链接。在跑服务的那台 Mac 上执行：</p>
<p><code>cat ~/.xiaozhi/access_token</code></p>
<p>然后拼到地址后面打开：<br>
<code>http://&lt;那台 Mac 的局域网 IP&gt;:8848/?token=上面的令牌</code></p>
</div></body></html>"""


def lan_ips() -> list[str]:
    """本机的局域网 IP（用来打印能访问的地址）。"""
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ":" not in ip:
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def resolve_token(explicit: str = "") -> str:
    """访问令牌：显式指定 > 环境变量 > ~/.xiaozhi/access_token（没有就生成）。"""
    if explicit:
        return explicit.strip()
    env = (os.getenv("XIAOZHI_TOKEN") or "").strip()
    if env:
        return env
    path = paths.USER_HOME / "access_token"
    if path.exists():
        old = path.read_text(encoding="utf-8").strip()
        if old:
            return old
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


class ConsoleServer:
    def __init__(self, host: str, port: int, token: str):
        self.host = host
        self.port = port
        self.token = token
        from console.app import Api

        self.api = Api()
        # Api 的快操作会调 loop_run（阻塞），放到线程池里跑，
        # 别把 aiohttp 的事件循环堵住。
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="console-http")

    # ---------------- 鉴权 ----------------

    def _token_ok(self, request) -> bool:
        if not self.token:
            return True
        given = (
            request.headers.get("X-Token")
            or request.query.get("token")
            or request.cookies.get(COOKIE)
            or ""
        )
        return bool(given) and hmac.compare_digest(str(given), self.token)

    # ---------------- 路由 ----------------

    async def index(self, request):
        return web.FileResponse(WEB_DIR / "index.html")

    async def static(self, request):
        # 路由是写死的 /app.css 这种字面量，取不到 match_info，
        # 直接从路径上剥掉前导斜杠。
        name = request.path.lstrip("/")
        try:
            path = (WEB_DIR / name).resolve()
            path.relative_to(WEB_DIR.resolve())
        except Exception:
            return web.Response(status=404, text="not found")
        if not path.is_file():
            return web.Response(status=404, text="not found")
        resp = web.FileResponse(path)
        if path.suffix == ".webmanifest":
            resp.content_type = "application/manifest+json"
        # 界面经常改（加卡片、改文案）。不设 no-cache 的话浏览器会拿
        # 启发式缓存里的旧版本，用户看到的还是老界面。
        # no-cache 不是「不缓存」，是「每次都问一次服务器有没有变」。
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def api_call(self, request):
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "请求体不是合法 JSON"}, status=400)
        name = str(body.get("name") or "")
        payload = body.get("payload") or {}
        server = request.app["server"]
        try:
            result = await _run(server.pool, server.api.call, name, payload)
        except Exception as e:
            return _json({"error": f"{type(e).__name__}: {e}"}, status=400)
        return _json(result)

    async def api_task_status(self, request):
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "请求体不是合法 JSON"}, status=400)
        task_id = str(body.get("task_id") or "")
        server = request.app["server"]
        return _json(await _run(server.pool, server.api.task_status, task_id))

    async def api_ping(self, request):
        server = request.app["server"]
        return _json(
            {
                "ok": True,
                "service": "小智控制台",
                "auth": bool(server.token),
                "version": "1.0.0",
            }
        )

    async def favicon(self, request):
        return web.Response(status=204)

    # ---------------- 组装 ----------------

    def build_app(self):
        server = self

        @web.middleware
        async def auth_middleware(request, handler):
            if request.path in PUBLIC_PATHS:
                return await handler(request)
            if server._token_ok(request):
                resp = await handler(request)
                # 带 ?token= 进来的，给它发个 cookie，之后刷新不用再带
                if server.token and request.query.get("token"):
                    resp.set_cookie(
                        COOKIE,
                        server.token,
                        max_age=60 * 60 * 24 * 90,
                        samesite="Lax",
                        httponly=True,
                    )
                return resp
            if request.path.startswith("/api/"):
                return _json({"error": "访问令牌无效"}, status=401)
            return web.Response(
                status=401,
                content_type="text/html",
                charset="utf-8",
                text=LOCKED_HTML,
            )

        app = web.Application(middlewares=[auth_middleware])
        app["server"] = server
        app.router.add_get("/", server.index)
        app.router.add_get("/index.html", server.index)
        for name in STATIC_FILES:
            app.router.add_get("/" + name, server.static)
        app.router.add_post("/api/call", server.api_call)
        app.router.add_post("/api/task_status", server.api_task_status)
        app.router.add_get("/api/ping", server.api_ping)
        app.router.add_get("/favicon.ico", server.favicon)
        return app


async def _run(pool: ThreadPoolExecutor, fn, *args):
    """在线程池里跑同步函数（别把 aiohttp 的事件循环堵住）。

    Api 的快操作会调 loop_run() 阻塞等待，所以必须丢到线程池。
    """
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, fn, *args)


def _json(obj, status: int = 200):
    return web.json_response(
        obj, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="console.server", description="小智控制台 · 浏览器版服务"
    )
    ap.add_argument("--host", default=os.getenv("XIAOZHI_HOST", DEFAULT_HOST))
    ap.add_argument("--port", type=int, default=int(os.getenv("XIAOZHI_PORT", DEFAULT_PORT)))
    ap.add_argument("--token", default="", help="访问令牌；不给就自动生成并存到 ~/.xiaozhi/access_token")
    ap.add_argument("--no-auth", action="store_true", help="不校验令牌（只在完全可信的网络里用）")
    ap.add_argument("--open", action="store_true", help="启动后自动用默认浏览器打开")
    args = ap.parse_args(argv)

    load_env(paths.ENV_PATH)

    # 常驻 asyncio loop：异步操作（小米云、MiniMax）都在上面跑
    from console.backend.loop import start as start_loop

    start_loop()

    token = "" if args.no_auth else resolve_token(args.token)
    server = ConsoleServer(args.host, args.port, token)
    app = server.build_app()

    print("=" * 64)
    print("  小智控制台 · 浏览器版")
    print("=" * 64)
    print(f"  配置：{paths.CONFIG_PATH}")
    print(f"  密钥：{paths.ENV_PATH}")
    ips = lan_ips()
    if not ips:
        print("  ⚠ 没找到局域网 IP，检查一下网络连接")
    for ip in ips:
        url = f"http://{ip}:{args.port}/"
        if token:
            url += f"?token={token}"
        print(f"  ▸ {url}")
    if not token:
        print("  （--no-auth：没开令牌，同网段任何人都能访问）")
    else:
        print(f"  令牌已存：{paths.USER_HOME / 'access_token'}（600）")
        print("  之后直接用上面那条带 token 的地址，浏览器会记住")

    # 已经有别的引擎在跑就提醒一句
    try:
        from console.backend.engine import external_instances

        others = external_instances()
        if others:
            print(f"  ⚠ 已经有 {len(others)} 个 xiaogpt 引擎在跑（pid {others}）。")
            print("    两个引擎会同时轮询小米云，音箱会答两遍 —— 建议先停掉一个。")
    except Exception:
        pass
    print("=" * 64)
    print("  Ctrl+C 停止")
    print()
    sys.stdout.flush()

    if args.open:
        target = f"http://127.0.0.1:{args.port}/"
        if token:
            target += f"?token={token}"
        try:
            webbrowser.open(target)
        except Exception:
            pass

    try:
        web.run_app(app, host=args.host, port=args.port, print=None)
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
