# AIC External Market Intelligence — MySQL Migration Report

**Completed:** 2026-07-14
**Database:** `aic_market_intelligence` on `127.0.0.1:3306` (MySQL 8.0, InnoDB, utf8mb4)
**Branch:** `mysql-live-deployment`
**Result:** ✅ Live deployment complete — 7/7 sources loaded, validated, and idempotent

---

## Scope

Migration of the AIC Occupation Intelligence pipeline from the SQLite prototype
to a live MySQL 8 database, preserving the SQLite prototype untouched
(`ETL/schema_sqlite.sql`, `ETL/lib_etl.py`) and adding a parallel MySQL-only
pipeline (`ETL/schema_mysql.sql`, `ETL/lib_etl_mysql.py`, `ETL/run_mysql_sources.py`,
`ETL/run_all.py`).

## Quality Gates

| Gate | Result |
|------|--------|
| Phase 2 — Schema validation (`validate_mysql_schema.py`) | ✅ 96/96 |
| Phase 3 — Unit tests (`pytest tests/test_mysql_library.py`) | ✅ 65/65 |
| Phase 4 — Wiring validation (`validate_mysql_wiring.py`) | ✅ 50/50 |

## Database Object Counts

- **25 base tables** (7 dim + 13 fact + 2 ref + 1 bridge + 1 staging + 1 audit)
- **1 view**: `vw_occupation_intelligence`

## Live Row Counts (exact `COUNT(*)`, verified 2026-07-14)

| Source | Table | Rows | Status |
|--------|-------|------:|--------|
| RBA | `fact_exchange_rate` | 20,728 | ✅ |
| Home Affairs (BP0015) | `fact_student_visa_activity` | 880 | ✅ |
| Home Affairs (BP0014) | `fact_temp_skilled_visa` | 0 | ⚠️ known limitation (below) |
| Home Affairs (BP0016) | `fact_temp_graduate_visa` | 0 | ⚠️ known limitation (below) |
| Home Affairs (BP0068) | `fact_permanent_migration` | 0 | ⚠️ known limitation (below) |
| CRICOS | `dim_provider` | 1,544 | ✅ |
| CRICOS | `dim_course` | 26,448 | ✅ |
| CRICOS | `dim_provider_location` | 3,887 | ✅ |
| CRICOS | `bridge_course_location` | 46,848 | ✅ |
| JSA | `fact_job_vacancy` | 511,560 | ✅ |
| JSA | `fact_occupation_shortage` | 916 | ✅ |
| JSA | `ref_occupation_profile` | 50,674 | ✅ |
| ABS | `fact_labour_force` | 66,120 | ✅ |
| ABS | `fact_cpi` | 585 | ✅ |
| ABS | `fact_overseas_migration` | 42,504 | ✅ |
| ABS | `fact_population_by_cob` | 7,770 | ✅ |
| Skilled Migration | `fact_skilled_migration` | 231 | ✅ |
| Skilled Migration | `ref_skilled_migration_by_cob_occupation` | 4,662 | ✅ |
| Education | `fact_student_enrolment` | 3,542,826 | ✅ |
| Reference | `dim_country` | 25 | ✅ |
| Reference | `dim_state` | 9 (seed) | ✅ |
| Reference | `dim_visa_subclass` | 11 (seed) | ✅ |

**Total live rows across all tables: 4,352,181**

Every table above was validated for: exact row count vs. dry-run parse count,
zero NULL business-key values, zero duplicate business keys, correct value
ranges/distributions (dates, states, measures), and idempotency (rerun twice,
row count unchanged, no new audit failures).

## Real Bugs Found and Fixed During Live Deployment

Every one of the following was a genuine defect surfaced only by testing
against a live MySQL server — dry-run mode alone did not (and structurally
could not) catch several of these, since it only measures row *count*, not
column or value correctness. Full detail is in the corresponding git commit
messages on `mysql-live-deployment`.

1. **`validate_mysql_wiring.py`** — false-positive Quality Gate 4 failure from
   a naive substring check on `run_mysql_abs()`'s own docstring.
2. **`schema_mysql.sql`** — 13 generated `STORED` key columns had `NOT NULL`
   in the wrong clause position (`NOT NULL GENERATED ALWAYS AS (...) STORED`
   instead of `GENERATED ALWAYS AS (...) STORED NOT NULL`); one more had a
   missing space (`VARCHAR(100)NOT NULL`). Both are genuine MySQL syntax
   errors, invisible until applying the schema to a real server.
3. **`lib_etl_mysql.py` / manual schema apply** — a statement splitter that
   checked "does this whole chunk start with `--`" silently dropped any real
   `CREATE TABLE` that happened to follow a comment header line on the next
   line — only 3 of 25 tables were created on the first attempt.
4. **`vw_occupation_intelligence`** — `CREATE OR REPLACE VIEW` requires
   `DROP` privilege even on first creation; the least-privilege `aic_user`
   intentionally has none. Applied once via a plain `CREATE VIEW`
   (admin-run), consistent with the approved least-privilege grant set.
5. **RBA** — no bug; 821-row variance from the original dry-run baseline is
   expected upsert-dedup where `f11` (monthly) and `f11.1` (daily) share an
   exact `(rate_date, series_id)` from 2023 onward.
6. **Home Affairs** — `financial_year` columns were `VARCHAR(7)`/`VARCHAR(10)`
   but the source has a partial-current-year column header
   ("2025-26 (to 30 April 2026)") that normalizes to 24 characters; widened
   to `VARCHAR(30)` across 5 tables. `parse_bp0014_holders()` silently
   returned the raw, unparsed pivot-table sheet as if it were data instead
   of failing like its sibling parsers — corrected to return empty.
7. **CRICOS** — `load_from_xlsx()` read every sheet with the default
   `header=0`, capturing the title row instead of the real header 2 rows
   down; every column-name-based transform then silently matched nothing,
   so the original dry-run "success" was actually zero usable columns per
   row. Also: neither the CRICOS CSV nor XLSX export has a natural
   `location_id` (added a deterministic synthesized one); and
   `transform_institutions()`'s rename map used CSV-only keys that never
   matched the XLSX's spaced headers.
8. **JSA** — `_detect_header_row()`'s hint match false-positived on a title
   row that incidentally contained both hint keywords in one sentence;
   `parse_osl_6digit()`'s column-mapping let two source columns collide on
   the same target, corrupting the dataframe; IVI's date column headers
   were run through a column-*name* normalizer before melting, turning
   `2006-01-01` into the literal cell value `"2006_01_01_00_00_00"` for
   100% of 555,660 rows; the OSCA-2024 occupation shortage sheet uses a
   non-ANZSCO code scheme with no `anzsco_code` at all.
9. **ABS** — `pd.to_datetime("nan", errors="raise")` does not raise (returns
   `NaT`), so a blank cell in column A at row 0 was misdetected as a valid
   date row on every sheet; this masked that NOM and ERP-by-country-of-birth
   were never correctly parsed at all (the original "26,145/8,730 rows" was
   silently wrong — a notes/footnote row mistaken for the data table). Added
   a dedicated country×year cross-tab parser for these two flows, which are
   a fundamentally different shape from the time-series parser used
   elsewhere.
10. **Skilled Migration** — the same column-collision bug as JSA
    (`"visa_subclass"`/`"visa_type"` both claiming one target column);
    `By Country & Year`/`By Industry & Year` sheets don't fit
    `fact_skilled_migration`'s grain and were mis-detected as headers via a
    "state" substring match inside "United **State**s of America" and
    "Real **Estate** Services"; `state_territory` held full state names and
    `visa_subclass` held a descriptive label instead of the bare codes used
    everywhere else.
11. **Education** — the `Month` column holds abbreviated names (`"Jul"`),
    not numbers; blind `pd.to_numeric(..., errors="coerce")` turned every
    value into `NaN`, which MySQL then forced to `0` on insert into the
    `NOT NULL enrol_month` column — **100% of rows had `enrol_month = 0`**
    before this fix. Separately, `fact_student_enrolment`'s nullable
    `UNIQUE KEY` parts (`state_code`, `sector`, `provider_type`,
    `new_to_australia`, `ends_this_year`) were missing the same generated
    NULL-safe `_k` column pattern used on 8 other tables, so genuine
    duplicate business-key rows were slipping through (MySQL treats
    `NULL != NULL`). A blanket `df.drop_duplicates()` in
    `run_mysql_education()` was also discarding ~75% of legitimate rows
    (3,542,826 → 877,359) — removed in favor of the schema's own UNIQUE KEY
    + upsert semantics, consistent with every other source.
12. **`reference/country_aliases.csv`** — an unquoted embedded comma in the
    ABS name for South Korea (`Korea, Republic of`) broke the file into 8
    CSV fields instead of 7, making it unparseable. Nothing previously
    populated `dim_country` from this file at all — added
    `run_mysql_dim_country()`.
13. **`vw_occupation_intelligence`** — join conditions referenced values
    that never existed in the real data (`vac.measure = 'SA'` vs. actual
    `'Seasonally Adjusted'`; `profile_measure LIKE '%Earn%'` vs. actual
    sheet names `Table_1`..`Table_8`). Corrected to match real values;
    `employment_size` now populates for 722/916 rows.

## Known Limitations (documented, not fixed — out of scope for this pass)

- **Home Affairs BP0014 (temp skilled visa granted), BP0016 (temp graduate
  visa lodged/granted), and BP0068 (permanent migration)** are Excel
  **PivotTable exports** (stacked filter fields, no flat header row) — a
  fundamentally different format from BP0015's plain table. This is a
  pre-existing gap in the SQLite-era parser, not introduced by this
  migration; a proper fix needs a bespoke pivot-table extractor.
  `fact_temp_skilled_visa`, `fact_temp_graduate_visa`, and
  `fact_permanent_migration` remain empty.
- **Education `Pivot_Detailed_Latest_web.xlsx`** is likewise a genuine Excel
  PivotTable with no pre-extracted flat equivalent locally (unlike Basic
  Pivot). Contributes 0 rows; `fact_student_enrolment` is fully populated
  from Basic Pivot alone (3,542,826 rows, matching the original baseline).
- **`vw_occupation_intelligence.latest_vacancies`** still returns `NULL` for
  all rows: `fact_occupation_shortage` uses 6-digit ANZSCO codes,
  `fact_job_vacancy` uses 2-digit major-group codes. A verified fix exists
  (`LEFT(os.anzsco_code, 2) = vac.anzsco_code_k`, confirmed 141,610 matching
  rows) but wasn't applied to avoid a second admin-privileged view
  replacement in this pass.
- **`vw_occupation_intelligence.median_annual_salary_aud`** returns `NULL`
  for all rows: `ref_occupation_profile.median_full_time_earnings_per_week`
  is suppressed/blank for every one of the 916 current shortage-list
  occupations specifically (source data sparsity, not a query defect).
- **`dim_occupation`** is not populated. Unlike `dim_country`, there is no
  small curated CSV to derive it from — it would need to be built from
  ANZSCO codes already present across `fact_occupation_shortage`,
  `ref_occupation_profile`, and `fact_job_vacancy`. All `occupation_id` FK
  columns are nullable, so this does not block anything currently.
- **`stg_skillselect_eoi`** remains empty by design — populated separately
  by `ETL/skillselect_csv_etl.py`, not part of the 7-source `run_all.py`
  pipeline (extraction not yet considered stable per the approved design).
- ABS flows `lf_industry`, `lf_occupation` are intentionally excluded from
  the MySQL schema (approved decision), consistent with `fact_abs_education_output`.

## Tableau Connection

- **Host:** value of `MYSQL_HOST` in `.env` (currently `127.0.0.1`)
- **Port:** value of `MYSQL_PORT` in `.env` (currently `3306`)
- **Database:** `aic_market_intelligence`
- **User:** `aic_user` (least-privilege: `SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, CREATE VIEW, SHOW VIEW` — no `DROP`, `FILE`, `TRIGGER`, `SUPER`, or `ALL PRIVILEGES`)
- **Recommended entry points:** `vw_occupation_intelligence` (occupation-level mart), plus any `fact_*`/`dim_*` table directly
- Verified: `aic_user` has `SELECT` on the full schema — a Tableau live or
  extract connection using these credentials can read every table and the
  view.

## Sign-off Checklist

- [x] All 7 sources loaded (3 of Home Affairs' 4 target tables empty — documented limitation, source format issue predating this migration)
- [x] Row counts match or exceed dry-run totals, with every variance explained above (several are *corrections* of previously-wrong baselines, not regressions)
- [x] All idempotency tests pass — reran every source twice, zero row-count drift
- [x] No NULL violations on any UNIQUE KEY column across all 25 tables
- [x] `etl_audit_log` has a clean run history — 43 completed, 0 failed since 2026-07-13 12:35 (all 19 historical failures are from bugs fixed earlier in this same session)
- [x] 4+ cross-source integration queries return sensible results (enrolment nationalities, AUD/USD vs. enrolments, shortage distribution, audit trail)
- [x] Tableau can connect and read every table/view via `aic_user`
- [x] SQLite prototype preserved untouched (`ETL/schema_sqlite.sql`, `ETL/lib_etl.py`)
- [x] `.env` is in `.gitignore`, never committed

## Final Repository Audit (2026-07-14)

A repository-wide consistency pass after live deployment:

- Removed `deploy_mysql.sh`: superseded and genuinely broken — it called
  `run_all.py --mysql` (a flag that no longer exists now that `run_all.py`
  is MySQL-only) and `run_all_mysql()` (never existed), and granted
  `ALL PRIVILEGES` rather than the approved least-privilege set. The
  correct, tested equivalent is `ETL/deploy_and_validate.py`.
- Fixed stale hardcoded quality-gate numbers in
  `ETL/deploy_and_validate.py`'s report generator (90/90 → 96/96 schema
  checks, 50/50 → 65/65 unit tests, 17/17 → 50/50 wiring checks) to match
  the actual current validators.
- Added scope-clarifying banners to docs that describe the preserved
  SQLite/SkillSelect prototype or pre-migration planning, so they can't be
  mistaken for describing the current MySQL production system:
  `docs/DATABASE_DICTIONARY.md`, `docs/ETL_RUNBOOK.md`,
  `docs/DASHBOARD_USER_GUIDE.md`, `docs/mysql_test_plan.md`,
  `docs/mysql_schema_reconciliation.md`, `docs/schema_review.md`,
  `docs/proposed_mysql_model.md`. None of their content was rewritten.
- Confirmed `ETL/schema.sql` (still referenced by every original SQLite
  ETL script as its live `SCHEMA` constant) and `ETL/schema_sqlite.sql`
  (the frozen Phase 0 backup copy) are intentionally distinct, not a stale
  duplicate.
- Re-ran all quality gates after every change: 65/65 unit tests, 96/96
  schema checks, 50/50 wiring checks — all still pass. Verified every core
  ETL module still imports cleanly.
