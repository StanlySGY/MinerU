#!/bin/bash
# =============================================================================
# MinerU 多实例部署脚本
# =============================================================================
# 当前架构：Router + 1 个 API；VLM 是已经独立启动的外部服务。
#
# 使用方式：
#   ./start-multi.sh import-images # 导入 images/ 下的离线镜像
#   ./start-multi.sh check         # 检查配置、镜像和 VLM 连通性
#   ./start-multi.sh start         # 启动 Router + API
#   ./start-multi.sh stop          # 停止 Router + API，保留运维控制台
#   ./start-multi.sh stop-all      # 停止包括运维控制台在内的全部服务
#   ./start-multi.sh restart       # 重启 Router + API
#   ./start-multi.sh ops           # 单独启动运维控制台
#   ./start-multi.sh status        # 查看状态
#   ./start-multi.sh logs          # 查看日志
#   ./start-multi.sh test FILE.pdf # 依次测试 Pipeline 和 Hybrid
# =============================================================================

set -euo pipefail

COMPOSE_BASE_FILE="compose-multi.yaml"
ENV_FILE="env.multi"
COMPOSE_FILES=("$COMPOSE_BASE_FILE")
OPS_RUNTIME_DIR=".mineru-ops"
OPS_CONFIG_FILE="$OPS_RUNTIME_DIR/compose-config.yaml"
OPS_SOCKET_FILE="$OPS_RUNTIME_DIR/agent.sock"
OPS_PID_FILE="$OPS_RUNTIME_DIR/agent.pid"
OPS_LOG_FILE="$OPS_RUNTIME_DIR/agent.log"

CALLER_DIR=$(pwd -P)
cd "$(dirname "$0")"

# 兼容 docker compose v1 和 v2；没有 Compose 时给出明确错误。
if command -v docker >/dev/null 2>&1 && docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    echo "错误：找不到 Docker Compose，请安装 Docker Compose v2。" >&2
    exit 1
fi

compose() {
    compose_args=()
    for compose_file in "${COMPOSE_FILES[@]}"; do
        compose_args+=(-f "$compose_file")
    done
    "${COMPOSE_CMD[@]}" "${compose_args[@]}" --env-file "$ENV_FILE" "$@"
}

# 按设备模式决定要叠加的硬件映射文件；纯 CPU 主机不追加任何叠加文件。
resolve_compose_files() {
    COMPOSE_FILES=("$COMPOSE_BASE_FILE")
    case "$MINERU_DEVICE_MODE" in
        cpu)
            ;;
        npu)
            COMPOSE_FILES+=("compose-multi.npu.yaml")
            ;;
        cuda)
            COMPOSE_FILES+=("compose-multi.nvidia.yaml")
            ;;
        *)
            echo "错误：MINERU_DEVICE_MODE 只支持 cpu、npu 或 cuda，当前为 $MINERU_DEVICE_MODE。" >&2
            exit 1
            ;;
    esac
    for compose_file in "${COMPOSE_FILES[@]}"; do
        if [ ! -f "$compose_file" ]; then
            echo "错误：缺少 Compose 文件：$compose_file" >&2
            exit 1
        fi
    done
}

load_env_defaults() {
    if [ -f "$ENV_FILE" ]; then
        set -a
        # env.multi 是本目录提供的 shell 兼容配置文件。
        # shellcheck disable=SC1090
        . "./$ENV_FILE"
        set +a
    fi
    MINERU_ENV_IMAGE="${MINERU_ENV_IMAGE:-mineru-env:npu-v1.0}"
    MINERU_CODE_IMAGE="${MINERU_CODE_IMAGE:-mineru-code:v3.4.2}"
    MINERU_API_OUTPUT_ROOT="${MINERU_API_OUTPUT_ROOT:-/var/lib/mineru/output}"
    PIPELINE_MODEL_HOST_PATH="${PIPELINE_MODEL_HOST_PATH:-./models/PDF-Extract-Kit-1.0}"
    PIPELINE_CONFIG_HOST_PATH="${PIPELINE_CONFIG_HOST_PATH:-./mineru.pipeline.json}"
    MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-local}"
    MINERU_TOOLS_CONFIG_JSON="${MINERU_TOOLS_CONFIG_JSON:-/etc/mineru/mineru.json}"
    # 没有加速卡的 x86/ARM 主机保持 cpu，不需要任何 NPU/GPU 设备节点。
    MINERU_DEVICE_MODE="${MINERU_DEVICE_MODE:-cpu}"
    PIPELINE_NPU_ID="${PIPELINE_NPU_ID:-6}"
    VLM_1_IP="${VLM_1_IP:-}"
    VLM_1_PORT="${VLM_1_PORT:-30000}"
    MINERU_OPS_PORT="${MINERU_OPS_PORT:-19000}"
    MINERU_OPS_TEST_HOST_PATH="${MINERU_OPS_TEST_HOST_PATH:-./test-pdfs}"
    MINERU_OPS_AUTH_TOKEN="${MINERU_OPS_AUTH_TOKEN:-}"
    resolve_compose_files
}


load_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "错误：找不到 $ENV_FILE。请先执行：cp env.multi.example env.multi" >&2
        exit 1
    fi
    load_env_defaults
}

absolute_existing_path() {
    path="$1"
    case "$path" in
        /*) printf '%s\n' "$path" ;;
        *)
            path_dir=$(dirname "$path")
            path_name=$(basename "$path")
            printf '%s/%s\n' "$(cd "$path_dir" && pwd -P)" "$path_name"
            ;;
    esac
}

prepare_ops_paths() {
    mkdir -p "$OPS_RUNTIME_DIR" "$MINERU_OPS_TEST_HOST_PATH"
    touch "$OPS_CONFIG_FILE"
}

render_ops_config() {
    prepare_ops_paths
    temp_config="$OPS_CONFIG_FILE.tmp"
    compose config > "$temp_config"
    mv "$temp_config" "$OPS_CONFIG_FILE"
}

ops_agent_running() {
    if [ ! -f "$OPS_PID_FILE" ] || [ ! -S "$OPS_SOCKET_FILE" ]; then
        return 1
    fi
    ops_pid=$(cat "$OPS_PID_FILE" 2>/dev/null || true)
    case "$ops_pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$ops_pid" 2>/dev/null
}

start_ops_agent() {
    if ops_agent_running; then
        return
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "错误：运维控制代理需要宿主机安装 python3。" >&2
        exit 1
    fi
    prepare_ops_paths
    rm -f "$OPS_SOCKET_FILE" "$OPS_PID_FILE"
    agent_args=(
        python3 ./mineru-ops-agent.py
        --socket "$PWD/$OPS_SOCKET_FILE"
        --project-dir "$PWD"
        --env-file "$ENV_FILE"
    )
    for compose_file in "${COMPOSE_FILES[@]}"; do
        agent_args+=(--compose-file "$compose_file")
    done
    nohup "${agent_args[@]}" >> "$OPS_LOG_FILE" 2>&1 &
    ops_pid=$!
    echo "$ops_pid" > "$OPS_PID_FILE"
    for _ in $(seq 1 30); do
        if [ -S "$OPS_SOCKET_FILE" ]; then
            return
        fi
        if ! kill -0 "$ops_pid" 2>/dev/null; then
            break
        fi
        sleep 0.2
    done
    echo "错误：mineru-ops-agent 启动失败，日志：$PWD/$OPS_LOG_FILE" >&2
    tail -n 30 "$OPS_LOG_FILE" >&2 || true
    exit 1
}

stop_ops_agent() {
    if ! ops_agent_running; then
        rm -f "$OPS_PID_FILE" "$OPS_SOCKET_FILE"
        return
    fi
    ops_pid=$(cat "$OPS_PID_FILE")
    kill "$ops_pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        if ! kill -0 "$ops_pid" 2>/dev/null; then
            break
        fi
        sleep 0.2
    done
    rm -f "$OPS_PID_FILE" "$OPS_SOCKET_FILE"
}

business_services() {
    compose config --services | while IFS= read -r service; do
        case "$service" in
            mineru-ops|mineru-code-sync|'') ;;
            *) printf '%s\n' "$service" ;;
        esac
    done
}

prepare_ops() {
    render_ops_config
    if ops_agent_running; then
        stop_ops_agent
    fi
    start_ops_agent
}

check_pipeline_files() {
    if [ ! -d "$PIPELINE_MODEL_HOST_PATH" ]; then
        echo "错误：Pipeline 模型根目录不存在：$PIPELINE_MODEL_HOST_PATH" >&2
        echo "请把完整 PDF-Extract-Kit-1.0 放到该路径，或修改 env.multi。" >&2
        exit 1
    fi
    if [ ! -f "$PIPELINE_CONFIG_HOST_PATH" ]; then
        echo "错误：Pipeline 配置文件不存在：$PIPELINE_CONFIG_HOST_PATH" >&2
        echo "可直接使用本目录提供的 mineru.pipeline.json。" >&2
        exit 1
    fi

    missing=0
    for relative_path in \
        models/Layout/PP-DocLayoutV2 \
        models/MFR/unimernet_hf_small_2503 \
        models/MFR/pp_formulanet_plus_m \
        models/OCR/paddleocr_torch \
        models/TabRec/SlanetPlus/slanet-plus.onnx \
        models/TabRec/UnetStructure/unet.onnx \
        models/TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx
    do
        if [ ! -e "$PIPELINE_MODEL_HOST_PATH/$relative_path" ]; then
            echo "错误：缺少 Pipeline 模型：$relative_path" >&2
            missing=1
        elif [ -d "$PIPELINE_MODEL_HOST_PATH/$relative_path" ] && \
            [ -z "$(find -L "$PIPELINE_MODEL_HOST_PATH/$relative_path" -type f -print -quit 2>/dev/null)" ]; then
            echo "错误：Pipeline 模型目录为空：$relative_path" >&2
            missing=1
        fi
    done
    if [ "$missing" -ne 0 ]; then
        echo "请复制完整的 PDF-Extract-Kit-1.0，不要只复制单个权重文件。" >&2
        exit 1
    fi

    if [ "$MINERU_MODEL_SOURCE" != "local" ]; then
        echo "错误：离线部署必须设置 MINERU_MODEL_SOURCE=local，当前为 $MINERU_MODEL_SOURCE。" >&2
        exit 1
    fi
    if [ "$MINERU_TOOLS_CONFIG_JSON" != "/etc/mineru/mineru.json" ]; then
        echo "错误：MINERU_TOOLS_CONFIG_JSON 必须保持 /etc/mineru/mineru.json。" >&2
        exit 1
    fi
}

check_npu_host_paths() {
    if [ "$MINERU_DEVICE_MODE" != "npu" ]; then
        return
    fi

    for path in \
        "/dev/davinci${PIPELINE_NPU_ID}" \
        /dev/davinci_manager \
        /dev/devmm_svm \
        /dev/hisi_hdc \
        /usr/local/Ascend/driver \
        /usr/local/Ascend/add-ons
    do
        if [ ! -e "$path" ]; then
            echo "错误：NPU 手工映射所需路径不存在：$path" >&2
            echo "如果这台主机没有 Ascend NPU，请在 env.multi 中改为 MINERU_DEVICE_MODE=cpu。" >&2
            exit 1
        fi
    done
}

check_npu_image() {
    if [ "$MINERU_DEVICE_MODE" != "npu" ]; then
        return
    fi

    echo "从临时容器检查 torch_npu 和物理 NPU ${PIPELINE_NPU_ID}..."
    docker run --rm --entrypoint python \
        --device "/dev/davinci${PIPELINE_NPU_ID}:/dev/davinci${PIPELINE_NPU_ID}" \
        --device /dev/davinci_manager:/dev/davinci_manager \
        --device /dev/devmm_svm:/dev/devmm_svm \
        --device /dev/hisi_hdc:/dev/hisi_hdc \
        -v /usr/local/Ascend/driver:/usr/local/Ascend/driver:ro \
        -v /usr/local/Ascend/add-ons:/usr/local/Ascend/add-ons:ro \
        -e "ASCEND_RT_VISIBLE_DEVICES=${PIPELINE_NPU_ID}" \
        "$MINERU_ENV_IMAGE" -c \
        'import torch, torch_npu; assert torch_npu.npu.is_available(), "torch_npu 已安装，但 NPU 不可用"; assert torch_npu.npu.device_count() == 1, f"期望只看见 1 张 NPU，实际为 {torch_npu.npu.device_count()}"; value=torch.empty(1).npu(); print("torch:", torch.__version__); print("torch_npu:", torch_npu.__version__); print("device:", value.device)'
}

check() {
    load_env
    echo "设备模式：$MINERU_DEVICE_MODE；Compose 文件：${COMPOSE_FILES[*]}"
    check_pipeline_files
    check_npu_host_paths
    case "$MINERU_API_OUTPUT_ROOT" in
        /app|/app/*)
            echo "错误：MINERU_API_OUTPUT_ROOT 不能位于只读的 /app 目录，请使用 /var/lib/mineru/output。" >&2
            exit 1
            ;;
    esac
    case "$VLM_1_IP" in
        127.0.0.1|localhost)
            echo "错误：VLM_1_IP 不能填写 $VLM_1_IP；容器中的该地址指向 API 容器自身。" >&2
            echo "请让 VLM 监听 0.0.0.0，并填写 VLM 主机的实际 IP。" >&2
            exit 1
            ;;
    esac
    echo "检查 Compose 配置..."
    compose config >/dev/null
    if [ ! -f ./mineru-ops-agent.py ] || [ ! -f ./batch-router-diagnose.py ]; then
        echo "错误：缺少 mineru-ops-agent.py 或 batch-router-diagnose.py。" >&2
        exit 1
    fi
    prepare_ops_paths
    if [ -z "$MINERU_OPS_AUTH_TOKEN" ]; then
        echo "提示：MINERU_OPS_AUTH_TOKEN 未配置，状态页保持只读，服务控制和批量测试不可用。"
    fi
    echo "检查本地镜像..."
    docker image inspect "$MINERU_ENV_IMAGE" >/dev/null 2>&1 || {
        echo "错误：本地没有 $MINERU_ENV_IMAGE，请先导入 mineru-env-*.tar.gz。" >&2
        exit 1
    }
    docker image inspect "$MINERU_CODE_IMAGE" >/dev/null 2>&1 || {
        echo "错误：本地没有 $MINERU_CODE_IMAGE，请先导入 mineru-code-*.tar.gz，或修改 env.multi 标签。" >&2
        exit 1
    }
    check_npu_image
    pipeline_model_abs=$(absolute_existing_path "$PIPELINE_MODEL_HOST_PATH")
    pipeline_config_abs=$(absolute_existing_path "$PIPELINE_CONFIG_HOST_PATH")
    echo "从临时容器检查 Pipeline 配置和模型挂载..."
    docker run --rm --entrypoint python \
        -v "$pipeline_model_abs:/models/pipeline:ro" \
        -v "$pipeline_config_abs:/etc/mineru/mineru.json:ro" \
        "$MINERU_ENV_IMAGE" -c \
        'import json, os; p="/etc/mineru/mineru.json"; c=json.load(open(p, encoding="utf-8")); assert c.get("models-dir", {}).get("pipeline") == "/models/pipeline", "models-dir.pipeline 必须是 /models/pipeline"; required=["models/Layout/PP-DocLayoutV2", "models/MFR/unimernet_hf_small_2503", "models/MFR/pp_formulanet_plus_m", "models/OCR/paddleocr_torch", "models/TabRec/SlanetPlus/slanet-plus.onnx", "models/TabRec/UnetStructure/unet.onnx", "models/TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx"]; missing=[x for x in required if not os.path.exists(os.path.join("/models/pipeline", x))]; assert not missing, "容器内缺少模型（可能是外部符号链接）：" + ", ".join(missing)'
    if [ -z "$VLM_1_IP" ]; then
        echo "提示：env.multi 中没有填写 VLM_1_IP，跳过外部 VLM 检查。"
        echo "      此时只能使用 backend=pipeline；hybrid/vlm 系列后端不可用。"
    else
        echo "从临时容器检查 VLM：http://${VLM_1_IP}:${VLM_1_PORT}/v1/models"
        docker run --rm --entrypoint curl "$MINERU_ENV_IMAGE" \
            --fail --silent --show-error --connect-timeout 5 --max-time 15 \
            "http://${VLM_1_IP}:${VLM_1_PORT}/v1/models" >/dev/null
    fi
    echo "检查通过。"
}

import_images() {
    load_env_defaults
    archive_dir="${1:-./images}"
    if [ ! -d "$archive_dir" ]; then
        echo "错误：镜像归档目录不存在：$archive_dir" >&2
        echo "请把 mineru-env-*.tar.gz 和 mineru-code-*.tar.gz 放入该目录。" >&2
        exit 1
    fi

    env_archive_found=0
    code_archive_found=0
    for archive in "$archive_dir"/mineru-env-*.tar.gz "$archive_dir"/mineru-code-*.tar.gz; do
        if [ -f "$archive" ]; then
            case "$(basename "$archive")" in
                mineru-env-*) env_archive_found=1 ;;
                mineru-code-*) code_archive_found=1 ;;
            esac
            echo "导入 $(basename "$archive")..."
            gzip -dc "$archive" | docker load
        fi
    done
    if [ "$env_archive_found" -eq 0 ] || [ "$code_archive_found" -eq 0 ]; then
        echo "错误：$archive_dir 必须同时包含 mineru-env-*.tar.gz 和 mineru-code-*.tar.gz。" >&2
        exit 1
    fi
    if ! docker image inspect "$MINERU_ENV_IMAGE" >/dev/null 2>&1 || \
        ! docker image inspect "$MINERU_CODE_IMAGE" >/dev/null 2>&1; then
        echo "错误：归档已加载，但镜像标签与 env.multi 不一致。" >&2
        echo "期望标签：$MINERU_ENV_IMAGE，$MINERU_CODE_IMAGE" >&2
        exit 1
    fi
    echo "镜像导入完成：$MINERU_ENV_IMAGE，$MINERU_CODE_IMAGE"
}

case "${1:-help}" in
    check)
        check
        ;;

    import-images)
        import_images "${2:-./images}"
        ;;

    start)
        echo "启动服务（Router + API-1 + Ops；VLM 为外部服务）..."
        check
        prepare_ops
        compose up -d
        echo ""
        echo "等待服务就绪..."
        sleep 10
        echo ""
        compose ps
        echo ""
        HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        HOST_IP="${HOST_IP:-127.0.0.1}"
        ROUTER_PORT="${ROUTER_PORT:-8002}"
        API_1_PORT="${API_1_PORT:-18000}"
        MINERU_OPS_PORT="${MINERU_OPS_PORT:-19000}"
        echo "Router 统一入口: http://${HOST_IP}:${ROUTER_PORT}/docs"
        echo "运维控制台:   http://${HOST_IP}:${MINERU_OPS_PORT}"
        echo ""
        echo "API 服务地址："
        echo "  API-1: http://${HOST_IP}:${API_1_PORT}/docs"
        if [ -n "$VLM_1_IP" ]; then
            echo "VLM 地址：${VLM_1_IP}:${VLM_1_PORT}（由你单独部署）"
        else
            echo "VLM 地址：未配置（只能使用 backend=pipeline）"
        fi
        ;;

    stop)
        echo "停止 MinerU 业务服务，保留运维控制台..."
        load_env
        mapfile -t services_to_stop < <(business_services)
        if [ "${#services_to_stop[@]}" -gt 0 ]; then
            compose stop "${services_to_stop[@]}"
        fi
        echo "业务服务已停止；运维控制台仍可用于检查和重新启动。"
        ;;

    stop-all)
        echo "停止全部服务和运维控制代理..."
        load_env
        compose down
        stop_ops_agent
        echo "已全部停止。"
        ;;

    restart)
        echo "重启 MinerU 业务服务，保留运维控制台..."
        load_env
        prepare_ops
        mapfile -t services_to_restart < <(business_services)
        if [ "${#services_to_restart[@]}" -gt 0 ]; then
            compose restart "${services_to_restart[@]}"
        fi
        render_ops_config
        echo "业务服务已重启。"
        ;;

    ops)
        echo "启动或更新 MinerU 运维控制台..."
        load_env
        prepare_ops
        compose up -d --no-deps mineru-ops
        HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        HOST_IP="${HOST_IP:-127.0.0.1}"
        echo "运维控制台: http://${HOST_IP}:${MINERU_OPS_PORT}"
        ;;

    status)
        echo "=========================================="
        echo "MinerU API/Router/Ops 服务状态"
        echo "=========================================="
        load_env
        compose ps
        ;;

    logs)
        load_env
        compose logs -f
        ;;

    test)
        load_env
        test_pdf="${2:-}"
        case "$test_pdf" in
            /*) ;;
            *) test_pdf="$CALLER_DIR/$test_pdf" ;;
        esac
        if [ -z "$test_pdf" ] || [ ! -f "$test_pdf" ]; then
            echo "错误：请指定存在的测试 PDF，例如：$0 test /data/test.pdf" >&2
            exit 1
        fi
        ROUTER_PORT="${ROUTER_PORT:-8002}"
        echo "测试 Pipeline（第一次加载模型会较慢）..."
        curl --fail --show-error -X POST "http://127.0.0.1:${ROUTER_PORT}/file_parse" \
            -F "files=@${test_pdf}" -F "backend=pipeline" \
            -o "$CALLER_DIR/pipeline-test-result.json"
        echo "Pipeline 通过，结果：$CALLER_DIR/pipeline-test-result.json"
        if [ -z "$VLM_1_IP" ]; then
            echo "env.multi 中没有 VLM_1_IP，跳过 Hybrid HTTP Client 测试。"
            exit 0
        fi
        echo "测试 Hybrid HTTP Client（使用 env.multi 中的外部 VLM）..."
        curl --fail --show-error -X POST "http://127.0.0.1:${ROUTER_PORT}/file_parse" \
            -F "files=@${test_pdf}" -F "backend=hybrid-http-client" \
            -F "server_url=http://${VLM_1_IP}:${VLM_1_PORT}" \
            -o "$CALLER_DIR/hybrid-test-result.json"
        echo "Hybrid 通过，结果：$CALLER_DIR/hybrid-test-result.json"
        ;;

    help|-h|--help)
        echo "MinerU 单 VLM + 单 API + Router 部署脚本"
        echo ""
        echo "使用：$0 {import-images|check|start|stop|stop-all|restart|ops|status|logs|test}"
        echo ""
        echo "  check  - 检查配置、镜像和 VLM 连通性"
        echo "  import-images - 从 images/ 导入两个离线镜像归档"
        echo "  start  - 启动 Router + API-1 + Ops"
        echo "  stop   - 停止 Router + API-1，保留 Ops"
        echo "  stop-all - 停止包括 Ops 在内的全部服务"
        echo "  restart - 重启 Router + API-1，保留 Ops"
        echo "  ops    - 单独启动或更新 Ops"
        echo "  status - 查看服务状态"
        echo "  logs   - 查看服务日志"
        echo "  test   - 指定 PDF，依次测试 Pipeline 和 Hybrid"
        echo ""
        echo "部署步骤："
        echo "  1. $0 import-images"
        echo "  2. 解压 WSL 带来的 Pipeline 模型归档"
        echo "  3. cp env.multi.example env.multi 并编辑 VLM 地址"
        echo "  4. $0 check"
        echo "  5. $0 start"
        echo ""
        echo "设备模式（env.multi 中的 MINERU_DEVICE_MODE）："
        echo "  cpu  - x86/ARM 纯 CPU 主机，不映射任何加速卡（默认）"
        echo "  npu  - Ascend NPU 主机，叠加 compose-multi.npu.yaml"
        echo "  cuda - NVIDIA GPU 主机，叠加 compose-multi.nvidia.yaml"
        echo ""
        echo "架构："
        echo "  Pipeline: Router (:8002) → API-1 (:18000) → 本地 Pipeline 模型"
        echo "  Hybrid:   Router (:8002) → API-1 (:18000) → 本地模型 + 外部 VLM (:30000)"
        ;;

    *)
        echo "错误：未知命令 $1。执行 $0 help 查看用法。" >&2
        exit 1
        ;;
esac
