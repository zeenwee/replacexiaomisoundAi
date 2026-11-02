#!/bin/bash
# 启动 xiaogpt + 自有 MiniMax 智能体（小米 Sound / L16A）
#
# 流程：载入 .env → 模型侧自检 → 补 L16A 硬件指令 → 启动
# 想跳过自检：./run.sh --skip-check
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

if [ ! -f config.yaml ]; then
  echo "还没有配置文件，请先运行："
  echo "    ./xiaogpt-env/bin/python tools/setup_wizard.py"
  exit 1
fi

# 载入 .env（小米账号凭据 + MiniMax key）
# 注意：.env 里如果写的是空值，会把外面已经导出的同名变量冲掉，
# 所以先记住外部的值，载入后再放回去（外部环境变量优先）。
_PRE_KEY="${MINIMAX_API_KEY:-}"
_PRE_OPENAI="${OPENAI_API_KEY:-}"
set -a
[ -f .env ] && . ./.env
set +a
[ -n "$_PRE_KEY" ] && export MINIMAX_API_KEY="$_PRE_KEY"
[ -n "$_PRE_OPENAI" ] && export OPENAI_API_KEY="$_PRE_OPENAI"

# 模型密钥：环境变量优先，其次 config.yaml 里的 openai_key
if [ -z "${MINIMAX_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ] \
   && grep -qE '^[[:space:]]*openai_key:[[:space:]]*""[[:space:]]*$' config.yaml; then
  echo "缺少模型密钥。请这样写入 .env（别写进 config.yaml，容易泄露）："
  echo "    echo 'MINIMAX_API_KEY=你的MiniMax密钥' >> .env"
  echo "    chmod 600 .env"
  echo "密钥获取地址：https://platform.minimaxi.com/user-center/basic-information/interface-key"
  exit 1
fi

# 参数：把 --skip-check 摘出来，剩下的原样传给 xiaogpt
ARGS=()
DO_CHECK=1
for arg in "$@"; do
  if [ "$arg" = "--skip-check" ]; then
    DO_CHECK=0
  else
    ARGS+=("$arg")
  fi
done

# ---- 模型侧自检：密钥对不对、模型名有没有、区域选没选错 ----
if [ "$DO_CHECK" = "1" ]; then
  echo "── 模型侧自检 ──"
  if ! "$PY" tools/check_minimax.py; then
    echo
    echo "模型侧自检没通过。先解决上面的问题再启动，否则音箱只会说「密钥无效」。"
    echo "（确认没问题可以加 --skip-check 跳过自检）"
    exit 1
  fi
  echo
fi

# ---- L16A 硬件指令补丁（幂等，只做一次）----
if [ -f tools/patch_l16a.py ]; then
  "$PY" tools/patch_l16a.py >/dev/null 2>&1 || true
fi

echo "启动自有智能体（MiniMax）……"
exec "$PY" -m mybot.main --config config.yaml ${ARGS[@]+"${ARGS[@]}"}
