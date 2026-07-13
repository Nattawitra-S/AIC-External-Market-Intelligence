# MySQL Migration Test Plan
**AIC Market Intelligence Database**  
**July 2026 — Pre-production validation**

---

## Prerequisites

Before running any test:

```bash
# 1. Install dependencies
pip install mysql-connector-python python-dotenv --break-system-packages

# 2. Configure credentials
cp .env.example .env
# Edit .env with actual MySQL credentials

# 3. Create target database
mysql -u root -p -e "
  CREATE DATABASE IF NOT EXISTS aic_market_intelligence
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER IF NOT EXISTS 'aic_user'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL PRIVILEGES ON aic_market_intelligence.* TO 'aic_user'@'localhost';
  FLUSH PRIVILEGES;
"

# 4. Apply MySQL schema (do NOT run the full load yet)
mysql -u aic_user -p aic_market_intelligence < ETL/schema_mysql.sql

# 5. Verify schema
mysql -u aic_user -p aic_market_intelligence -e "
  SELECT TABLE_NAME, TABLE_ROWS
  FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = 'aic_market_intelligence'
  ORDER BY TABLE_NAME;
"
# Expected: 23 tables created, TABLE_ROWS = 0 for fact tables,
#           9 rows in dim_state, 11 rows in dim_visa_subclass

# 6. Test connection
python -c "from ETL.lib_etl_mysql import test_connection; test_connection()"
```

---

## Test Execution Order

Sources are tested smallest-first to validate the pipeline before committing to large loads.

| Order | Source | Table(s) | Expected Rows | Reason |
|-------|--------|---------|--------------|--------|
| 1 | RBA | fact_exchange_rate | ~21,549 | Smallest, clean data, stable format |
| 2 | Home Affairs | fact_student_visa_activity + others | ~916 | Small, validates BP0015 parser |
| 3 | CRICOS | dim_provider + dim_course + dim_provider_location + bridge_course_location | ~78,752 | Validates dimension loading |
| 4 | JSA | fact_job_vacancy + fact_occupation_shortage + ref_occupation_profile | ~611,280 | Medium, validates long-format |
| 5 | ABS | fact_labour_force + fact_cpi + others | ~133,170 | Validates API-sourced data |
| 6 | Skilled Migration | fact_skilled_migration + ref_skilled_migration_by_cob_occupation | ~15,040 | Validates cross-grain data |
| 7 | Education | fact_student_enrolment | ~3,542,826 | LAST — largest, needs bulk load |

---

## Test 1: RBA Exchange Rates

### Run command
```bash
cd ~/Documents/Gov_ETL_data
python -c "
from ETL.lib_etl_mysql import get_mysql_conn, test_connection
from pathlib import Path
test_connection()
"

# Once connection works, adapt etl_rba.py to use MySQL:
# (In the interim, run the dry-run SQLite version to confirm row count)
python ETL/run_all.py --sources rba --local-only --dry-run
```

### Expected output
```
✅ RBA ETL complete — 21,549 rows total
  f11-data.csv:   ~4,875 rows (195 months × 25 series)
  f11.1-data.csv: ~16,674 rows (daily from 2023-01-03 × 25 series)
```

### Validation queries
```sql
-- Row count
SELECT COUNT(*) FROM fact_exchange_rate;
-- Expected: ~21,549

-- Date range
SELECT MIN(rate_date), MAX(rate_date) FROM fact_exchange_rate WHERE source_table = 'f11';
-- Expected: 2010-01-29 to current month

SELECT MIN(rate_date), MAX(rate_date) FROM fact_exchange_rate WHERE source_table = 'f11.1';
-- Expected: 2023-01-03 to recent date

-- Series completeness
SELECT series_id, COUNT(*) AS n, MIN(rate_date), MAX(rate_date)
FROM fact_exchange_rate
GROUP BY series_id
ORDER BY n DESC
LIMIT 5;
-- Expected: FXRUSD, FXRTWI, FXRCR, FXRJY, FXREUR each with similar counts

-- No nulls on key columns
SELECT COUNT(*) FROM fact_exchange_rate WHERE rate_date IS NULL OR series_id IS NULL OR value IS NULL;
-- Expected: 0

-- Units populated
SELECT series_id, units, frequency FROM fact_exchange_rate
WHERE source_table = 'f11' GROUP BY series_id, units, frequency LIMIT 5;
-- Expected: USD, Index, CNY etc. (not NULL)
```

### Idempotency test
```bash
# Run twice — verify no duplicate rows
python ETL/run_all.py --sources rba --local-only
python ETL/run_all.py --sources rba --local-only

SELECT COUNT(*) FROM fact_exchange_rate;
-- Row count must be identical after both runs
```

---

## Test 2: Home Affairs (BP0015 + BP0014 + BP0016 + BP0068)

### Run command
```bash
python ETL/run_all.py --sources home_affairs --local-only --dry-run
```

### Expected output
```
~916 rows across multiple tables
```

### ⚠️ Note on low row count
916 total rows suggests most Home Affairs files may not be present locally. Check which specific files exist:
```bash
ls ~/Documents/Gov_ETL_data/raw_data/home_affairs/
```

### Validation queries
```sql
-- Row count per table
SELECT 'fact_student_visa_activity' AS tbl, COUNT(*) AS n FROM fact_student_visa_activity
UNION ALL
SELECT 'fact_temp_skilled_visa', COUNT(*) FROM fact_temp_skilled_visa
UNION ALL
SELECT 'fact_temp_graduate_visa', COUNT(*) FROM fact_temp_graduate_visa
UNION ALL
SELECT 'fact_permanent_migration', COUNT(*) FROM fact_permanent_migration;

-- BP0015 financial years present
SELECT DISTINCT financial_year FROM fact_student_visa_activity ORDER BY financial_year;
-- Expected: multiple years from 2013-14 onwards

-- Measures present
SELECT DISTINCT measure FROM fact_student_visa_activity;
-- Expected: lodged, granted, grant_rate_pct

-- Sectors present
SELECT DISTINCT sector FROM fact_student_visa_activity;
-- Expected: Higher Education, VET, ELICOS, Schools, NPOS, Total (may include Total rows)
```

### Idempotency test
```sql
-- Run twice, verify same count
SELECT COUNT(*) FROM fact_student_visa_activity;   -- before second run
-- Re-run ETL
SELECT COUNT(*) FROM fact_student_visa_activity;   -- must be same
```

---

## Test 3: CRICOS

### Run command
```bash
python ETL/run_all.py --sources cricos --local-only --dry-run
```

### Expected
~78,752 rows total across 4 tables

### Validation queries
```sql
-- Provider count by state
SELECT state_code, COUNT(*) AS providers
FROM dim_provider
GROUP BY state_code ORDER BY providers DESC;
-- Expected: NSW, VIC, QLD leading; NT, TAS smallest

-- Provider type breakdown
SELECT provider_type, COUNT(*) FROM dim_provider
GROUP BY provider_type ORDER BY COUNT(*) DESC;
-- Expected: University, Private Provider, TAFE, etc.

-- Course count by broad field
SELECT broad_field, COUNT(*) AS courses
FROM dim_course GROUP BY broad_field ORDER BY courses DESC LIMIT 10;

-- Referential integrity: courses with valid provider_id
SELECT COUNT(*) FROM dim_course c
WHERE provider_id NOT IN (SELECT provider_id FROM dim_provider);
-- Expected: 0 (or small number from historical/cancelled providers)

-- Course-location bridge
SELECT COUNT(*) AS links,
       COUNT(DISTINCT cricos_code) AS courses,
       COUNT(DISTINCT location_id) AS locations
FROM bridge_course_location;

-- Fees distribution
SELECT
  MIN(annual_fees_aud), MAX(annual_fees_aud), AVG(annual_fees_aud),
  COUNT(CASE WHEN annual_fees_aud IS NULL THEN 1 END) AS null_fees
FROM dim_course;
```

### Idempotency test
```sql
SELECT COUNT(*) FROM dim_provider;   -- record before second run
-- Re-run ETL
SELECT COUNT(*) FROM dim_provider;   -- must be same (ON DUPLICATE KEY UPDATE)
```

---

## Test 4: JSA

### Run command
```bash
python ETL/run_all.py --sources jsa --local-only --dry-run
```

### Expected
~611,280 rows (mostly IVI data)

### Validation queries
```sql
-- Vacancy data: period range
SELECT MIN(vacancy_period), MAX(vacancy_period), COUNT(DISTINCT vacancy_period) AS n_months
FROM fact_job_vacancy;
-- Expected: ~2006-01 to 2026-03 (15+ years), 200+ distinct months

-- Vacancy data: ANZSCO level breakdown
SELECT anzsco_level, COUNT(*) AS rows, COUNT(DISTINCT anzsco_code) AS unique_codes
FROM fact_job_vacancy
GROUP BY anzsco_level;
-- Expected: level=2 and level=4 (rows in both)

-- Measure types
SELECT measure, COUNT(*) FROM fact_job_vacancy GROUP BY measure;
-- Expected: SA, Trend, Original

-- Shortage status distribution
SELECT shortage_status, COUNT(*) AS n
FROM fact_occupation_shortage GROUP BY shortage_status;
-- Expected: Shortage, No Shortage, Regional Shortage

-- Occupation profiles: measure types
SELECT profile_measure, COUNT(*) AS rows, COUNT(DISTINCT anzsco_code) AS codes
FROM ref_occupation_profile GROUP BY profile_measure;

-- Check for duplicate vacancies (key violation would have prevented insert)
SELECT vacancy_period, anzsco_code, state_code, measure, COUNT(*) AS n
FROM fact_job_vacancy
GROUP BY vacancy_period, anzsco_code, state_code, measure
HAVING n > 1;
-- Expected: 0 rows
```

### NULL validation
```sql
-- Critical: anzsco_code nulls in vacancy (some are skill-level aggregates, expected)
SELECT COUNT(*) FROM fact_job_vacancy WHERE anzsco_code IS NULL;
-- May be non-zero for skill level rows — verify it's intentional

-- Check no period is NULL
SELECT COUNT(*) FROM fact_job_vacancy WHERE vacancy_period IS NULL;
-- Expected: 0
```

---

## Test 5: ABS

### Run command
```bash
python ETL/run_all.py --sources abs --local-only --dry-run
```

### Expected
~133,170 rows across multiple tables

### Validation queries
```sql
-- Table distribution
SELECT 'fact_labour_force' AS tbl, COUNT(*) FROM fact_labour_force
UNION ALL SELECT 'fact_cpi', COUNT(*) FROM fact_cpi
UNION ALL SELECT 'fact_overseas_migration', COUNT(*) FROM fact_overseas_migration
UNION ALL SELECT 'fact_population_by_cob', COUNT(*) FROM fact_population_by_cob
UNION ALL SELECT 'fact_abs_education_output', COUNT(*) FROM fact_abs_education_output;

-- Labour force: period range
SELECT MIN(lf_period), MAX(lf_period) FROM fact_labour_force;
-- Expected: 2015-01 to recent month (API source starts 2015)

-- Labour force: series count
SELECT COUNT(DISTINCT series_id) FROM fact_labour_force;
-- Expected: 30+ series

-- CPI: quarterly periods
SELECT MIN(cpi_period), MAX(cpi_period), COUNT(*) FROM fact_cpi;
-- Expected: 2015-Q1 to recent quarter

-- ⚠️ Check for measure as key issue
-- If same (period, series_id) appears with different measure text → UNIQUE violation
SELECT lf_period, series_id, COUNT(*) AS n
FROM fact_labour_force
GROUP BY lf_period, series_id
HAVING n > 1;
-- Expected: 0 rows
```

---

## Test 6: Skilled Migration

### Run command
```bash
python ETL/run_all.py --sources skilled_migration --local-only --skip-heavy --dry-run
```

### Expected
~15,040 rows across 2 tables

### Validation queries
```sql
-- Financial years present
SELECT DISTINCT financial_year FROM fact_skilled_migration ORDER BY financial_year;
-- Expected: multiple years from FY2010-11 onwards

-- Visa subclasses in skilled migration
SELECT DISTINCT visa_subclass FROM fact_skilled_migration
WHERE visa_subclass IS NOT NULL ORDER BY visa_subclass;
-- Expected: 189, 190, 491, 186, 494 and others

-- Country × occupation data
SELECT COUNT(*) AS rows,
       COUNT(DISTINCT country_name) AS countries,
       COUNT(DISTINCT anzsco_code) AS occupations
FROM ref_skilled_migration_by_cob_occupation;

-- Grain check: no grain-level duplicates
SELECT financial_year, country_name, anzsco_code, visa_subclass, measure, COUNT(*)
FROM ref_skilled_migration_by_cob_occupation
GROUP BY financial_year, country_name, anzsco_code, visa_subclass, measure
HAVING COUNT(*) > 1
LIMIT 5;
-- Expected: 0 rows
```

---

## Test 7: Education Enrolments (LAST — 3.5M rows)

### ⚠️ Pre-test checklist
Before loading education data to MySQL:
- [ ] All other 6 sources loaded and validated
- [ ] `fact_student_enrolment` table created with UNIQUE key and indexes (from schema_mysql.sql)
- [ ] `LOAD DATA LOCAL INFILE` enabled: `SHOW GLOBAL VARIABLES LIKE 'local_infile';` → must be ON
- [ ] Sufficient disk space (3.5M rows × ~500 bytes ≈ ~1.7 GB)
- [ ] MySQL `max_allowed_packet` ≥ 64M for staging CSV

### Enable LOAD DATA (if needed)
```sql
-- In MySQL 8, may require:
SET GLOBAL local_infile = 1;
```

### Run command
```bash
# Dry-run first (will not write to DB)
python ETL/run_all.py --sources education --local-only --dry-run

# Then actual load (may take 5-20 minutes)
# Use lib_etl_mysql.load_education_enrolments() for bulk load
# TODO: wire into run_all.py with --mysql flag
```

### Expected
~3,542,826 rows in `fact_student_enrolment`

### Validation queries
```sql
-- Basic count
SELECT COUNT(*) FROM fact_student_enrolment;
-- Expected: ~3,542,826

-- Year range
SELECT MIN(enrol_year), MAX(enrol_year) FROM fact_student_enrolment;
-- Expected: multiple years ending 2026

-- Month distribution (should have all 12 months if full history)
SELECT enrol_month, COUNT(*) FROM fact_student_enrolment
GROUP BY enrol_month ORDER BY enrol_month;

-- Top nationalities
SELECT nationality, SUM(ytd_enrolments) AS total_enrolments
FROM fact_student_enrolment
WHERE enrol_year = 2025 AND enrol_month = (
    SELECT MAX(enrol_month) FROM fact_student_enrolment WHERE enrol_year = 2025
)
GROUP BY nationality
ORDER BY total_enrolments DESC
LIMIT 10;
-- Expected: China, India, Nepal, Colombia, Vietnam leading

-- Sector breakdown
SELECT sector, COUNT(*) AS rows FROM fact_student_enrolment
GROUP BY sector ORDER BY rows DESC;
-- Expected: Higher Education, VET, ELICOS, Schools, NPOS

-- NULL check on critical columns
SELECT COUNT(*) FROM fact_student_enrolment
WHERE enrol_year IS NULL OR enrol_month IS NULL OR nationality IS NULL OR state_code IS NULL;
-- Expected: 0

-- YTD note: check that Feb 2026 YTD ≠ sum of all months
-- (The data is YTD cumulative, not monthly incremental)
SELECT enrol_year, enrol_month, SUM(ytd_enrolments) AS total_ytd
FROM fact_student_enrolment
WHERE sector = 'Higher Education' AND nationality = 'China'
GROUP BY enrol_year, enrol_month
ORDER BY enrol_year, enrol_month;
-- Values should increase through the year, not sum to unrealistic numbers
```

### Idempotency test
```sql
SELECT COUNT(*) FROM fact_student_enrolment;   -- before second run

-- Re-run ETL (LOAD DATA path uses INSERT ... ON DUPLICATE KEY UPDATE or staging REPLACE)
-- After second run:
SELECT COUNT(*) FROM fact_student_enrolment;
-- Must be SAME count — no duplicates added
```

### Performance benchmark
```sql
-- Test index effectiveness
EXPLAIN SELECT COUNT(*) FROM fact_student_enrolment
WHERE enrol_year = 2025 AND state_code = 'NSW' AND sector = 'Higher Education';
-- Should use idx_enrol_ym or idx_enrol_state (not full table scan)
```

---

## Cross-Source Integration Tests

After all sources load, run these cross-source queries to verify end-to-end pipeline:

```sql
-- Q1: Which nationalities have high enrolments AND high visa shortage occupations?
-- (Requires: fact_student_enrolment + fact_occupation_shortage)
-- This test verifies fact tables are independently queryable

SELECT e.nationality, SUM(e.ytd_enrolments) AS enrolments
FROM fact_student_enrolment e
WHERE e.enrol_year = 2025 AND e.enrol_month = 2
GROUP BY e.nationality
ORDER BY enrolments DESC LIMIT 10;

-- Q2: AUD/USD rate vs Education enrolments year-over-year
-- (Requires: fact_exchange_rate + fact_student_enrolment)
SELECT r.enrol_year, r.total_enrolments, fx.avg_rate_usd
FROM (
    SELECT enrol_year, SUM(ytd_enrolments) AS total_enrolments
    FROM fact_student_enrolment
    WHERE enrol_month = 12
    GROUP BY enrol_year
) r
JOIN (
    SELECT YEAR(rate_date) AS yr, AVG(value) AS avg_rate_usd
    FROM fact_exchange_rate
    WHERE series_id = 'FXRUSD' AND frequency = 'Monthly'
    GROUP BY YEAR(rate_date)
) fx ON r.enrol_year = fx.yr
ORDER BY r.enrol_year;

-- Q3: Top shortage occupations with active SkillSelect EOIs
-- (Requires: fact_occupation_shortage + stg_skillselect_eoi)
SELECT s.shortage_status, COUNT(DISTINCT s.anzsco_code) AS shortage_occupations
FROM fact_occupation_shortage s
WHERE s.assessment_year = '2025' AND s.state_code = 'AUS'
GROUP BY s.shortage_status;

-- Q4: Audit log — ETL run history
SELECT source, table_name, started_at, status, rows_inserted, rows_rejected
FROM etl_audit_log
ORDER BY started_at DESC
LIMIT 20;
```

---

## Failure Scenarios and Expected Behaviour

| Scenario | Expected Behaviour |
|----------|-------------------|
| UNIQUE key violation during upsert | `ON DUPLICATE KEY UPDATE` silently updates — no error, count unchanged |
| MySQL server unreachable | `get_mysql_conn()` raises `EnvironmentError` with clear message |
| .env file missing | Raises `EnvironmentError`: "MYSQL_USER and MYSQL_DB environment variables are required" |
| `LOAD DATA LOCAL INFILE` disabled | Raises `mysql.connector.Error: 3950` — enable with `SET GLOBAL local_infile=1` |
| Education CSV staging dir not writable | `PermissionError` from `bulk_load_csv()` — change `MYSQL_STAGING_DIR` |
| ETL crashes mid-load | `conn.rollback()` called; `etl_audit_log.status = 'failed'` with error message |

---

## Final Sign-off Checklist

Before declaring MySQL migration complete:

- [ ] All 7 sources loaded without errors
- [ ] Row counts match dry-run totals (±5%)
- [ ] All idempotency tests pass (no duplicate rows on rerun)
- [ ] No NULL violations on UNIQUE key columns
- [ ] `etl_audit_log` has clean run records for all sources
- [ ] At least 3 cross-source integration queries return sensible results
- [ ] Tableau can connect and render basic viz from `fact_student_enrolment` + `fact_job_vacancy`
- [ ] SQLite prototype preserved as `ETL/schema_sqlite.sql` + old `lib_etl.py`
- [ ] `.env` is in `.gitignore` (never committed)

---

## Next Commands (do NOT run yet)

```bash
# Step 1: Create MySQL database and apply schema
mysql -u root -p -e "CREATE DATABASE aic_market_intelligence CHARACTER SET utf8mb4;"
mysql -u aic_user -p aic_market_intelligence < ETL/schema_mysql.sql

# Step 2: Test connection
python -c "from ETL.lib_etl_mysql import test_connection; test_connection()"

# Step 3: Load smallest source first
# (requires ETL scripts to be adapted to use lib_etl_mysql.py)
python ETL/run_all.py --sources rba --local-only --mysql

# Step 4: Validate with SQL queries above
# Step 5: Load remaining sources in order
# Step 6: Education last with bulk load
```
