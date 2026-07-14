# AIC Market Intelligence — Tableau Data Dictionary

**Database:** `aic_market_intelligence` (MySQL 8, InnoDB, utf8mb4)
**Generated:** 2026-07-15 (live inspection, read-only — no data or schema modified)
**Scope:** All 25 base tables + 1 view currently in the production MySQL database.

> This document is the companion to `docs/final_migration_summary.md` (migration
> history, bug fixes, known limitations). This document is specifically for
> **Tableau development** — what to connect to, how objects relate, and how to
> lay out dashboards.

---

## How to read the inventory

For each object:
- **Grain** — what one row represents (derived from its actual `UNIQUE KEY`,
  verified live, not assumed from naming).
- **Foreign keys** — MySQL has **no enforced FK constraints** anywhere in this
  schema (verified via `information_schema.KEY_COLUMN_USAGE` — zero rows). All
  relationships below are **logical/convention-based**: an `*_id` or
  `*_code` column that matches a dimension's key by naming convention and ETL
  design, populated post-load. Build these as **relationships** in Tableau
  (not assumed referential integrity).
- **Tableau suitability** — ✅ direct use, ⚠️ direct use with a caveat (long/EAV
  shape, needs a `measure` filter, or is currently empty), 🚫 not for
  dashboard use (operational table), or 🔗 view-only entry point.

---

## 1. Dimension Tables

### `dim_country` — 25 rows
| Column | Type | Notes |
|---|---|---|
| `country_id` | `smallint unsigned` | **PK**, auto-increment |
| `canonical_name` | `varchar(150)` | **Unique.** Standard country name used across the model |
| `iso_alpha2` / `iso_alpha3` | `char(2)` / `char(3)` | ISO codes |
| `name_education` / `name_home_affairs` / `name_abs` / `name_skilled_mig` | `varchar(150)` | Per-source name aliases, used to join raw source country/nationality text back to `canonical_name` |
| `is_active` | `tinyint(1)` | |

- **Grain:** one row per canonical country.
- **FKs (logical):** referenced by `country_id` in `fact_overseas_migration`, `fact_population_by_cob`, `fact_student_enrolment`, `fact_permanent_migration`, `fact_temp_graduate_visa`, `fact_temp_skilled_visa`, `ref_skilled_migration_by_cob_occupation`.
- **Business description:** Canonical country reference list with per-source name aliases, used to unify country-of-birth/nationality naming across every fact table.
- **Tableau suitability:** ✅ Direct use as a dimension. **Caveat:** only 25 curated countries are populated (the top source countries) — `country_id` is `NULL` on the majority of rows in large fact tables (e.g. ~64% of `fact_student_enrolment`). Always keep the raw text column (`nationality`, `country_name`) available alongside `country_id` so non-matched countries aren't silently dropped from a dashboard.

### `dim_state` — 9 rows
| Column | Type | Notes |
|---|---|---|
| `state_id` | `tinyint unsigned` | **PK** |
| `state_code` | `varchar(3)` | **Unique.** `NSW, VIC, QLD, SA, WA, TAS, NT, ACT, AUS` (`AUS` = national aggregate) |
| `state_name` | `varchar(50)` | |
| `is_territory` | `tinyint(1)` | |

- **Grain:** one row per state/territory (+ 1 national pseudo-row).
- **FKs (logical):** referenced by `state_code` in nearly every fact/dim table (not a declared FK — matched by 3-letter code convention).
- **Business description:** Australian state/territory reference list, including a national "AUS" aggregate row.
- **Tableau suitability:** ✅ Direct use.

### `dim_visa_subclass` — 11 rows
| Column | Type | Notes |
|---|---|---|
| `visa_id` | `smallint unsigned` | **PK** |
| `subclass_code` | `varchar(10)` | **Unique.** e.g. `500`, `485`, `482`, `189`, `190`, `186`, `491`, `494`, `407`, `408`, `600` |
| `visa_name` | `varchar(200)` | |
| `visa_category` | `varchar(50)` | Student / Skilled / Graduate / Family / Humanitarian / etc. |
| `is_temporary` / `is_permanent` | `tinyint(1)` | |

- **Grain:** one row per visa subclass code.
- **FKs (logical):** referenced by `visa_subclass` in `fact_temp_skilled_visa`, `fact_skilled_migration`, `ref_skilled_migration_by_cob_occupation`.
- **Business description:** Visa subclass reference list with temporary/permanent and category classification.
- **Tableau suitability:** ✅ Direct use.

### `dim_provider` — 1,544 rows
| Column | Type | Notes |
|---|---|---|
| `provider_id` | `varchar(10)` | **PK** (CRICOS provider code, natural key) |
| `provider_name` | `varchar(250)` | |
| `provider_type` | `varchar(60)` | University, TAFE, English Language, School, etc. |
| `state_code` | `varchar(3)` | |
| `website`, `registration_status`, `registration_end_date` | | |

- **Grain:** one row per CRICOS-registered education provider (institution).
- **FKs (logical):** referenced by `provider_id` in `dim_course`, `dim_provider_location`, `bridge_course_location`.
- **Business description:** CRICOS-registered education providers (institutions) operating in Australia.
- **Tableau suitability:** ✅ Direct use.

### `dim_course` — 26,448 rows
| Column | Type | Notes |
|---|---|---|
| `cricos_code` | `varchar(10)` | **PK** (natural key) |
| `course_name` | `varchar(350)` | |
| `field_of_education`, `broad_field` | `varchar` | |
| `duration_weeks` | `decimal(6,1)` | |
| `min_age` | `tinyint unsigned` | |
| `annual_fees_aud` | `decimal(10,2)` | |
| `provider_id` | `varchar(10)` | logical FK → `dim_provider` |

- **Grain:** one row per CRICOS-registered course.
- **Business description:** CRICOS-registered courses, with field of education, duration, fees and delivering provider.
- **Tableau suitability:** ✅ Direct use. High cardinality (26K) — best filtered by `broad_field`/`provider_id` rather than browsed raw.

### `dim_provider_location` — 3,887 rows
| Column | Type | Notes |
|---|---|---|
| `location_id` | `varchar(20)` | **PK.** ⚠️ Synthesized (deterministic hash of provider_id + location_name) — the source CRICOS export has no natural location ID |
| `provider_id` | `varchar(10)` | logical FK → `dim_provider` |
| `location_name`, `address`, `suburb`, `postcode` | | |
| `state_code` | `varchar(3)` | |

- **Grain:** one row per physical CRICOS campus/location.
- **Business description:** Physical campus/delivery locations operated by CRICOS providers.
- **Tableau suitability:** ✅ Direct use.

### `dim_occupation` — 0 rows ⚠️
| Column | Type | Notes |
|---|---|---|
| `occupation_id` | `int unsigned` | **PK** |
| `anzsco_code` | `varchar(8)` | **Unique with** `anzsco_level` |
| `anzsco_level`, `occupation_name`, `major_group_code`...`unit_group_code`, `anzsco_skill_level` | | |

- **Grain (intended):** one row per (ANZSCO code, ANZSCO level).
- **Business description:** Intended ANZSCO occupation reference dimension; **not yet populated** (no ETL step currently derives it — unlike `dim_country`, there is no small curated source file to build it from).
- **Tableau suitability:** 🚫 **Not usable — empty.** Every `occupation_id` FK column across the model is `NULL`. Use the occupation code/name columns that already live directly on each fact/ref table instead (`anzsco_code`, `occupation_name`).

---

## 2. Fact Tables

### `fact_exchange_rate` — 20,728 rows
| Column | Type |
|---|---|
| `id` PK, `rate_date` DATE, `series_id` VARCHAR(12), `currency_pair`, `units`, `frequency`, `value` DECIMAL(14,6), `source_table` (`f11`/`f11.1`) |

- **Grain:** one row per (`rate_date`, `series_id`).
- **Business description:** RBA daily/monthly exchange rate series (F11 monthly, F11.1 daily) by currency and date.
- **Tableau suitability:** ✅ Direct use. Wide-ish (one series per row) — filter on `series_id` (e.g. `FXRUSD`) for a single currency trend.

### `fact_labour_force` — 66,120 rows
| Column | Type |
|---|---|
| `id` PK, `lf_period` VARCHAR(7) `YYYY-MM`, `series_id`, `measure` (descriptive label, not a key), `sex`, `adjustment_type`, `state_code`, `value` DECIMAL(12,3), `unit` |

- **Grain:** one row per (`lf_period`, `series_id`).
- **Business description:** ABS Labour Force series (employment, participation, unemployment, etc.) by month and demographic/adjustment breakdown.
- **Tableau suitability:** ⚠️ Direct use, but long-format — filter/pivot on `adjustment_type` (Trend/Seasonally Adjusted/Original) and `sex` before charting a single trend line.

### `fact_cpi` — 585 rows
| Column | Type |
|---|---|
| `id` PK, `cpi_period` VARCHAR(7), `series_id`, `title`, `cpi_group`, `city`, `measure`, `value` DECIMAL(10,3) |

- **Grain:** one row per (`cpi_period`, `series_id`).
- **Business description:** ABS Consumer Price Index by quarter/city/expenditure group.
- **Tableau suitability:** ✅ Direct use.

### `fact_overseas_migration` — 42,504 rows
| Column | Type |
|---|---|
| `id` PK, `nom_period` VARCHAR(7), `country_name`, `country_id` (logical FK), `state_code`, `direction` (`net`/`arrivals`/`departures`), `series_id`, `value` DECIMAL(12,1) |

- **Grain:** one row per (`nom_period`, `country_name`, `state_code`, `direction`).
- **Business description:** ABS Net Overseas Migration counts by country of birth, state and financial year, split by net/arrivals/departures.
- **Tableau suitability:** ✅ Direct use. Filter `direction='net'` for the headline NOM measure.

### `fact_population_by_cob` — 7,770 rows
| Column | Type |
|---|---|
| `id` PK, `erp_period` VARCHAR(7), `country_name`, `country_id` (logical FK), `state_code`, `series_id`, `population` DECIMAL(14,1) |

- **Grain:** one row per (`erp_period`, `country_name`, `state_code`).
- **Business description:** ABS Estimated Resident Population by country of birth and year (Australia-wide only — no state breakdown in the source).
- **Tableau suitability:** ✅ Direct use.

### `fact_job_vacancy` — 511,560 rows
| Column | Type |
|---|---|
| `id` PK, `vacancy_period` VARCHAR(7), `anzsco_code`, `anzsco_level` (2 or 4 digit), `state_code`, `measure` (`Seasonally Adjusted` / `Seasonally Adjusted Index` / `Trend` / `Trend Index`), `vacancy_count` INT |

- **Grain:** one row per (`vacancy_period`, `anzsco_code`, `state_code`, `measure`).
- **Business description:** JSA Internet Vacancy Index counts by month, occupation (major-group level) and state.
- **Tableau suitability:** ⚠️ Direct use — must filter `measure='Seasonally Adjusted'` (or `'Trend'`) to avoid double-counting the paired `*Index` rows. ANZSCO codes here are **2-digit major group**, coarser than the 6-digit codes in `fact_occupation_shortage`/`ref_occupation_profile` — do not join directly without truncating (`LEFT(anzsco_code, 2)`).

### `fact_occupation_shortage` — 916 rows
| Column | Type |
|---|---|
| `id` PK, `anzsco_code` VARCHAR(8) (6-digit), `anzsco_level`, `occupation_name`, `shortage_status` (`NS`/`S`/`R`/`M`), `osca_category`, `assessment_year`, `state_code` |

- **Grain:** one row per (`anzsco_code`, `anzsco_level`, `state_code`, `assessment_year`).
- **Business description:** JSA Occupation Shortage List rating per 6-digit occupation, state and assessment year.
- **Tableau suitability:** ✅ Direct use, but prefer `vw_occupation_intelligence` (below) which already enriches this table.

### `ref_occupation_profile` — 50,674 rows
| Column | Type |
|---|---|
| `id` PK, `anzsco_code`, `profile_measure` (source sheet name `Table_1`..`Table_8` — **not descriptive**), `dimension` (the real descriptive label, e.g. `median_full_time_earnings_per_week`, `employed`), `value_num` (always NULL in this dataset), `value_text` (actual values live here), `profile_year` |

- **Grain:** one row per (`anzsco_code`, `profile_measure`, `dimension`, `profile_year`).
- **Business description:** JSA occupation profile attributes (earnings, employment size, demographics, education requirements) — one attribute per row.
- **Tableau suitability:** ⚠️ Direct use possible but awkward (EAV shape, real value in `value_text` as a string, `profile_measure` is meaningless without decoding `dimension`). **Recommend accessing via `vw_occupation_intelligence`** for the two attributes already extracted (salary, employment size); use this table directly only for exploratory/ad-hoc analysis of other `dimension` values.

### `fact_skilled_migration` — 231 rows
| Column | Type |
|---|---|
| `id` PK, `financial_year` VARCHAR(30), `visa_subclass`, `stream`, `state_code`, `measure`, `value` DECIMAL(14,2) |

- **Grain:** one row per (`financial_year`, `visa_subclass`, `stream`, `state_code`, `measure`).
- **Business description:** Home Affairs Skilled Migration Programme grant counts by financial year, visa subclass/stream and state.
- **Tableau suitability:** ✅ Direct use.

### `ref_skilled_migration_by_cob_occupation` — 4,662 rows
| Column | Type |
|---|---|
| `id` PK, `financial_year`, `country_name`, `country_id` (logical FK), `anzsco_code`, `occupation_name`, `visa_subclass`, `measure`, `value` DECIMAL(14,2) |

- **Grain:** one row per (`financial_year`, `country_name`, `anzsco_code`, `visa_subclass`, `measure`).
- **Business description:** Skilled migration grants cross-tabulated by country of birth and ANZSCO occupation.
- **Tableau suitability:** ✅ Direct use.

### `fact_student_visa_activity` — 880 rows
| Column | Type |
|---|---|
| `id` PK, `applicant_type`, `sector`, `financial_year` VARCHAR(30), `measure` (`lodged`/`granted`/`grant_rate_pct`), `value` DECIMAL(12,2) |

- **Grain:** one row per (`applicant_type`, `sector`, `financial_year`, `measure`).
- **Business description:** Home Affairs BP0015 student visa lodged/granted counts and grant rate, by applicant type and education sector.
- **Tableau suitability:** ✅ Direct use. Filter `measure` to pick lodged vs. granted vs. rate.

### `fact_temp_skilled_visa` — 0 rows ⚠️
### `fact_temp_graduate_visa` — 0 rows ⚠️
### `fact_permanent_migration` — 0 rows ⚠️

All three share the same status: schema is complete and correct (grain, keys, columns all verified), but **currently empty**. Root cause: their Home Affairs source files (BP0014, BP0016, BP0068) are genuine Excel **PivotTable exports** (stacked filter fields, no flat header row) — a fundamentally different, currently-unsupported format from BP0015's plain table. This predates the MySQL migration; see `docs/final_migration_summary.md` for detail.

- **Tableau suitability:** 🚫 **Not usable today — empty.** Do not wire into a dashboard until populated; if built now, any chart against these tables will render blank with no error, which is worse than a missing connection.

### `fact_student_enrolment` — 3,542,826 rows
| Column | Type |
|---|---|
| `id` PK, `enrol_year` SMALLINT, `enrol_month` TINYINT (1-12), `nationality` VARCHAR(200), `country_id` (logical FK, ~35% populated), `state_code`, `sector`, `provider_type`, `new_to_australia` (`Yes`/`No`), `ends_this_year` (`Yes`/`No`), `ytd_enrolments`, `ytd_commencements`, `total` |

- **Grain:** one row per (`enrol_year`, `enrol_month`, `nationality`, `state_code`, `sector`, `provider_type`, `new_to_australia`, `ends_this_year`).
- **Business description:** Department of Education international student enrolment counts, **YTD cumulative** (not monthly incremental) by year/month/nationality/state/sector.
- **Tableau suitability:** ✅ Direct use — but **critical caveat**: values are YTD-cumulative. For a point-in-time "how many students this year" figure, filter to the **latest `enrol_month` for each `enrol_year`** — do not `SUM(ytd_enrolments)` across months within the same year, or totals will be wildly overstated. By far the largest table (3.5M rows) — always filter by year/month range before rendering.

---

## 3. Bridge, Staging, Audit

### `bridge_course_location` — 46,848 rows
- **Grain:** one row per (`cricos_code`, `location_id`).
- **Business description:** Many-to-many link between CRICOS courses and the physical locations where they're delivered.
- **Tableau suitability:** ✅ Direct use as a bridge table between `dim_course` and `dim_provider_location` (e.g. "which locations offer this course").

### `stg_skillselect_eoi` — 0 rows ⚠️
- **Grain (intended):** one row per (`as_at_month`, `visa_type`, `eoi_status`, `source_view`, dimension values).
- **Business description:** Staging table for SkillSelect Expression of Interest data, captured by a separate Playwright-based script (`ETL/skillselect_csv_etl.py`), not part of the 7-source pipeline.
- **Tableau suitability:** 🚫 Not usable — empty by design (extraction not yet considered stable; explicitly staging-tier, not promoted to a fact table).

### `etl_audit_log` — 62 rows
- **Grain:** one row per ETL run attempt, per source × target table.
- **Business description:** Operational log of every ETL run (rows read/inserted/updated/rejected, status, error message) — pipeline monitoring, not business data.
- **Tableau suitability:** 🚫 Not for dashboards. Useful only for an internal ops/pipeline-health view, if ever needed, kept separate from business dashboards.

---

## 4. View

### `vw_occupation_intelligence` — 916 rows 🔗
```sql
anzsco_code, occupation_name, shortage_status, osca_category, assessment_year,
state_code, latest_vacancies, vacancy_as_at, median_annual_salary_aud, employment_size
```

- **Grain:** one row per (`anzsco_code`, `state_code`, `assessment_year`) — same grain as `fact_occupation_shortage`, left-joined with enrichment.
- **Business description:** Derived occupation-level mart joining shortage status with the latest job vacancy count, median annual salary, and employment size for each occupation/state — the single-table entry point for occupation-level analysis.
- **Tableau suitability:** ✅ **Primary recommended entry point for occupation-level dashboards.**
  - **Known gaps (documented, not defects to "debug" in Tableau):** `latest_vacancies`/`vacancy_as_at` are `NULL` for all 916 rows (the view's vacancy join needs a verified-but-unapplied ANZSCO-level fix, `LEFT(anzsco_code,2)` — see `docs/final_migration_summary.md`); `median_annual_salary_aud` is `NULL` for all rows (source data genuinely suppressed at this occupation grain, not a query defect). `employment_size` is populated for 722/916 rows and is reliable.
  - Cannot be `CREATE OR REPLACE`'d by the app user (`aic_user` has no `DROP`) — any future view change needs an admin-run statement.

---

## 5. Tableau Object Recommendations

### Use as Tableau **dimensions**
`dim_country`, `dim_state`, `dim_visa_subclass`, `dim_provider`, `dim_course`, `dim_provider_location`.
(`dim_occupation` excluded — empty; use the occupation code/name columns embedded directly in fact tables instead.)

### Use as Tableau **facts**
`fact_student_enrolment`, `fact_exchange_rate`, `fact_labour_force`, `fact_cpi`,
`fact_overseas_migration`, `fact_population_by_cob`, `fact_job_vacancy`,
`fact_occupation_shortage`, `fact_skilled_migration`, `fact_student_visa_activity`,
`ref_skilled_migration_by_cob_occupation`, `ref_occupation_profile`, `bridge_course_location`.
(`fact_temp_skilled_visa`, `fact_temp_graduate_visa`, `fact_permanent_migration` excluded — currently empty.)

### Primary Tableau **data sources** (views/entry points)
1. **`vw_occupation_intelligence`** — primary entry point for any occupation-shortage/vacancy dashboard.
2. **`fact_student_enrolment`** joined to **`dim_country`** — primary entry point for the international-student market dashboard (largest, most requested dataset).
3. **`fact_student_visa_activity` + `fact_skilled_migration` + `ref_skilled_migration_by_cob_occupation`** — primary entry points for the visa/migration pathway dashboard.
4. **`dim_provider` + `dim_course` + `dim_provider_location` + `bridge_course_location`** — primary entry point for the CRICOS provider/course landscape dashboard.

---

## 6. Tableau Data Source Guide

**Connection**
- Server: value of `MYSQL_HOST` in `.env` (currently `127.0.0.1`), Port: `MYSQL_PORT` (`3306`)
- Database: `aic_market_intelligence`
- User: `aic_user` (read access confirmed — `SELECT` is part of its least-privilege grant)
- Connection type: **Live connection recommended** during dashboard development (data volumes are moderate except `fact_student_enrolment`); switch to an **extract** for the enrolment table specifically once dashboards stabilize, to avoid repeatedly scanning 3.5M rows.

**Recommended data sources to build in Tableau** (one per dashboard, not one giant model):

| Data source name | Tables (join) | Used by |
|---|---|---|
| `DS_Occupation_Intelligence` | `vw_occupation_intelligence` (single table) | Occupation & Skills Shortage dashboard |
| `DS_Vacancy_Trend` | `fact_job_vacancy` + `dim_state` | Vacancy trend drill-down |
| `DS_Student_Enrolment` | `fact_student_enrolment` + `dim_country` (left join on `country_id`) | International Student Market dashboard |
| `DS_Visa_Pathways` | `fact_student_visa_activity`, `fact_skilled_migration`, `ref_skilled_migration_by_cob_occupation` (blended, not joined — different grains), + `dim_visa_subclass`, `dim_country` | Visa & Migration Pathways dashboard |
| `DS_CRICOS_Landscape` | `dim_provider` + `dim_course` + `bridge_course_location` + `dim_provider_location` | Provider/Course Landscape dashboard |
| `DS_Macro_Context` | `fact_exchange_rate`, `fact_labour_force`, `fact_cpi`, `fact_overseas_migration`, `fact_population_by_cob` (blended by period, not joined) | Macroeconomic Context dashboard |

**General rules for every source built above:**
- Always add a **filter on `measure`** (or `direction`, `adjustment_type`) as the first step for any long/EAV-shaped fact table — never aggregate across mixed measures.
- Always filter `fact_student_enrolment` to a specific `enrol_month` per `enrol_year` before summing — it is YTD cumulative.
- Treat `country_id`/`occupation_id` joins as **left joins**, never inner — most rows will not match the small curated `dim_country` list, and `dim_occupation` is empty. An inner join would silently drop the majority of rows.

---

## 7. Recommended Relationship Model

Use Tableau **relationships** (not joins) for the primary data model, since grains genuinely differ across fact tables (this is a real star-ish schema, not one flat table) — relationships let each sheet aggregate at its own natural level without fan-out.

```
                         dim_country ─────────────┐
                        (country_id)              │ (left, ~25-35% match rate)
                                                   │
dim_state ──────┬── fact_student_enrolment ────────┘
(state_code)    │
                ├── fact_job_vacancy
                ├── fact_occupation_shortage ── vw_occupation_intelligence (derived, use this instead)
                ├── fact_labour_force
                ├── fact_overseas_migration ──── dim_country
                ├── fact_population_by_cob ───── dim_country
                ├── fact_skilled_migration
                └── fact_student_visa_activity

dim_visa_subclass (subclass_code) ── fact_skilled_migration.visa_subclass
                                   └─ ref_skilled_migration_by_cob_occupation.visa_subclass

dim_provider (provider_id) ── dim_course.provider_id
                            └─ dim_provider_location.provider_id
                                        │
                    bridge_course_location (cricos_code, location_id)
                                        │
                            dim_course.cricos_code
```

- **Hub dimensions:** `dim_state` (relates to almost every fact table via `state_code`) and `dim_country` (relates via `country_id`, left/optional).
- **`vw_occupation_intelligence`** stands alone as a pre-joined mart — do not also relate `fact_occupation_shortage` into the same worksheet, to avoid double-counting.
- **CRICOS cluster** (`dim_provider` / `dim_course` / `dim_provider_location` / `bridge_course_location`) is its own self-contained star, unrelated to the rest of the model — keep it in a separate data source.
- **Cross-fact analysis** (e.g. enrolments vs. exchange rate, per the original test-plan cross-source queries) should be done at the **period/year grain only**, via a blend or a calculated relationship on year — these fact tables have no shared natural key otherwise.

---

## 8. Dashboard Design Recommendation

Matching the project's stated objective (external market intelligence for
occupation demand, visa pathways, and the international education market),
five focused dashboards, each backed by one of the data sources above:

### 1. Occupation & Skills Shortage Intelligence
**Source:** `DS_Occupation_Intelligence` (`vw_occupation_intelligence`) + `DS_Vacancy_Trend`
- Map/heatmap: shortage status (`NS`/`S`/`R`/`M`) by state and occupation.
- Trend: vacancy counts over time per occupation (from `fact_job_vacancy`, joined at the 2-digit major-group level as a supporting sheet — note the granularity caveat above).
- Table: occupations with employment size, filterable by shortage status.
- **Caveat banner:** median salary is not currently available (source suppression) — omit or clearly label as "not available" rather than showing blank.

### 2. International Student Market Overview
**Source:** `DS_Student_Enrolment`
- KPI tiles: total enrolments (latest month of latest year, **not summed across months**), YoY change.
- Bar/map: enrolments by nationality (top 10) and by state.
- Line: enrolment trend by sector (Higher Ed / VET / ELICOS / Schools / Non-award) over years.
- Secondary context strip: `fact_exchange_rate` (AUD/USD) on the same year axis, to visually correlate currency movement with enrolment growth (mirrors the validated cross-source query from the migration test plan).

### 3. Visa & Migration Pathways
**Source:** `DS_Visa_Pathways`
- Student visa funnel: lodged → granted → grant rate (`fact_student_visa_activity`, filtered by `measure`).
- Skilled migration by visa subclass/stream/state (`fact_skilled_migration`).
- Country × occupation matrix for skilled migration grants (`ref_skilled_migration_by_cob_occupation`).
- **Explicitly omit** temporary skilled/graduate visa and permanent migration panels until `fact_temp_skilled_visa`/`fact_temp_graduate_visa`/`fact_permanent_migration` are populated — a placeholder note is better than a blank chart.

### 4. CRICOS Provider & Course Landscape
**Source:** `DS_CRICOS_Landscape`
- Map: provider locations by state.
- Table: courses by field of education, duration, fees, filterable by provider.
- Provider directory with course/location counts.

### 5. Macroeconomic Context (supporting reference dashboard)
**Source:** `DS_Macro_Context`
- Labour force and CPI trend lines.
- Net overseas migration by country of birth.
- Exchange rate trend.
- Designed as a secondary/reference dashboard that other dashboards' currency and labour-market callouts point back to, not a standalone deliverable.

---

## Appendix: Row counts at time of writing

| Table/View | Rows | Table/View | Rows |
|---|---:|---|---:|
| `fact_student_enrolment` | 3,542,826 | `dim_course` | 26,448 |
| `fact_job_vacancy` | 511,560 | `dim_provider_location` | 3,887 |
| `bridge_course_location` | 46,848 | `ref_skilled_migration_by_cob_occupation` | 4,662 |
| `ref_occupation_profile` | 50,674 | `dim_country` | 25 |
| `fact_labour_force` | 66,120 | `dim_state` | 9 |
| `fact_overseas_migration` | 42,504 | `dim_visa_subclass` | 11 |
| `fact_exchange_rate` | 20,728 | `etl_audit_log` | 62 |
| `dim_provider` | 1,544 | `fact_cpi` | 585 |
| `fact_population_by_cob` | 7,770 | `fact_skilled_migration` | 231 |
| `fact_student_visa_activity` | 880 | `vw_occupation_intelligence` | 916 |
| `fact_occupation_shortage` | 916 | `dim_occupation` | 0 |
| `fact_temp_skilled_visa` | 0 | `fact_temp_graduate_visa` | 0 |
| `fact_permanent_migration` | 0 | `stg_skillselect_eoi` | 0 |
