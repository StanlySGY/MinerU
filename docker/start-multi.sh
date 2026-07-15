#!/bin/bash
# =============================================================================
# MinerU 多卡部署脚本
# =============================================================================
# 架构：Router + 多组 API+VLM（每张 NPU 卡一组）
#
# 使用方式：
#   ./start-multi.sh build         # 构建镜像
#   ./start-multi.sh start         # 启动全部（Router + API + VLM）
#   ./start-multi.sh stop          # 停止全部
#   ./start-multi.sh status        # 查看状态
#   ./start-multi.sh logs          # 查看日志
# =============================================================================

set -e

COMPOSE_FILE="compose-multi.yaml"
ENV_FILE="env.multi"

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
        echo "构建 MinerU 镜像..."
        DOCKER_BUILDKIT=0 docker build -t mineru-full:v1.0 -f Dockerfile.full .
        echo "构建完成！"
        ;;

    start)
        echo "启动全部服务（Router + API + VLM）..."
        $COMPOSE_CMD $COMMON_ARGS up -d
        echo ""
        echo "等待服务就绪..."
        sleep 60
        echo ""
        $COMPOSE_CMD $COMMON_ARGS ps
        echo ""
        echo "Router 统一入口: http://$(hostname -I | awk '{print $1}'):8002/docs"
        echo ""
        echo "各服务地址："
        echo "  API-1: http://$(hostname -I | awk '{print $1}'):18000/docs"
        echo "  API-2: http://$(hostname -I | awk '{print $1}'):18001/docs"
        echo "  API-3: http://$(hostname -I | awk '{print $1}'):18002/docs"
        echo "  VLM-1: http://$(hostname -I | awk '{print $1}'):30000/v1/models"
        echo "  VLM-2: http://$(hostname -I | awk '{print $1}'):30001/v1/models"
        echo "  VLM-3: http://$(hostname -I | awk '{print $1}'):30002/v1/models"
        ;;

    stop)
        echo "停止全部服务..."
        $COMPOSE_CMD $COMMON_ARGS down
        echo "已停止。"
        ;;

    status)
        echo "=========================================="
        echo "MinerU 多卡服务状态"
        echo "=========================================="
        $COMPOSE_CMD $COMMON_ARGS ps
        ;;

    logs)
        $COMPOSE_CMD $COMMON_ARGS logs -f
        ;;

    *)
        echo "MinerU 多卡部署脚本"
        echo ""
        echo "使用：$0 {build|start|stop|status|logs}"
        echo ""
        echo "  build  - 构建镜像"
        echo "  start  - 启动全部（Router + API + VLM）"
        echo "  stop   - 停止全部"
        echo "  status - 查看状态"
        echo "  logs   - 查看日志"
        echo ""
        echo "部署步骤："
        echo "  1. 复制配置文件：cp env.multi.example env.multi"
        echo "  2. 编辑 env.multi，配置端口和 IP"
        echo "  3. $0 build"
        echo "  4. $0 start"
        echo ""
        echo "架构："
        echo "  Router (:8002) → API-1+VLM-1 + API-2+VLM-2 + API-3+VLM-3"
        exit 1
        ;;
esac
