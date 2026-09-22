#!/usr/bin/env bash
set -euo pipefail

script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_directory/../.." && pwd)
export PYTHONPATH="$repository_root${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -lt 4 || $# -gt 6 ]]; then
  echo "usage: $0 EXPERIMENT INITIAL_RESULTS OUTPUT SEED [STEPS] [SIM_WORKERS]" >&2
  exit 2
fi

experiment=$1
initial_results=$2
output=$3
seed=$4
steps=${5:-120}
simulation_workers=${6:-60}

model=${AGENTIC_MODEL:-qwen3:235b-a22b-thinking-2507-q4_K_M}
gpu_ids=${AGENTIC_GPU_IDS:-}
listen_address=${AGENTIC_OLLAMA_LISTEN:-127.0.0.1:11435}
context=${AGENTIC_CONTEXT:-65536}
task_timeout=${AGENTIC_TASK_TIMEOUT_SECONDS:-0}
expected_digest=${AGENTIC_MODEL_DIGEST:-}
model_timeout=${AGENTIC_MODEL_TIMEOUT_SECONDS:-1800}
agent_retries=${AGENTIC_AGENT_RETRIES:-2}
max_attempts=${AGENTIC_MAX_ATTEMPTS:-}

if [[ -z "$gpu_ids" ]]; then
  echo "AGENTIC_GPU_IDS must contain one to four explicit GPU UUIDs." >&2
  echo "Use 'nvidia-smi --query-gpu=uuid --format=csv,noheader' to list them." >&2
  exit 2
fi
if [[ ! "$expected_digest" =~ ^[0-9a-f]{64}$ ]]; then
  echo "AGENTIC_MODEL_DIGEST must be the exact 64-character lowercase digest." >&2
  exit 2
fi
if [[ ! "$gpu_ids" =~ ^GPU-[0-9A-Fa-f-]+(,GPU-[0-9A-Fa-f-]+){0,3}$ ]]; then
  echo "AGENTIC_GPU_IDS must be a canonical comma-separated GPU UUID list." >&2
  exit 2
fi

IFS=',' read -r -a selected_gpus <<< "$gpu_ids"
if (( ${#selected_gpus[@]} < 1 || ${#selected_gpus[@]} > 4 )); then
  echo "AGENTIC_GPU_IDS selected ${#selected_gpus[@]} GPUs; the allowed range is 1-4." >&2
  exit 2
fi
declare -A unique_gpus=()
for gpu in "${selected_gpus[@]}"; do
  if [[ -n "${unique_gpus[$gpu]:-}" ]]; then
    echo "AGENTIC_GPU_IDS contains a duplicate UUID: $gpu" >&2
    exit 2
  fi
  unique_gpus[$gpu]=1
done
if [[ "$model" == "qwen3:235b-a22b-thinking-2507-q4_K_M" ]] \
    && (( ${#selected_gpus[@]} != 4 )); then
  echo "The 142 GB production model requires four 48 GB A6000 GPUs." >&2
  exit 2
fi

for command in nvidia-smi ollama curl conda; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "required command is unavailable: $command" >&2
    exit 2
  fi
done
if ! available_gpus=$(nvidia-smi --query-gpu=uuid --format=csv,noheader); then
  echo "NVIDIA driver access failed; refusing to claim a GPU-backed run." >&2
  exit 2
fi
for gpu in "${selected_gpus[@]}"; do
  if ! grep -Fxq "$gpu" <<< "$available_gpus"; then
    echo "selected GPU UUID is not visible to nvidia-smi: $gpu" >&2
    exit 2
  fi
done

mkdir -p "$output"
export CUDA_VISIBLE_DEVICES=$gpu_ids
export OLLAMA_HOST=$listen_address
export OLLAMA_CONTEXT_LENGTH=$context
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_SCHED_SPREAD=1
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_NO_CLOUD=1
export OLLAMA_VULKAN=0
export GGML_VK_VISIBLE_DEVICES=-1

endpoint="http://$listen_address"
if curl -fsS "$endpoint/api/tags" >/dev/null 2>&1; then
  echo "An Ollama server already owns $listen_address; choose an unused" >&2
  echo "AGENTIC_OLLAMA_LISTEN so this launcher can enforce the four-GPU cap." >&2
  exit 2
fi

ollama serve >"$output/ollama-server.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" >/dev/null 2>&1 || true
  wait "$server_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

ready=0
for _ in {1..30}; do
  if curl -fsS "$endpoint/api/tags" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if (( ready == 0 )); then
  echo "Ollama did not become ready; see $output/ollama-server.log" >&2
  exit 2
fi

if ! ollama list | awk 'NR > 1 {print $1}' | grep -Fxq "$model"; then
  echo "Exact model is not installed: $model" >&2
  echo "Install it deliberately with: OLLAMA_HOST=$listen_address ollama pull $model" >&2
  exit 2
fi

run_preflight() {
  conda run --no-capture-output -n G4CMP python -m material_scan agentic-preflight \
    --ollama-host "$endpoint" \
    --ollama-model "$model" \
    --expected-digest "$expected_digest" \
    --context "$context" \
    --timeout "$model_timeout" \
    --warmup \
    --require-gpu
}

if ! run_preflight; then
  if [[ "$context" == "65536" ]]; then
    context=32768
    export OLLAMA_CONTEXT_LENGTH=$context
    echo "64K preflight failed; retrying once at a 32K context." >&2
    run_preflight
  else
    exit 2
  fi
fi

search_arguments=(
  "$experiment"
  --catalog "$repository_root/material_scan/parameters.yaml"
  --initial-results "$initial_results"
  --method agentic
  --seed "$seed"
  --steps "$steps"
  --workers "$simulation_workers"
  --task-timeout "$task_timeout"
  --ollama-host "$endpoint"
  --ollama-model "$model"
  --ollama-model-digest "$expected_digest"
  --agent-timeout "$model_timeout"
  --agent-context "$context"
  --agent-temperature 0
  --agent-retries "$agent_retries"
  --output "$output"
)
if [[ -n "$max_attempts" ]]; then
  search_arguments+=(--max-attempts "$max_attempts")
fi

conda run --no-capture-output -n G4CMP python -m material_scan search \
  "${search_arguments[@]}"
