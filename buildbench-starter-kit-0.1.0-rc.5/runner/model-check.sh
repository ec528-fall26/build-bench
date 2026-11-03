#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
if [[ $# -gt 0 ]]; then
  if [[ $# -eq 1 && "$1" == --help ]]; then
    printf '%s\n' 'Usage: ./bb model-check' 'Offline synthetic SDK/example self-check; no real model access or target build.'
    exit 0
  fi
  fail 'Usage: ./bb model-check' 2
fi
have_command docker || fail 'Docker is required.' 3
ensure_image "$BB_AGENT_IMAGE"
image="$("$ROOT/runner/prepare-agent-image.sh" "$ROOT/agents/model-hello")"
printf '%s\n' 'Synthetic model self-check: no provider calls, no build feedback, no hosted qualification.'
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --cpus 1 --memory 1g --pids-limit 128 \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m -v "$ROOT:/kit:ro" \
  -w /kit "$image" python /kit/model-access/check_example.py
