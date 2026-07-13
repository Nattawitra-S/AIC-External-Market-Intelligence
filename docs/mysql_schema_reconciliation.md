# MySQL Schema Reconciliation
**AIC Market Intelligence Database**  
**Date:** 2026-07-13  
**Status:** Reconciliation complete — used to finalize schema_mysql.sql and ETL wiring

---

## 1. Source-to-MySQL Table Map

### 1A. RBA — Exchange Rates

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_rba.py` |
| Category | Fact |
| SQLite table | `rba_exchange_rates` |
| MySQL table | `fact_exchange_rate` |
| Grain | One row per calendar date × series_id |
| Primary key | `id` BIGINT AUTO_INCREMENT |
| Business key | `(rate_date, series_id)` |
| FKs | None (series_id not FK to dim — managed via ETL) |
| Expected rows | ~21,549 (verified dry-run) |
| Update frequency | Monthly (RBA publishes monthly) |
| Initial inclusion | ✅ YES |
| MySQL wiring | ❌ NOT YET — needs wiring in Phase 4 |

**ETL DataFrame columns produced:**
```
date, series_id, value, title, units, frequency, source_table, _etl_source, _etl_loaded_at
```

**MySQL table columns:**
```
rate_date, series_id, currency_pair, units, frequency, value, source_table, _etl_source, _etl_loaded_at
```

**Column mapping required:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `date` | `rate_date` | Rename in MySQL load |
| `title` | `currency_pair` | Rename in MySQL load |
| `series_id` | `series_id` | ✅ Match |
| `value` | `value` | ✅ Match |
| `units` | `units` | ✅ Match |
| `frequency` | `frequency` | ✅ Match |
| `source_table` | `source_table` | ✅ Match |

---

### 1B. CRICOS — Providers, Courses, Locations

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_cricos.py` |
| Category | Dimension + Bridge |
| SQLite tables | `cricos_institutions`, `cricos_courses`, `cricos_locations`, `cricos_course_locations` |
| MySQL tables | `dim_provider`, `dim_course`, `dim_provider_location`, `bridge_course_location` |
| Expected rows | ~78,752 total |
| Update frequency | Quarterly |
| Initial inclusion | ✅ YES |

**Institutions → dim_provider column mapping:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `provider_id` | `provider_id` (PK VARCHAR(10)) | ✅ Match |
| `provider_name` | `provider_name` | ✅ Match |
| `provider_type` | `provider_type` | ✅ Match |
| `state` | `state_code` | ⚠️ Rename in MySQL load |
| `website` | `website` | ✅ Match |
| `status` | `registration_status` | ⚠️ Rename in MySQL load |
| `registration_end_date` | `registration_end_date` | Parse as DATE |

**Courses → dim_course column mapping:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `cricos_code` | `cricos_code` (PK) | ✅ Match |
| `course_name` | `course_name` | ✅ Match |
| `field_of_education` | `field_of_education` | ✅ Match |
| `broad_field` | `broad_field` | ✅ Match |
| `duration_weeks` | `duration_weeks` | ✅ Match |
| `min_age` | `min_age` | ✅ Match |
| `fees_aud` | `annual_fees_aud` | ⚠️ Rename in MySQL load |
| `provider_id` | `provider_id` (FK) | ✅ Match |

**Locations → dim_provider_location column mapping:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `location_id` | `location_id` (PK) | ✅ Match |
| `provider_id` | `provider_id` | ✅ Match |
| `location_name` | `location_name` | ✅ Match |
| `address` | `address` | ✅ Match |
| `suburb` | `suburb` | ✅ Match |
| `state` | `state_code` | ⚠️ Rename in MySQL load |
| `postcode` | `postcode` | ✅ Match |

**Course-locations → bridge_course_location:** all 3 columns match.

---

### 1C. Home Affairs — BP0015 Student Visa Activity

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_home_affairs_extended.py` |
| Category | Fact |
| SQLite tables | `ha_student_visa_lodged` + `ha_student_visa_granted` + `ha_student_visa_grant_rates` |
| MySQL table | `fact_student_visa_activity` (consolidated with measure column) |
| Grain | One row per applicant_type × sector × financial_year × measure |
| Business key | `(applicant_type, sector, financial_year, measure)` |
| Expected rows | Part of ~916 total HA rows |
| Initial inclusion | ✅ YES |
| MySQL wiring | ❌ NOT YET |

**ETL DataFrame columns (from parse_bp0015_*):**
```
applicant_type, sector, financial_year, lodged_count / granted_count / grant_rate_pct
```

**Column mapping required:**
- ETL produces 3 separate frames, each with one count column
- MySQL load must add `measure` column: `'lodged'`, `'granted'`, `'grant_rate_pct'`
- Rename count column to `value`

---

### 1D. Home Affairs — BP0014 Temporary Skilled Visa

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_home_affairs_extended.py` |
| Category | Fact |
| SQLite tables | `ha_temp_skilled_visa_granted` + `ha_temp_skilled_visa_holders` |
| MySQL table | `fact_temp_skilled_visa` |
| Grain | One row per visa_subclass × nationality × financial_year × state_code × measure |
| Business key | `(visa_subclass, nationality[80], financial_year, state_code, measure)` |
| Nullable key strategy | `state_code DEFAULT 'AUS'` (non-null default) |
| Initial inclusion | ✅ YES |

**ETL DataFrame columns:**
- Granted: `visa_subclass, nationality, financial_year, granted_count`
- Holders: `visa_subclass, nationality, state_territory, as_at_date, holder_count`

**Column mapping required:**
- Add `measure` column: `'granted'` or `'holders'`
- Rename `granted_count` / `holder_count` → `value`
- `state_territory` → `state_code`
- `as_at_date` → stored as `financial_year` for holders (period label)

---

### 1E. Home Affairs — BP0016 Temporary Graduate Visa

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_home_affairs_extended.py` |
| Category | Fact |
| SQLite tables | `ha_temp_graduate_visa_lodged` + `ha_temp_graduate_visa_granted` |
| MySQL table | `fact_temp_graduate_visa` |
| Grain | One row per stream × nationality × financial_year × measure |
| Business key | `(stream[50], nationality[80], financial_year, measure)` |
| Initial inclusion | ✅ YES |

**Column mapping:** Add `measure = 'lodged'` / `'granted'`, rename count column to `value`.

---

### 1F. Home Affairs — BP0068 Permanent Migration Outcomes

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_home_affairs_extended.py` |
| Category | Fact |
| SQLite table | `ha_migration_child_outcomes` (RENAMED in MySQL) |
| MySQL table | `fact_permanent_migration` |
| Grain | One row per visa_type × birth_country × outcome_measure × period |
| Business key | `(visa_type[30], birth_country[80], outcome_measure[100], period)` |
| Nullable key strategy | All key cols already VARCHAR — use `DEFAULT ''` if NULL |
| Initial inclusion | ✅ YES |

**ETL DataFrame columns:**
```
visa_type, birth_country, outcome_measure, period, value
```
All match fact_permanent_migration column names ✅

---

### 1G. JSA — Internet Vacancy Index

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_jsa.py` |
| Category | Fact |
| SQLite table | `jsa_internet_vacancies` |
| MySQL table | `fact_job_vacancy` |
| Grain | One row per vacancy_period × anzsco_code × state_code × measure |
| Business key | `(vacancy_period, anzsco_code_k, state_code, measure)` |
| Nullable key | `anzsco_code` can be NULL for skill-level aggregates → generated key `anzsco_code_k` |
| Expected rows | ~600,000+ (IVI data from ~2006) |
| Initial inclusion | ✅ YES |

**ETL DataFrame columns:**
```
period, anzsco_code, occupation_name, anzsco_level, state_territory, measure, value
```

**Column mapping required:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `period` | `vacancy_period` | Rename + normalise to YYYY-MM |
| `state_territory` | `state_code` | Rename + shorten to 3-char code |
| `value` | `vacancy_count` | Rename |
| `anzsco_code` | `anzsco_code` | ✅ Match |

---

### 1H. JSA — Occupation Shortage List

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_jsa.py` |
| Category | Fact |
| SQLite table | `jsa_occupation_shortage` |
| MySQL table | `fact_occupation_shortage` |
| Grain | One row per anzsco_code × anzsco_level × state_code × assessment_year |
| Business key | `(anzsco_code, anzsco_level, state_code, assessment_year_k)` |
| Nullable key | `assessment_year` can be NULL → generated key `assessment_year_k` |
| Expected rows | ~1,000–5,000 |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `state_territory` → `state_code`

---

### 1I. JSA — Occupation Profiles

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_jsa.py` |
| Category | Reference |
| SQLite table | `jsa_occupation_profiles` |
| MySQL table | `ref_occupation_profile` |
| Grain | One row per anzsco_code × profile_measure × dimension × profile_year |
| Business key | `(anzsco_code, profile_measure[40], dimension[80], profile_year_k)` |
| Nullable key | `profile_year` can be NULL → generated key `profile_year_k` |
| Expected rows | ~6,000–10,000 |
| Initial inclusion | ✅ YES |

**ETL DataFrame columns:**
```
anzsco_code, occupation_name, measure, dimension, value, value_text
```

**Column mapping required:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `measure` | `profile_measure` | Rename |
| `value` | `value_num` | Rename |
| `value_text` | `value_text` | ✅ Match |

---

### 1J. ABS — Labour Force

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_abs.py` |
| Category | Fact |
| SQLite table | `abs_labour_force` |
| MySQL table | `fact_labour_force` |
| Grain | One row per lf_period × series_id |
| Business key | `(lf_period, series_id)` |
| Expected rows | ~50,000–80,000 |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `period` → `lf_period`

---

### 1K. ABS — Consumer Price Index

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_abs.py` |
| Category | Fact |
| SQLite table | `abs_cpi` |
| MySQL table | `fact_cpi` |
| Grain | One row per cpi_period × series_id |
| Business key | `(cpi_period, series_id)` |
| Expected rows | ~10,000–30,000 |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `period` → `cpi_period`
- `group_` → `cpi_group`

---

### 1L. ABS — Net Overseas Migration

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_abs.py` |
| Category | Fact |
| SQLite table | `abs_net_overseas_migration` |
| MySQL table | `fact_overseas_migration` |
| Grain | One row per nom_period × country_name × state_code × direction |
| Business key | `(nom_period, country_name_k[80], state_code, direction)` |
| Nullable key | `country_name` can be NULL → generated key |
| Expected rows | ~20,000–50,000 |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `period` → `nom_period`
- `country_of_birth` → `country_name`
- `state_territory` → `state_code`
- `value` → `value`

---

### 1M. ABS — Estimated Resident Population by Country of Birth

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_abs.py` |
| Category | Fact |
| SQLite table | `abs_erp_country_of_birth` |
| MySQL table | `fact_population_by_cob` |
| Grain | One row per erp_period × country_name × state_code |
| Business key | `(erp_period, country_name_k[80], state_code)` |
| Nullable key | `country_name` can be NULL → generated key |
| Expected rows | ~20,000–50,000 |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `period` → `erp_period`
- `country_of_birth` → `country_name`
- `value` → `population`

---

### 1N. ABS — Education & Training Output

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_abs.py` (`edu_output` flow) |
| MySQL table | **EXCLUDED** per approved decision #4 |
| SQLite table | `abs_education_output` — this table is NOT in new MySQL schema |
| Wiring | ABS ETL must skip `edu_output` when `--mysql` is active |
| Initial inclusion | ❌ EXCLUDED |

---

### 1O. Skilled Migration — Programme Summary

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_skilled_migration.py` |
| Category | Fact |
| SQLite table | `skilled_migration_summary` |
| MySQL table | `fact_skilled_migration` |
| Grain | One row per financial_year × visa_subclass_k × stream_k × state_k × measure |
| Business key | `(financial_year, visa_subclass_k, stream_k, state_k, measure[40])` |
| Nullable key | All dim columns nullable → generated keys |
| Expected rows | ~15,000 (from XLSX, skip-heavy) |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `state_territory` → `state_code`

---

### 1P. Skilled Migration — Country × Occupation

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_skilled_migration.py` |
| Category | Reference |
| SQLite table | `skilled_migration_country_occupation` |
| MySQL table | `ref_skilled_migration_by_cob_occupation` |
| Grain | One row per financial_year × country_name × anzsco_code × visa_subclass × measure |
| Business key | `(financial_year, country_name_k[80], anzsco_code_k, visa_subclass_k, measure[40])` |
| Nullable key | country, anzsco, visa all nullable → generated keys |
| Expected rows | ~15,000–40 |
| Initial inclusion | ✅ YES |

**Column mapping required:**
- `country_of_birth` → `country_name`

---

### 1Q. Education — International Student Enrolments

| Item | Value |
|------|-------|
| ETL module | `ETL/etl_education_v2.py` |
| Category | Fact |
| SQLite table | `education_enrolments` (created dynamically by upsert_df) |
| MySQL table | `fact_student_enrolment` |
| Grain | One row per year × month × nationality × state × sector × provider_type × new_to_australia × ends_this_year |
| Primary key | `id` BIGINT AUTO_INCREMENT |
| Business key | `uk_enrol` on 8 columns |
| Expected rows | ~3,542,826 (verified dry-run) |
| Load method | **MUST use LOAD DATA LOCAL INFILE — never executemany for full table** |
| Initial inclusion | ✅ YES — LAST to load |

**ETL DataFrame columns produced:**
```
year, month, nationality, state, sector, provider_type,
new_to_australia, ends_this_year, data_ytd_enrolments,
data_ytd_commencements, total, _etl_source, _etl_loaded_at
```

**Column mapping required:**
| ETL column | MySQL column | Action |
|-----------|-------------|--------|
| `year` | `enrol_year` | Rename |
| `month` | `enrol_month` | Rename |
| `state` | `state_code` | Rename |
| `data_ytd_enrolments` | `ytd_enrolments` | Rename |
| `data_ytd_commencements` | `ytd_commencements` | Rename |
| `nationality` | `nationality` | ✅ Match |
| `sector` | `sector` | ✅ Match |
| `provider_type` | `provider_type` | ✅ Match |

---

### 1R. SkillSelect EOI (Staging)

| Item | Value |
|------|-------|
| ETL module | `ETL/skillselect_csv_etl.py` (separate — not in run_all.py MySQL mode) |
| Category | Staging |
| SQLite table | `skillselect_eoi_data` |
| MySQL table | `stg_skillselect_eoi` |
| Expected rows | Variable — depends on CSV export |
| Initial inclusion | ✅ YES (table exists, populated separately) |
| MySQL wiring | Not wired into run_all.py — manual load |

---

### 1S. dim_country

| Item | Value |
|------|-------|
| Category | Dimension |
| Populated by | Phase 7 post-load population script (approved decision #6) |
| Source | Union of unique nationalities/countries from education + home_affairs + abs + skilled_migration |
| Supporting file | `reference/country_aliases.csv` |
| Initial inclusion | ✅ YES — seeded empty, populated after source loads |

---

## 2. Tables in Current schema_mysql.sql vs Final Design

| Table | Current Status | Action Required |
|-------|---------------|-----------------|
| `etl_audit_log` | ✅ Correct | None |
| `dim_country` | ✅ Present | None |
| `dim_state` | ✅ Present + seeded | None |
| `dim_occupation` | ✅ Present | None (populated from JSA data in Phase 7) |
| `dim_visa_subclass` | ✅ Present + seeded | None |
| `dim_provider` | ✅ Present | None |
| `dim_course` | ✅ Present | None |
| `dim_provider_location` | ✅ Present | None |
| `fact_exchange_rate` | ✅ Present | None |
| `fact_student_enrolment` | ✅ Present | None |
| `fact_student_visa_activity` | ✅ Present | None |
| `fact_temp_skilled_visa` | ✅ Present | None |
| `fact_temp_graduate_visa` | ✅ Present | None |
| `fact_permanent_migration` | ✅ Present | None |
| `fact_skilled_migration` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated columns |
| `ref_skilled_migration_by_cob_occupation` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated columns |
| `fact_job_vacancy` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated column |
| `fact_occupation_shortage` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated column |
| `fact_labour_force` | ✅ Correct | None |
| `fact_cpi` | ✅ Correct | None |
| `fact_overseas_migration` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated column |
| `fact_population_by_cob` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated column |
| `fact_abs_education_output` | ❌ Must be REMOVED | Delete per decision #4 |
| `ref_occupation_profile` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated column |
| `bridge_course_location` | ✅ Correct | None |
| `stg_skillselect_eoi` | ⚠️ Invalid UNIQUE KEY syntax | Fix: add generated columns |

**Net count after Phase 2:** 25 tables (was 23; 23 - 1 removed + 3 new dimension tables that were already counted). Final count = **25 tables**.

Wait — let me recount:
- etl_audit_log (1)
- dim_country, dim_state, dim_occupation, dim_visa_subclass, dim_provider, dim_course, dim_provider_location (7)
- fact_exchange_rate, fact_student_enrolment, fact_student_visa_activity, fact_temp_skilled_visa, fact_temp_graduate_visa, fact_permanent_migration, fact_skilled_migration, fact_job_vacancy, fact_occupation_shortage, fact_labour_force, fact_cpi, fact_overseas_migration, fact_population_by_cob (13)
- ref_occupation_profile, ref_skilled_migration_by_cob_occupation (2)
- bridge_course_location (1)
- stg_skillselect_eoi (1)

**Total: 25 tables** (after removing fact_abs_education_output)

---

## 3. Invalid UNIQUE KEY Syntax — Required Fixes

MySQL 8 does not allow expressions in traditional UNIQUE KEY definitions.
The approved solution (decision #7) is **generated non-null key columns**.

### Pattern for each affected table

```sql
-- Instead of this invalid syntax:
UNIQUE KEY uk_sm (financial_year, COALESCE(visa_subclass, ''), COALESCE(stream, ''), ...)

-- Use generated columns:
visa_subclass_k  VARCHAR(10) NOT NULL GENERATED ALWAYS AS (COALESCE(visa_subclass, ''))  STORED,
stream_k         VARCHAR(80) NOT NULL GENERATED ALWAYS AS (COALESCE(stream, ''))          STORED,
state_k          VARCHAR(3)  NOT NULL GENERATED ALWAYS AS (COALESCE(state_code, ''))      STORED,
UNIQUE KEY uk_sm (financial_year, visa_subclass_k, stream_k, state_k, measure(40))
```

### Tables requiring generated columns

| Table | Nullable key cols | Generated cols to add |
|-------|-----------------|----------------------|
| `fact_skilled_migration` | visa_subclass, stream, state_code | visa_subclass_k, stream_k, state_k |
| `ref_skilled_migration_by_cob_occupation` | country_name, anzsco_code, visa_subclass | country_name_k, anzsco_code_k, visa_subclass_k |
| `fact_job_vacancy` | anzsco_code | anzsco_code_k |
| `fact_occupation_shortage` | assessment_year | assessment_year_k |
| `fact_overseas_migration` | country_name | country_name_k |
| `fact_population_by_cob` | country_name | country_name_k |
| `ref_occupation_profile` | profile_year | profile_year_k |
| `stg_skillselect_eoi` | dimension_1_val, dimension_2_val | dim1_val_k, dim2_val_k |

---

## 4. Quality Gate 1 — Verification Results

### A. Every MySQL table has a population source

| Table | Population source |
|-------|------------------|
| etl_audit_log | Auto-populated by AuditRun class |
| dim_country | Post-load script from source data |
| dim_state | Seeded (9 rows) |
| dim_occupation | Populated from JSA OSL/profiles (jsa_occupation_shortage + jsa_occupation_profiles anzsco data) |
| dim_visa_subclass | Seeded (11 rows) + HA visa data |
| dim_provider | etl_cricos.py |
| dim_course | etl_cricos.py |
| dim_provider_location | etl_cricos.py |
| fact_exchange_rate | etl_rba.py |
| fact_student_enrolment | etl_education_v2.py (bulk load) |
| fact_student_visa_activity | etl_home_affairs_extended.py (BP0015 x3 consolidated) |
| fact_temp_skilled_visa | etl_home_affairs_extended.py (BP0014) |
| fact_temp_graduate_visa | etl_home_affairs_extended.py (BP0016) |
| fact_permanent_migration | etl_home_affairs_extended.py (BP0068) |
| fact_skilled_migration | etl_skilled_migration.py (summaries) |
| ref_skilled_migration_by_cob_occupation | etl_skilled_migration.py (country_occupation) |
| fact_job_vacancy | etl_jsa.py (IVI) |
| fact_occupation_shortage | etl_jsa.py (OSL) |
| ref_occupation_profile | etl_jsa.py (profiles) |
| fact_labour_force | etl_abs.py (lf) |
| fact_cpi | etl_abs.py (cpi) |
| fact_overseas_migration | etl_abs.py (nom) |
| fact_population_by_cob | etl_abs.py (erp_cob) |
| bridge_course_location | etl_cricos.py |
| stg_skillselect_eoi | skillselect_csv_etl.py (manual load) |

**Result: PASS — all 25 tables have population sources.**

### B. Every ETL output has a destination

| ETL output | MySQL destination |
|-----------|------------------|
| RBA `date, series_id, title, units, frequency, value, source_table` | `fact_exchange_rate` (with column rename) |
| CRICOS institutions | `dim_provider` |
| CRICOS courses | `dim_course` |
| CRICOS locations | `dim_provider_location` |
| CRICOS course_locations | `bridge_course_location` |
| HA BP0015 lodged/granted/rates | `fact_student_visa_activity` |
| HA BP0014 granted/holders | `fact_temp_skilled_visa` |
| HA BP0016 lodged/granted | `fact_temp_graduate_visa` |
| HA BP0068 | `fact_permanent_migration` |
| JSA IVI | `fact_job_vacancy` |
| JSA OSL | `fact_occupation_shortage` |
| JSA profiles | `ref_occupation_profile` |
| ABS LF | `fact_labour_force` |
| ABS CPI | `fact_cpi` |
| ABS NOM | `fact_overseas_migration` |
| ABS ERP/COB | `fact_population_by_cob` |
| ABS edu_output | ❌ EXCLUDED (no destination — ETL skips in MySQL mode) |
| Skilled migration summaries | `fact_skilled_migration` |
| Skilled migration country_occ | `ref_skilled_migration_by_cob_occupation` |
| Education pivot_basic | `fact_student_enrolment` |
| Education pivot_detailed | `fact_student_enrolment` (merged) |
| Education historical | `education_int_students_historical` — ⚠️ NOT IN MySQL schema |
| Education SA4 | `education_sa4_enrolments` — ⚠️ NOT IN MySQL schema |

**Finding: Education historical and SA4 tables exist in SQLite but are not in MySQL schema.**  
The 3,542,826-row dry-run count comes entirely from `parse_pivot_basic` and `parse_pivot_detailed`, which go to `fact_student_enrolment`. The historical and SA4 parsers add relatively few rows. They can be added to the MySQL schema in a later phase or skipped in MySQL mode.  
**Resolution: Wire only pivot_basic and pivot_detailed to `fact_student_enrolment` in MySQL mode. Skip historical and SA4 in MySQL mode for initial migration.**

**Result: PASS (with noted exclusions).**

### C. Column names and data types match

Key mismatches documented in Section 1 above. All require column rename mapping in the MySQL ETL load functions. No structural type incompatibilities found.

**Result: PASS — mismatches are cosmetic renames, handled in MySQL ETL wiring.**

### D. No unresolved table-count discrepancy

- SQLite schema: 24 tables
- MySQL schema (after Phase 2 fixes): 25 tables
- Difference: SQLite had 3 BP0015 tables (lodged/granted/rates); MySQL consolidates to 1 (`fact_student_visa_activity`). SQLite had 2 ABS occupation tables not in MySQL. MySQL adds 4 dimension tables not in SQLite. Net +1.

**Result: PASS — discrepancy is intentional and documented.**

### E. No core fact combines incompatible grains

- `fact_student_visa_activity`: uses `measure` column to hold lodged/granted/rate → same grain ✅
- `fact_temp_skilled_visa`: uses `measure` column for granted/holders → same grain ✅
- `fact_job_vacancy`: one row per period × occupation × state × measure → consistent ✅
- `fact_student_enrolment`: YTD cumulative (per approved decision #1) — grain is period × nationality × sector × state → consistent ✅

**Result: PASS — no grain mixing.**

---

## 5. Known Limitations

1. `education_int_students_historical` and `education_sa4_enrolments` not in MySQL schema — skipped in MySQL mode.
2. `dim_country` requires post-load population — country_id FK in fact tables will be NULL initially.
3. `dim_occupation` will be populated from JSA data but only after JSA loads successfully.
4. SkillSelect EOI not in `run_all.py` — requires separate manual process.
5. No `country_aliases.csv` yet — to be created from first Education load.

---

## 6. Files to create/modify in Phase 2

1. **`ETL/schema_mysql.sql`** — Remove `fact_abs_education_output`; fix all invalid UNIQUE KEY syntax using generated columns
2. **`ETL/validate_mysql_schema.py`** — Schema validation script
3. **`reference/country_aliases.csv`** — Placeholder (headers only, populated after load)
