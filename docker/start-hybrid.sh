#!/bin/bash
# =============================================================================
# MinerU Hybrid 模式启动脚本
# =============================================================================
# 使用方式：
#   ./start-hybrid.sh build          # 构建镜像
#   ./start-hybrid.sh start-api      # 启动 API 服务（含 Pipeline 模型）
#   ./start-hybrid.sh start-router   # 启动 Router 负载均衡
#   ./start-hybrid.sh start-all      # 启动全部服务
#   ./start-hybrid.sh stop           # 停止全部服务
#   ./start-hybrid.sh status         # 查看服务状态
#   ./start-hybrid.sh logs           # 查看日志
# =============================================================================

set -e

COMPOSE_FILE="compose-hybrid.yaml"
ENV_FILE="compose-hybrid.env"

cd "$(dirname "$0")"

# 兼容 docker compose v1 和 v2
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

COMMON_ARGS="-f $COMPOSE_FILE --env-file $ENV_FILE"

case "${1:-help}" in
    build)
        echo "=========================================="
        echo "构建 MinerU Hybrid 镜像..."
        echo "=========================================="
        DOCKER_BUILDKIT=0 $COMPOSE_CMD $COMMON_ARGS --profile api build --no-cache
        echo "构建完成。"
        ;;

    start-api)
        echo "=========================================="
        echo "启动 API 服务（含 Pipeline 模型）..."
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile api up -d
        echo ""
        echo "API 服务已启动。"
        sleep 5
        $COMPOSE_CMD $COMMON_ARGS --profile api ps
        echo ""
        echo "访问地址："
        echo "  API 1: http://$(hostname -I | awk '{print $1}'):18000/docs"
        echo "  API 2: http://$(hostname -I | awk '{print $1}'):18001/docs"
        echo "  API 3: http://$(hostname -I | awk '{print $1}'):18002/docs"
        ;;

    start-router)
        echo "=========================================="
        echo "启动 Router 负载均衡..."
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile router up -d
        echo ""
        echo "Router 已启动。"
        echo "统一入口：http://$(hostname -I | awk '{print $1}'):8002/docs"
        ;;

    start-all)
        echo "=========================================="
        echo "启动 MinerU Hybrid 全部服务..."
        echo "=========================================="
        echo ""
        echo "[1/2] 启动 API 服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile api up -d
        echo ""
        echo "[2/2] 等待 API 服务就绪（30秒）..."
        sleep 30
        echo ""
        $COMPOSE_CMD $COMMON_ARGS --profile router up -d
        echo ""
        echo "=========================================="
        echo "全部服务已启动！"
        echo "=========================================="
        echo ""
        $COMPOSE_CMD $COMMON_ARGS --profile api --profile router ps
        echo ""
        echo "统一入口：http://$(hostname -I | awk '{print $1}'):8002/docs"
        ;;

    stop)
        echo "停止全部服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile api --profile router down
        echo "全部服务已停止。"
        ;;

    status)
        echo "=========================================="
        echo "MinerU Hybrid 服务状态"
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile api --profile router ps
        ;;

    logs)
        $COMPOSE_CMD $COMMON_ARGS --profile api --profile router logs -f
        ;;

    logs-api)
        $COMPOSE_CMD $COMMON_ARGS --profile api logs -f
        ;;

    logs-router)
        $COMPOSE_CMD $COMMON_ARGS --profile router logs -f
        ;;

    *)
        echo "MinerU Hybrid 部署脚本"
        echo ""
        echo "使用方式：$0 {build|start-api|start-router|start-all|stop|status|logs|logs-api|logs-router}"
        echo ""
        echo "命令说明："
        echo "  build          - 构建 Hybrid 镜像（含 Pipeline 模型依赖）"
        echo "  start-api      - 启动 API 服务（本地跑 Pipeline + 远程调 VLM）"
        echo "  start-router   - 启动 Router 负载均衡"
        echo "  start-all      - 按顺序启动全部服务"
        echo "  stop           - 停止全部服务"
        echo "  status         - 查看服务状态"
        echo "  logs           - 查看全部日志"
        echo "  logs-api       - 仅查看 API 日志"
        echo "  logs-router    - 仅查看 Router 日志"
        echo ""
        echo "部署步骤："
        echo "  1. 在 NPU 机器上启动 VLM Server（用 compose-npu.yaml）"
        echo "  2. 编辑 compose-hybrid.env，填入 VLM 服务器 IP"
        echo "  3. $0 build"
        echo "  4. $0 start-all"
        exit 1
        ;;
esac
