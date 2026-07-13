#!/bin/bash
# =============================================================================
# MinerU 集群部署启动脚本
# =============================================================================
# 使用方式：
#   ./start-cluster.sh build          # 构建所有镜像
#   ./start-cluster.sh start-vlm      # 启动 VLM 推理服务（NPU 卡）
#   ./start-cluster.sh start-api      # 启动 API 代理服务
#   ./start-cluster.sh start-router   # 启动 Router 负载均衡
#   ./start-cluster.sh start-all      # 启动全部服务
#   ./start-cluster.sh stop           # 停止全部服务
#   ./start-cluster.sh status         # 查看服务状态
#   ./start-cluster.sh logs           # 查看所有日志
#   ./start-cluster.sh logs-vlm       # 查看 VLM 日志
#   ./start-cluster.sh logs-api       # 查看 API 日志
# =============================================================================

set -e

COMPOSE_FILE="compose-cluster.yaml"
ENV_FILE="compose-cluster.env"

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
        echo "构建 MinerU 集群镜像..."
        echo "=========================================="
        DOCKER_BUILDKIT=0 $COMPOSE_CMD $COMMON_ARGS --profile vlm build --no-cache
        DOCKER_BUILDKIT=0 $COMPOSE_CMD $COMMON_ARGS --profile api build --no-cache
        echo "构建完成。"
        ;;

    start-vlm)
        echo "=========================================="
        echo "启动 VLM 推理服务（NPU）..."
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile vlm up -d
        echo ""
        echo "VLM 服务已启动。健康检查中..."
        sleep 5
        $COMPOSE_CMD $COMMON_ARGS --profile vlm ps
        echo ""
        echo "访问地址："
        echo "  VLM Server 1: http://$(hostname -I | awk '{print $1}'):30000/v1/models"
        echo "  VLM Server 2: http://$(hostname -I | awk '{print $1}'):30001/v1/models"
        echo "  VLM Server 3: http://$(hostname -I | awk '{print $1}'):30002/v1/models"
        ;;

    start-api)
        echo "=========================================="
        echo "启动 API 代理服务..."
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile api up -d
        echo ""
        echo "API 服务已启动。健康检查中..."
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
        echo "启动 MinerU 全部集群服务..."
        echo "=========================================="
        echo ""
        echo "[1/3] 启动 VLM 推理服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile vlm up -d
        echo ""
        echo "[2/3] 等待 VLM 服务就绪（30秒）..."
        sleep 30
        echo ""
        echo "[3/3] 启动 API 和 Router 服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile api --profile router up -d
        echo ""
        echo "=========================================="
        echo "全部服务已启动！"
        echo "=========================================="
        echo ""
        $COMPOSE_CMD $COMMON_ARGS --profile vlm --profile api --profile router ps
        echo ""
        echo "统一入口：http://$(hostname -I | awk '{print $1}'):8002/docs"
        echo "或分别访问各个 API 实例。"
        ;;

    stop)
        echo "停止全部服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile vlm --profile api --profile router down
        echo "全部服务已停止。"
        ;;

    status)
        echo "=========================================="
        echo "MinerU 集群服务状态"
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile vlm --profile api --profile router ps
        ;;

    logs)
        $COMPOSE_CMD $COMMON_ARGS --profile vlm --profile api --profile router logs -f
        ;;

    logs-vlm)
        $COMPOSE_CMD $COMMON_ARGS --profile vlm logs -f
        ;;

    logs-api)
        $COMPOSE_CMD $COMMON_ARGS --profile api logs -f
        ;;

    logs-router)
        $COMPOSE_CMD $COMMON_ARGS --profile router logs -f
        ;;

    *)
        echo "MinerU 集群部署脚本"
        echo ""
        echo "使用方式：$0 {build|start-vlm|start-api|start-router|start-all|stop|status|logs|logs-vlm|logs-api|logs-router}"
        echo ""
        echo "命令说明："
        echo "  build          - 构建所有 Docker 镜像"
        echo "  start-vlm      - 启动 VLM 推理服务（需要 NPU 卡）"
        echo "  start-api      - 启动 API 代理服务（CPU）"
        echo "  start-router   - 启动 Router 负载均衡"
        echo "  start-all      - 按顺序启动全部服务"
        echo "  stop           - 停止全部服务"
        echo "  status         - 查看服务状态"
        echo "  logs           - 查看全部日志"
        echo "  logs-vlm       - 仅查看 VLM 日志"
        echo "  logs-api       - 仅查看 API 日志"
        echo "  logs-router    - 仅查看 Router 日志"
        echo ""
        echo "部署步骤："
        echo "  1. 编辑 compose-cluster.env，填写实际的服务器 IP"
        echo "  2. $0 build"
        echo "  3. $0 start-all"
        exit 1
        ;;
esac
