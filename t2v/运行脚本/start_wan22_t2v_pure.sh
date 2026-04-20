#!/usr/bin/env bash
set -euo pipefail

# Pure image + 4-card parallel startup for T2V.
# Safe defaults:
# - only recreates target container when FORCE_RECREATE=1
# - does not touch other containers/processes

IMAGE="${IMAGE:-quay.io/ascend/vllm-omni:v0.18.0-local-20260411}"
MODEL_DIR="${MODEL_DIR:-/docker/models/Wan2.2-T2V-A14B-Diffusers}"
PORT="${PORT:-18191}"
NAME="${NAME:-ax-wan22-t2v-pure}"
FORCE_RECREATE="${FORCE_RECREATE:-0}"

if [ ! -d "${MODEL_DIR}" ]; then
  echo "ERROR: MODEL_DIR not found: ${MODEL_DIR}"
  exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -qx "${NAME}"; then
  if [ "${FORCE_RECREATE}" = "1" ]; then
    echo "[INFO] remove existing container: ${NAME}"
    docker rm -f "${NAME}" >/dev/null
    sleep 1
  else
    echo "ERROR: container already exists: ${NAME}"
    echo "Set FORCE_RECREATE=1 to recreate it."
    exit 1
  fi
fi

if ss -ltn | awk '{print $4}' | grep -q ":${PORT}$"; then
  echo "ERROR: port ${PORT} already in use"
  exit 1
fi

echo "[INFO] starting ${NAME} from ${IMAGE} on port ${PORT}"
docker run -d \
  --privileged \
  --name "${NAME}" \
  --shm-size=1g \
  --device /dev/davinci0 \
  --device /dev/davinci1 \
  --device /dev/davinci2 \
  --device /dev/davinci3 \
  --device /dev/davinci_manager \
  --device /dev/devmm_svm \
  --device /dev/hisi_hdc \
  --network=host \
  -v /usr/local/dcmi:/usr/local/dcmi \
  -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
  -v /usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64 \
  -v /usr/local/Ascend/driver/version.info:/usr/local/Ascend/driver/version.info \
  -v /etc/ascend_install.info:/etc/ascend_install.info \
  -v "${MODEL_DIR}:/model" \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn \
  -e ASCEND_RT_VISIBLE_DEVICES=0,1,2,3 \
  -e NPU_VISIBLE_DEVICES=0,1,2,3 \
  "${IMAGE}" \
  bash -lc "cd /vllm-workspace/vllm-omni && \
    vllm serve /model --omni \
      --port ${PORT} \
      --tensor-parallel-size 1 \
      --ulysses-degree 4 \
      --cfg-parallel-size 1 \
      --vae-patch-parallel-size 4 \
      --use-hsdp \
      --vae-use-slicing \
      --vae-use-tiling"

echo "[INFO] container started. check logs with:"
echo "  docker logs -f ${NAME}"
echo "[INFO] health check command:"
echo "  curl -sS http://127.0.0.1:${PORT}/health"

