#!/bin/bash
# MinerU ARM CPU Server - Build and Start
# Usage: ./start-cpu.sh [build|start|stop|restart|logs]

set -e

COMPOSE_FILE="compose-cpu.yaml"
ENV_FILE="compose-cpu.env"

cd "$(dirname "$0")"

# 兼容 docker compose v1 和 v2
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

case "${1:-start}" in
    build)
        echo "Building MinerU CPU image..."
        DOCKER_BUILDKIT=0 $COMPOSE_CMD -f $COMPOSE_FILE --env-file $ENV_FILE --profile api build --no-cache
        echo "Build complete."
        ;;
    start)
        echo "Starting MinerU API service..."
        $COMPOSE_CMD -f $COMPOSE_FILE --env-file $ENV_FILE --profile api up -d
        echo "Service started. API: http://$(hostname -I | awk '{print $1}'):18000/docs"
        ;;
    stop)
        echo "Stopping MinerU API service..."
        $COMPOSE_CMD -f $COMPOSE_FILE --env-file $ENV_FILE --profile api down
        ;;
    restart)
        echo "Restarting MinerU API service..."
        $COMPOSE_CMD -f $COMPOSE_FILE --env-file $ENV_FILE --profile api up -d --force-recreate
        ;;
    logs)
        $COMPOSE_CMD -f $COMPOSE_FILE --env-file $ENV_FILE --profile api logs -f
        ;;
    *)
        echo "Usage: $0 {build|start|stop|restart|logs}"
        exit 1
        ;;
esac
