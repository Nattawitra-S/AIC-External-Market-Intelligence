# AIC SkillSelect ETL — Runbook
**Owner:** Mild (Nattawitra Saengcha) @ AIC  
**Last updated:** 2026-07-09  
**Frequency:** Monthly (1st of each month)

> ⚠️ **Scope note (2026-07-14):** This runbook covers only the SkillSelect
> capture → SQLite prototype pipeline (`AIC_SkillSelect_ETL.ipynb`,
> `ETL/occupation_intelligence_etl.py`), preserved unchanged and separate
> from the production MySQL pipeline. For the current 7-source MySQL
> pipeline (RBA, Home Affairs, CRICOS, JSA, ABS, Skilled Migration,
> Education) — its run commands, validators, and Tableau connection — see
> `docs/final_migration_summary.md`, `ETL/run_all.py`, and
> `ETL/deploy_and_validate.py`.

---

## Overview

This runbook covers the monthly data refresh process for the AIC Occupation Intelligence database. The process has two parts:

1. **Manual capture** — run a browser script to collect SkillSelect WebSocket data (~10 min)
2. **Automated ETL** — script transforms and loads data into SQLite (~2 min, runs via cron)

---

## Prerequisites

| Requirement | Check |
|-------------|-------|
| Python 3.10+ | `python3 --version` |
| venv activated | `source .venv/bin/activate` |
| Playwright installed | `playwright install chromium` |
| Internet access to immi.homeaffairs.gov.au | Open in browser to confirm |

---

## Monthly Process

### Step 1 — Capture SkillSelect Data (~10 min, manual)

> Run on the 1st of each month, after new invitation round data is published (usually released by the 5th business day).

**Recommended: Use Jupyter Notebook**
```bash
cd ~/Documents/Gov_ETL_data
jupyter notebook AIC_SkillSelect_ETL.ipynb
# Run Cell 2 — browser opens, interact with all filters, then interrupt
```

**Or command line:**
```bash
cd ~/Documents/Gov_ETL_data
source .venv/bin/activate
python capture_skillselect_ws.py
```

**In the browser that opens:**
1. Go to: `https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect/invitation-rounds`
2. Wait for Qlik charts to fully load
3. Use all filters (visa subclass, occupation, state) to trigger data loads
4. Let each view load completely
5. When done → press **Ctrl+C** in terminal (or interrupt Jupyter cell)

**Output saved to `captures/`:**
- `ws_payload_YYYYMMDD_HHMMSS.json` ← main data
- `network_log_YYYYMMDD_HHMMSS.json`

---

### Step 2 — Run ETL Pipeline (~2 min)

**Via Jupyter (recommended):**
```
Run Cells 3 → 4 → 4b → 5 in AIC_SkillSelect_ETL.ipynb
```

**Via command line:**
```bash
cd ~/Documents/Gov_ETL_data
source .venv/bin/activate

WS_FILE=$(ls captures/ws_payload_*.json | sort | tail -1)
python ETL/occupation_intelligence_etl.py \
    --ws-payload "$WS_FILE" \
    --osl-file   "raw_data/jobs_and_skills_australia/Occupation Shortage List - 6 digit ANZSCO and OSCA.xlsx" \
    --db         data/aic_occupation_intelligence.db
```

---

### Step 3 — Verify

```bash
python ETL/verify_db.py
```

---

### Step 4 — Refresh Tableau

Data → Refresh All Extracts

---

## Automated Monthly Run (cron)

```bash
chmod +x scheduler_cron.sh

# Install cron job (runs 1st of each month at 10am):
(crontab -l; echo "0 10 1 * * /Users/nattawitrasaengcha/Documents/Gov_ETL_data/scheduler_cron.sh") | crontab -

# Verify:
crontab -l | grep Gov_ETL_data
```

> Note: Cron only runs ETL (Step 2+). Capture (Step 1) must be done manually.

---

## Troubleshooting

### Capture returns 0 WebSocket messages
- Did you interact with all filters? Qlik only sends data when views are triggered.
- URL changed? Check https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect
- SkillSelect switched to REST API? Check `network_log_*.json` for JSON calls instead.

### ETL fails with "column not found"
- Qlik payload structure changed → run Notebook Cell 3 to see actual column structure → update `COLUMN_LABELS` in `ETL/occupation_intelligence_etl.py`

### SQLite error
```bash
sqlite3 data/aic_occupation_intelligence.db "PRAGMA integrity_check;"
# If corrupted: rm data/aic_occupation_intelligence.db && re-run ETL
```

### Tableau "data source not found"
DB absolute path: `/Users/nattawitrasaengcha/Documents/Gov_ETL_data/data/aic_occupation_intelligence.db`

---

## Contact

**Script issues:** Raise with Claude (upload error log + capture file)  
**Data questions:** Mild @ AIC
