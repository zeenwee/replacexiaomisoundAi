#!/bin/bash
# 小智控制台 · 浏览器版服务 —— 启停管理脚本
#
#   ./console/run_server.sh start     # 后台起（脱离当前终端）
#   ./console/run_server.sh stop      # 停
#   ./console/run_server.sh restart   # 重启
#   ./console/run_server.sh status    # 看状态
#   ./console/run_server.sh log       # 跟日志
#   ./console/run_server.sh url       # 打印局域网访问地址
#
# 想开机自启见 console/launchd/install.sh（需要正常登录会话）。
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="$ROOT/xiaogpt-env/bin/python"
PORT="${XIAOZHI_PORT:-8848}"
PIDFILE="$HOME/.xiaozhi/server.pid"
LOGFILE="$HOME/.xiaozhi/server.log"
ACTION="${1:-status}"

mkdir -p "$HOME/.xiaozhi"

is_running() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

show_urls() {
  local token=""
  [ -f "$HOME/.xiaozhi/access_token" ] && token="$(cat "$HOME/.xiaozhi/access_token")"
  local ip
  for ip in $(ipconfig getifaddr en0 2>/dev/null; ipconfig getifaddr en1 2>/dev/null); do
    local url="http://$ip:$PORT/"
    [ -n "$token" ] && url="$url?token=$token"
    echo "  $url"
  done
}

case "$ACTION" in
  start)
    if is_running; then
      echo "· 已经在跑了（pid $(cat "$PIDFILE")）"
      show_urls
      exit 0
    fi
    nohup "$PY" -m console.server --port "$PORT" >>"$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 3
    if is_running; then
      echo "✓ 已启动（pid $(cat "$PIDFILE")），日志：$LOGFILE"
      show_urls
    else
      echo "✗ 没起来，看日志：tail -30 $LOGFILE"
      exit 1
    fi
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PIDFILE")" 2>/dev/null
      sleep 2
      kill -9 "$(cat "$PIDFILE")" 2>/dev/null
      echo "✓ 已停止"
    else
      echo "· 没在跑"
    fi
    rm -f "$PIDFILE"
    ;;
  restart)
    "$0" stop >/dev/null 2>&1
    sleep 1
    "$0" start
    ;;
  status)
    if is_running; then
      echo "✓ 运行中（pid $(cat "$PIDFILE")）"
    else
      echo "✗ 没在跑"
    fi
    show_urls
    ;;
  url)
    show_urls
    ;;
  log)
    tail -f "$LOGFILE"
    ;;
  *)
    echo "用法：$0 {start|stop|restart|status|url|log}"
    exit 1
    ;;
esac
