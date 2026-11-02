#!/bin/bash
# 录一段人声给音色克隆用。
#
# 用法：
#     ./tools/record_voice.sh                 # 录到 ~/Desktop/voice_sample.m4a
#     ./tools/record_voice.sh 90              # 录 90 秒
#     ./tools/record_voice.sh 60 /tmp/a.wav   # 指定时长和输出路径
#
# 录完会自动检查素材合不合格（时长/格式/大小），不合格会告诉你差在哪。
#
# 前提：需要一个能录的麦克风输入设备。
#   - Mac mini 本机：系统设置 > 隐私与安全性 > 麦克风，允许终端访问；
#     然后插上 USB 麦克风，或用手机当麦克风（见下）。
#   - 没有麦克风的话，最省事的是用手机录（见《音色克隆操作指南.md》）。

set -euo pipefail

DUR="${1:-60}"
OUT="${2:-$HOME/Desktop/voice_sample.m4a}"

if [[ ! "$DUR" =~ ^[0-9]+$ ]] || [ "$DUR" -lt 10 ] || [ "$DUR" -gt 300 ]; then
    echo "x 时长要在 10~300 秒之间（素材要求 10 秒 ~ 5 分钟）"
    exit 2
fi

mkdir -p "$(dirname "$OUT")"

# 找一个能用的输入设备
DEV=""
if command -v ffmpeg >/dev/null 2>&1; then
    # ffmpeg：优先 avfoundation 的第一个输入
    DEV="$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -o '\[[0-9]*\].*' | head -1 | sed 's/^\[[0-9]*\] //')"
    echo ">>> 用 ffmpeg 录制，输入设备：${DEV:-默认}"
    ffmpeg -hide_banner -loglevel warning -f avfoundation -i ":${FFMPEG_DEV:-0}" \
        -t "$DUR" -ac 1 -ar 44100 -b:a 128k "$OUT"
elif command -v sox >/dev/null 2>&1; then
    echo ">>> 用 sox 录制，设备：default"
    sox -d -r 44100 -c 1 "$OUT" trim 0 "$DUR"
else
    echo "x 本机没有录音工具（ffmpeg / sox 都没装）"
    echo ""
    echo "两个办法，选一个："
    echo "  1) 装 ffmpeg（推荐）：brew install ffmpeg"
    echo "  2) 用手机录 —— 见《音色克隆操作指南.md》，录完把文件拖到本目录，"
    echo "     再跑 ./xiaogpt-env/bin/python tools/voice_clone.py check <文件>"
    exit 3
fi

echo ""
echo ">>> 录好了：$OUT"
"$(dirname "$0")/../xiaogpt-env/bin/python" "$(dirname "$0")/voice_clone.py" check "$OUT" || true