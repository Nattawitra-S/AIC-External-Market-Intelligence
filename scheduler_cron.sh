#!/bin/bash
# =============================================================================
# scheduler_cron.sh — AIC SkillSelect ETL Monthly Cron Wrapper
# Install: crontab -e → add:
#   0 10 1 * * /Users/nattawitrasaengcha/Documents/Gov_ETL_data/scheduler_cron.sh
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
PYTHON="${PYTHON:-python3}"
LOG_DIR="${PROJECT_DIR}/logs"
TIMESTAMP=$(date +"%Y-%m-%d_%H%M%S")
LOG_FILE="${LOG_DIR}/etl_run_${TIMESTAMP}.log"
DATA_MONTH=$(date +"%Y-%m")

# Auto-find latest capture (notebook format: captures/ws_payload_*.json)
WS_PAYLOAD=$(ls "${PROJECT_DIR}/captures"/ws_payload_*.json 2>/dev/null | sort | tail -1 || \
             ls "${PROJECT_DIR}/captures"/skillselect_ws_payload_*.json 2>/dev/null | sort | tail -1 || \
             echo "")

# OSL and Visa lists — update paths to match raw_data structure
OSL_FILE="${PROJECT_DIR}/raw_data/jobs_and_skills_australia/Occupation Shortage List - 6 digit ANZSCO and OSCA.xlsx"
VISA_LIST="${PROJECT_DIR}/raw_data/home_affairs/skilled_occupation_lists.xlsx"
DB_FILE="${PROJECT_DIR}/data/aic_occupation_intelligence.db"
NOTIFY_EMAIL="s.nattawitra@gmail.com"
SEND_EMAIL=false

mkdir -p "${LOG_DIR}"
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

log "=========================================="
log "AIC SkillSelect ETL — ${DATA_MONTH}"
log "Project: ${PROJECT_DIR}"
log "=========================================="

[[ -f "${PROJECT_DIR}/.venv/bin/activate" ]] && source "${PROJECT_DIR}/.venv/bin/activate"

if [[ -z "${WS_PAYLOAD}" ]]; then
    log "❌ No WebSocket payload in captures/ — run AIC_SkillSelect_ETL.ipynb Cell 2 first"
    exit 1
fi

log "Payload: ${WS_PAYLOAD}"
cd "${PROJECT_DIR}"
"${PYTHON}" ETL/occupation_intelligence_etl.py \
    --ws-payload "${WS_PAYLOAD}" \
    --osl-file "${OSL_FILE}" \
    --visa-list "${VISA_LIST}" \
    --db "${DB_FILE}" \
    --month "${DATA_MONTH}" 2>&1 | tee -a "${LOG_FILE}"

ETL_EXIT=${PIPESTATUS[0]}
STATUS=$([[ ${ETL_EXIT} -eq 0 ]] && echo "SUCCESS" || echo "FAILED")
log "${STATUS} — exit ${ETL_EXIT}"

if [[ "${SEND_EMAIL}" == "true" ]]; then
    echo "AIC ETL ${STATUS} — ${DATA_MONTH}
$(tail -20 "${LOG_FILE}")" | mail -s "AIC ETL ${STATUS} ${DATA_MONTH}" "${NOTIFY_EMAIL}"
fi

exit ${ETL_EXIT}
