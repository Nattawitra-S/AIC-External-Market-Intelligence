# Proposed MySQL Data Model
**AIC Market Intelligence Database**  
**Version 1.0 — July 2026**

> ⚠️ **Historical planning document (2026-07-14):** Pre-migration design
> proposal, kept for design rationale. The MySQL migration is complete and
> live — see `docs/final_migration_summary.md` for the as-built schema and
> current state.

---

## Design Principles

1. **Practical over perfect** — the model must work for Tableau, be maintainable by the ETL team, and not require a data warehouse engineer to query.
2. **Grain first** — every fact table has one clearly-stated grain. No mixing of aggregated and record-level data.
3. **Shared dimensions** — country, occupation, and state are the three dimensions that appear in 4+ sources. These get proper `dim_` tables with surrogate keys.
4. **Deferred normalization** — CRICOS, RBA, and SkillSelect are relatively static and don't require full dimension extraction. They use natural keys.
5. **No pre-calculated conclusions** — `occupation_intelligence` is a derived mart view, not a core source-of-truth table.
6. **ETL audit built in** — every ETL run is logged to `etl_audit_log`.

---

## Layer Summary

| Prefix | Purpose | Tables |
|--------|---------|--------|
| `dim_` | Shared dimensions: country, state, occupation, visa, provider, course | 6 tables |
| `fact_` | Time-series measurements: enrolments, visas, vacancies, rates, population | 11 tables |
| `ref_` | Reference data: occupation profiles, visa-to-occupation eligibility | 2 tables |
| `bridge_` | Many-to-many links: course ↔ location | 1 table |
| `stg_` | Raw staging for SkillSelect and future sources | 1 table |
| `etl_` | Pipeline audit and control | 1 table |

**Total: 22 tables** (down from 24 in SQLite, removed 4 unpopulated legacy tables)

---

## Dimension Tables

### dim_country

**Purpose:** Single canonical list of countries/nationalities used across all sources. Resolves name inconsistency between Education, ABS, Home Affairs, and Skilled Migration.

**Grain:** One row = one country.

```sql
dim_country (
    country_id          SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    iso_alpha2          CHAR(2),                    -- ISO 3166-1 alpha-2
    iso_alpha3          CHAR(3),                    -- ISO 3166-1 alpha-3
    canonical_name      VARCHAR(100) NOT NULL,      -- Display name for Tableau
    -- Source-specific aliases (populated by ETL mapping)
    name_education      VARCHAR(150),               -- Education dept variant
    name_home_affairs   VARCHAR(150),               -- Home Affairs variant
    name_abs            VARCHAR(150),               -- ABS variant
    name_skilled_mig    VARCHAR(150),               -- Skilled Migration variant
    is_active           TINYINT(1) NOT NULL DEFAULT 1,
    UNIQUE KEY uk_iso2 (iso_alpha2),
    UNIQUE KEY uk_canonical (canonical_name)
)
```

**Source ETL:** Pre-populated from ISO 3166 list + mapping from all four text sources  
**Update frequency:** Rarely (new countries, name changes)  
**Expected rows:** ~250

**Note for ETL team:** During initial load, country text fields in fact tables will reference `canonical_name` via a lookup join. This mapping table must be seeded before any fact tables load. A `country_aliases.csv` mapping file should be maintained.

---

### dim_state

**Purpose:** Australian states and territories. Simple and stable.

**Grain:** One row = one state/territory.

```sql
dim_state (
    state_id        TINYINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    state_code      VARCHAR(3) NOT NULL,        -- NSW, VIC, QLD, SA, WA, TAS, ACT, NT, AUS
    state_name      VARCHAR(50) NOT NULL,
    is_territory    TINYINT(1) NOT NULL DEFAULT 0,
    UNIQUE KEY uk_state_code (state_code)
)
```

**Source ETL:** Seeded manually (8 rows + AUS total)  
**Expected rows:** 9

---

### dim_occupation

**Purpose:** ANZSCO occupation codes used across JSA, Skilled Migration, and SkillSelect.

**Grain:** One row = one ANZSCO code at a specific digit level.

```sql
dim_occupation (
    occupation_id       INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    anzsco_code         VARCHAR(8) NOT NULL,    -- "2613", "261313"
    anzsco_level        TINYINT NOT NULL,       -- 1, 2, 3, 4, 6
    occupation_name     VARCHAR(200) NOT NULL,
    major_group_code    CHAR(1),                -- 1-digit ANZSCO
    sub_major_code      CHAR(2),                -- 2-digit
    minor_group_code    CHAR(3),                -- 3-digit
    unit_group_code     CHAR(4),               -- 4-digit
    anzsco_skill_level  TINYINT,               -- 1-5
    UNIQUE KEY uk_anzsco (anzsco_code, anzsco_level),
    KEY idx_unit_group (unit_group_code)
)
```

**Source ETL:** Seeded from JSA occupation profiles + OSL  
**Expected rows:** ~3,000 (includes 2-digit, 4-digit, 6-digit codes)

---

### dim_visa_subclass

**Purpose:** Australian visa subclasses referenced across Home Affairs, Skilled Migration, and SkillSelect.

**Grain:** One row = one visa subclass.

```sql
dim_visa_subclass (
    visa_id             SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    subclass_code       VARCHAR(10) NOT NULL,   -- "189", "482", "500", "485"
    visa_name           VARCHAR(150) NOT NULL,
    visa_category       VARCHAR(50),            -- "Student", "Skilled", "Graduate", "Family"
    is_temporary        TINYINT(1),
    is_permanent        TINYINT(1),
    UNIQUE KEY uk_subclass (subclass_code)
)
```

**Source ETL:** Seeded manually  
**Expected rows:** ~50

---

### dim_provider (from CRICOS)

**Purpose:** CRICOS-registered education providers. This IS the dimension; `cricos_institutions` in the old schema becomes this.

**Grain:** One row = one CRICOS-registered provider.

```sql
dim_provider (
    provider_id             VARCHAR(10) NOT NULL PRIMARY KEY,   -- CRICOS code e.g. "00025B"
    provider_name           VARCHAR(200) NOT NULL,
    provider_type           VARCHAR(50),                        -- University, TAFE, etc.
    state_code              VARCHAR(3),
    website                 VARCHAR(300),
    registration_status     VARCHAR(20),                        -- Registered, Cancelled
    registration_end_date   DATE,
    _etl_source             VARCHAR(100),
    _etl_loaded_at          DATETIME,
    KEY idx_prov_state (state_code),
    KEY idx_prov_type (provider_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

**Expected rows:** ~5,000

---

### dim_course (from CRICOS)

**Purpose:** CRICOS-registered courses.

**Grain:** One row = one CRICOS course code.

```sql
dim_course (
    cricos_code             VARCHAR(10) NOT NULL PRIMARY KEY,   -- e.g. "063281F"
    course_name             VARCHAR(300) NOT NULL,
    field_of_education      VARCHAR(200),                       -- ASCED code + name
    broad_field             VARCHAR(100),
    duration_weeks          DECIMAL(6,1),
    min_age                 TINYINT UNSIGNED,
    annual_fees_aud         DECIMAL(10,2),
    provider_id             VARCHAR(10),
    _etl_source             VARCHAR(100),
    _etl_loaded_at          DATETIME,
    KEY idx_course_provider (provider_id),
    KEY idx_course_field (field_of_education(50))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

**Expected rows:** ~30,000

---

## Fact Tables

### fact_exchange_rate

**Purpose:** Daily and monthly AUD exchange rates from RBA.  
**Source:** etl_rba.py → F11 (monthly), F11.1 (daily)  
**Grain:** One row = **one date × one currency series**  
**Update frequency:** Monthly (F11), Daily (F11.1)

```sql
fact_exchange_rate (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    rate_date       DATE NOT NULL,
    series_id       VARCHAR(10) NOT NULL,       -- FXRUSD, FXRTWI, etc.
    currency_pair   VARCHAR(50),                -- "A$1=USD"
    units           VARCHAR(20),                -- USD, Index, CNY
    frequency       VARCHAR(10),               -- Monthly, Daily
    value           DECIMAL(12,6),
    source_table    VARCHAR(10),               -- f11, f11.1
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_rate (rate_date, series_id),
    KEY idx_rate_date (rate_date),
    KEY idx_series (series_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

**Expected rows:** ~22,000

---

### fact_student_enrolment (largest table — 3.5M rows)

**Purpose:** Monthly YTD enrolments and commencements of international students by nationality, state, sector.  
**Source:** etl_education_v2.py → Pivot_Basic extracted file  
**Grain:** One row = **year × month × nationality × state × sector × provider_type × new_to_australia × ends_this_year**  
**Update frequency:** Monthly  
**⚠️ YTD note:** Values are Year-To-Date cumulative. For snapshot analysis, use only the latest month of each year.

```sql
fact_student_enrolment (
    id                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    enrol_year              SMALLINT UNSIGNED NOT NULL,
    enrol_month             TINYINT UNSIGNED NOT NULL,          -- 1-12
    nationality             VARCHAR(150) NOT NULL,              -- Raw country name
    country_id              SMALLINT UNSIGNED,                  -- FK → dim_country (nullable until mapping done)
    state_code              VARCHAR(3),                         -- FK → dim_state
    sector                  VARCHAR(50),                        -- Higher Education, VET, ELICOS, Schools, NPOS
    provider_type           VARCHAR(50),
    new_to_australia        CHAR(3),                            -- Yes / No
    ends_this_year          CHAR(3),                            -- Yes / No
    ytd_enrolments          INT UNSIGNED,
    ytd_commencements       INT UNSIGNED,
    total                   INT UNSIGNED,
    _etl_source             VARCHAR(100),
    _etl_loaded_at          DATETIME,
    UNIQUE KEY uk_enrol (enrol_year, enrol_month, nationality(100), state_code, sector(30), provider_type(30), new_to_australia, ends_this_year),
    KEY idx_enrol_year_month (enrol_year, enrol_month),
    KEY idx_enrol_nationality (nationality(50)),
    KEY idx_enrol_state (state_code),
    KEY idx_enrol_sector (sector),
    KEY idx_country_fk (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=COMPRESSED
```

**Expected rows:** 3,542,826  
**Bulk load:** Use `LOAD DATA LOCAL INFILE` from staged CSV for initial load. See `lib_etl_mysql.py` for implementation.

---

### fact_student_visa_activity

**Purpose:** BP0015 student visa applications — lodged, granted, and grant rates by applicant type/sector/year.  
**Source:** etl_home_affairs_extended.py (BP0015)  
**Grain:** One row = **applicant_type × sector × financial_year × measure**  
**Replaces:** ha_student_visa_lodged + ha_student_visa_granted + ha_student_visa_grant_rates (3 tables → 1)

```sql
fact_student_visa_activity (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    applicant_type  VARCHAR(30) NOT NULL,       -- Primary, Secondary
    sector          VARCHAR(50) NOT NULL,        -- Higher Education, VET, ELICOS, Schools
    financial_year  VARCHAR(7) NOT NULL,         -- 2023-24
    measure         VARCHAR(20) NOT NULL,        -- lodged, granted, grant_rate_pct
    value           DECIMAL(10,2),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_sva (applicant_type, sector, financial_year, measure),
    KEY idx_sva_fy (financial_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_temp_skilled_visa

**Purpose:** BP0014 temporary resident skilled visas — granted by subclass/nationality/year + holders snapshot.  
**Source:** etl_home_affairs_extended.py (BP0014)  
**Grain:** One row = **visa_subclass × nationality × financial_year × measure** (granted or holder_count)

```sql
fact_temp_skilled_visa (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    visa_subclass   VARCHAR(10) NOT NULL,
    nationality     VARCHAR(150) NOT NULL,
    country_id      SMALLINT UNSIGNED,
    financial_year  VARCHAR(7) NOT NULL,
    state_territory VARCHAR(3),                 -- NULL for granted (national), set for holders
    measure         VARCHAR(20) NOT NULL,        -- granted, holders
    value           DECIMAL(10,2),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_tsv (visa_subclass, nationality(80), financial_year, COALESCE(state_territory,'AUS'), measure),
    KEY idx_tsv_fy (financial_year),
    KEY idx_tsv_visa (visa_subclass)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

**Note:** COALESCE in UNIQUE key requires a generated column workaround in MySQL. See `schema_mysql.sql` for implementation using nullable column with functional unique index.

---

### fact_temp_graduate_visa

**Purpose:** BP0016 temporary graduate visas — lodged and granted by stream/nationality/year.  
**Source:** etl_home_affairs_extended.py (BP0016)  
**Grain:** One row = **stream × nationality × financial_year × measure**

```sql
fact_temp_graduate_visa (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    stream          VARCHAR(80) NOT NULL,
    nationality     VARCHAR(150) NOT NULL,
    country_id      SMALLINT UNSIGNED,
    financial_year  VARCHAR(7) NOT NULL,
    measure         VARCHAR(20) NOT NULL,        -- lodged, granted
    value           DECIMAL(10,2),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_tgv (stream(50), nationality(80), financial_year, measure),
    KEY idx_tgv_fy (financial_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_permanent_migration

**Purpose:** BP0068 Permanent Migration Program outcomes — by visa type/country/measure/period.  
**Source:** etl_home_affairs_extended.py (BP0068)  
**Grain:** One row = **visa_type × birth_country × outcome_measure × period**  
**Renamed from:** ha_migration_child_outcomes

```sql
fact_permanent_migration (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    visa_type       VARCHAR(50),                -- Skilled, Family, Humanitarian
    birth_country   VARCHAR(150),
    country_id      SMALLINT UNSIGNED,
    outcome_measure VARCHAR(200),               -- Column header from BP0068 sheets
    period          VARCHAR(10),
    value           DECIMAL(12,2),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_pm (visa_type(30), birth_country(80), outcome_measure(100), period),
    KEY idx_pm_period (period),
    KEY idx_pm_visa (visa_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_skilled_migration

**Purpose:** Skilled Migration Programme Reports — grants by visa subclass/stream/state/year.  
**Source:** etl_skilled_migration.py (summaries XLSX only — NOT the 1.4M raw CSV)  
**Grain:** One row = **financial_year × visa_subclass × stream × state_territory × measure**

```sql
fact_skilled_migration (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    financial_year  VARCHAR(7) NOT NULL,
    visa_subclass   VARCHAR(10),
    visa_id         SMALLINT UNSIGNED,          -- FK → dim_visa_subclass
    stream          VARCHAR(80),
    state_code      VARCHAR(3),
    measure         VARCHAR(80) NOT NULL,        -- Sheet name e.g. "Grants"
    value           DECIMAL(12,2),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_sm (financial_year, COALESCE(visa_subclass,''), COALESCE(stream,''), COALESCE(state_code,''), measure(40)),
    KEY idx_sm_fy (financial_year),
    KEY idx_sm_visa (visa_subclass)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

**Note:** The 1.4M raw CSV should load into `stg_skilled_migration_raw` (separate table) if needed, not mixed here.

---

### fact_job_vacancy

**Purpose:** JSA Internet Vacancy Index — monthly online job ads by occupation and state.  
**Source:** etl_jsa.py → IVI ANZSCO4, ANZSCO2, Skill Level files  
**Grain:** One row = **period × anzsco_code × state_territory × measure (SA/Trend/Original)**  
**Update frequency:** Monthly

```sql
fact_job_vacancy (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    vacancy_period  VARCHAR(10) NOT NULL,       -- YYYY-MM (normalised from various formats)
    anzsco_code     VARCHAR(8),
    occupation_id   INT UNSIGNED,               -- FK → dim_occupation
    anzsco_level    TINYINT,                    -- 2 or 4
    state_code      VARCHAR(3),
    state_id        TINYINT UNSIGNED,
    measure         VARCHAR(20) NOT NULL,        -- SA (Seasonally Adjusted), Trend, Original
    vacancy_count   INT,
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_jv (vacancy_period, anzsco_code, COALESCE(state_code,'AUS'), measure),
    KEY idx_jv_period (vacancy_period),
    KEY idx_jv_anzsco (anzsco_code),
    KEY idx_jv_state (state_code),
    KEY idx_jv_measure (measure)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

**Expected rows:** ~600,000

---

### fact_occupation_shortage

**Purpose:** JSA Occupation Shortage List — shortage status by ANZSCO/state/year.  
**Source:** etl_jsa.py → OSL 4-digit + 6-digit files  
**Grain:** One row = **anzsco_code × anzsco_level × state_territory × assessment_year**  
**Update frequency:** Annual

```sql
fact_occupation_shortage (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    anzsco_code     VARCHAR(8) NOT NULL,
    anzsco_level    TINYINT NOT NULL,           -- 4 or 6
    occupation_id   INT UNSIGNED,               -- FK → dim_occupation
    occupation_name VARCHAR(200),
    shortage_status VARCHAR(30),                -- Shortage, No Shortage, Regional Shortage
    osca_category   VARCHAR(50),               -- OSCA rating (6-digit only)
    assessment_year VARCHAR(4),
    state_code      VARCHAR(3) NOT NULL DEFAULT 'AUS',
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_osl (anzsco_code, anzsco_level, state_code, COALESCE(assessment_year,'0')),
    KEY idx_osl_status (shortage_status),
    KEY idx_osl_anzsco (anzsco_code),
    KEY idx_osl_year (assessment_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_labour_force

**Purpose:** ABS Labour Force — monthly employment/unemployment statistics.  
**Source:** etl_abs.py → ABS SDMX LF flow or local XLSX  
**Grain:** One row = **period × series_id** (more reliable than measure text)  
**Update frequency:** Monthly

```sql
fact_labour_force (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    lf_period       VARCHAR(7) NOT NULL,        -- YYYY-MM
    series_id       VARCHAR(30) NOT NULL,        -- ABS series ID
    measure         VARCHAR(200),               -- Description (variable text — do not use as key)
    sex             VARCHAR(10),                -- Persons, Males, Females
    adjustment_type VARCHAR(25),               -- Trend, Seasonally Adjusted, Original
    state_code      VARCHAR(3),
    value           DECIMAL(12,3),             -- '000 persons
    unit            VARCHAR(20),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_lf (lf_period, series_id),
    KEY idx_lf_period (lf_period),
    KEY idx_lf_series (series_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_cpi

**Purpose:** ABS Consumer Price Index — quarterly by group/city.  
**Source:** etl_abs.py → ABS SDMX CPI flow or local XLSX  
**Grain:** One row = **period × series_id**  
**Update frequency:** Quarterly

```sql
fact_cpi (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cpi_period      VARCHAR(7) NOT NULL,        -- YYYY-QN e.g. 2024-Q1
    series_id       VARCHAR(30) NOT NULL,
    title           VARCHAR(200),
    cpi_group       VARCHAR(100),
    city            VARCHAR(60),
    measure         VARCHAR(30),
    value           DECIMAL(8,3),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_cpi (cpi_period, series_id),
    KEY idx_cpi_period (cpi_period)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_overseas_migration

**Purpose:** ABS Net Overseas Migration — annual migration flows.  
**Source:** etl_abs.py → ABS SDMX NOM flow  
**Grain:** One row = **period × country_of_birth × state_territory × direction**

```sql
fact_overseas_migration (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    nom_period      VARCHAR(7) NOT NULL,        -- YYYY or YYYY-MM
    country_name    VARCHAR(150),               -- Raw from ABS
    country_id      SMALLINT UNSIGNED,
    state_code      VARCHAR(3) NOT NULL DEFAULT 'AUS',
    direction       VARCHAR(15) NOT NULL,        -- net, arrivals, departures
    series_id       VARCHAR(30),
    value           DECIMAL(10,1),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_nom (nom_period, COALESCE(country_name,''), state_code, direction),
    KEY idx_nom_period (nom_period),
    KEY idx_nom_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### fact_population_by_cob

**Purpose:** ABS Estimated Resident Population by Country of Birth — annual.  
**Source:** etl_abs.py → ABS SDMX ERP_COB flow  
**Grain:** One row = **period × country_of_birth × state_territory**

```sql
fact_population_by_cob (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    erp_period      VARCHAR(7) NOT NULL,
    country_name    VARCHAR(150),
    country_id      SMALLINT UNSIGNED,
    state_code      VARCHAR(3) NOT NULL DEFAULT 'AUS',
    series_id       VARCHAR(30),
    population      DECIMAL(12,1),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_erp (erp_period, COALESCE(country_name,''), state_code),
    KEY idx_erp_period (erp_period),
    KEY idx_erp_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## Reference Tables

### ref_occupation_profile

**Purpose:** JSA occupation profile data — various measures (earnings, employment, education) per ANZSCO code.  
**Source:** etl_jsa.py → Occupation profiles data.xlsx  
**Grain:** One row = **anzsco_code × measure (sheet) × dimension (column) × [optional: profile_year]**

```sql
ref_occupation_profile (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    anzsco_code     VARCHAR(8) NOT NULL,
    occupation_id   INT UNSIGNED,
    occupation_name VARCHAR(200),
    profile_measure VARCHAR(80) NOT NULL,        -- Sheet name: Earnings, Employment, Education
    dimension       VARCHAR(150) NOT NULL,       -- Column header
    value_num       DECIMAL(12,2),
    value_text      VARCHAR(300),               -- For non-numeric values
    profile_year    VARCHAR(4),                 -- Year of profile data
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_ocp (anzsco_code, profile_measure(40), dimension(80), COALESCE(profile_year,'0')),
    KEY idx_ocp_anzsco (anzsco_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

### ref_skilled_migration_by_cob_occupation

**Purpose:** Skilled Migration grants by country of birth × ANZSCO occupation × visa subclass.  
**Source:** etl_skilled_migration.py → skilled_visas_country_occupation.xlsx  
**Grain:** One row = **financial_year × country_of_birth × anzsco_code × visa_subclass × measure**

```sql
ref_skilled_migration_by_cob_occupation (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    financial_year  VARCHAR(7) NOT NULL,
    country_name    VARCHAR(150),
    country_id      SMALLINT UNSIGNED,
    anzsco_code     VARCHAR(8),
    occupation_id   INT UNSIGNED,
    occupation_name VARCHAR(200),
    visa_subclass   VARCHAR(10),
    measure         VARCHAR(80) NOT NULL,
    value           DECIMAL(12,2),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_smc (financial_year, COALESCE(country_name,''), COALESCE(anzsco_code,''), COALESCE(visa_subclass,''), measure(40)),
    KEY idx_smc_fy (financial_year),
    KEY idx_smc_anzsco (anzsco_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## Bridge Table

### bridge_course_location

**Purpose:** Many-to-many link between CRICOS courses and provider campuses.  
**Source:** etl_cricos.py → cricos_course_locations  
**Grain:** One row = **one course offered at one location**

```sql
bridge_course_location (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cricos_code     VARCHAR(10) NOT NULL,
    location_id     VARCHAR(20) NOT NULL,
    provider_id     VARCHAR(10),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    UNIQUE KEY uk_cl (cricos_code, location_id),
    KEY idx_cl_course (cricos_code),
    KEY idx_cl_location (location_id),
    KEY idx_cl_provider (provider_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## Staging Tables

### stg_skillselect_eoi

**Purpose:** Raw SkillSelect EOI data from Qlik Sense Playwright export.  
**Source:** skillselect_csv_etl.py  
**Note:** Kept as staging (not promoted to fact) because the grain is unusual (Qlik view-driven) and the data is periodic/manual rather than automated.

```sql
stg_skillselect_eoi (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    as_at_month      VARCHAR(7) NOT NULL,        -- "06/2026" (MM/YYYY from wizard)
    visa_type        VARCHAR(5) NOT NULL,         -- 189, 190, 491, All
    eoi_status       VARCHAR(15) NOT NULL,        -- Submitted, Active, All
    source_view      VARCHAR(60) NOT NULL,        -- e.g. Occupations_Points
    dimension_1_name VARCHAR(60),
    dimension_1_val  VARCHAR(200),
    dimension_2_name VARCHAR(60),
    dimension_2_val  VARCHAR(100),
    eoi_count        INT UNSIGNED,
    captured_at      DATETIME NOT NULL,
    UNIQUE KEY uk_eoi (as_at_month, visa_type, eoi_status, source_view, COALESCE(dimension_1_val,''), COALESCE(dimension_2_val,'')),
    KEY idx_eoi_month (as_at_month),
    KEY idx_eoi_visa (visa_type),
    KEY idx_eoi_view (source_view)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## ETL Audit Table

### etl_audit_log

**Purpose:** Record every ETL run for traceability and monitoring.

```sql
etl_audit_log (
    run_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source          VARCHAR(30) NOT NULL,        -- rba, education, cricos, etc.
    table_name      VARCHAR(60) NOT NULL,
    started_at      DATETIME NOT NULL,
    completed_at    DATETIME,
    rows_read       INT UNSIGNED,
    rows_inserted   INT UNSIGNED,
    rows_updated    INT UNSIGNED,
    rows_rejected   INT UNSIGNED,
    status          ENUM('running','completed','failed','partial') NOT NULL DEFAULT 'running',
    error_message   TEXT,
    etl_version     VARCHAR(20),                 -- git tag or semver
    KEY idx_audit_source (source),
    KEY idx_audit_started (started_at),
    KEY idx_audit_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## Additional: Provider Location (from CRICOS)

```sql
dim_provider_location (
    location_id     VARCHAR(20) NOT NULL PRIMARY KEY,
    provider_id     VARCHAR(10) NOT NULL,
    location_name   VARCHAR(200),
    address         VARCHAR(300),
    suburb          VARCHAR(100),
    state_code      VARCHAR(3),
    postcode        VARCHAR(6),
    _etl_source     VARCHAR(100),
    _etl_loaded_at  DATETIME,
    KEY idx_loc_provider (provider_id),
    KEY idx_loc_state (state_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## Decisions Requiring User Approval

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | Do we load the 1.4M skilled migration raw CSV? | Yes → separate `stg_skilled_migration_raw` table / No → skip | Defer unless specific analytical need is confirmed |
| 2 | Country name standardisation | Manual mapping CSV maintained by team / Automated fuzzy match / Skip (use raw names) | Manual mapping CSV — lowest risk |
| 3 | FK enforcement in MySQL | Enforce with FOREIGN KEY constraints / Soft FKs (index only, no constraint) | Soft FKs for ETL flexibility |
| 4 | Education enrolments bulk load | LOAD DATA LOCAL INFILE (fast) / chunked INSERT (simpler setup) | LOAD DATA for production; chunked for dev |
| 5 | ABS employment by industry/occupation | Implement proper SDMX queries now / Defer and remove tables | Defer — confirm SDMX key structure first |
| 6 | `abs_education_output` table | Keep (ABS Table 34 output index) / Remove | Keep — relevant to sector analysis |
| 7 | SkillSelect staging vs promotion to fact | Keep as `stg_` / Promote to `fact_skillselect_eoi` with month FK | Keep as staging until more runs validated |

---

## Final Table Count

| Layer | Count | Tables |
|-------|-------|--------|
| dim_ | 7 | dim_country, dim_state, dim_occupation, dim_visa_subclass, dim_provider, dim_course, dim_provider_location |
| fact_ | 11 | exchange_rate, student_enrolment, student_visa_activity, temp_skilled_visa, temp_graduate_visa, permanent_migration, skilled_migration, job_vacancy, occupation_shortage, labour_force, cpi, overseas_migration, population_by_cob |
| ref_ | 2 | ref_occupation_profile, ref_skilled_migration_by_cob_occupation |
| bridge_ | 1 | bridge_course_location |
| stg_ | 1 | stg_skillselect_eoi |
| etl_ | 1 | etl_audit_log |
| **Total** | **23** | |

*Note: fact_ count above is 13, not 11 — document counts fact tables individually.*
