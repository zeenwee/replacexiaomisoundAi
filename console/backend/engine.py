"""引擎进程管理：启停 / 重启 xiaogpt，并实时收集日志。

控制台不重写引擎 —— 这里跑的就是命令行那个
    python -m mybot.main --config config.yaml
所以「界面里改的触发词」和「引擎实际跑的」永远是同一套代码。

「只能有一个引擎」
------------------
两个 xiaogpt 引擎会同时轮询小米云，问一句它答两遍。
所以这里做两件事：

1. 控制台自己起的引擎，pid 记到 `~/.xiaozhi/engine.pid`。
2. 启动前先看看**别的进程**里有没有引擎在跑
   （pgrep 找 mybot.main，加上 pid 文件），有就不启第二个；
   「停止」则会把它们一起停掉。

浏览器版尤其需要这个：服务进程可能不是起引擎的那个
（比如引擎是 `./run.sh` 起的），界面得如实反映状态。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from console.backend import paths

ROOT = paths.BUNDLE_DIR
PYTHON = paths.ENGINE_PYTHON
CONFIG = paths.CONFIG_PATH

PID_FILE = "engine.pid"


# ---------------------------------------------------------------- pid 文件


def _pid_file() -> Path:
    return paths.USER_HOME / PID_FILE


def read_pid_file() -> int | None:
    """pid 文件里记的引擎 pid（进程还在才返回）。"""
    p = _pid_file()
    if not p.exists():
        return None
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def write_pid_file(pid: int) -> None:
    try:
        p = _pid_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(pid), encoding="utf-8")
        os.chmod(p, 0o600)
    except OSError:
        pass


def clear_pid_file() -> None:
    try:
        _pid_file().unlink(missing_ok=True)
    except OSError:
        pass


def _is_engine_process(pid: int) -> bool:
    """这个 pid 真的是 xiaogpt 引擎吗？

    `pgrep -f` 匹配的是**整条命令行**，所以任何命令行里恰好提到
    "mybot.main" 的进程都会被算进来 —— 包括你手敲的 pgrep、grep，
    甚至包着它们的 shell。那种假阳性很危险：界面上会显示一个幽灵 pid，
    点「停止」还会去杀那个无辜的 shell。

    所以再核一遍：可执行文件得是 python（或者打包版的 App），
    并且命令行里真的有引擎的特征参数。
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return False
    cmd = (out.stdout or "").strip()
    if not cmd:
        return False

    parts = cmd.split()
    base = os.path.basename(parts[0]) if parts else ""

    # 源码态：python -m mybot.main --config xxx
    if base.startswith("python") and "-m mybot.main" in cmd:
        return True
    # 打包态：小智控制台 --engine --config xxx
    if "小智控制台" in base and "--engine" in cmd:
        return True
    return False


def external_instances(exclude: int | None = None) -> list[int]:
    """别的进程里已经在跑的 xiaogpt 引擎（pid 列表）。

    两个来源：
    - pgrep 找 `-m mybot.main`（源码态 `./run.sh` 起的）
    - pid 文件（控制台自己起的，打包态命令行里没有 "mybot.main"）
    """
    pids: set[int] = set()

    pid = read_pid_file()
    if pid and _is_engine_process(pid):
        pids.add(pid)

    try:
        out = subprocess.run(
            # 模式不能以 "-" 开头（pgrep 会当成选项）。
            # 匹配放宽没关系，真正的把关在 _is_engine_process()。
            ["pgrep", "-f", "mybot[.]main"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for item in out.stdout.split():
            if item.isdigit():
                pid = int(item)
                if _is_engine_process(pid):
                    pids.add(pid)
    except Exception:
        pass

    pids.discard(os.getpid())
    if exclude:
        pids.discard(exclude)
    return sorted(pids)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate(pid: int) -> bool:
    """停掉一个外部引擎进程。"""
    try:
        os.kill(pid, 15)
    except OSError:
        return False
    for _ in range(20):
        time.sleep(0.3)
        if not _alive(pid):
            return True
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    return True


class Engine:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.logs: deque[str] = deque(maxlen=3000)
        self._offset = 0
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None

    # ---------------- 状态 ----------------

    @property
    def owned(self) -> bool:
        """本进程起的引擎在跑吗。"""
        return self.proc is not None and self.proc.poll() is None

    @property
    def owned_pid(self) -> int | None:
        return self.proc.pid if self.owned and self.proc else None

    @property
    def running(self) -> bool:
        """有没有引擎在跑（不管是谁起的）。"""
        if self.owned:
            return True
        return bool(self.external)

    @property
    def external(self) -> list[int]:
        return external_instances(exclude=self.owned_pid)

    def status(self) -> dict:
        return {
            "running": self.running,
            "owned": self.owned,
            "pid": self.owned_pid,
            "external": self.external,
            "log_count": len(self.logs),
        }

    # ---------------- 日志 ----------------

    def tail(self, limit: int = 200) -> list[str]:
        with self._lock:
            return list(self.logs)[-limit:]

    def since(self, offset: int) -> tuple[list[str], int]:
        """取增量日志 —— 前端轮询用。"""
        with self._lock:
            items = list(self.logs)
        new = items[offset:]
        return new, len(items)

    def _pump(self) -> None:
        assert self.proc is not None
        stream = self.proc.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, ""):
            line = raw.rstrip("\n")
            if not line:
                continue
            with self._lock:
                self.logs.append(line)
        with self._lock:
            self.logs.append("--- 引擎进程已退出 ---")

    # ---------------- 启停 ----------------

    def start(self) -> dict:
        if self.owned:
            return {"ok": True, "already": True, **self.status()}

        others = self.external
        if others:
            return {
                "ok": False,
                "error": (
                    f"已经有引擎在跑了（pid {', '.join(str(p) for p in others)}）。"
                    "两个引擎会同时轮询小米云，音箱会答两遍 —— "
                    "先在「控制台」页点「停止」把它停掉再启动。"
                ),
                **self.status(),
            }

        if not Path(PYTHON).exists():
            return {"ok": False, "error": f"找不到 {PYTHON}"}

        env = dict(os.environ)
        # 载入 .env（外部已导出的优先）
        env_file = paths.ENV_PATH
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v and not env.get(k):
                    env[k] = v

        self.logs.clear()
        self._offset = 0
        self.proc = subprocess.Popen(
            [str(PYTHON), *paths.ENGINE_ARGS, "--config", str(CONFIG)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        write_pid_file(self.proc.pid)
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        time.sleep(0.3)
        return {"ok": True, **self.status()}

    def stop(self) -> dict:
        stopped = []
        if self.owned:
            assert self.proc is not None
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
            self.proc = None
            stopped.append("本进程的引擎")

        # 别的进程里的引擎也一并停掉（用户点了停止，意图很明确）
        for pid in self.external:
            if _terminate(pid):
                stopped.append(f"外部引擎 pid {pid}")

        clear_pid_file()
        if not stopped:
            return {"ok": True, "already": True, "running": False, "stopped": []}
        return {
            "ok": True,
            "running": False,
            "stopped": stopped,
            "detail": "已停止：" + "、".join(stopped),
        }

    def restart(self) -> dict:
        self.stop()
        time.sleep(0.8)
        return self.start()


ENGINE = Engine()
