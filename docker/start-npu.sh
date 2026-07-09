#!/bin/bash
# MinerU 910B NPU Server - Build and Start
# Usage: ./start-npu.sh [build|start|stop|restart|logs]

set -e

COMPOSE_FILE="compose-npu.yaml"
ENV_FILE="compose-npu.env"

cd "$(dirname "$0")"

case "${1:-start}" in
    build)
        echo "Building MinerU NPU image..."
        DOCKER_BUILDKIT=0 docker compose -f $COMPOSE_FILE --env-file $ENV_FILE --profile openai-server build
        echo "Build complete."
        ;;
    start)
        echo "Starting MinerU OpenAI Server..."
        docker compose -f $COMPOSE_FILE --env-file $ENV_FILE --profile openai-server up -d
        echo "Service started. API: http://$(hostname -I | awk '{print $1}'):30000/v1/models"
        ;;
    stop)
        echo "Stopping MinerU OpenAI Server..."
        docker compose -f $COMPOSE_FILE --env-file $ENV_FILE --profile openai-server down
        ;;
    restart)
        echo "Restarting MinerU OpenAI Server..."
        docker compose -f $COMPOSE_FILE --env-file $ENV_FILE --profile openai-server up -d --force-recreate
        ;;
    logs)
        docker compose -f $COMPOSE_FILE --env-file $ENV_FILE --profile openai-server logs -f
        ;;
    *)
        echo "Usage: $0 {build|start|stop|restart|logs}"
        exit 1
        ;;
esac
