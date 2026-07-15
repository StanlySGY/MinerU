#!/bin/bash
# =============================================================================
# MinerU 镜像构建与导出脚本
# =============================================================================
# 用于有网环境构建镜像，导出为 tar.gz 后拿到离线环境部署。
#
# 镜像分层：
#   1. mineru-env:v1.0  — 环境镜像（依赖包，不常变）
#   2. mineru-full:v1.0 — 代码镜像（MinerU 代码，常更新）
#
# 使用方式：
#   ./build.sh env              # 只构建环境镜像
#   ./build.sh code             # 构建代码镜像（依赖环境镜像）
#   ./build.sh all              # 构建全部
#   ./build.sh export           # 导出为 tar.gz
#   ./build.sh export-env       # 只导出环境镜像
#   ./build.sh export-code      # 只导出代码镜像
#   ./build.sh import <dir>     # 从 tar.gz 导入
# =============================================================================

set -e

cd "$(dirname "$0")"

IMAGE_ENV="mineru-env:v1.0"
IMAGE_CODE="mineru-full:v1.0"
EXPORT_DIR="./export"

case "${1:-help}" in
    env)
        echo "=========================================="
        echo "构建环境镜像: ${IMAGE_ENV}"
        echo "=========================================="
        DOCKER_BUILDKIT=0 docker build -t ${IMAGE_ENV} -f Dockerfile.env .
        echo "环境镜像构建完成！"
        docker images ${IMAGE_ENV}
        ;;

    code)
        echo "=========================================="
        echo "构建代码镜像: ${IMAGE_CODE}"
        echo "=========================================="
        # 检查环境镜像是否存在
        if ! docker image inspect ${IMAGE_ENV} &>/dev/null; then
            echo "环境镜像 ${IMAGE_ENV} 不存在，先构建环境镜像..."
            ./build.sh env
        fi
        DOCKER_BUILDKIT=0 docker build -t ${IMAGE_CODE} -f Dockerfile.code ..
        echo "代码镜像构建完成！"
        docker images ${IMAGE_CODE}
        ;;

    all)
        echo "=========================================="
        echo "构建全部镜像"
        echo "=========================================="
        ./build.sh env
        ./build.sh code
        echo ""
        echo "全部镜像构建完成！"
        docker images | grep -E "mineru-env|mineru-full"
        ;;

    export)
        echo "=========================================="
        echo "导出全部镜像为 tar.gz"
        echo "=========================================="
        mkdir -p ${EXPORT_DIR}

        echo "[1/2] 导出环境镜像..."
        docker save ${IMAGE_ENV} | gzip > ${EXPORT_DIR}/mineru-env-v1.0.tar.gz
        echo "  → ${EXPORT_DIR}/mineru-env-v1.0.tar.gz"

        echo "[2/2] 导出代码镜像..."
        docker save ${IMAGE_CODE} | gzip > ${EXPORT_DIR}/mineru-full-v1.0.tar.gz
        echo "  → ${EXPORT_DIR}/mineru-full-v1.0.tar.gz"

        echo ""
        echo "导出完成！文件位置："
        ls -lh ${EXPORT_DIR}/*.tar.gz
        echo ""
        echo "拷贝到离线机器后执行："
        echo "  ./build.sh import ${EXPORT_DIR}"
        ;;

    export-env)
        echo "导出环境镜像..."
        mkdir -p ${EXPORT_DIR}
        docker save ${IMAGE_ENV} | gzip > ${EXPORT_DIR}/mineru-env-v1.0.tar.gz
        echo "  → ${EXPORT_DIR}/mineru-env-v1.0.tar.gz"
        ls -lh ${EXPORT_DIR}/mineru-env-v1.0.tar.gz
        ;;

    export-code)
        echo "导出代码镜像..."
        mkdir -p ${EXPORT_DIR}
        docker save ${IMAGE_CODE} | gzip > ${EXPORT_DIR}/mineru-full-v1.0.tar.gz
        echo "  → ${EXPORT_DIR}/mineru-full-v1.0.tar.gz"
        ls -lh ${EXPORT_DIR}/mineru-full-v1.0.tar.gz
        ;;

    import)
        IMPORT_DIR="${2:-${EXPORT_DIR}}"
        echo "=========================================="
        echo "从 ${IMPORT_DIR} 导入镜像"
        echo "=========================================="

        if [ ! -d "${IMPORT_DIR}" ]; then
            echo "错误：目录 ${IMPORT_DIR} 不存在"
            exit 1
        fi

        for f in ${IMPORT_DIR}/mineru-*.tar.gz; do
            if [ -f "$f" ]; then
                echo "导入: $(basename $f)"
                gunzip -c "$f" | docker load
            fi
        done

        echo ""
        echo "导入完成！"
        docker images | grep -E "mineru-env|mineru-full"
        ;;

    *)
        echo "MinerU 镜像构建与导出脚本"
        echo ""
        echo "使用：$0 {env|code|all|export|export-env|export-code|import [dir]}"
        echo ""
        echo "构建命令："
        echo "  env          构建环境镜像（依赖包）"
        echo "  code         构建代码镜像（MinerU 代码）"
        echo "  all          构建全部镜像"
        echo ""
        echo "导出命令："
        echo "  export       导出全部镜像为 tar.gz"
        echo "  export-env   只导出环境镜像"
        echo "  export-code  只导出代码镜像（更新代码时用这个）"
        echo ""
        echo "导入命令："
        echo "  import [dir] 从 tar.gz 导入镜像（默认从 ./export 目录）"
        echo ""
        echo "离线部署流程："
        echo "  1. 在有网机器：$0 all && $0 export"
        echo "  2. 拷贝 export/ 目录到离线机器"
        echo "  3. 在离线机器：$0 import export/"
        echo ""
        echo "更新代码流程："
        echo "  1. 在有网机器：$0 code && $0 export-code"
        echo "  2. 拷贝 mineru-full-v1.0.tar.gz 到离线机器"
        echo "  3. 在离线机器：$0 import export/"
        exit 1
        ;;
esac
