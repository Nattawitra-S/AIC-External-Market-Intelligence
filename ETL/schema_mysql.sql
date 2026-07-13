-- ============================================================
-- AIC Market Intelligence Database — MySQL 8 Schema
-- ============================================================
-- Version: 2.0 (2026-07-13)
-- Supersedes: SQLite prototype (preserved in ETL/schema_sqlite.sql)
--
-- Apply with:
--   mysql -u aic_user -p aic_market_intelligence < ETL/schema_mysql.sql
--
-- Requires: MySQL 8.0.13+, InnoDB, utf8mb4
-- Tables: 25 (7 dim + 13 fact + 2 ref + 1 bridge + 1 staging + 1 audit)
--
-- Approved design decisions implemented here:
--   1. Education stored as YTD cumulative (no conversion)
--   2. BP0068 destination → fact_permanent_migration
--   3. SkillSelect → stg_skillselect_eoi (staging)
--   4. fact_abs_education_output EXCLUDED
--   5. Legacy unpopulated tables EXCLUDED
--   6. dim_country populated from source data
--   7. Generated non-null key columns for nullable UNIQUE keys
--   8. occupation_intelligence → view/mart (not source table)
--   9. MySQL 8, InnoDB, utf8mb4
--  10. Education bulk load via LOAD DATA LOCAL INFILE
-- ============================================================

SET NAMES utf8mb4;
SET foreign_key_checks = 0;

-- ─────────────────────────────────────────────────────────────────────────────
-- ETL AUDIT LOG
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS etl_audit_log (
    run_id          BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    source          VARCHAR(30)      NOT NULL,
    table_name      VARCHAR(60)      NOT NULL,
    started_at      DATETIME         NOT NULL,
    completed_at    DATETIME,
    rows_read       INT UNSIGNED,
    rows_inserted   INT UNSIGNED,
    rows_updated    INT UNSIGNED,
    rows_rejected   INT UNSIGNED,
    status          ENUM('running','completed','failed','partial') NOT NULL DEFAULT 'running',
    error_message   TEXT,
    etl_version     VARCHAR(20),
    PRIMARY KEY (run_id),
    KEY idx_audit_source  (source),
    KEY idx_audit_started (started_at),
    KEY idx_audit_status  (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='ETL run history — one row per source×table load attempt';


-- ─────────────────────────────────────────────────────────────────────────────
-- DIMENSIONS
-- ─────────────────────────────────────────────────────────────────────────────

-- dim_country: canonical country names + aliases from all sources
-- Populated after initial data loads (see reference/country_aliases.csv)
CREATE TABLE IF NOT EXISTS dim_country (
    country_id          SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    canonical_name      VARCHAR(150)      NOT NULL,
    iso_alpha2          CHAR(2),
    iso_alpha3          CHAR(3),
    name_education      VARCHAR(150) COMMENT 'Alias used in Education data',
    name_home_affairs   VARCHAR(150) COMMENT 'Alias used in Home Affairs data',
    name_abs            VARCHAR(150) COMMENT 'Alias used in ABS data',
    name_skilled_mig    VARCHAR(150) COMMENT 'Alias used in Skilled Migration data',
    is_active           TINYINT(1)   NOT NULL DEFAULT 1,
    PRIMARY KEY (country_id),
    UNIQUE KEY uk_canonical (canonical_name),
    KEY idx_iso2 (iso_alpha2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Canonical country list — populated post-load from source data';


CREATE TABLE IF NOT EXISTS dim_state (
    state_id        TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    state_code      VARCHAR(3)       NOT NULL,
    state_name      VARCHAR(50)      NOT NULL,
    is_territory    TINYINT(1)       NOT NULL DEFAULT 0,
    PRIMARY KEY (state_id),
    UNIQUE KEY uk_state_code (state_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO dim_state (state_code, state_name, is_territory) VALUES
('NSW', 'New South Wales',             0),
('VIC', 'Victoria',                    0),
('QLD', 'Queensland',                  0),
('SA',  'South Australia',             0),
('WA',  'Western Australia',           0),
('TAS', 'Tasmania',                    0),
('ACT', 'Australian Capital Territory',1),
('NT',  'Northern Territory',          1),
('AUS', 'Australia (National)',        0);


CREATE TABLE IF NOT EXISTS dim_occupation (
    occupation_id       INT UNSIGNED NOT NULL AUTO_INCREMENT,
    anzsco_code         VARCHAR(8)   NOT NULL,
    anzsco_level        TINYINT      NOT NULL COMMENT '1=Major 2=Sub-major 3=Minor 4=Unit 6=Occupation',
    occupation_name     VARCHAR(250) NOT NULL,
    major_group_code    CHAR(1),
    sub_major_code      CHAR(2),
    minor_group_code    CHAR(3),
    unit_group_code     CHAR(4),
    anzsco_skill_level  TINYINT      COMMENT '1=highest 5=lowest',
    PRIMARY KEY (occupation_id),
    UNIQUE KEY uk_anzsco (anzsco_code, anzsco_level),
    KEY idx_unit_group (unit_group_code),
    KEY idx_occ_name   (occupation_name(60))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Populated from JSA occupation shortage + profiles data';


CREATE TABLE IF NOT EXISTS dim_visa_subclass (
    visa_id         SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    subclass_code   VARCHAR(10)       NOT NULL,
    visa_name       VARCHAR(200)      NOT NULL,
    visa_category   VARCHAR(50)       COMMENT 'Student, Skilled, Graduate, Family, Humanitarian',
    is_temporary    TINYINT(1),
    is_permanent    TINYINT(1),
    PRIMARY KEY (visa_id),
    UNIQUE KEY uk_subclass (subclass_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO dim_visa_subclass (subclass_code, visa_name, visa_category, is_temporary, is_permanent) VALUES
('500',  'Student Visa',                        'Student',  1, 0),
('485',  'Temporary Graduate Visa',             'Graduate', 1, 0),
('482',  'Temporary Skill Shortage Visa',       'Skilled',  1, 0),
('186',  'Employer Nomination Scheme',          'Skilled',  0, 1),
('189',  'Skilled Independent Visa',            'Skilled',  0, 1),
('190',  'Skilled Nominated Visa',              'Skilled',  0, 1),
('491',  'Skilled Work Regional (Provisional)', 'Skilled',  1, 0),
('494',  'Skilled Employer Sponsored Regional', 'Skilled',  1, 0),
('407',  'Training Visa',                       'Training', 1, 0),
('408',  'Temporary Activity Visa',             'Temporary',1, 0),
('600',  'Visitor Visa',                        'Visitor',  1, 0);


-- CRICOS Provider Dimension
CREATE TABLE IF NOT EXISTS dim_provider (
    provider_id           VARCHAR(10)  NOT NULL,
    provider_name         VARCHAR(250) NOT NULL,
    provider_type         VARCHAR(60)  COMMENT 'University, TAFE, English Language, School, etc.',
    state_code            VARCHAR(3),
    website               VARCHAR(300),
    registration_status   VARCHAR(30),
    registration_end_date DATE,
    _etl_source           VARCHAR(150),
    _etl_loaded_at        DATETIME,
    PRIMARY KEY (provider_id),
    KEY idx_prov_state (state_code),
    KEY idx_prov_type  (provider_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- CRICOS Course Dimension
CREATE TABLE IF NOT EXISTS dim_course (
    cricos_code         VARCHAR(10)   NOT NULL,
    course_name         VARCHAR(350)  NOT NULL,
    field_of_education  VARCHAR(250),
    broad_field         VARCHAR(120),
    duration_weeks      DECIMAL(6,1),
    min_age             TINYINT UNSIGNED,
    annual_fees_aud     DECIMAL(10,2),
    provider_id         VARCHAR(10),
    _etl_source         VARCHAR(150),
    _etl_loaded_at      DATETIME,
    PRIMARY KEY (cricos_code),
    KEY idx_course_provider (provider_id),
    KEY idx_course_field    (field_of_education(60)),
    KEY idx_course_broad    (broad_field)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- CRICOS Provider Location Dimension
CREATE TABLE IF NOT EXISTS dim_provider_location (
    location_id     VARCHAR(20)  NOT NULL,
    provider_id     VARCHAR(10)  NOT NULL,
    location_name   VARCHAR(250),
    address         VARCHAR(400),
    suburb          VARCHAR(120),
    state_code      VARCHAR(3),
    postcode        VARCHAR(6),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (location_id),
    KEY idx_loc_provider (provider_id),
    KEY idx_loc_state    (state_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────────────────────
-- FACT TABLES
-- ─────────────────────────────────────────────────────────────────────────────

-- RBA Exchange Rates (F11, F11.1)
-- Grain: one row per rate_date × series_id
CREATE TABLE IF NOT EXISTS fact_exchange_rate (
    id              BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    rate_date       DATE             NOT NULL,
    series_id       VARCHAR(12)      NOT NULL,
    currency_pair   VARCHAR(80)      COMMENT 'From RBA "Title" metadata row',
    units           VARCHAR(30),
    frequency       VARCHAR(15),
    value           DECIMAL(14,6),
    source_table    VARCHAR(10)      COMMENT 'f11 or f11.1',
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_rate  (rate_date, series_id),
    KEY idx_rate_date   (rate_date),
    KEY idx_rate_series (series_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Education International Student Enrolments (3.5M rows)
-- Grain: one row per year × month × nationality × state × sector × provider_type × new_to_aus × ends_this_year
-- YTD cumulative values per approved decision #1
-- Load via LOAD DATA LOCAL INFILE — never executemany
CREATE TABLE IF NOT EXISTS fact_student_enrolment (
    id                  BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    enrol_year          SMALLINT UNSIGNED NOT NULL,
    enrol_month         TINYINT UNSIGNED  NOT NULL,
    nationality         VARCHAR(200)      NOT NULL,
    country_id          SMALLINT UNSIGNED COMMENT 'FK dim_country (populated post-load)',
    state_code          VARCHAR(3),
    sector              VARCHAR(60),
    provider_type       VARCHAR(60),
    new_to_australia    VARCHAR(5)        COMMENT 'Yes | No',
    ends_this_year      VARCHAR(5)        COMMENT 'Yes | No',
    ytd_enrolments      INT UNSIGNED,
    ytd_commencements   INT UNSIGNED,
    total               INT UNSIGNED,
    _etl_source         VARCHAR(150),
    _etl_loaded_at      DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_enrol (
        enrol_year,
        enrol_month,
        nationality(100),
        state_code,
        sector(40),
        provider_type(40),
        new_to_australia,
        ends_this_year
    ),
    KEY idx_enrol_ym          (enrol_year, enrol_month),
    KEY idx_enrol_nationality (nationality(60)),
    KEY idx_enrol_state       (state_code),
    KEY idx_enrol_sector      (sector),
    KEY idx_enrol_country_fk  (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  ROW_FORMAT=COMPRESSED
  COMMENT='YTD cumulative enrolments — use latest month per year for point-in-time analysis';


-- Home Affairs BP0015 — Student Visa Activity
-- Consolidates lodged + granted + grant_rate into single measure column
-- Grain: one row per applicant_type × sector × financial_year × measure
CREATE TABLE IF NOT EXISTS fact_student_visa_activity (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    applicant_type  VARCHAR(40)  NOT NULL,
    sector          VARCHAR(60)  NOT NULL,
    financial_year  VARCHAR(7)   NOT NULL COMMENT 'YYYY-YY e.g. 2023-24',
    measure         VARCHAR(20)  NOT NULL COMMENT 'lodged | granted | grant_rate_pct',
    value           DECIMAL(12,2),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_sva   (applicant_type(30), sector(40), financial_year, measure),
    KEY idx_sva_fy      (financial_year),
    KEY idx_sva_sector  (sector)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Home Affairs BP0014 — Temporary Skilled Visa (granted + holders)
-- Grain: one row per visa_subclass × nationality × financial_year × state_code × measure
CREATE TABLE IF NOT EXISTS fact_temp_skilled_visa (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    visa_subclass   VARCHAR(10)  NOT NULL,
    nationality     VARCHAR(200) NOT NULL,
    country_id      SMALLINT UNSIGNED,
    financial_year  VARCHAR(10)  NOT NULL,
    state_code      VARCHAR(3)   NOT NULL DEFAULT 'AUS',
    measure         VARCHAR(15)  NOT NULL COMMENT 'granted | holders',
    value           DECIMAL(12,2),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_tsv   (visa_subclass, nationality(80), financial_year, state_code, measure),
    KEY idx_tsv_fy      (financial_year),
    KEY idx_tsv_visa    (visa_subclass),
    KEY idx_tsv_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Home Affairs BP0016 — Temporary Graduate Visa (lodged + granted)
-- Grain: one row per stream × nationality × financial_year × measure
CREATE TABLE IF NOT EXISTS fact_temp_graduate_visa (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    stream          VARCHAR(100) NOT NULL,
    nationality     VARCHAR(200) NOT NULL,
    country_id      SMALLINT UNSIGNED,
    financial_year  VARCHAR(10)  NOT NULL,
    measure         VARCHAR(15)  NOT NULL COMMENT 'lodged | granted',
    value           DECIMAL(12,2),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_tgv   (stream(60), nationality(80), financial_year, measure),
    KEY idx_tgv_fy      (financial_year),
    KEY idx_tgv_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Home Affairs BP0068 — Permanent Migration Programme Outcomes
-- Renamed from ha_migration_child_outcomes (BP0068 covers permanent migration, not child outcomes)
-- Grain: one row per visa_type × birth_country × outcome_measure × period
CREATE TABLE IF NOT EXISTS fact_permanent_migration (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    visa_type       VARCHAR(60)  NOT NULL DEFAULT '',
    birth_country   VARCHAR(200) NOT NULL DEFAULT '',
    country_id      SMALLINT UNSIGNED,
    outcome_measure VARCHAR(250) NOT NULL DEFAULT '',
    period          VARCHAR(15)  NOT NULL DEFAULT '',
    value           DECIMAL(14,2),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_pm     (visa_type(40), birth_country(80), outcome_measure(100), period),
    KEY idx_pm_period    (period),
    KEY idx_pm_visa      (visa_type),
    KEY idx_pm_country   (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='BP0068 Permanent Migration Programme — renamed from ha_migration_child_outcomes';


-- Skilled Migration Programme Summary
-- Grain: one row per financial_year × visa_subclass_k × stream_k × state_k × measure
-- Generated columns used because visa_subclass, stream, state_code are all nullable
CREATE TABLE IF NOT EXISTS fact_skilled_migration (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    financial_year  VARCHAR(7)   NOT NULL,
    visa_subclass   VARCHAR(10),
    stream          VARCHAR(100),
    state_code      VARCHAR(3),
    measure         VARCHAR(100) NOT NULL,
    value           DECIMAL(14,2),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated non-null key columns (approved decision #7)
    visa_subclass_k VARCHAR(10) GENERATED ALWAYS AS (COALESCE(visa_subclass, '')) STORED NOT NULL,
    stream_k        VARCHAR(100) GENERATED ALWAYS AS (COALESCE(stream, '')) STORED NOT NULL,
    state_k         VARCHAR(3)  GENERATED ALWAYS AS (COALESCE(state_code, '')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_sm    (financial_year, visa_subclass_k, stream_k(60), state_k, measure(60)),
    KEY idx_sm_fy       (financial_year),
    KEY idx_sm_visa     (visa_subclass_k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- JSA Internet Vacancy Index
-- Grain: one row per vacancy_period × anzsco_code_k × state_code × measure
-- anzsco_code can be NULL for skill-level aggregate rows
CREATE TABLE IF NOT EXISTS fact_job_vacancy (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    vacancy_period  VARCHAR(7)      NOT NULL COMMENT 'YYYY-MM',
    anzsco_code     VARCHAR(8),
    occupation_id   INT UNSIGNED,
    anzsco_level    TINYINT         COMMENT '2=Sub-major 4=Unit group',
    state_code      VARCHAR(3)      NOT NULL DEFAULT 'AUS',
    measure         VARCHAR(20)     NOT NULL COMMENT 'SA | Trend | Original',
    vacancy_count   INT,
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated non-null key column for nullable anzsco_code
    anzsco_code_k   VARCHAR(8)  GENERATED ALWAYS AS (COALESCE(anzsco_code, '')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_jv    (vacancy_period, anzsco_code_k, state_code, measure),
    KEY idx_jv_period   (vacancy_period),
    KEY idx_jv_anzsco   (anzsco_code_k),
    KEY idx_jv_state    (state_code),
    KEY idx_jv_measure  (measure)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- JSA Occupation Shortage List
-- Grain: one row per anzsco_code × anzsco_level × state_code × assessment_year_k
CREATE TABLE IF NOT EXISTS fact_occupation_shortage (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    anzsco_code     VARCHAR(8)   NOT NULL,
    anzsco_level    TINYINT      NOT NULL,
    occupation_id   INT UNSIGNED,
    occupation_name VARCHAR(250),
    shortage_status VARCHAR(40),
    osca_category   VARCHAR(80),
    assessment_year VARCHAR(4),
    state_code      VARCHAR(3)   NOT NULL DEFAULT 'AUS',
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated key for nullable assessment_year
    assessment_year_k VARCHAR(4) GENERATED ALWAYS AS (COALESCE(assessment_year, '0')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_osl   (anzsco_code, anzsco_level, state_code, assessment_year_k),
    KEY idx_osl_status  (shortage_status),
    KEY idx_osl_anzsco  (anzsco_code),
    KEY idx_osl_year    (assessment_year_k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ABS Labour Force (monthly)
-- Grain: one row per lf_period × series_id
CREATE TABLE IF NOT EXISTS fact_labour_force (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lf_period       VARCHAR(7)      NOT NULL COMMENT 'YYYY-MM',
    series_id       VARCHAR(30)     NOT NULL,
    measure         VARCHAR(250)    COMMENT 'Descriptive label — not used as key',
    sex             VARCHAR(15),
    adjustment_type VARCHAR(30),
    state_code      VARCHAR(3),
    value           DECIMAL(12,3)   COMMENT '000 persons',
    unit            VARCHAR(30),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_lf    (lf_period, series_id),
    KEY idx_lf_period   (lf_period),
    KEY idx_lf_series   (series_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ABS Consumer Price Index (quarterly)
-- Grain: one row per cpi_period × series_id
CREATE TABLE IF NOT EXISTS fact_cpi (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    cpi_period      VARCHAR(7)   NOT NULL COMMENT 'YYYY-QN e.g. 2024-Q1, or YYYY-MM',
    series_id       VARCHAR(30)  NOT NULL,
    title           VARCHAR(250),
    cpi_group       VARCHAR(120),
    city            VARCHAR(80),
    measure         VARCHAR(40),
    value           DECIMAL(10,3),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_cpi   (cpi_period, series_id),
    KEY idx_cpi_period  (cpi_period)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ABS Net Overseas Migration
-- Grain: one row per nom_period × country_name_k × state_code × direction
CREATE TABLE IF NOT EXISTS fact_overseas_migration (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    nom_period      VARCHAR(7)   NOT NULL COMMENT 'YYYY-MM or YYYY',
    country_name    VARCHAR(200),
    country_id      SMALLINT UNSIGNED,
    state_code      VARCHAR(3)   NOT NULL DEFAULT 'AUS',
    direction       VARCHAR(15)  NOT NULL COMMENT 'net | arrivals | departures',
    series_id       VARCHAR(30),
    value           DECIMAL(12,1),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated key for nullable country_name
    country_name_k  VARCHAR(200) GENERATED ALWAYS AS (COALESCE(country_name, '')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_nom   (nom_period, country_name_k(80), state_code, direction),
    KEY idx_nom_period  (nom_period),
    KEY idx_nom_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ABS Estimated Resident Population by Country of Birth
-- Grain: one row per erp_period × country_name_k × state_code
CREATE TABLE IF NOT EXISTS fact_population_by_cob (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    erp_period      VARCHAR(7)   NOT NULL COMMENT 'YYYY or YYYY-MM',
    country_name    VARCHAR(200),
    country_id      SMALLINT UNSIGNED,
    state_code      VARCHAR(3)   NOT NULL DEFAULT 'AUS',
    series_id       VARCHAR(30),
    population      DECIMAL(14,1),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated key for nullable country_name
    country_name_k  VARCHAR(200) GENERATED ALWAYS AS (COALESCE(country_name, '')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_erp   (erp_period, country_name_k(80), state_code),
    KEY idx_erp_period  (erp_period),
    KEY idx_erp_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- NOTE: fact_abs_education_output EXCLUDED per approved decision #4


-- ─────────────────────────────────────────────────────────────────────────────
-- REFERENCE TABLES
-- ─────────────────────────────────────────────────────────────────────────────

-- JSA Occupation Profiles (earnings, employment outlook, educational requirements)
-- Grain: one row per anzsco_code × profile_measure × dimension × profile_year_k
CREATE TABLE IF NOT EXISTS ref_occupation_profile (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    anzsco_code     VARCHAR(8)   NOT NULL,
    occupation_id   INT UNSIGNED,
    occupation_name VARCHAR(250),
    profile_measure VARCHAR(100) NOT NULL COMMENT 'Sheet name: Earnings, Employment, Education, etc.',
    dimension       VARCHAR(200) NOT NULL COMMENT 'Column header from JSA file',
    value_num       DECIMAL(14,2),
    value_text      VARCHAR(400),
    profile_year    VARCHAR(4),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated key for nullable profile_year
    profile_year_k  VARCHAR(4)   GENERATED ALWAYS AS (COALESCE(profile_year, '0')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ocp   (anzsco_code, profile_measure(50), dimension(80), profile_year_k),
    KEY idx_ocp_anzsco  (anzsco_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Skilled Migration — By Country of Birth × Occupation
-- Grain: one row per financial_year × country_name_k × anzsco_code_k × visa_subclass_k × measure
CREATE TABLE IF NOT EXISTS ref_skilled_migration_by_cob_occupation (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    financial_year  VARCHAR(7)   NOT NULL,
    country_name    VARCHAR(200),
    country_id      SMALLINT UNSIGNED,
    anzsco_code     VARCHAR(8),
    occupation_id   INT UNSIGNED,
    occupation_name VARCHAR(250),
    visa_subclass   VARCHAR(10),
    measure         VARCHAR(100) NOT NULL,
    value           DECIMAL(14,2),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    -- Generated keys for nullable dimension columns
    country_name_k  VARCHAR(200) GENERATED ALWAYS AS (COALESCE(country_name, '')) STORED NOT NULL,
    anzsco_code_k   VARCHAR(8)   GENERATED ALWAYS AS (COALESCE(anzsco_code, '')) STORED NOT NULL,
    visa_subclass_k VARCHAR(10)  GENERATED ALWAYS AS (COALESCE(visa_subclass, '')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_smc   (financial_year, country_name_k(80), anzsco_code_k, visa_subclass_k, measure(50)),
    KEY idx_smc_fy      (financial_year),
    KEY idx_smc_anzsco  (anzsco_code_k),
    KEY idx_smc_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────────────────────
-- BRIDGE TABLES
-- ─────────────────────────────────────────────────────────────────────────────

-- CRICOS Course × Location bridge (M:M)
CREATE TABLE IF NOT EXISTS bridge_course_location (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    cricos_code     VARCHAR(10)  NOT NULL,
    location_id     VARCHAR(20)  NOT NULL,
    provider_id     VARCHAR(10),
    _etl_source     VARCHAR(150),
    _etl_loaded_at  DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uk_cl    (cricos_code, location_id),
    KEY idx_cl_course   (cricos_code),
    KEY idx_cl_location (location_id),
    KEY idx_cl_provider (provider_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────────────────────
-- STAGING TABLES
-- ─────────────────────────────────────────────────────────────────────────────

-- SkillSelect EOI (staging — not yet production-grade extraction)
-- Populated by ETL/skillselect_csv_etl.py separately from run_all.py
CREATE TABLE IF NOT EXISTS stg_skillselect_eoi (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    as_at_month      VARCHAR(7)      NOT NULL COMMENT 'MM/YYYY from SkillSelect wizard',
    visa_type        VARCHAR(5)      NOT NULL,
    eoi_status       VARCHAR(15)     NOT NULL,
    source_view      VARCHAR(80)     NOT NULL,
    dimension_1_name VARCHAR(80),
    dimension_1_val  VARCHAR(250),
    dimension_2_name VARCHAR(80),
    dimension_2_val  VARCHAR(150),
    eoi_count        INT UNSIGNED,
    captured_at      DATETIME        NOT NULL,
    -- Generated keys for nullable dimension values
    dim1_val_k       VARCHAR(250) GENERATED ALWAYS AS (COALESCE(dimension_1_val, '')) STORED NOT NULL,
    dim2_val_k       VARCHAR(150) GENERATED ALWAYS AS (COALESCE(dimension_2_val, '')) STORED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_eoi   (as_at_month, visa_type, eoi_status, source_view, dim1_val_k(100), dim2_val_k(60)),
    KEY idx_eoi_month   (as_at_month),
    KEY idx_eoi_visa    (visa_type),
    KEY idx_eoi_view    (source_view)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='SkillSelect EOI staging — promoted to fact table when extraction is stable';


-- ─────────────────────────────────────────────────────────────────────────────
-- DERIVED VIEWS (occupation intelligence — decision #8)
-- ─────────────────────────────────────────────────────────────────────────────

-- Occupation intelligence mart: joins JSA shortage + vacancy + profile data
-- NOT a source-of-truth table — derived read-only view
CREATE OR REPLACE VIEW vw_occupation_intelligence AS
SELECT
    os.anzsco_code,
    os.occupation_name,
    os.shortage_status,
    os.osca_category,
    os.assessment_year,
    os.state_code,
    vac.vacancy_count      AS latest_vacancies,
    vac.vacancy_period     AS vacancy_as_at,
    prof_earn.value_num    AS median_annual_salary_aud,
    prof_emp.value_text    AS employment_size
FROM fact_occupation_shortage os
LEFT JOIN fact_job_vacancy vac
    ON  vac.anzsco_code_k = os.anzsco_code
    AND vac.state_code    = os.state_code
    AND vac.measure       = 'SA'
    AND vac.vacancy_period = (
        SELECT MAX(v2.vacancy_period) FROM fact_job_vacancy v2
        WHERE v2.anzsco_code_k = os.anzsco_code
          AND v2.state_code    = os.state_code
          AND v2.measure       = 'SA'
    )
LEFT JOIN ref_occupation_profile prof_earn
    ON  prof_earn.anzsco_code   = os.anzsco_code
    AND prof_earn.profile_measure LIKE '%Earn%'
    AND prof_earn.dimension LIKE '%Median%'
LEFT JOIN ref_occupation_profile prof_emp
    ON  prof_emp.anzsco_code   = os.anzsco_code
    AND prof_emp.profile_measure LIKE '%Employ%'
    AND prof_emp.dimension LIKE '%Size%';


SET foreign_key_checks = 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE COUNT VERIFICATION
-- After applying: SELECT COUNT(*) FROM information_schema.TABLES
--   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE';
-- Expected: 25 base tables
--
-- Tables:
--   1  etl_audit_log
--   2  dim_country          3  dim_state           4  dim_occupation
--   5  dim_visa_subclass    6  dim_provider         7  dim_course
--   8  dim_provider_location
--   9  fact_exchange_rate   10 fact_student_enrolment
--  11  fact_student_visa_activity  12 fact_temp_skilled_visa
--  13  fact_temp_graduate_visa     14 fact_permanent_migration
--  15  fact_skilled_migration      16 fact_job_vacancy
--  17  fact_occupation_shortage    18 fact_labour_force
--  19  fact_cpi             20 fact_overseas_migration
--  21  fact_population_by_cob
--  22  ref_occupation_profile
--  23  ref_skilled_migration_by_cob_occupation
--  24  bridge_course_location
--  25  stg_skillselect_eoi
-- ─────────────────────────────────────────────────────────────────────────────
