-- ==========================================
-- AIC Occupation Intelligence Database
-- SkillSelect ETL Pipeline - Schema DDL
-- SQLite version (preserved as backup before MySQL migration)
-- Original: ETL/schema.sql
-- Copied:   2026-07-13
-- ==========================================

CREATE TABLE IF NOT EXISTS occupation_ceilings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    anzsco_code         TEXT NOT NULL,
    occupation_name     TEXT NOT NULL,
    visa_subclass       TEXT NOT NULL,
    state               TEXT,
    ceiling             INTEGER,
    invitations_issued  INTEGER,
    fill_rate_pct       REAL,
    trend               TEXT,
    data_month          TEXT NOT NULL,
    extracted_at        TEXT NOT NULL,
    source_url          TEXT,
    UNIQUE(anzsco_code, visa_subclass, state, data_month)
);

CREATE TABLE IF NOT EXISTS occupation_shortage_ratings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    anzsco_code         TEXT NOT NULL,
    occupation_name     TEXT NOT NULL,
    shortage_status     TEXT,
    shortage_level      TEXT,
    state               TEXT,
    osl_year            INTEGER,
    source              TEXT,
    extracted_at        TEXT NOT NULL,
    UNIQUE(anzsco_code, state, osl_year)
);

CREATE TABLE IF NOT EXISTS visa_eligibility (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    anzsco_code         TEXT NOT NULL,
    occupation_name     TEXT NOT NULL,
    list_type           TEXT NOT NULL,
    visa_subclass       TEXT,
    assessing_body      TEXT,
    effective_date      TEXT,
    source              TEXT,
    extracted_at        TEXT NOT NULL,
    UNIQUE(anzsco_code, list_type, visa_subclass)
);

CREATE TABLE IF NOT EXISTS occupation_intelligence (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    anzsco_code             TEXT NOT NULL,
    occupation_name         TEXT NOT NULL,
    shortage_status         TEXT,
    shortage_level          TEXT,
    shortage_national       INTEGER DEFAULT 0,
    eligible_189            INTEGER DEFAULT 0,
    eligible_190            INTEGER DEFAULT 0,
    eligible_491            INTEGER DEFAULT 0,
    list_type               TEXT,
    assessing_body          TEXT,
    ceiling_189             INTEGER,
    ceiling_190             INTEGER,
    ceiling_491             INTEGER,
    invitations_189         INTEGER,
    invitations_190         INTEGER,
    invitations_491         INTEGER,
    fill_rate_189_pct       REAL,
    fill_rate_190_pct       REAL,
    fill_rate_491_pct       REAL,
    trend_189               TEXT,
    trend_190               TEXT,
    trend_491               TEXT,
    median_salary_aud       INTEGER,
    data_month              TEXT NOT NULL,
    last_updated            TEXT NOT NULL,
    UNIQUE(anzsco_code, data_month)
);

CREATE INDEX IF NOT EXISTS idx_oc_anzsco     ON occupation_ceilings(anzsco_code);
CREATE INDEX IF NOT EXISTS idx_oc_month      ON occupation_ceilings(data_month);
CREATE INDEX IF NOT EXISTS idx_oc_visa       ON occupation_ceilings(visa_subclass);
CREATE INDEX IF NOT EXISTS idx_osr_anzsco    ON occupation_shortage_ratings(anzsco_code);
CREATE INDEX IF NOT EXISTS idx_ve_anzsco     ON visa_eligibility(anzsco_code);
CREATE INDEX IF NOT EXISTS idx_oi_anzsco     ON occupation_intelligence(anzsco_code);
CREATE INDEX IF NOT EXISTS idx_oi_shortage   ON occupation_intelligence(shortage_status);

-- ──────────────────────────────────────────────────────────────────────────
-- SkillSelect CSV Export — Long Format (Tidy Data)
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skillselect_eoi_data (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    as_at_month      TEXT NOT NULL,
    visa_type        TEXT NOT NULL,
    eoi_status       TEXT NOT NULL,
    source_view      TEXT NOT NULL,
    dimension_1_name TEXT,
    dimension_1_val  TEXT,
    dimension_2_name TEXT,
    dimension_2_val  TEXT,
    eoi_count        INTEGER,
    captured_at      TEXT NOT NULL,
    UNIQUE(as_at_month, visa_type, eoi_status, source_view,
           dimension_1_val, dimension_2_val)
);

CREATE INDEX IF NOT EXISTS idx_ss_month     ON skillselect_eoi_data(as_at_month);
CREATE INDEX IF NOT EXISTS idx_ss_visa      ON skillselect_eoi_data(visa_type);
CREATE INDEX IF NOT EXISTS idx_ss_dim1      ON skillselect_eoi_data(dimension_1_name, dimension_1_val);
CREATE INDEX IF NOT EXISTS idx_ss_view      ON skillselect_eoi_data(source_view);

-- ─────────────────────────────────────────────────────────────────────────────
-- HOME AFFAIRS — Student Visa Program (BP0015)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ha_student_visa_lodged (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    applicant_type  TEXT,
    sector          TEXT,
    financial_year  TEXT,
    lodged_count    REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(applicant_type, sector, financial_year)
);
CREATE TABLE IF NOT EXISTS ha_student_visa_granted (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    applicant_type  TEXT,
    sector          TEXT,
    financial_year  TEXT,
    granted_count   REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(applicant_type, sector, financial_year)
);
CREATE TABLE IF NOT EXISTS ha_student_visa_grant_rates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    applicant_type  TEXT,
    sector          TEXT,
    financial_year  TEXT,
    grant_rate_pct  REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(applicant_type, sector, financial_year)
);
-- HOME AFFAIRS — Temporary Resident Skilled Visas (BP0014)
CREATE TABLE IF NOT EXISTS ha_temp_skilled_visa_granted (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    visa_subclass   TEXT,
    nationality     TEXT,
    financial_year  TEXT,
    granted_count   REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(visa_subclass, nationality, financial_year)
);
CREATE TABLE IF NOT EXISTS ha_temp_skilled_visa_holders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    visa_subclass   TEXT,
    nationality     TEXT,
    state_territory TEXT,
    as_at_date      TEXT,
    holder_count    REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(visa_subclass, nationality, state_territory, as_at_date)
);
-- HOME AFFAIRS — Temporary Graduate Visas (BP0016)
CREATE TABLE IF NOT EXISTS ha_temp_graduate_visa_lodged (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stream          TEXT,
    nationality     TEXT,
    financial_year  TEXT,
    lodged_count    REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(stream, nationality, financial_year)
);
CREATE TABLE IF NOT EXISTS ha_temp_graduate_visa_granted (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stream          TEXT,
    nationality     TEXT,
    financial_year  TEXT,
    granted_count   REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(stream, nationality, financial_year)
);
-- HOME AFFAIRS — Migration & Child Outcomes (BP0068) → renamed fact_permanent_migration in MySQL
CREATE TABLE IF NOT EXISTS ha_migration_child_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    visa_type       TEXT,
    birth_country   TEXT,
    outcome_measure TEXT,
    period          TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(visa_type, birth_country, outcome_measure, period)
);
CREATE INDEX IF NOT EXISTS idx_ha_lodged_fy  ON ha_student_visa_lodged(financial_year);
CREATE INDEX IF NOT EXISTS idx_ha_granted_fy ON ha_student_visa_granted(financial_year);

-- ─────────────────────────────────────────────────────────────────────────────
-- CRICOS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cricos_institutions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id             TEXT,
    provider_name           TEXT,
    provider_type           TEXT,
    state                   TEXT,
    website                 TEXT,
    status                  TEXT,
    registration_end_date   TEXT,
    _etl_source             TEXT,
    _etl_loaded_at          TEXT,
    UNIQUE(provider_id)
);
CREATE TABLE IF NOT EXISTS cricos_courses (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cricos_code             TEXT,
    course_name             TEXT,
    field_of_education      TEXT,
    broad_field             TEXT,
    duration_weeks          REAL,
    min_age                 INTEGER,
    fees_aud                REAL,
    provider_id             TEXT,
    _etl_source             TEXT,
    _etl_loaded_at          TEXT,
    UNIQUE(cricos_code)
);
CREATE TABLE IF NOT EXISTS cricos_locations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id             TEXT,
    provider_id             TEXT,
    location_name           TEXT,
    address                 TEXT,
    suburb                  TEXT,
    state                   TEXT,
    postcode                TEXT,
    _etl_source             TEXT,
    _etl_loaded_at          TEXT,
    UNIQUE(location_id)
);
CREATE TABLE IF NOT EXISTS cricos_course_locations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cricos_code             TEXT,
    location_id             TEXT,
    provider_id             TEXT,
    _etl_source             TEXT,
    _etl_loaded_at          TEXT,
    UNIQUE(cricos_code, location_id)
);
CREATE INDEX IF NOT EXISTS idx_cricos_provider  ON cricos_courses(provider_id);
CREATE INDEX IF NOT EXISTS idx_cricos_field     ON cricos_courses(field_of_education);
CREATE INDEX IF NOT EXISTS idx_cricos_state     ON cricos_locations(state);

-- ─────────────────────────────────────────────────────────────────────────────
-- RBA — Exchange Rates
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rba_exchange_rates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    series_id       TEXT NOT NULL,
    title           TEXT,
    units           TEXT,
    frequency       TEXT,
    value           REAL,
    source_table    TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(date, series_id)
);
CREATE INDEX IF NOT EXISTS idx_rba_date      ON rba_exchange_rates(date);
CREATE INDEX IF NOT EXISTS idx_rba_series    ON rba_exchange_rates(series_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- ABS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS abs_labour_force (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    frequency       TEXT,
    measure         TEXT,
    sex             TEXT,
    adjustment_type TEXT,
    state           TEXT,
    value           REAL,
    unit            TEXT,
    series_id       TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, measure, sex, adjustment_type, state)
);
CREATE TABLE IF NOT EXISTS abs_employment_by_industry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    industry_div    TEXT,
    adjustment_type TEXT,
    value           REAL,
    unit            TEXT,
    series_id       TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, industry_div, adjustment_type)
);
CREATE TABLE IF NOT EXISTS abs_employment_by_occupation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    occupation_major TEXT,
    sex             TEXT,
    measure         TEXT,
    value           REAL,
    unit            TEXT,
    series_id       TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, occupation_major, sex, measure)
);
CREATE TABLE IF NOT EXISTS abs_cpi (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    series_id       TEXT NOT NULL,
    title           TEXT,
    group_          TEXT,
    city            TEXT,
    measure         TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, series_id)
);
CREATE TABLE IF NOT EXISTS abs_net_overseas_migration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    country_of_birth TEXT,
    state_territory TEXT,
    direction       TEXT,
    value           REAL,
    series_id       TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, country_of_birth, state_territory, direction)
);
CREATE TABLE IF NOT EXISTS abs_erp_country_of_birth (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    country_of_birth TEXT,
    state_territory TEXT,
    value           REAL,
    series_id       TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, country_of_birth, state_territory)
);
CREATE TABLE IF NOT EXISTS abs_education_output (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    series_id       TEXT NOT NULL,
    title           TEXT,
    industry_group  TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, series_id)
);
CREATE INDEX IF NOT EXISTS idx_abs_lf_period   ON abs_labour_force(period);
CREATE INDEX IF NOT EXISTS idx_abs_cpi_period  ON abs_cpi(period);
CREATE INDEX IF NOT EXISTS idx_abs_nom_period  ON abs_net_overseas_migration(period);
CREATE INDEX IF NOT EXISTS idx_abs_erp_period  ON abs_erp_country_of_birth(period);

-- ─────────────────────────────────────────────────────────────────────────────
-- JSA
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jsa_internet_vacancies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    anzsco_code     TEXT,
    occupation_name TEXT,
    anzsco_level    INTEGER,
    state_territory TEXT,
    measure         TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(period, anzsco_code, state_territory, measure)
);
CREATE TABLE IF NOT EXISTS jsa_occupation_shortage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    anzsco_code     TEXT NOT NULL,
    anzsco_level    INTEGER,
    occupation_name TEXT,
    shortage_status TEXT,
    osca_category   TEXT,
    assessment_year TEXT,
    state_territory TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(anzsco_code, anzsco_level, state_territory, assessment_year)
);
CREATE TABLE IF NOT EXISTS jsa_occupation_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    anzsco_code     TEXT,
    occupation_name TEXT,
    measure         TEXT,
    dimension       TEXT,
    value           REAL,
    value_text      TEXT,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(anzsco_code, measure, dimension)
);
CREATE INDEX IF NOT EXISTS idx_jsa_ivi_period   ON jsa_internet_vacancies(period);
CREATE INDEX IF NOT EXISTS idx_jsa_ivi_anzsco   ON jsa_internet_vacancies(anzsco_code);
CREATE INDEX IF NOT EXISTS idx_jsa_osl_anzsco   ON jsa_occupation_shortage(anzsco_code);
CREATE INDEX IF NOT EXISTS idx_jsa_osl_status   ON jsa_occupation_shortage(shortage_status);

-- ─────────────────────────────────────────────────────────────────────────────
-- DEPARTMENT OF EDUCATION
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS education_int_students_historical (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            INTEGER,
    nationality     TEXT,
    state           TEXT,
    sector          TEXT,
    measure         TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(year, nationality, state, sector, measure)
);
CREATE TABLE IF NOT EXISTS education_sa4_enrolments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            INTEGER,
    month           TEXT,
    sa4_name        TEXT,
    remoteness      TEXT,
    sector          TEXT,
    broad_field     TEXT,
    measure         TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(year, month, sa4_name, sector, broad_field, measure)
);
CREATE INDEX IF NOT EXISTS idx_edu_hist_year      ON education_int_students_historical(year);
CREATE INDEX IF NOT EXISTS idx_edu_sa4_year       ON education_sa4_enrolments(year);

-- ─────────────────────────────────────────────────────────────────────────────
-- SKILLED MIGRATION
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skilled_migration_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    financial_year  TEXT NOT NULL,
    visa_subclass   TEXT,
    stream          TEXT,
    state_territory TEXT,
    measure         TEXT,
    value           REAL,
    _etl_source     TEXT,
    _etl_loaded_at  TEXT,
    UNIQUE(financial_year, visa_subclass, stream, state_territory, measure)
);
CREATE TABLE IF NOT EXISTS skilled_migration_country_occupation (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    financial_year      TEXT,
    country_of_birth    TEXT,
    anzsco_code         TEXT,
    occupation_name     TEXT,
    visa_subclass       TEXT,
    value               REAL,
    measure             TEXT,
    _etl_source         TEXT,
    _etl_loaded_at      TEXT,
    UNIQUE(financial_year, country_of_birth, anzsco_code, visa_subclass, measure)
);
CREATE INDEX IF NOT EXISTS idx_sm_sum_fy     ON skilled_migration_summary(financial_year);
CREATE INDEX IF NOT EXISTS idx_sm_co_fy      ON skilled_migration_country_occupation(financial_year);
CREATE INDEX IF NOT EXISTS idx_sm_co_anzsco  ON skilled_migration_country_occupation(anzsco_code);
