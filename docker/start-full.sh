#!/bin/bash
# =============================================================================
# MinerU 全功能部署脚本
# =============================================================================
# 支持：pipeline / vlm-http-client / hybrid-http-client
#
# 使用方式：
#   ./start-full.sh build       # 构建全功能镜像
#   ./start-full.sh start       # 启动服务
#   ./start-full.sh stop        # 停止服务
#   ./start-full.sh restart     # 重启服务
#   ./start-full.sh status      # 查看状态
#   ./start-full.sh logs        # 查看日志
# =============================================================================

set -e

COMPOSE_FILE="compose-full.yaml"
ENV_FILE="compose-full.env"

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
        echo "构建 MinerU 全功能镜像..."
        echo "=========================================="
        echo ""
        echo "[1/2] 基于 mineru-server:v1.0 添加 Pipeline 依赖..."
        DOCKER_BUILDKIT=0 docker build -t mineru-full:v1.0 -f Dockerfile.full .
        echo ""
        echo "[2/2] 构建完成！"
        echo ""
        echo "支持的 backend："
        echo "  ✅ pipeline"
        echo "  ✅ vlm-http-client"
        echo "  ✅ hybrid-http-client"
        ;;

    start)
        echo "=========================================="
        echo "启动 MinerU 全功能服务..."
        echo "=========================================="

        # 检查 mineru-server:v1.0 镜像是否存在
        if ! docker image inspect mineru-server:v1.0 &>/dev/null; then
            echo "错误：找不到 mineru-server:v1.0 镜像"
            echo "请先构建基础镜像："
            echo "  docker build -t mineru-server:v1.0 -f china/Dockerfile ."
            exit 1
        fi

        # 检查 mineru-full:v1.0 镜像是否存在
        if ! docker image inspect mineru-full:v1.0 &>/dev/null; then
            echo "mineru-full:v1.0 镜像不存在，开始构建..."
            ./start-full.sh build
        fi

        echo ""
        echo "[1/2] 启动 VLM Server..."
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server up -d
        echo ""
        echo "[2/2] 等待 VLM Server 就绪（60秒）..."
        sleep 60
        echo ""
        echo "启动 API Server..."
        $COMPOSE_CMD $COMMON_ARGS --profile api up -d
        echo ""
        echo "=========================================="
        echo "服务已启动！"
        echo "=========================================="
        echo ""
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server --profile api ps
        echo ""
        echo "访问地址："
        echo "  API 文档: http://$(hostname -I | awk '{print $1}'):18000/docs"
        echo "  VLM 模型: http://$(hostname -I | awk '{print $1}'):30000/v1/models"
        echo ""
        echo "支持的 backend："
        echo "  pipeline:           -F 'backend=pipeline'"
        echo "  vlm-http-client:    -F 'backend=vlm-http-client'"
        echo "  hybrid-http-client: -F 'backend=hybrid-http-client'"
        ;;

    stop)
        echo "停止全部服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server --profile api down
        echo "全部服务已停止。"
        ;;

    restart)
        echo "重启全部服务..."
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server --profile api down
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server --profile api up -d
        echo "全部服务已重启。"
        ;;

    status)
        echo "=========================================="
        echo "MinerU 全功能服务状态"
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server --profile api ps
        ;;

    logs)
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server --profile api logs -f
        ;;

    logs-api)
        $COMPOSE_CMD $COMMON_ARGS --profile api logs -f
        ;;

    logs-vlm)
        $COMPOSE_CMD $COMMON_ARGS --profile openai-server logs -f
        ;;

    *)
        echo "MinerU 全功能部署脚本"
        echo ""
        echo "使用方式：$0 {build|start|stop|restart|status|logs|logs-api|logs-vlm}"
        echo ""
        echo "命令说明："
        echo "  build      - 构建全功能镜像（在 mineru-server:v1.0 基础上添加 Pipeline 依赖）"
        echo "  start      - 启动 VLM Server + API Server"
        echo "  stop       - 停止全部服务"
        echo "  restart    - 重启全部服务"
        echo "  status     - 查看服务状态"
        echo "  logs       - 查看全部日志"
        echo "  logs-api   - 仅查看 API 日志"
        echo "  logs-vlm   - 仅查看 VLM Server 日志"
        echo ""
        echo "部署步骤："
        echo "  1. 确保 mineru-server:v1.0 镜像已构建"
        echo "  2. 编辑 compose-full.env，配置 VLM 服务器 IP"
        echo "  3. $0 build"
        echo "  4. $0 start"
        echo ""
        echo "支持的 backend："
        echo "  ✅ pipeline            - 纯本地小模型"
        echo "  ✅ vlm-http-client     - 远程 VLM"
        echo "  ✅ hybrid-http-client  - 本地小模型 + 远程 VLM"
        exit 1
        ;;
esac
