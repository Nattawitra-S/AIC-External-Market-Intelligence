# AIC ETL — Data Profile Report
**Generated from ETL source code analysis · July 2026**  
**Dry-run confirmed row counts from pipeline run**

---

## Summary Table

| Source | ETL Module | Table(s) Written | Rows (dry-run) | Schema Status |
|--------|-----------|-----------------|---------------|---------------|
| RBA Exchange Rates | etl_rba.py | rba_exchange_rates | 21,549 | ✅ Defined |
| CRICOS | etl_cricos.py | cricos_institutions, cricos_courses, cricos_locations, cricos_course_locations | 78,752 total | ✅ Defined |
| JSA IVI + OSL | etl_jsa.py | jsa_internet_vacancies, jsa_occupation_shortage, jsa_occupation_profiles | 611,280 total | ✅ Defined |
| Home Affairs | etl_home_affairs_extended.py | ha_student_visa_lodged/granted/grant_rates, ha_temp_skilled_visa_granted/holders, ha_temp_graduate_visa_lodged/granted, ha_migration_child_outcomes | 916 total | ✅ Defined |
| ABS | etl_abs.py | abs_labour_force, abs_cpi, abs_net_overseas_migration, abs_erp_country_of_birth, abs_education_output | 133,170 total | ✅ Defined (partial mismatch) |
| Department of Education | etl_education_v2.py | **education_enrolments** | **3,542,826** | ❌ **MISSING FROM SCHEMA** |
| Skilled Migration | etl_skilled_migration.py | skilled_migration_summary, skilled_migration_country_occupation | 15,040 total | ✅ Defined |
| SkillSelect EOI | skillselect_csv_etl.py | skillselect_eoi_data | manual/periodic | ✅ Defined |

**Tables in schema.sql with NO active ETL populating them:**
- `occupation_ceilings` — replaced by `skillselect_eoi_data`
- `occupation_shortage_ratings` — replaced by `jsa_occupation_shortage`
- `visa_eligibility` — no ETL written
- `occupation_intelligence` — no ETL written (derived mart concept, never implemented)
- `abs_employment_by_industry` — ETL exists but SDMX key `4..AUS` likely fails
- `abs_employment_by_occupation` — ETL exists but SDMX key `7+12..AUS` likely fails

---

## 1. RBA Exchange Rates

**Source:** Reserve Bank of Australia F11 (monthly) + F11.1 (daily from Jan 2023)  
**ETL:** `ETL/etl_rba.py` → `lib_etl.read_rba_csv()`  
**Table:** `rba_exchange_rates`  
**Row count:** ~21,549 (f11 ≈ 4,875 + f11.1 ≈ 16,674)

### Columns produced by ETL

| Column | Pandas dtype | Null risk | Notes |
|--------|-------------|-----------|-------|
| date | datetime64[ns] → TEXT "%Y-%m-%d" | None | Parsed dayfirst=True from "29-Jan-2010" |
| series_id | object | None | e.g. FXRUSD, FXRTWI, FXRCR |
| title | object | Low | e.g. "A$1=USD" — from metadata row |
| units | object | Low | USD, Index, CNY, JPY, etc. |
| frequency | object | Low | "Monthly" or "Daily" |
| value | float64 | ~5% (missing periods) | Exchange rate |
| source_table | object | None | "f11" or "f11.1" |
| _etl_source | object | None | "rba/f11-data.csv" |
| _etl_loaded_at | object | None | ISO 8601 timestamp |

### Grain
One row = **one date × one currency series** (FXRUSD, FXRTWI, FXRCR, etc.)

### Business / Natural Key
`(date, series_id)` — matches UNIQUE constraint in schema ✅

### Candidate Issues
- `date` stored as TEXT (string comparison not date arithmetic in SQLite)
- `frequency` column is redundant (always "Monthly" for f11, "Daily" for f11.1) — could be derived from `source_table`
- f11.1 data starts only from 2023-01-03, f11 from 2010-01-29

### Sample rows
```
date        series_id  title    units  frequency  value    source_table
2024-06-30  FXRUSD     A$1=USD  USD    Monthly    0.6676   f11
2024-06-30  FXRTWI     TWI      Index  Monthly    62.4     f11
2024-06-30  FXRCR      A$1=CNY  CNY    Monthly    4.8456   f11
2024-01-03  FXRUSD     A$1=USD  USD    Daily      0.6780   f11.1
```

---

## 2. CRICOS

**Source:** data.gov.au CKAN / cricos.education.gov.au  
**ETL:** `ETL/etl_cricos.py`  
**Row count:** ~78,752 total across 4 tables

### 2a. cricos_institutions

**Grain:** One row = one CRICOS-registered education provider  
**Business Key:** `provider_id` (CRICOS provider code, e.g. "00025B")

| Column | Dtype | Null risk |
|--------|-------|-----------|
| provider_id | object | Very low |
| provider_name | object | Low |
| provider_type | object | ~5% | (University, TAFE, Private Provider, etc.) |
| state | object | Low | NSW, VIC, QLD, etc. |
| website | object | ~15% |
| status | object | Low | "Registered" |
| registration_end_date | object | ~10% |

**Estimated rows:** ~5,000 providers

### 2b. cricos_courses

**Grain:** One row = one CRICOS course registration  
**Business Key:** `cricos_code` (e.g. "0101M", "063281F")

| Column | Dtype | Null risk |
|--------|-------|-----------|
| cricos_code | object | None |
| course_name | object | Very low |
| field_of_education | object | ~5% | ASCED code + name |
| broad_field | object | ~5% |
| duration_weeks | float64 | ~10% |
| min_age | float64 | ~50% | Often not specified |
| fees_aud | float64 | ~20% |
| provider_id | object | Very low |

**Estimated rows:** ~30,000 courses

### 2c. cricos_locations

**Grain:** One row = one campus/location for a provider  
**Business Key:** `location_id`

| Column | Dtype | Null risk |
|--------|-------|-----------|
| location_id | object | None |
| provider_id | object | None |
| location_name | object | ~5% |
| address | object | ~10% |
| suburb | object | ~10% |
| state | object | Very low |
| postcode | object | ~5% |

**Estimated rows:** ~6,000 locations

### 2d. cricos_course_locations

**Grain:** One row = one course offered at one location  
**Business Key:** `(cricos_code, location_id)`

| Column | Dtype | Null risk |
|--------|-------|-----------|
| cricos_code | object | None |
| location_id | object | None |
| provider_id | object | None |

**Estimated rows:** ~37,000 mappings

---

## 3. JSA — Internet Vacancies + Occupation Shortage

**Source:** Jobs and Skills Australia XLSX files  
**ETL:** `ETL/etl_jsa.py`  
**Row count:** 611,280 total

### 3a. jsa_internet_vacancies

**Grain:** One row = **period (month) × anzsco_code × state_territory × measure (SA/Trend/Original)**  
**Business Key:** `(period, anzsco_code, state_territory, measure)`

| Column | Dtype | Null risk | Notes |
|--------|-------|-----------|-------|
| period | object | None | "YYYY-MM" format (from wide columns like "Jan-05") |
| anzsco_code | object | ~5% | 2 or 4-digit ANZSCO |
| occupation_name | object | ~10% |
| anzsco_level | int64 | Low | 2 or 4 |
| state_territory | object | ~5% | NSW, VIC, AUS, etc. |
| measure | object | None | Sheet name: "SA", "Trend", "Original" |
| value | float64 | ~3% | Number of online job ads |

**Estimated rows:** ~600,000 (dominant source: ANZSCO4 × 8 states × ~15 years × 12 months × 3 measures)

### ⚠️ Duplicate risk
All three IVI files (ANZSCO4, ANZSCO2, Skill Level) write to the SAME table `jsa_internet_vacancies`. The UNIQUE key `(period, anzsco_code, state_territory, measure)` does separate them since ANZSCO2 and ANZSCO4 codes differ. However, ANZSCO2 codes (2-digit) overlap with start digits of ANZSCO4 codes — not identical strings but risk of confusion in analytics.

### 3b. jsa_occupation_shortage

**Grain:** One row = **anzsco_code × anzsco_level × state_territory × assessment_year**  
**Business Key:** `(anzsco_code, anzsco_level, state_territory, assessment_year)`

| Column | Dtype | Null risk | Notes |
|--------|-------|-----------|-------|
| anzsco_code | object | None | 4 or 6-digit |
| anzsco_level | int64 | None | 4 or 6 |
| occupation_name | object | Low |
| shortage_status | object | None | "Shortage", "No Shortage", "Regional Shortage" |
| osca_category | object | ~30% | Only in 6-digit file |
| assessment_year | object | ~10% | "2024", "2025", etc. |
| state_territory | object | ~20% | Some records are national-level |

### 3c. jsa_occupation_profiles

**Grain:** One row = **anzsco_code × measure (sheet) × dimension (column)**  
**Business Key:** `(anzsco_code, measure, dimension)`

| Column | Dtype | Null risk |
|--------|-------|-----------|
| anzsco_code | object | None |
| occupation_name | object | Low |
| measure | object | None | Sheet name (e.g. "Earnings", "Employment", "Education") |
| dimension | object | None | Column header |
| value | float64 | ~40% | Some cells are text |
| value_text | object | Low | String version of value |

### ⚠️ UNIQUE key risk
`(anzsco_code, measure, dimension)` — if column headers differ slightly between years of occupation profile data, reruns may create near-duplicate rows.

---

## 4. Home Affairs

**Source:** data.gov.au CKAN / XLSX files  
**ETL:** `ETL/etl_home_affairs_extended.py`  
**Row count:** ~916 total (all tables combined)

### Note on low row count
916 rows across 8 destination tables suggests most Home Affairs files either weren't found locally or parsers returned partial data. BP0015 (student visa) is most likely to have populated data; BP0014, BP0016, BP0068 may have been skipped for missing local files.

### 4a. ha_student_visa_lodged / ha_student_visa_granted / ha_student_visa_grant_rates (BP0015)

**Grain:** One row = **applicant_type × sector × financial_year**

| Column | Dtype | Null risk |
|--------|-------|-----------|
| applicant_type | object | Low | "Primary", "Secondary" |
| sector | object | Low | "Higher Education", "VET", "ELICOS", etc. |
| financial_year | object | None | "2014-15", "2015-16", etc. |
| lodged_count / granted_count / grant_rate_pct | float64 | Low |

### 4b. ha_temp_skilled_visa_granted (BP0014)

**Grain:** visa_subclass × nationality × financial_year

| Column | Dtype | Notes |
|--------|-------|-------|
| visa_subclass | object | "482", "186", etc. |
| nationality | object | Country name (not ISO) |
| financial_year | object |
| granted_count | float64 |

### 4c. ha_temp_graduate_visa_lodged/granted (BP0016)

**Grain:** stream × nationality × financial_year

| Column | Dtype | Notes |
|--------|-------|-------|
| stream | object | "Graduate Work Stream", "Post-Study Work Stream" |
| nationality | object |
| financial_year | object |
| lodged_count / granted_count | float64 |

### 4d. ha_migration_child_outcomes (BP0068)

**⚠️ NAMING ERROR:** BP0068 is the **Permanent Migration Program Report** (not specifically "child outcomes"). This table should be renamed.

**Grain:** visa_type × birth_country × outcome_measure × period

| Column | Dtype | Notes |
|--------|-------|-------|
| visa_type | object | "Skilled", "Family", "Humanitarian" |
| birth_country | object | Country name |
| outcome_measure | object | Column header from each sheet |
| period | object | Year or period string |
| value | float64 |

---

## 5. ABS

**Source:** ABS SDMX REST API + local XLSX  
**ETL:** `ETL/etl_abs.py`  
**Row count:** ~133,170 total

### 5a. abs_labour_force

**Grain:** period × measure × sex × adjustment_type × state  
**Business Key:** `(period, measure, sex, adjustment_type, state)` — risky if `measure` text varies

| Column | Dtype | Notes |
|--------|-------|-------|
| period | object | "YYYY-MM" |
| frequency | object | "Monthly" |
| measure | object | Long descriptive string — UNIQUE key risk |
| sex | object | "Persons", "Males", "Females" |
| adjustment_type | object | "Trend", "Seasonally Adjusted", "Original" |
| state | object | "AUS" or state code |
| value | float64 | '000 persons |
| unit | object | "'000" |
| series_id | object | ABS series ID string |

### 5b. abs_cpi

**Grain:** period × series_id  
**Business Key:** `(period, series_id)` ✅

| Column | Dtype | Notes |
|--------|-------|-------|
| period | object | "YYYY-QN" e.g. "2024-Q1" |
| series_id | object | ABS CPI series code |
| title | object | CPI group description |
| group_ | object | Expenditure class |
| city | object | "All Groups" or city name |
| measure | object | "Index" |
| value | float64 | CPI index number |

### 5c. abs_net_overseas_migration

**Grain:** period × country_of_birth × state_territory × direction

| Column | Dtype | Notes |
|--------|-------|-------|
| period | object | "YYYY" or "YYYY-MM" |
| country_of_birth | object | From title string or "Unknown" |
| state_territory | object | "AUS" (limited geographic detail from API) |
| direction | object | "net", "arrivals", "departures" |
| value | float64 | Persons |
| series_id | object |

### 5d. abs_erp_country_of_birth

**Grain:** period × country_of_birth × state_territory

| Column | Dtype | Notes |
|--------|-------|-------|
| period | object | Annual "YYYY-MM" |
| country_of_birth | object | From title string or "Unknown" |
| state_territory | object | "AUS" (AUS-level aggregates from API) |
| value | float64 | Estimated resident population |
| series_id | object |

### ⚠️ ABS column quality note
The ABS SDMX API returns structured dimension columns (sex, region, adjustment_type) but the local XLSX fallback path uses `_parse_abs_xlsx_generic()` which cannot reliably extract these dimensions — it puts them all into `title` and `series_id`. This creates a mismatch between API-sourced and XLSX-sourced rows in the same table.

### Tables in schema with no confirmed data (abs_employment_by_industry, abs_employment_by_occupation)
ETL code exists but SDMX keys `4..AUS` and `7+12..AUS` use structure-specific notation that may not match the actual LF dataflow structure. These tables likely have 0 rows from the current dry-run's 133,170 total.

---

## 6. Department of Education (CRITICAL)

**Source:** Pre-extracted flat file from `raw_data/Zz Extracted files/`  
**ETL:** `ETL/etl_education_v2.py` → `parse_pivot_basic()`  
**Table written:** `education_enrolments`  
**Row count:** **3,542,826**

### ❌ CRITICAL: Table NOT in schema.sql
`education_enrolments` is created dynamically by pandas `to_sql(if_exists="ignore")` in `upsert_df()`. It has:
- No proper DDL definition
- **No UNIQUE constraint** → every rerun appends duplicates
- No indexes → table scans on 3.5M rows
- No `month` column indexing

### Columns produced by parser

| Column | Dtype | Null risk | Notes |
|--------|-------|-----------|-------|
| year | float64 | Low | 4-digit year (2024, 2025, etc.) |
| month | float64 | Low | 1-12 |
| nationality | object | Low | Country name string (free text) |
| state | object | Low | NSW, VIC, QLD, etc. |
| sector | object | Low | Higher Education, VET, ELICOS, Schools, NPOS |
| provider_type | object | ~10% | Renamed from "providertype" |
| new_to_australia | object | ~30% | "Yes"/"No" flag |
| ends_this_year | object | ~30% | "Yes"/"No" flag |
| data_ytd_enrolments | float64 | ~5% |
| data_ytd_commencements | float64 | ~5% |
| total | float64 | ~5% |

### Grain
One row = **year × month × nationality × state × sector × provider_type × new_to_australia × ends_this_year**

This is a **YTD cumulative table** (Year-To-Date), not a snapshot per period. Each month replaces the YTD total. This must be treated carefully in analytics — you can't sum across months.

### Candidate Business Key
`(year, month, nationality, state, sector, provider_type, new_to_australia, ends_this_year)` — very wide key, 8 columns.

### Key Issues
1. No UNIQUE constraint → duplicates on every re-ETL run
2. `nationality` is free-text country name — doesn't match Home Affairs/ABS country names
3. YTD nature means only the latest month of each year is analytically useful
4. `new_to_australia` and `ends_this_year` are Yes/No strings, not booleans
5. 3.5M rows × `executemany()` insert will be very slow without bulk load

---

## 7. Skilled Migration

**Source:** XLSX reports from `raw_data/skilled_migration/`  
**ETL:** `ETL/etl_skilled_migration.py`  
**Row count:** ~15,040 total

### 7a. skilled_migration_summary

**Grain:** financial_year × visa_subclass × stream × state_territory × measure

| Column | Dtype | Null risk | Notes |
|--------|-------|-----------|-------|
| financial_year | object | None | "2019-20", etc. |
| visa_subclass | object | ~20% | "189", "190", "491" |
| stream | object | ~30% | "Independent", "State Nominated" |
| state_territory | object | ~20% |
| measure | object | None | Sheet name |
| value | float64 | Low |

### 7b. skilled_migration_country_occupation

**Grain:** financial_year × country_of_birth × anzsco_code × visa_subclass × measure

| Column | Dtype | Null risk | Notes |
|--------|-------|-----------|-------|
| financial_year | object | None |
| country_of_birth | object | Low | Country name |
| anzsco_code | object | ~15% |
| occupation_name | object | ~15% |
| visa_subclass | object | ~20% |
| value | float64 | Low |
| measure | object | None | Sheet name |

### ⚠️ Grain mixing risk
`skilled_migration_summary` receives data from both:
1. `skilled_visas_summaries.xlsx` (aggregated, FY totals by stream/state)
2. `skilled_visas_raw_all_1.4M_rows.csv` (record-level data, loaded if `--skip-raw` not set)

These have different grains and should NOT be in the same table.

---

## 8. SkillSelect EOI (Periodic / Manual)

**Source:** SkillSelect Qlik Sense via Playwright automation  
**ETL:** `ETL/skillselect_csv_etl.py`  
**Table:** `skillselect_eoi_data`

### Columns

| Column | Dtype | Notes |
|--------|-------|-------|
| as_at_month | object | "06/2026" |
| visa_type | object | "189", "190", "491", "All" |
| eoi_status | object | "Submitted", "Active", "All" |
| source_view | object | "Occupations_Points", etc. |
| dimension_1_name | object | "Occupations" |
| dimension_1_val | object | "Software Engineer (261313)" |
| dimension_2_name | object | "Points" |
| dimension_2_val | object | "65-69" |
| eoi_count | int | Count of EOIs |
| captured_at | object | ISO timestamp |

**Grain:** as_at_month × visa_type × eoi_status × source_view × dim1_val × dim2_val

---

## Cross-Source Issues

### Country name inconsistency
Different sources use different country name formats:

| Source | Example |
|--------|---------|
| Education dept | "China (People's Republic of)" |
| ABS NOM/ERP | "China (excl. SARs and Taiwan)" |
| Home Affairs | "China" or "People's Republic of China" |
| Skilled Migration | "China PR" |
| CRICOS | (no country data) |

**No shared country dimension** — analytics requiring cross-source country aggregation will need manual mapping.

### Occupation code consistency
- JSA IVI uses 4-digit ANZSCO (Unit Group)
- JSA OSL uses both 4-digit and 6-digit ANZSCO
- Skilled Migration uses ANZSCO codes from reports (4 or 6-digit)
- Education has no occupation data
- Home Affairs has no occupation data

### Financial year vs calendar year vs month
- Home Affairs: financial_year TEXT "2019-20"
- ABS: period TEXT "2024-03" or "2024-Q1"
- RBA: date DATE "2024-06-30"
- Education: year INTEGER + month INTEGER
- JSA IVI: period TEXT from column headers (variable format)
- Skilled Migration: financial_year TEXT "2019-20"
