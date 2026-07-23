#!/bin/bash
# Build, export, and import the two images used by offline deployments.

set -eu

cd "$(dirname "$0")"

ENV_TAG="${MINERU_ENV_TAG:-v1.0}"
CODE_TAG="${MINERU_CODE_TAG:-v1.0}"
DEPENDENCY_VERSION="${MINERU_DEPENDENCY_VERSION:-3.4.2}"
IMAGE_ENV="mineru-env:${ENV_TAG}"
IMAGE_CODE="mineru-code:${CODE_TAG}"
EXPORT_DIR="${MINERU_EXPORT_DIR:-./export}"
REPO_ROOT="$(cd ../.. && pwd)"

require_image() {
    if ! docker image inspect "$1" >/dev/null 2>&1; then
        echo "错误：镜像 $1 不存在，请先构建。" >&2
        exit 1
    fi
}

export_image() {
    image="$1"
    output="$2"
    require_image "$image"
    mkdir -p "$EXPORT_DIR"
    echo "导出 $image -> $output"
    docker save "$image" | gzip > "$output"
    ls -lh "$output"
}

case "${1:-help}" in
    env)
        echo "构建环境镜像：${IMAGE_ENV}（MinerU 依赖基线 ${DEPENDENCY_VERSION}）"
        docker build \
            --build-arg "MINERU_DEPENDENCY_VERSION=${DEPENDENCY_VERSION}" \
            -t "$IMAGE_ENV" \
            -f Dockerfile.env \
            .
        ;;

    code)
        echo "构建代码镜像：${IMAGE_CODE}"
        code_context="$(mktemp -d "${TMPDIR:-/tmp}/mineru-code-build.XXXXXX")"
        trap 'rm -rf "$code_context"' EXIT
        cp -a "$REPO_ROOT/mineru" "$code_context/mineru"
        cp "$REPO_ROOT/pyproject.toml" "$code_context/pyproject.toml"
        cp Dockerfile.code "$code_context/Dockerfile"
        docker build -t "$IMAGE_CODE" "$code_context"
        ;;

    all)
        "$0" env
        "$0" code
        ;;

    export-env)
        export_image "$IMAGE_ENV" "$EXPORT_DIR/mineru-env-${ENV_TAG}.tar.gz"
        ;;

    export-code)
        export_image "$IMAGE_CODE" "$EXPORT_DIR/mineru-code-${CODE_TAG}.tar.gz"
        ;;

    export)
        "$0" export-env
        "$0" export-code
        ;;

    import)
        import_dir="${2:-$EXPORT_DIR}"
        if [ ! -d "$import_dir" ]; then
            echo "错误：目录 $import_dir 不存在。" >&2
            exit 1
        fi

        found=0
        for archive in "$import_dir"/mineru-env-*.tar.gz "$import_dir"/mineru-code-*.tar.gz; do
            if [ -f "$archive" ]; then
                found=1
                echo "导入 $(basename "$archive")"
                gzip -dc "$archive" | docker load
            fi
        done
        if [ "$found" -eq 0 ]; then
            echo "错误：$import_dir 中没有 MinerU 镜像归档。" >&2
            exit 1
        fi
        ;;

    update)
        cat <<EOF
离线代码更新：
  1. 有网机器执行：MINERU_CODE_TAG=<新版本> $0 code
  2. 有网机器执行：MINERU_CODE_TAG=<新版本> $0 export-code
  3. 离线机器导入 mineru-code-<新版本>.tar.gz
  4. 将部署目录 .env 中的 MINERU_CODE_IMAGE 改为 mineru-code:<新版本>
  5. 执行 docker compose --env-file .env up -d

只有 pyproject.toml 依赖发生变化时，才需要升级并重新传输 mineru-env。
EOF
        ;;

    *)
        cat <<EOF
用法：$0 {env|code|all|export-env|export-code|export|import [目录]|update}

环境变量：
  MINERU_ENV_TAG                 环境镜像标签，默认 v1.0
  MINERU_CODE_TAG                代码镜像标签，默认 v1.0
  MINERU_DEPENDENCY_VERSION      依赖解析使用的 MinerU 版本，默认 3.4.2
  MINERU_EXPORT_DIR              导出目录，默认 ./export
EOF
        exit 1
        ;;
esac
