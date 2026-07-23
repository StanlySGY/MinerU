#!/bin/bash
# =============================================================================
# MinerU 一键部署脚本
# =============================================================================
# 使用方式：
#   ./start.sh build              # 构建全功能镜像
#   ./start.sh vlm                # 启动 VLM Server（NPU/GPU 机器）
#   ./start.sh api                # 启动 API Server（CPU/GPU 机器）
#   ./start.sh all                # 启动全部（同机部署）
#   ./start.sh stop               # 停止全部
#   ./start.sh status             # 查看状态
#   ./start.sh logs               # 查看日志
# =============================================================================

set -e

cd "$(dirname "$0")"

# 兼容 docker compose v1 和 v2
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

if [ ! -f .env ]; then
    echo "错误：.env 不存在，请先执行 cp env.example .env 并完成配置。" >&2
    exit 1
fi

VLM_DEVICE_TYPE="$(sed -n 's/^VLM_DEVICE_TYPE=//p' .env | tail -n 1)"
VLM_DEVICE_TYPE="${VLM_DEVICE_TYPE:-ascend}"
VLM_OVERRIDE="compose-vlm.${VLM_DEVICE_TYPE}.yaml"
if [ ! -f "$VLM_OVERRIDE" ]; then
    echo "错误：VLM_DEVICE_TYPE 必须是 nvidia 或 ascend。" >&2
    exit 1
fi
VLM_ARGS="-f compose-vlm.yaml -f $VLM_OVERRIDE --env-file .env"

case "${1:-help}" in
    build)
        echo "构建 MinerU 环境镜像和代码镜像..."
        ../base/build.sh all
        ;;

    vlm)
        echo "启动 VLM Server..."
        $COMPOSE_CMD $VLM_ARGS up -d
        echo "VLM Server 已启动: http://$(hostname -I | awk '{print $1}'):30000/v1/models"
        ;;

    api)
        echo "启动 API Server..."
        $COMPOSE_CMD -f compose-api.yaml --env-file .env up -d
        echo "API Server 已启动: http://$(hostname -I | awk '{print $1}'):18000/docs"
        ;;

    all)
        echo "启动全部服务（VLM + API）..."
        $COMPOSE_CMD $VLM_ARGS up -d
        sleep 30
        $COMPOSE_CMD -f compose-api.yaml --env-file .env up -d
        echo "全部服务已启动！"
        echo "  API: http://$(hostname -I | awk '{print $1}'):18000/docs"
        echo "  VLM: http://$(hostname -I | awk '{print $1}'):30000/v1/models"
        ;;

    stop)
        echo "停止全部服务..."
        $COMPOSE_CMD $VLM_ARGS down 2>/dev/null || true
        $COMPOSE_CMD -f compose-api.yaml --env-file .env down 2>/dev/null || true
        echo "已停止。"
        ;;

    status)
        echo "=== VLM Server ==="
        $COMPOSE_CMD $VLM_ARGS ps 2>/dev/null || echo "未运行"
        echo ""
        echo "=== API Server ==="
        $COMPOSE_CMD -f compose-api.yaml --env-file .env ps 2>/dev/null || echo "未运行"
        ;;

    logs)
        $COMPOSE_CMD $VLM_ARGS logs -f 2>/dev/null &
        $COMPOSE_CMD -f compose-api.yaml --env-file .env logs -f 2>/dev/null &
        wait
        ;;

    *)
        echo "MinerU 部署脚本"
        echo ""
        echo "使用：$0 {build|vlm|api|all|stop|status|logs}"
        echo ""
        echo "  build  - 构建环境镜像和代码镜像"
        echo "  vlm    - 启动 VLM Server（NPU/GPU 机器）"
        echo "  api    - 启动 API Server（支持三种 backend）"
        echo "  all    - 启动全部（同机部署）"
        echo "  stop   - 停止全部"
        echo "  status - 查看状态"
        echo "  logs   - 查看日志"
        exit 1
        ;;
esac
