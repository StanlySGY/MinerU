#!/bin/bash
# =============================================================================
# MinerU Backend 性能测试脚本
# =============================================================================
# 测试 pipeline / vlm-http-client / hybrid-http-client 三种 backend
# 同时上传多个 PDF 文件，测量处理速度和时间
#
# 使用方式：
#   cd test-pdf
#   nohup bash benchmark.sh &
# =============================================================================

set -e

# ========== 配置 ==========
API_URL="${API_URL:-http://127.0.0.1:18000}"
PDF_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULT_FILE="${PDF_DIR}/benchmark_result.md"
CONCURRENT="${CONCURRENT:-3}"  # 同时请求数

# 支持的 backend
BACKENDS=("pipeline" "vlm-http-client" "hybrid-http-client")

# ========== 函数 ==========

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${RESULT_FILE}.log"
}

# 单次请求测试：上传一个 PDF，返回耗时（秒）
test_single() {
    local backend=$1
    local pdf_file=$2
    local filename=$(basename "$pdf_file")
    local start_time end_time duration status_code response

    start_time=$(date +%s%N)
    response=$(curl -s -w "\n%{http_code}" \
        -X POST "${API_URL}/file_parse" \
        -F "files=@${pdf_file}" \
        -F "backend=${backend}" \
        -F "return_md=true" \
        --max-time 1800)
    end_time=$(date +%s%N)

    status_code=$(echo "$response" | tail -1)
    response=$(echo "$response" | sed '$d')
    duration=$(( (end_time - start_time) / 1000000 ))

    echo "${backend}|${filename}|${status_code}|${duration}|$(date '+%Y-%m-%d %H:%M:%S')"
}

# 并发测试：同时上传多个 PDF
test_concurrent() {
    local backend=$1
    local pdf_files=("$@")
    local pids=()
    local results=()
    local tmp_dir=$(mktemp -d)

    log "开始测试 backend: ${backend}，并发数: ${#pdf_files[@]}"

    for pdf_file in "${pdf_files[@]}"; do
        (
            result=$(test_single "${backend}" "${pdf_file}")
            echo "${result}" > "${tmp_dir}/result_$(basename "${pdf_file}").txt"
        ) &
        pids+=($!)
    done

    for pid in "${pids[@]}"; do
        wait "$pid"
    done

    for result_file in "${tmp_dir}"/result_*.txt; do
        if [ -f "$result_file" ]; then
            cat "$result_file"
            echo ""
        fi
    done

    rm -rf "${tmp_dir}"
}

# 等待 API 就绪
wait_for_api() {
    local max_wait=60
    local waited=0
    log "等待 API 就绪..."
    while ! curl -s "${API_URL}/health" > /dev/null 2>&1; do
        sleep 2
        waited=$((waited + 2))
        if [ $waited -ge $max_wait ]; then
            log "错误：API 未就绪"
            exit 1
        fi
    done
    log "API 已就绪"
}

# ========== 主流程 ==========

# 清空结果文件
cat > "${RESULT_FILE}" << 'HEADER'
# MinerU Backend 性能测试结果

## 测试环境

| 项目 | 值 |
|---|---|
| 测试时间 | PLACEHOLDER_TIME |
| API 地址 | PLACEHOLDER_API |
| 并发数 | PLACEHOLDER_CONCURRENT |

## 测试文件

HEADER

sed -i "s/PLACEHOLDER_TIME/$(date '+%Y-%m-%d %H:%M:%S')/" "${RESULT_FILE}"
sed -i "s|PLACEHOLDER_API|${API_URL}|" "${RESULT_FILE}"
sed -i "s/PLACEHOLDER_CONCURRENT/${CONCURRENT}/" "${RESULT_FILE}"

# 列出测试文件
echo "| 文件名 | 大小 |" >> "${RESULT_FILE}"
echo "|---|---|" >> "${RESULT_FILE}"
for pdf in "${PDF_DIR}"/*.pdf; do
    if [ -f "$pdf" ]; then
        size=$(ls -lh "$pdf" | awk '{print $5}')
        echo "| $(basename "$pdf") | ${size} |" >> "${RESULT_FILE}"
    fi
done
echo "" >> "${RESULT_FILE}"

wait_for_api

# 收集所有 PDF 文件
pdf_files=()
for pdf in "${PDF_DIR}"/*.pdf; do
    if [ -f "$pdf" ]; then
        pdf_files+=("$pdf")
    fi
done

if [ ${#pdf_files[@]} -eq 0 ]; then
    log "错误：未找到 PDF 文件"
    exit 1
fi

log "找到 ${#pdf_files[@]} 个 PDF 文件"

# 逐个 backend 测试
for backend in "${BACKENDS[@]}"; do
    echo "" >> "${RESULT_FILE}"
    echo "## Backend: ${backend}" >> "${RESULT_FILE}"
    echo "" >> "${RESULT_FILE}"
    echo "| 文件名 | 状态码 | 耗时(ms) | 完成时间 |" >> "${RESULT_FILE}"
    echo "|---|---|---|---|" >> "${RESULT_FILE}"

    # 同时提交所有 PDF
    start_total=$(date +%s%N)
    results=$(test_concurrent "${backend}" "${pdf_files[@]}")
    end_total=$(date +%s%N)
    total_duration=$(( (end_total - start_total) / 1000000 ))

    # 写入结果
    echo "${results}" | while IFS='|' read -r b filename code duration time; do
        [ -z "$filename" ] && continue
        echo "| ${filename} | ${code} | ${duration} | ${time} |" >> "${RESULT_FILE}"
    done

    echo "" >> "${RESULT_FILE}"
    echo "**总耗时: ${total_duration}ms**" >> "${RESULT_FILE}"
    echo "" >> "${RESULT_FILE}"

    log "Backend ${backend} 测试完成，总耗时: ${total_duration}ms"

    # 等待 VLM 释放资源
    sleep 5
done

log "全部测试完成！结果保存在: ${RESULT_FILE}"
