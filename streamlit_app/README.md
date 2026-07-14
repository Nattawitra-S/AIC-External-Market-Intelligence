# AIC Market Intelligence -- Streamlit Dashboard

A production-style Streamlit application reading **live** data from the
`aic_market_intelligence` MySQL 8 database (see
`../docs/final_migration_summary.md` and `../docs/tableau_data_dictionary.md`
for full data lineage). This app is a Streamlit-native equivalent of the
Tableau data source guide, not a replacement for it.

## Quick start

```bash
cd /Users/nattawitrasaengcha/Documents/Gov_ETL_data
cp .env.example .env   # if not already done -- fill in MYSQL_HOST/PORT/USER/PASS/DB
pip install -r streamlit_app/requirements.txt

streamlit run streamlit_app/Home.py
```

Then open the URL Streamlit prints (default `http://localhost:8501`).

**Credentials are read only from `.env` in the project root** (via
`python-dotenv`) -- never hardcoded, never displayed on any page, never
committed (`.env` is in `.gitignore`).

## Pages

| Page | What it shows |
|---|---|
| **Home** | Connection status, last ETL update, navigation guide |
| **Market Overview** | Total enrolments, YoY growth/decline, largest and fastest-moving source countries |
| **Country Analysis** | Trend, sector demand, and skilled-migration visa indicators for one selected country |
| **Student Demand** | Sector-level demand trends, increasing/declining sectors, multi-country comparison |
| **Visa Analysis** | Every visa/migration measure that genuinely exists (student visa activity, skilled migration) |
| **Competitor Analysis** | CRICOS provider/location/course landscape by state |
| **Opportunity Score** | Transparent, rule-based country ranking (growth + market size + visa pathway; competition explicitly excluded) |
| **Market Drivers** | Enrolment trend vs. exchange rate / CPI / population / migration / labour force (association only) |

## Design principles this app enforces

- **No fabricated data.** Visa refusal rates, processing times, and
  per-country course/study-area demand do not exist in the schema and are
  never shown -- every page that would need them says so explicitly
  instead (see `modules/validation.py`).
- **YTD-aware.** `fact_student_enrolment` is year-to-date cumulative.
  Every trend and year-on-year comparison in this app uses the **same
  calendar month** across years -- never a blind `SUM()` across months.
- **Deterministic scoring only.** `modules/scoring.py` documents every
  rule; the Opportunity Score is explicitly labeled as not a prediction.
- **Not real-time.** Source publication cadence ranges from monthly
  (Dept. of Education) to quarterly (ABS CPI) to annual (ABS population).
  Every page shows the last successful ETL timestamp from `etl_audit_log`.

## Architecture

```
streamlit_app/
├── Home.py                  <- entry point (streamlit run streamlit_app/Home.py)
├── pages/                   <- one file per page, Streamlit's native multipage convention
├── modules/
│   ├── db.py                <- .env credential loading, cached SQLAlchemy engine, run_query()
│   ├── queries.py            <- every SQL query, each @st.cache_data(ttl=900)
│   ├── charts.py              <- reusable Plotly chart builders (no Streamlit calls -- unit-testable)
│   ├── scoring.py             <- deterministic Opportunity Score + marketing recommendation rules
│   ├── formatting.py          <- pure number/percent/date formatting helpers
│   ├── validation.py          <- empty-table / unavailable-measure guards, with documented reasons
│   ├── common.py              <- shared sidebar refresh control, ETL freshness banner, CSV download
│   └── theme.py               <- AIC color palette + Streamlit page config
├── tests/                    <- pytest suite (see below)
├── screenshots/               <- reference screenshots of every page (see docs/final_migration_summary.md audit)
├── requirements.txt
└── .streamlit/config.toml
```

## Caching and refresh behaviour

- Every query result is cached for **15 minutes** (`CACHE_TTL` in
  `modules/queries.py`) -- long enough to avoid hammering MySQL on every
  widget interaction, short enough that a manual ETL reload during a work
  session is picked up without restarting the app.
- The **Refresh Data** button (sidebar, every page) calls
  `st.cache_data.clear()` and reruns the page -- forces an immediate,
  full re-read from MySQL.
- The database engine uses `pool_pre_ping=True`, so a dropped MySQL
  connection is transparently reconnected on the next query rather than
  raising -- no app restart needed after a brief MySQL restart.

## Running the tests

```bash
cd /Users/nattawitrasaengcha/Documents/Gov_ETL_data/streamlit_app
python -m pytest tests/ -v
```

- `test_formatting.py`, `test_scoring.py`, `test_validation.py` -- pure
  logic, no database required.
- `test_queries.py` -- live, read-only integration tests against the
  actual MySQL database (skipped automatically if unreachable). Includes
  regression tests for two real bugs found while building this app:
  an `IN (:tuple)` binding error, and three queries that were silently
  summing ABS aggregate rows ("Total", "Australia") on top of individual
  country/series rows.

## Known data limitations (surfaced in the app, not hidden)

- `fact_temp_skilled_visa`, `fact_temp_graduate_visa`,
  `fact_permanent_migration` are currently empty (Home Affairs BP0014/16/68
  source files are Excel PivotTable exports, not yet parseable).
- `dim_course.broad_field` / `field_of_education` are 100% NULL --
  course/study-area demand cannot be shown; sector-level demand is used
  as the closest available proxy.
- `dim_occupation` is unpopulated -- no ETL step currently derives it.
- `vw_occupation_intelligence.latest_vacancies` is NULL (ANZSCO
  granularity mismatch between 6-digit shortage codes and 2-digit
  vacancy codes) and `median_annual_salary_aud` is NULL (source data
  genuinely suppressed at this grain) -- not used by this app for that
  reason; see `docs/final_migration_summary.md`.

See `docs/tableau_data_dictionary.md` for the complete, exhaustive list.

## Deployment

See `DEPLOYMENT.md` in this directory.
