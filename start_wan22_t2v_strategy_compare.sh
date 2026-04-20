#!/usr/bin/env bash
set -euo pipefail

# Start T2V service with ingress scheduling strategy (DRR) enabled.
# This script does NOT modify the pure image on host. It only starts a
# container from the strategy-enabled image for comparison runs.

IMAGE="${IMAGE:-aixuan/vllm-omni:t2v-drr-budgeted-20260414}"
MODEL_DIR="${MODEL_DIR:-/docker/models/Wan2.2-T2V-A14B-Diffusers}"
PORT="${PORT:-18191}"
NAME="${NAME:-ax-wan22-t2v-strategy}"
FORCE_RECREATE="${FORCE_RECREATE:-1}"

# Strategy defaults (can be overridden from shell before running this script).
OMNI_VIDEO_INGRESS_BATCH_ENABLE="${OMNI_VIDEO_INGRESS_BATCH_ENABLE:-1}"
OMNI_VIDEO_INGRESS_DEFAULT_BS="${OMNI_VIDEO_INGRESS_DEFAULT_BS:-1}"
OMNI_VIDEO_INGRESS_MAX_WAIT_MS="${OMNI_VIDEO_INGRESS_MAX_WAIT_MS:-0}"
OMNI_VIDEO_INGRESS_STRICT_BATCHING="${OMNI_VIDEO_INGRESS_STRICT_BATCHING:-0}"
OMNI_VIDEO_INGRESS_Q_BASE="${OMNI_VIDEO_INGRESS_Q_BASE:-12}"
OMNI_VIDEO_INGRESS_AGE_THRESHOLD_MS="${OMNI_VIDEO_INGRESS_AGE_THRESHOLD_MS:-1200}"
OMNI_VIDEO_INGRESS_AGE_BONUS_FACTOR="${OMNI_VIDEO_INGRESS_AGE_BONUS_FACTOR:-1.0}"
OMNI_VIDEO_DRR_MAX_QUEUES="${OMNI_VIDEO_DRR_MAX_QUEUES:-3}"
OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES="${OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES:-{\"854x480_3\":6,\"854x480_4\":4,\"1280x720_6\":2}}"

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

echo "[INFO] launching strategy container"
echo "  IMAGE=${IMAGE}"
echo "  NAME=${NAME}"
echo "  PORT=${PORT}"
echo "  OMNI_VIDEO_DRR_MAX_QUEUES=${OMNI_VIDEO_DRR_MAX_QUEUES}"
echo "  OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES=${OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES}"

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
  -e OMNI_VIDEO_INGRESS_BATCH_ENABLE="${OMNI_VIDEO_INGRESS_BATCH_ENABLE}" \
  -e OMNI_VIDEO_INGRESS_DEFAULT_BS="${OMNI_VIDEO_INGRESS_DEFAULT_BS}" \
  -e OMNI_VIDEO_INGRESS_MAX_WAIT_MS="${OMNI_VIDEO_INGRESS_MAX_WAIT_MS}" \
  -e OMNI_VIDEO_INGRESS_STRICT_BATCHING="${OMNI_VIDEO_INGRESS_STRICT_BATCHING}" \
  -e OMNI_VIDEO_INGRESS_Q_BASE="${OMNI_VIDEO_INGRESS_Q_BASE}" \
  -e OMNI_VIDEO_INGRESS_AGE_THRESHOLD_MS="${OMNI_VIDEO_INGRESS_AGE_THRESHOLD_MS}" \
  -e OMNI_VIDEO_INGRESS_AGE_BONUS_FACTOR="${OMNI_VIDEO_INGRESS_AGE_BONUS_FACTOR}" \
  -e OMNI_VIDEO_DRR_MAX_QUEUES="${OMNI_VIDEO_DRR_MAX_QUEUES}" \
  -e OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES="${OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES}" \
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

