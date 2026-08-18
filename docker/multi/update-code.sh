#!/bin/bash
# Rebuild mineru-code with a new tag, update env.multi, and restart
# Router, API, and the Ops console so all three load the new code.
# Usage: ./update-code.sh [tag]   (tag defaults to a timestamp)
set -euo pipefail
cd "$(dirname "$0")"

CFG="env.multi"

if [ ! -f "$CFG" ]; then
  echo "ERROR: env.multi not found. Run: cp env.multi.example env.multi"
  exit 1
fi

TAG="${1:-$(date +%Y%m%d%H%M%S)}"
IMG="mineru-code:${TAG}"

echo "Step 1: building image $IMG"
(
  cd ../base
  MINERU_CODE_TAG="$TAG" ./build.sh code
)

echo ""
echo "Step 2: updating MINERU_CODE_IMAGE in env.multi"
if grep -q '^MINERU_CODE_IMAGE=' "$CFG"; then
  OLD=$(grep '^MINERU_CODE_IMAGE=' "$CFG" | head -n 1 | cut -d '=' -f 2-)
  sed -i "s|^MINERU_CODE_IMAGE=.*|MINERU_CODE_IMAGE=${IMG}|" "$CFG"
  echo "old image: ${OLD}"
else
  echo "MINERU_CODE_IMAGE=${IMG}" >> "$CFG"
fi
echo "new image: ${IMG}"

echo ""
echo "Step 3: restarting Router + API"
./start-multi.sh stop
./start-multi.sh start

echo ""
echo "Step 4: restarting the Ops console so it loads new code"
if docker ps --format '{{.Names}}' | grep -qx mineru-ops; then
  docker restart mineru-ops
  echo "mineru-ops restarted."
else
  echo "mineru-ops is not running, skipping restart (run ./start-multi.sh ops to start it)."
fi

echo ""
echo "Done. Code image is now: ${IMG}"
echo "Check status with: ./start-multi.sh status"
