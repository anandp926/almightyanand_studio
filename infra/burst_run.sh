#!/usr/bin/env bash
# ============================================================================
# burst_run.sh — One daily run for a VM that is only on for the batch.
#
# Starts the gateway services, runs analytics + batch + scheduled uploads
# (python -m agents.orchestrator --burst), then powers the VM off. YouTube
# publishes the scheduled Shorts at 12:30 / 18:30 / 21:00 IST by itself.
#
# Install on the VM (see infra/burst_mode.md):
#   crontab -e   →   @reboot /home/<user>/almightyanand_studio/infra/burst_run.sh
#
# BURST_SHUTDOWN=false keeps the VM on afterwards (for debugging).
# ============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/burst_$(date +%Y%m%d_%H%M%S).log"
cd "${REPO_DIR}"

{
  echo "== Burst run started $(date -Is) =="

  # The always-on daemon must not run alongside burst mode: both would batch.
  docker compose stop orchestrator >/dev/null 2>&1 || true
  docker compose rm -f orchestrator >/dev/null 2>&1 || true

  docker compose up -d --build redis freellm omniroute

  docker compose run --rm orchestrator python -m agents.orchestrator --burst
  STATUS=$?
  echo "== Burst run finished with status ${STATUS} at $(date -Is) =="

  docker compose stop
} >> "${LOG_FILE}" 2>&1

# Keep only the last 30 logs
ls -1t "${LOG_DIR}"/burst_*.log 2>/dev/null | tail -n +31 | xargs -r rm -f

if [ "${BURST_SHUTDOWN:-true}" = "true" ]; then
  sudo shutdown -h +1 "Burst run complete"
fi
