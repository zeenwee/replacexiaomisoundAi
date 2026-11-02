#!/bin/bash
# 打包「小智控制台」成 macOS .app
#
#   ./console/build_app.sh
#
# 产物：dist/小智控制台.app
# 打完会做 ad-hoc 签名（codesign --deep --force --sign -），
# 双击就能用，不用「右键 → 打开」那一套。
#
# 想跳过签名/想改配置，看下面的注释。
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$PWD"
PY="$ROOT/xiaogpt-env/bin/python"
# 清理旧产物的命令。默认走 macOS 回收站（trash），找不到就用 rm -rf。
if command -v trash >/dev/null 2>&1; then
  TRASH="trash"
else
  TRASH="rm -rf"
fi
APP_NAME="小智控制台"
APP="$ROOT/dist/$APP_NAME.app"

if [ ! -x "$PY" ]; then
  echo "✗ 找不到 venv：$PY"
  echo "  先建环境：uv venv --python 3.12 xiaogpt-env"
  exit 1
fi

if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
  echo "✗ 没装 PyInstaller"
  echo "  cd $ROOT"
  echo "  VIRTUAL_ENV=$ROOT/xiaogpt-env UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv pip install pyinstaller"
  exit 1
fi

# ---- 清理旧产物（走回收站，不直接 rm）----
for d in build dist; do
  if [ -e "$ROOT/$d" ]; then
    "$TRASH" "$ROOT/$d" >/dev/null 2>&1 || true
  fi
done

# ---- 图标（没有就现生成一个）----
if [ ! -f "$ROOT/console/assets/icon.icns" ] && [ -f "$ROOT/tools/make_icon.py" ]; then
  echo "── 生成图标 ──"
  "$PY" "$ROOT/tools/make_icon.py" | sed 's/^/  /'
fi

echo "── 打包 ──"
"$PY" -m PyInstaller --noconfirm --clean "$ROOT/console/console.spec"

if [ ! -d "$APP" ]; then
  echo "✗ 没生成 $APP"
  exit 1
fi

echo
echo "── ad-hoc 签名 ──"
# ad-hoc 签名：不需要开发者证书，就能让 macOS 认这个 App。
# --deep 会连里面的可执行文件一起签。
codesign --deep --force --sign - "$APP"
codesign --verify --verbose=1 "$APP" 2>&1 | sed 's/^/  /' || true

echo
echo "── 产物 ──"
du -sh "$APP" | sed 's/^/  /'
file "$APP/Contents/MacOS/$APP_NAME" | sed 's/^/  /'
echo
echo "✓ 完成：$APP"
echo
echo "── 用户数据目录 ──"
# 密钥不放进 App 包（包里一改就签名失效，密钥也等于明文分发），
# 放 ~/.xiaozhi/.env，权限 600。
if [ ! -f "$HOME/.xiaozhi/.env" ] && [ -f "$ROOT/.env" ]; then
  mkdir -p "$HOME/.xiaozhi"
  cp "$ROOT/.env" "$HOME/.xiaozhi/.env"
  chmod 600 "$HOME/.xiaozhi/.env"
  echo "  已把项目里的 .env 复制到 ~/.xiaozhi/.env（600）"
else
  echo "  ~/.xiaozhi/.env 已存在，保持不动"
fi
echo "  首次运行会在 ~/.xiaozhi/ 下生成 config.yaml"
echo "  密钥也可以直接在界面「智能体」页填"
echo
echo "拖进「应用程序」即可；右键打开可查看包内日志 ~/.xiaozhi/console.log"
