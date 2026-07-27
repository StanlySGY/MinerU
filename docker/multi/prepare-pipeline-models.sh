#!/bin/bash
# Run on the networked x86_64 WSL machine. Do not run at the offline site.

set -euo pipefail

cd "$(dirname "$0")"

DOWNLOAD_SOURCE="${1:-modelscope}"
PACKAGE_VERSION="${MINERU_PIPELINE_PACKAGE_VERSION:-3.4.2}"
MODEL_DIR="${PIPELINE_DOWNLOAD_DIR:-./models/PDF-Extract-Kit-1.0}"
ARTIFACT_DIR="${PIPELINE_ARTIFACT_DIR:-./artifacts}"
ARCHIVE_NAME="mineru-pipeline-models-${PACKAGE_VERSION}.tar.gz"

case "$DOWNLOAD_SOURCE" in
    modelscope|huggingface) ;;
    *)
        echo "错误：下载源只能是 modelscope 或 huggingface。" >&2
        exit 1
        ;;
esac

if ! command -v uv >/dev/null 2>&1; then
    echo "错误：WSL 中找不到 uv。请先安装 uv，再重新执行本脚本。" >&2
    exit 1
fi

mkdir -p "$MODEL_DIR" "$ARTIFACT_DIR"
MODEL_DIR_ABS="$(cd "$MODEL_DIR" && pwd -P)"

echo "下载完整 Pipeline 模型到：$MODEL_DIR_ABS"
echo "下载源：$DOWNLOAD_SOURCE"

if [ "$DOWNLOAD_SOURCE" = "modelscope" ]; then
    uv run --isolated --no-project \
        --with "modelscope>=1.26.0" \
        python - "$MODEL_DIR_ABS" <<'PY'
import sys
from modelscope import snapshot_download

snapshot_download(
    "OpenDataLab/PDF-Extract-Kit-1.0",
    local_dir=sys.argv[1],
)
PY
else
    uv run --isolated --no-project \
        --with "huggingface-hub>=0.32.4" \
        python - "$MODEL_DIR_ABS" <<'PY'
import sys
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="opendatalab/PDF-Extract-Kit-1.0",
    local_dir=sys.argv[1],
)
PY
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
    if [ ! -e "$MODEL_DIR/$relative_path" ]; then
        echo "错误：下载结果缺少 $relative_path" >&2
        missing=1
    elif [ -d "$MODEL_DIR/$relative_path" ] && \
        [ -z "$(find -L "$MODEL_DIR/$relative_path" -type f -print -quit 2>/dev/null)" ]; then
        echo "错误：下载结果中的目录为空：$relative_path" >&2
        missing=1
    fi
done
if [ "$missing" -ne 0 ]; then
    exit 1
fi

ARCHIVE_PATH="$ARTIFACT_DIR/$ARCHIVE_NAME"
echo "打包模型：$ARCHIVE_PATH"
tar --dereference -czf "$ARCHIVE_PATH" -C "$MODEL_DIR" .

(
    cd "$ARTIFACT_DIR"
    sha256sum "$ARCHIVE_NAME" > "${ARCHIVE_NAME}.sha256"
    sha256sum -c "${ARCHIVE_NAME}.sha256"
)

echo ""
echo "WSL 侧准备完成，需要带去现场的模型文件："
echo "  $ARCHIVE_PATH"
echo "  ${ARCHIVE_PATH}.sha256"
du -sh "$MODEL_DIR" "$ARCHIVE_PATH"
