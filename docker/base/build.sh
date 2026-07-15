#!/bin/bash
# =============================================================================
# MinerU 镜像构建与导出脚本
# =============================================================================
# 镜像分层策略：
#   - mineru-env:v1.0  — 环境镜像（依赖包，~6GB，构建一次，传输一次）
#   - mineru-full:v1.0 — 环境 + 代码（本地构建，不导出）
#   - mineru-code.tar.gz — 纯代码包（~几MB，更新代码时传输）
#
# 离线部署流程：
#   首次：传输 mineru-env.tar.gz → 导入 → 在离线机器构建 mineru-full
#   更新：传输 mineru-code.tar.gz → 解压 → 重建 mineru-full
# =============================================================================

set -e

cd "$(dirname "$0")"

IMAGE_ENV="mineru-env:v1.0"
IMAGE_CODE="mineru-full:v1.0"
EXPORT_DIR="./export"

case "${1:-help}" in
    env)
        echo "构建环境镜像: ${IMAGE_ENV}"
        DOCKER_BUILDKIT=0 docker build -t ${IMAGE_ENV} -f Dockerfile.env .
        echo "完成！"
        docker images ${IMAGE_ENV}
        ;;

    code)
        echo "构建代码镜像: ${IMAGE_CODE}"
        if ! docker image inspect ${IMAGE_ENV} &>/dev/null; then
            echo "环境镜像不存在，先构建..."
            $0 env
        fi
        REPO_ROOT="$(cd ../.. && pwd)"
        DOCKER_BUILDKIT=0 docker build -t ${IMAGE_CODE} -f Dockerfile.code "${REPO_ROOT}"
        echo "完成！"
        docker images ${IMAGE_CODE}
        ;;

    all)
        $0 env
        $0 code
        echo ""
        docker images | grep -E "mineru-env|mineru-full"
        ;;

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    export)
        echo "导出环境镜像 + 代码包..."
        mkdir -p ${EXPORT_DIR}

        echo "[1/2] 导出环境镜像（~6GB，只传一次）..."
        docker save ${IMAGE_ENV} | gzip > ${EXPORT_DIR}/mineru-env-v1.0.tar.gz

        echo "[2/2] 导出代码包（~几MB，更新代码时传这个）..."
        REPO_ROOT="$(cd ../.. && pwd)"
        tar czf ${EXPORT_DIR}/mineru-code.tar.gz \
            -C "${REPO_ROOT}" \
            mineru/ pyproject.toml

        echo ""
        echo "导出完成："
        ls -lh ${EXPORT_DIR}/*.tar.gz
        ;;

    export-env)
        mkdir -p ${EXPORT_DIR}
        echo "导出环境镜像..."
        docker save ${IMAGE_ENV} | gzip > ${EXPORT_DIR}/mineru-env-v1.0.tar.gz
        ls -lh ${EXPORT_DIR}/mineru-env-v1.0.tar.gz
        ;;

    export-code)
        mkdir -p ${EXPORT_DIR}
        echo "导出代码包..."
        REPO_ROOT="$(cd ../.. && pwd)"
        tar czf ${EXPORT_DIR}/mineru-code.tar.gz \
            -C "${REPO_ROOT}" \
            mineru/ pyproject.toml
        ls -lh ${EXPORT_DIR}/mineru-code.tar.gz
        ;;

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------

    import)
        IMPORT_DIR="${2:-${EXPORT_DIR}}"
        echo "从 ${IMPORT_DIR} 导入..."

        if [ ! -d "${IMPORT_DIR}" ]; then
            echo "错误：${IMPORT_DIR} 不存在"
            exit 1
        fi

        # 导入环境镜像
        if [ -f "${IMPORT_DIR}/mineru-env-v1.0.tar.gz" ]; then
            echo "导入环境镜像..."
            gunzip -c "${IMPORT_DIR}/mineru-env-v1.0.tar.gz" | docker load
        fi

        echo "导入完成！"
        docker images | grep -E "mineru-env|mineru-full"
        ;;

    update)
        echo "=========================================="
        echo "离线机器更新代码"
        echo "=========================================="
        echo ""
        echo "步骤："
        echo "  1. 将 mineru-code.tar.gz 拷贝到离线机器"
        echo "  2. 在离线机器的 MinerU 仓库根目录执行："
        echo ""
        echo "     tar xzf mineru-code.tar.gz"
        echo "     cd docker/base"
        echo "     ./build.sh code"
        echo ""
        echo "这会用本地已有的 mineru-env 镜像重建 mineru-full。"
        ;;

    *)
        echo "MinerU 镜像构建与导出脚本"
        echo ""
        echo "构建："
        echo "  $0 env          构建环境镜像（依赖包，~6GB）"
        echo "  $0 code         构建代码镜像（环境+代码）"
        echo "  $0 all          构建全部"
        echo ""
        echo "导出："
        echo "  $0 export       导出环境镜像 + 代码包"
        echo "  $0 export-env   只导出环境镜像"
        echo "  $0 export-code  只导出代码包（更新代码用）"
        echo ""
        echo "导入："
        echo "  $0 import [dir] 导入环境镜像"
        echo "  $0 update       查看离线更新步骤"
        echo ""
        echo "====== 离线部署流程 ======"
        echo ""
        echo "【首次部署】在有网机器："
        echo "  $0 all && $0 export"
        echo ""
        echo "【首次部署】拷贝 export/ 到离线机器后："
        echo "  $0 import export/"
        echo "  $0 code  （在离线机器本地构建代码镜像）"
        echo ""
        echo "【更新代码】在有网机器："
        echo "  $0 export-code"
        echo ""
        echo "【更新代码】拷贝 mineru-code.tar.gz 到离线机器后："
        echo "  tar xzf mineru-code.tar.gz  （在仓库根目录）"
        echo "  $0 code  （在离线机器重建代码镜像）"
        exit 1
        ;;
esac
