#!/bin/bash
# 把「小智控制台 · 浏览器版」装成开机自启的常驻服务（launchd LaunchAgent）
#
#   ./console/launchd/install.sh            # 安装 + 立刻启动
#   ./console/launchd/install.sh --no-start # 只装不启
#   ./console/launchd/install.sh --uninstall # 卸载
#
# 装完之后：
#   · 开机自动起
#   · 崩了自动拉起（KeepAlive）
#   · 日志在 ~/.xiaozhi/server.log
#   · 卸载：launchctl bootout gui/$(id -u)/com.xiaozhi.console
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$PWD"
PY="$ROOT/xiaogpt-env/bin/python"
LABEL="com.xiaozhi.console"
AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENT_DIR/$LABEL.plist"
PORT="${XIAOZHI_PORT:-8848}"
UID_NUM="$(id -u)"

if [ ! -x "$PY" ]; then
  echo "✗ 找不到 $PY"
  exit 1
fi

uninstall() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  if [ -f "$PLIST" ]; then
    if command -v trash >/dev/null 2>&1; then
      trash "$PLIST" >/dev/null 2>&1 || rm -f "$PLIST"
    else
      rm -f "$PLIST"
    fi
    echo "✓ 已卸载并移除 $PLIST"
  else
    echo "· 本来就没装"
  fi
}

if [ "${1:-}" = "--uninstall" ]; then
  uninstall
  exit 0
fi

mkdir -p "$AGENT_DIR" "$HOME/.xiaozhi"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>console.server</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$HOME/.xiaozhi/server.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.xiaozhi/server.log</string>
</dict>
</plist>
PLIST_EOF

echo "✓ 已写入 $PLIST"

if [ "${1:-}" = "--no-start" ]; then
  echo "  （没启动。下次开机自动起，或者：launchctl bootstrap gui/$UID_NUM/$LABEL）"
  exit 0
fi

# 先停掉可能存在的旧实例，再挂上
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"

echo "✓ 已启动（开机自启 + 崩了自动拉起）"
echo
echo "  地址："
TOKEN=""
[ -f "$HOME/.xiaozhi/access_token" ] && TOKEN="$(cat "$HOME/.xiaozhi/access_token")"
for ip in $(ipconfig getifaddr en0 2>/dev/null; ipconfig getifaddr en1 2>/dev/null); do
  URL="http://$ip:$PORT/"
  [ -n "$TOKEN" ] && URL="$URL?token=$TOKEN"
  echo "    $URL"
done
echo
echo "  日志：~/.xiaozhi/server.log"
echo "  停止：launchctl bootout gui/$UID_NUM/$LABEL"
