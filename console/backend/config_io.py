"""读写 config.yaml 和 .env。

为什么不用 ruamel.yaml 做 round-trip
------------------------------------
试过了：ruamel 能保住大部分注释，但**列表项之间的独立注释块会被丢掉**
（比如 keyword 列表后面那段「触发排除」说明就没了）。配置文件里的
注释是精心写的说明，不能随手丢。

所以这里用「按行定点编辑」：
- 读：用 yaml.safe_load 拿结构
- 写：只替换要改的那几行，其余**一个字节都不动**

这样改配置永远只影响目标字段，注释、顺序、空行全部原样保留。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

SCALAR_RE = re.compile(r"^(\s*)([^\s:#][^:]*):(\s*)(.*)$")


def _needs_quotes(value: str) -> bool:
    if value == "":
        return True
    if value[0] in "#&*!|>%@`\"'[]{},":
        return True
    if ": " in value or value.endswith(":"):
        return True
    if value.strip() != value:
        return True
    return False


def _dump_scalar(value: Any) -> str:
    """把一个标量转成 YAML 里该有的样子。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if "\n" in text:
        # 多行用块标量
        return "|-\n" + "\n".join("    " + ln for ln in text.split("\n"))
    if _needs_quotes(text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


class ConfigEditor:
    """按行定点编辑 YAML，只改目标字段。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    # ---------------- 读 ----------------

    def load(self) -> dict:
        if not self.text.strip():
            return {}
        return yaml.safe_load(self.text) or {}

    def get(self, path: str, default: Any = None) -> Any:
        """按点号路径取值，如 get("gpt_options.model")。"""
        node: Any = self.load()
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # ---------------- 定位 ----------------

    def _key_line(self, path: str) -> tuple[int, int] | None:
        """返回 (行号, 缩进) —— 定位点号路径最后一段的 key 行。"""
        parts = path.split(".")
        lines = self.text.splitlines()
        # 先逐层收窄范围
        start, end = 0, len(lines)
        indent = 0
        for part in parts[:-1]:
            found = None
            for i in range(start, end):
                m = SCALAR_RE.match(lines[i])
                if m and m.group(2).strip() == part and len(m.group(1)) == indent:
                    found = i
                    break
            if found is None:
                return None
            start = found + 1
            indent += 2
            end = len(lines)
        # 最后一段
        for i in range(start, end):
            m = SCALAR_RE.match(lines[i])
            if m and m.group(2).strip() == parts[-1] and len(m.group(1)) == indent:
                return i, indent
        return None

    def _list_block(self, key_line: int, indent: int) -> tuple[int, int]:
        """返回列表项块的 (起始行, 结束行)，不含 key 行本身。"""
        lines = self.text.splitlines()
        i = key_line + 1
        start = i
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if not stripped:
                break
            cur_indent = len(raw) - len(raw.lstrip())
            if cur_indent <= indent:
                break
            if stripped.startswith("- "):
                i += 1
                continue
            if stripped.startswith("#"):
                # 夹在列表项之间的注释，算作块的一部分
                i += 1
                continue
            break
        return start, i

    # ---------------- 写 ----------------

    def set_scalar(self, path: str, value: Any) -> bool:
        """设置一个标量字段，保留行尾注释。"""
        loc = self._key_line(path)
        if loc is None:
            return False
        idx, _ = loc
        lines = self.text.splitlines()
        raw = lines[idx]
        m = SCALAR_RE.match(raw)
        if not m:
            return False
        tail = m.group(4)
        # 保留行尾注释
        comment = ""
        hash_pos = tail.find(" #")
        if hash_pos != -1:
            comment = tail[hash_pos:]
        new_raw = f"{m.group(1)}{m.group(2)}: {_dump_scalar(value)}{comment}"
        lines[idx] = new_raw
        self.text = "\n".join(lines) + "\n"
        return True

    def set_list(self, path: str, items: list[str]) -> bool:
        """设置一个字符串列表，保留块外所有注释。"""
        loc = self._key_line(path)
        if loc is None:
            return False
        idx, indent = loc
        lines = self.text.splitlines()
        start, end = self._list_block(idx, indent)
        pad = " " * (indent + 2)
        new_items = [f"{pad}- {it}" for it in items]
        lines[start:end] = new_items
        self.text = "\n".join(lines) + "\n"
        return True

    def save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(self.text, encoding="utf-8")
        os.replace(tmp, self.path)


def load_env(path: str | Path) -> dict:
    """把 .env 读进 os.environ。

    **已经存在的真环境变量优先，不覆盖** —— 和 run.sh 的行为保持一致，
    免得外部导出的值被 .env 里的空值冲掉。

    为什么需要它：mybot 里取密钥的顺序是
    「config.yaml 的 openai_key → os.environ['MINIMAX_API_KEY']」，
    而密钥只存在 .env 里（不写进 config.yaml）。控制台进程不载入 .env，
    TTS 和对话测试就会拿到空 key。
    """
    env = EnvFile(path)
    out: dict[str, str] = {}
    for k, v in env.as_dict().items():
        out[k] = v
        if v and not os.environ.get(k):
            os.environ[k] = v
    return out


class EnvFile:
    """.env 的极简读写（KEY=VALUE，# 开头是注释）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lines: list[str] = (
            self.path.read_text(encoding="utf-8").splitlines()
            if self.path.exists()
            else []
        )

    def get(self, key: str, default: str = "") -> str:
        prefix = key + "="
        for line in self.lines:
            if line.startswith(prefix):
                return line[len(prefix):].strip().strip('"').strip("'")
        return default

    def set(self, key: str, value: str) -> None:
        prefix = key + "="
        for i, line in enumerate(self.lines):
            if line.startswith(prefix):
                self.lines[i] = f"{prefix}{value}"
                break
        else:
            self.lines.append(f"{prefix}{value}")
        self.save()

    def unset(self, key: str) -> None:
        prefix = key + "="
        self.lines = [ln for ln in self.lines if not ln.startswith(prefix)]
        self.save()

    def as_dict(self) -> dict:
        out = {}
        for line in self.lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
        return out

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        os.chmod(self.path, 0o600)
