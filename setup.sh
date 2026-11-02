#!/bin/bash
# 一键配置：填密钥 → 登录小米 → 补 L16A 指令 → 模型自检 → 启动
#
# 用法：
#     ./setup.sh
#
# 你只需要输入两样东西（都是只有你有的）：
#     1) MiniMax API key
#     2) 小米ID 和密码（如果 .env 里已经有了就不会问）
# 其余全自动。配置好之后，日常启动直接用 ./run.sh。
set -euo pipefail
cd "$(dirname "$0")"

PY=./xiaogpt-env/bin/python

if [ ! -x "$PY" ]; then
  echo "环境不存在，请先运行："
  echo "    uv venv --python 3.12 xiaogpt-env"
  echo "    VIRTUAL_ENV=\$PWD/xiaogpt-env UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \\"
  echo "      uv pip install 'xiaogpt[locked]' miservice_fork"
  exit 1
fi

touch .env
chmod 600 .env

# ---------- 1. MiniMax 密钥 ----------
if ! grep -qE '^[[:space:]]*MINIMAX_API_KEY=.+' .env; then
  echo "======================================================"
  echo " 第 1 步：填 MiniMax API key"
  echo "======================================================"
  echo " 中国大陆区密钥地址："
  echo "   https://platform.minimaxi.com/user-center/basic-information/interface-key"
  echo " ⚠️ key 不能跨区域用，国内 key 只能打 api.minimaxi.com"
  echo
  printf " 请输入密钥（不回显）: "
  read -rs KEY
  echo
  if [ -z "${KEY:-}" ]; then
    echo "✗ 没输入密钥，退出。"
    exit 1
  fi
  if grep -qE '^[[:space:]]*MINIMAX_API_KEY=' .env; then
    KEY="$KEY" awk -F= '/^[[:space:]]*MINIMAX_API_KEY=/{print "MINIMAX_API_KEY=" ENVIRON["KEY"]; next} {print}' .env > .env.tmp
    mv .env.tmp .env
  else
    printf 'MINIMAX_API_KEY=%s\n' "$KEY" >> .env
  fi
  chmod 600 .env
  unset KEY
  echo "✓ 密钥已写入 .env（权限 600）"
  echo
fi

set -a
. ./.env
set +a

# ---------- 2. 小米账号 ----------
if [ -z "${MI_USER:-}" ] || [ -z "${MI_PASS:-}" ]; then
  echo "======================================================"
  echo " 第 2 步：登录小米云，找音箱"
  echo "======================================================"
  "$PY" tools/setup_wizard.py || exit 1
  set -a
  . ./.env
  set +a
  echo
fi

# ---------- 3. L16A 硬件指令 ----------
echo "======================================================"
echo " 第 3 步：补 L16A 硬件指令"
echo "======================================================"
"$PY" tools/patch_l16a.py
echo

# ---------- 4. 模型自检 ----------
echo "======================================================"
echo " 第 4 步：模型侧自检"
echo "======================================================"
if ! "$PY" tools/check_minimax.py; then
  echo
  echo "模型侧自检没通过，请按上面的提示处理。"
  exit 1
fi
echo

# ---------- 5. 启动 ----------
echo "======================================================"
echo " 第 5 步：启动"
echo "======================================================"
exec "$PY" -m mybot.main --config config.yaml "$@"
