# Schema Review — ETL/schema.sql
**AIC Market Intelligence Project**  
**Reviewed July 2026 — Prior to MySQL migration**

> ⚠️ **Historical planning document (2026-07-14):** Pre-migration review,
> kept for design rationale. The MySQL migration is complete and live —
> see `docs/final_migration_summary.md` for current state.

---

## Overview

`ETL/schema.sql` was designed as a SQLite prototype with 24 tables. This review identifies issues that must be resolved before migration to MySQL and before the schema can be considered production-quality.

**Verdict:** The schema cannot be syntax-converted to MySQL as-is. It requires structural redesign in several areas.

---

## CRITICAL Issues (must fix before MySQL migration)

### C1 — `education_enrolments` table MISSING from schema.sql

**Severity:** CRITICAL  
**Impact:** 3,542,826 rows (largest dataset) are loaded into a table with no DDL

The ETL `etl_education_v2.py` writes to table `education_enrolments` (see SOURCES list, parser="parse_pivot_basic" and "parse_pivot_detailed"). This table is NOT defined anywhere in `schema.sql`.

The current `upsert_df()` in `lib_etl.py` calls `df.head(0).to_sql(table, conn, if_exists="ignore")` which creates the table with pandas-inferred types. Consequences:
- No UNIQUE constraint → every rerun **appends 3.5M duplicate rows**
- No indexes → table scans
- Pandas type inference: all TEXT columns (no INT for year/month, no DECIMAL for counts)
- Schema incompatible with MySQL without explicit DDL

**Fix:** Add `education_enrolments` to schema with proper types, UNIQUE constraint, and indexes.

### C2 — `occupation_intelligence` table: no ETL populates it, dangerous wide structure

**Severity:** HIGH  
**Impact:** Misleads anyone reading the schema about what data exists

This table combines SkillSelect ceiling data + OSL shortage data + visa eligibility into one denormalized wide row. No ETL script currently writes to it. If someone does populate it, mixing three different update frequencies (monthly SkillSelect, annual OSL, occasional visa list changes) in a single row creates stale data problems.

**Fix:** Remove from core schema. If retained as a mart/view, label it clearly as `mart_occupation_snapshot` and document it's a derived table, not source-of-truth.

### C3 — `ha_migration_child_outcomes` naming is incorrect

**Severity:** HIGH  
**Impact:** BP0068 is the **Permanent Migration Program report**, not a "migration and child outcomes" report

This misname will confuse every analyst using the database. The table structure (visa_type, birth_country, outcome_measure, period, value) is generically correct for BP0068 but the name does not match the dataset.

**Fix:** Rename to `ha_permanent_migration_outcomes` and update ETL.

---

## SERIOUS Issues (fix before production load)

### S1 — SQLite-only AUTO_INCREMENT syntax

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```

MySQL syntax requires:
```sql
id INT NOT NULL AUTO_INCREMENT PRIMARY KEY
```

AUTOINCREMENT is also semantically different: SQLite guarantees no reuse, MySQL does not. For ETL tables this is acceptable.

**Fix:** Replace all occurrences in new schema_mysql.sql.

### S2 — All date columns are TEXT

Every date/period column uses `TEXT` type:
```sql
date TEXT NOT NULL,
period TEXT NOT NULL,
financial_year TEXT NOT NULL,
```

**Problems:**
- No date arithmetic (DATEDIFF, DATE_ADD, etc.)
- String ordering: "2010-12" < "2010-2" = FALSE (alphabetical sorts correctly, but "Jun-10" would not)
- Cannot enforce valid date format
- Tableau date calculations disabled

**Fix table:**

| Column | Current | Recommended MySQL type |
|--------|---------|----------------------|
| rba_exchange_rates.date | TEXT | DATE |
| abs_labour_force.period | TEXT | VARCHAR(7) (YYYY-MM) |
| abs_cpi.period | TEXT | VARCHAR(7) (YYYY-QN) |
| education_enrolments.year | (not in schema) | SMALLINT UNSIGNED |
| education_enrolments.month | (not in schema) | TINYINT UNSIGNED |
| ha_*.financial_year | TEXT | VARCHAR(7) (YYYY-YY) |
| jsa_internet_vacancies.period | TEXT | VARCHAR(10) |
| skilled_migration_summary.financial_year | TEXT | VARCHAR(7) |

### S3 — Stale / unpopulated tables mixed with active tables

Tables with no current ETL:
- `occupation_ceilings` — old SkillSelect scraper approach
- `occupation_shortage_ratings` — duplicate of `jsa_occupation_shortage`
- `visa_eligibility` — no ETL written
- `occupation_intelligence` — no ETL, derived concept

Keeping these in the MySQL schema means:
- Confusion about what's populated
- Wasted DDL maintenance
- Risk of confusing downstream analytics

**Fix:** Archive to `_archived_sqlite_schema.sql`. Define only tables with active ETL in `schema_mysql.sql`. Reinstate `visa_eligibility` only when an ETL is written for it.

### S4 — Three separate BP0015 tables for the same dataset

`ha_student_visa_lodged`, `ha_student_visa_granted`, `ha_student_visa_grant_rates` are three tables for one BP0015 dataset, identical grain `(applicant_type, sector, financial_year)`.

These should be a single `fact_student_visa_activity` table with a `measure` column:
```
applicant_type | sector | financial_year | measure       | value
Primary        | HE     | 2023-24        | lodged        | 82,340
Primary        | HE     | 2023-24        | granted       | 79,120
Primary        | HE     | 2023-24        | grant_rate_pct| 96.1
```

**Fix:** Consolidate into one table with `measure` column.

### S5 — `skilled_migration_summary` mixes two different grains

Data from `skilled_visas_summaries.xlsx` (annual aggregates) and `skilled_visas_raw_all_1.4M_rows.csv` (individual records) are loaded into the same table. These have incompatible grains.

**Fix:** Two separate tables:
- `fact_skilled_migration_summary` — aggregated XLSX data
- `stg_skilled_migration_raw` — raw CSV records (if needed; 1.4M rows suggest it may be better as a separate analytical dataset)

### S6 — `abs_employment_by_industry` and `abs_employment_by_occupation` defined but likely empty

ETL code uses SDMX keys `4..AUS` and `7+12..AUS` which use table-specific dimension structures for the LF flow. These are unlikely to work with the generic ABS API client. With local XLSX fallback, the generic parser cannot reliably extract industry/occupation dimensions.

**Fix:** Either implement proper LF table parsers (with correct SDMX queries) or remove these tables from schema until proper ETL is written.

---

## MODERATE Issues (fix for correctness)

### M1 — UNIQUE keys that omit important dimensions

**jsa_occupation_shortage**: `UNIQUE(anzsco_code, anzsco_level, state_territory, assessment_year)`
- If `state_territory` is NULL (national level), multiple NULL records cannot be distinguished
- In SQL, NULL != NULL, so multiple national-level rows for same code+year can exist

**Fix:** Make `state_territory` default to `'AUS'` (not NULL) when national-level.

**abs_labour_force**: `UNIQUE(period, measure, sex, adjustment_type, state)`
- `measure` is a long descriptive string from the ABS API (e.g., "Employed full-time ;  Persons ;  New South Wales ;")
- Any formatting difference between runs creates phantom non-duplicates

**Fix:** Use `series_id` instead of `measure` as the key component.

### M2 — `jsa_occupation_profiles` UNIQUE key is too narrow

`UNIQUE(anzsco_code, measure, dimension)` 
- `dimension` is a column header string that may change between annual profile releases
- Cannot track historical profile data without the dimension text being stable

**Fix:** Add `profile_year` or `source_file` to the key to allow versioning.

### M3 — `education_sa4_enrolments` month column is TEXT in schema

Schema defines `month TEXT` but the ETL would produce month as an integer (1-12).

**Fix:** Use `TINYINT UNSIGNED` for month.

### M4 — Missing indexes on large tables

`education_enrolments` (3.5M rows) has no indexes because it's not in schema.sql.

`jsa_internet_vacancies` (600k rows) only has indexes on `period` and `anzsco_code`, but queries will commonly filter by `state_territory` and `measure`.

**Fix:** Add compound indexes for common query patterns.

### M5 — Country name fields: no standardisation dimension

Multiple tables store country names as free text (`nationality`, `country_of_birth`, `birth_country`) with different naming conventions across sources. No shared lookup/dimension exists.

This makes cross-source country analysis require manual case-by-case mapping in Tableau.

**Fix:** Introduce `dim_country` with canonical name + source-specific aliases.

### M6 — `cricos_institutions.status` column: no validation

The `status` column can contain any string. For CRICOS, valid values are "Registered" and "Cancelled". No CHECK constraint is defined.

**Fix:** In MySQL, use an ENUM or CHECK constraint.

---

## MINOR Issues

### N1 — Inconsistent `_etl_*` metadata column names

All tables include `_etl_source` and `_etl_loaded_at` but these are not part of any UNIQUE key (correct), yet they consume space in every row and are not indexed.

**Recommendation:** Move ETL run metadata to a separate `etl_audit_log` table keyed by run_id. Reference the run_id in each fact table if needed.

### N2 — `occupation_ceilings.fill_rate_pct` is REAL

SQLite REAL vs MySQL FLOAT vs DECIMAL precision issue. For percentage rates, `DECIMAL(5,2)` is more appropriate than floating point.

### N3 — Integer overflow risk on large tables

Schema uses `INTEGER` (SQLite, flexible size) everywhere. MySQL INT is 32-bit (max ~2.1 billion). For tables like `education_enrolments` with 3.5M rows, a surrogate `id INT AUTO_INCREMENT` column will reach ~3.5M per load cycle. With reruns, this may exhaust INT range eventually.

**Fix:** Use `BIGINT AUTO_INCREMENT` for high-volume tables.

### N4 — No foreign keys anywhere in current schema

No referential integrity between related tables (e.g., cricos_courses.provider_id → cricos_institutions.provider_id).

This is common in analytical schemas for performance, but for CRICOS data with a clear parent-child relationship, a soft FK (indexed join column) at minimum is expected.

---

## Tables: ETL vs Schema Reconciliation

| Table | In schema.sql | ETL writes to it | Action |
|-------|--------------|-----------------|--------|
| occupation_ceilings | ✅ | ❌ (old approach) | Archive |
| occupation_shortage_ratings | ✅ | ❌ (replaced by jsa_occupation_shortage) | Archive |
| visa_eligibility | ✅ | ❌ (no ETL written) | Archive (reinstate when ETL ready) |
| occupation_intelligence | ✅ | ❌ (no ETL written) | Archive |
| skillselect_eoi_data | ✅ | ✅ | Keep |
| ha_student_visa_lodged | ✅ | ✅ (BP0015) | Consolidate → fact_student_visa_activity |
| ha_student_visa_granted | ✅ | ✅ (BP0015) | Consolidate → fact_student_visa_activity |
| ha_student_visa_grant_rates | ✅ | ✅ (BP0015) | Consolidate → fact_student_visa_activity |
| ha_temp_skilled_visa_granted | ✅ | ✅ (BP0014) | Rename → fact_temp_skilled_visa |
| ha_temp_skilled_visa_holders | ✅ | ✅ (BP0014) | Rename → fact_temp_skilled_visa_holders |
| ha_temp_graduate_visa_lodged | ✅ | ✅ (BP0016) | Rename → fact_temp_graduate_visa |
| ha_temp_graduate_visa_granted | ✅ | ✅ (BP0016) | Merge with lodged |
| ha_migration_child_outcomes | ✅ | ✅ (BP0068) | Rename → ha_permanent_migration_outcomes |
| cricos_institutions | ✅ | ✅ | Keep as dim_provider |
| cricos_courses | ✅ | ✅ | Keep as dim_course |
| cricos_locations | ✅ | ✅ | Keep as dim_provider_location |
| cricos_course_locations | ✅ | ✅ | Keep as bridge_course_location |
| rba_exchange_rates | ✅ | ✅ | Keep as fact_exchange_rate |
| abs_labour_force | ✅ | ✅ | Keep as fact_labour_force |
| abs_employment_by_industry | ✅ | ⚠️ (likely 0 rows) | Fix ETL or remove |
| abs_employment_by_occupation | ✅ | ⚠️ (likely 0 rows) | Fix ETL or remove |
| abs_cpi | ✅ | ✅ | Keep as fact_cpi |
| abs_net_overseas_migration | ✅ | ✅ | Keep as fact_overseas_migration |
| abs_erp_country_of_birth | ✅ | ✅ | Keep as fact_population_by_cob |
| abs_education_output | ✅ | ✅ | Keep |
| jsa_internet_vacancies | ✅ | ✅ | Keep as fact_job_vacancy |
| jsa_occupation_shortage | ✅ | ✅ | Keep as fact_occupation_shortage |
| jsa_occupation_profiles | ✅ | ✅ | Keep as ref_occupation_profile |
| education_enrolments | ❌ **MISSING** | ✅ (3.5M rows) | **ADD TO SCHEMA — CRITICAL** |
| education_int_students_historical | ✅ | ⚠️ (may be 0 rows) | Verify |
| education_sa4_enrolments | ✅ | ⚠️ (may be 0 rows) | Verify |
| skilled_migration_summary | ✅ | ✅ | Split grain issue |
| skilled_migration_country_occupation | ✅ | ✅ | Keep |

---

## Summary of Required Changes

| Priority | Change |
|----------|--------|
| 🔴 CRITICAL | Add `education_enrolments` table with UNIQUE key and indexes |
| 🔴 CRITICAL | Replace `AUTOINCREMENT` with MySQL `AUTO_INCREMENT` syntax throughout |
| 🔴 HIGH | Archive 4 unpopulated tables (`occupation_ceilings`, `occupation_shortage_ratings`, `visa_eligibility`, `occupation_intelligence`) |
| 🔴 HIGH | Rename `ha_migration_child_outcomes` → `ha_permanent_migration_outcomes` |
| 🟠 SERIOUS | Convert date/period TEXT columns to proper types |
| 🟠 SERIOUS | Consolidate BP0015 lodged/granted/rates into one fact table |
| 🟠 SERIOUS | Fix `skilled_migration_summary` grain mixing |
| 🟡 MODERATE | Fix UNIQUE keys with NULL dimension values |
| 🟡 MODERATE | Use `series_id` not `measure` text in `abs_labour_force` UNIQUE key |
| 🟡 MODERATE | Add `dim_country` for cross-source country name standardisation |
| 🟢 MINOR | Introduce ETL audit table |
| 🟢 MINOR | Add compound indexes for common query patterns |
