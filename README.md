# SkillSelect ETL Pipeline
**Australian Centre of English (AIC) — Market Intelligence Project**

Automated pipeline: SkillSelect (Qlik WebSocket) → SQLite → Tableau dashboard.
Tracks occupation ceilings, visa invitation rates, shortage status, and visa eligibility — updated monthly.

---

## Quick Start

```bash
cd ~/Documents/Gov_ETL_data

# Option A: Jupyter Notebook (recommended)
jupyter notebook AIC_SkillSelect_ETL.ipynb
# → Run cells 1–5 in order

# Option B: Command line
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python capture_skillselect_ws.py           # manual browser capture
python ETL/occupation_intelligence_etl.py \
    --ws-payload captures/ws_payload_LATEST.json \
    --osl-file   "raw_data/jobs_and_skills_australia/Occupation Shortage List - 6 digit ANZSCO and OSCA.xlsx" \
    --db         data/aic_occupation_intelligence.db
python ETL/verify_db.py                    # check results
```

---

## Project Structure

```
Gov_ETL_data/
├── AIC_SkillSelect_ETL.ipynb          ← Main notebook (start here)
├── capture_skillselect_ws.py          ← Browser WebSocket capture
├── scheduler_cron.sh                  ← Monthly automation
├── requirements.txt
├── README.md
│
├── ETL/
│   ├── schema.sql                     ← Database DDL
│   ├── skillselect_qlik_parser.py     ← Qlik WebSocket → DataFrame
│   ├── occupation_intelligence_etl.py ← Main ETL pipeline
│   └── verify_db.py                   ← Data quality check
│
├── raw_data/                          ← Source data files (existing)
│   ├── jobs_and_skills_australia/     ← OSL shortage lists
│   ├── home_affairs/                  ← Student visa data
│   ├── abs/                           ← Labour force, migration stats
│   └── ...
│
├── captures/                          ← WebSocket capture output
├── data/                              ← SQLite database output
├── logs/                              ← ETL run logs
└── docs/
    ├── DATABASE_DICTIONARY.md
    ├── ETL_RUNBOOK.md
    └── DASHBOARD_USER_GUIDE.md
```

---

## Database Tables

| Table | Description |
|-------|-------------|
| `occupation_ceilings` | Raw SkillSelect — ceiling & invitations per visa/state/month |
| `occupation_shortage_ratings` | OSL shortage ratings from Jobs & Skills Australia |
| `visa_eligibility` | MLTSSL/STSOL/ROL eligibility from Home Affairs |
| `occupation_intelligence` | Denormalized fact table for Tableau |

Connect Tableau to: `data/aic_occupation_intelligence.db`

---

## Monthly Workflow

1. Open `AIC_SkillSelect_ETL.ipynb` → run Cell 2 (browser capture)
2. Run Cell 4 → check column mapping → update RENAME_MAP
3. Run Cell 4b → load to SQLite
4. Run Cell 5 → verify
5. Refresh Tableau

**Automate ETL step (after capture):**
```bash
chmod +x scheduler_cron.sh
crontab -e
# Add: 0 10 1 * * /Users/nattawitrasaengcha/Documents/Gov_ETL_data/scheduler_cron.sh
```

---

**Owner:** Mild (Nattawitra Saengcha) @ AIC  
**Last updated:** 2026-07-09
