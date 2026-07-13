# AIC External Market Intelligence Database
**Australian Centre of English (AIC) — Market Intelligence Project**

Pipeline: RBA, Home Affairs, CRICOS, JSA, ABS, Skilled Migration, and
Department of Education data → **MySQL 8** → Tableau dashboard.
Tracks occupation shortages, job vacancies, student visa/enrolment activity,
skilled migration, exchange rates, labour force and CPI data — updated per
source release cadence.

The original SkillSelect-only SQLite prototype (`AIC_SkillSelect_ETL.ipynb`,
`ETL/occupation_intelligence_etl.py`, `ETL/schema_sqlite.sql`) is preserved
unchanged; see `docs/final_migration_summary.md` for the full migration
report.

---

## Quick Start (MySQL — current pipeline)

```bash
cd ~/Documents/Gov_ETL_data
cp .env.example .env   # fill in MYSQL_HOST/PORT/USER/PASS/DB
pip install -r requirements.txt mysql-connector-python

# Dry run (zero-write, parse-only) — all 7 sources
python ETL/run_all.py --dry-run --local-only

# Live load — all 7 sources, in the approved order
python ETL/run_all.py --local-only

# Verify
python ETL/verify_mysql_database.py
```

Tableau connects directly to MySQL — see `docs/final_migration_summary.md`
for connection details, live row counts, and known limitations.

## Quick Start (SkillSelect / SQLite prototype — preserved, unchanged)

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

## Database Tables (MySQL, current)

25 base tables (dim/fact/ref/bridge/staging/audit) + `vw_occupation_intelligence`
view. Full schema: `ETL/schema_mysql.sql`. Column-level dictionary:
`docs/DATABASE_DICTIONARY.md`. Live row counts and per-table status:
`docs/final_migration_summary.md`.

Connect Tableau to MySQL using the `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_DB`/
`MYSQL_USER` values in `.env`. Recommended entry point:
`vw_occupation_intelligence`.

### SkillSelect / SQLite prototype tables (preserved, separate database)

| Table | Description |
|-------|-------------|
| `occupation_ceilings` | Raw SkillSelect — ceiling & invitations per visa/state/month |
| `occupation_shortage_ratings` | OSL shortage ratings from Jobs & Skills Australia |
| `visa_eligibility` | MLTSSL/STSOL/ROL eligibility from Home Affairs |
| `occupation_intelligence` | Denormalized fact table for Tableau |

Connect Tableau to: `data/aic_occupation_intelligence.db` (SQLite, separate from the MySQL pipeline above)

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
**Last updated:** 2026-07-14
