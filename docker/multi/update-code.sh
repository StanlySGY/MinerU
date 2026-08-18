#!/bin/bash
# =============================================================================
# 一键更新代码镜像并重启 Router + API
# =============================================================================
# 用途：git pull 拉到新代码后，在本目录执行本脚本，自动完成：
#   1. 用新标签重建 mineru-code 镜像（不改 mineru-env）
#   2. 就地修改本机 env.multi 里的 MINERU_CODE_IMAGE（不需要手动编辑）
#   3. 停止并重新启动 Router + API，加载新代码
#
# 用法：
#   ./update-code.sh            # 自动生成时间戳标签，例如 20260818143000
#   ./update-code.sh v3.4.2-ops-ui6   # 指定标签
# =============================================================================

set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="env.multi"
if [ ! -f "$ENV_FILE" ]; then
    echo "错误：找不到 $ENV_FILE，请先完成现场配置：cp env.multi.example env.multi" >&2
    exit 1
fi

NEW_TAG="${1:-$(date +%Y%m%d%H%M%S)}"
NEW_IMAGE="mineru-code:${NEW_TAG}"

echo "=========================================="
echo "1. 构建新代码镜像：${NEW_IMAGE}"
echo "=========================================="
(cd ../base && MINERU_CODE_TAG="$NEW_TAG" ./build.sh code)

echo ""
echo "=========================================="
echo "2. 更新 ${ENV_FILE} 中的 MINERU_CODE_IMAGE"
echo "=========================================="
if grep -q '^MINERU_CODE_IMAGE=' "$ENV_FILE"; then
    OLD_IMAGE=$(grep '^MINERU_CODE_IMAGE=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
    sed -i "s|^MINERU_CODE_IMAGE=.*|MINERU_CODE_IMAGE=${NEW_IMAGE}|" "$ENV_FILE"
    echo "旧镜像：${OLD_IMAGE}"
else
    echo "MINERU_CODE_IMAGE=${NEW_IMAGE}" >> "$ENV_FILE"
fi
echo "新镜像：${NEW_IMAGE}"

echo ""
echo "=========================================="
echo "3. 重启 Router + API 加载新代码"
echo "=========================================="
./start-multi.sh stop
./start-multi.sh start

echo ""
echo "完成。当前代码镜像：${NEW_IMAGE}"
echo "可用 ./start-multi.sh status 查看容器状态。"
