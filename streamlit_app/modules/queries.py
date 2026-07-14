"""
queries.py
===========
Every SQL query the app runs, in one place, each wrapped in
st.cache_data(ttl=...) so pages share a cache and the "Refresh Data" button
(app.py) can clear all of them with a single st.cache_data.clear().

Design rules followed throughout this file (see docs/tableau_data_dictionary.md
and docs/final_migration_summary.md for the full rationale):
  - fact_student_enrolment is YTD-cumulative -> every "total" query pins to
    one specific (year, month) snapshot; never SUM() across months.
  - fact_student_visa_activity and fact_skilled_migration have NO
    country/nationality column -- queries here never claim otherwise.
  - financial_year on those two tables is stored as "2005_06"; normalized to
    "2005-06" at the query layer (REPLACE), not by mutating the database.
  - dim_course.broad_field / field_of_education are 100% NULL -- no query
    here references them; course "demand" queries use what actually exists
    (course/provider counts), never a fabricated field-of-education split.
  - Read-only throughout: every function is a SELECT. Nothing in this module
    writes to the database.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.db import run_query

CACHE_TTL = 900  # 15 minutes: source data refreshes on a monthly/quarterly/
                 # annual cadence at fastest, so this only needs to be short
                 # enough that a manual ETL reload during a work session is
                 # picked up promptly via the Refresh Data button.


# ── ETL freshness (used on every page header) ──────────────────────────────

@st.cache_data(ttl=CACHE_TTL)
def get_last_etl_updates() -> pd.DataFrame:
    return run_query(
        """
        SELECT source, table_name, MAX(completed_at) AS last_completed
        FROM etl_audit_log
        WHERE status = 'completed'
        GROUP BY source, table_name
        ORDER BY source, table_name
        """
    )


@st.cache_data(ttl=CACHE_TTL)
def get_recent_failures(limit: int = 10) -> pd.DataFrame:
    return run_query(
        """
        SELECT source, table_name, started_at, error_message
        FROM etl_audit_log
        WHERE status = 'failed'
        ORDER BY started_at DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )


# ── Enrolment (fact_student_enrolment) ──────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL)
def get_latest_period() -> tuple[int, int]:
    df = run_query("SELECT MAX(enrol_year) AS y FROM fact_student_enrolment")
    year = int(df.iloc[0]["y"])
    df2 = run_query(
        "SELECT MAX(enrol_month) AS m FROM fact_student_enrolment WHERE enrol_year = :y",
        {"y": year},
    )
    month = int(df2.iloc[0]["m"])
    return year, month


@st.cache_data(ttl=CACHE_TTL)
def get_yearly_snapshot_totals(nationality: str | None = None) -> pd.DataFrame:
    """
    One row per year: the LATEST available month's YTD figure that year.
    This is the correct way to build a year-over-year enrolment trend from
    YTD-cumulative data -- never SUM across months within a year.
    """
    where_nat = "AND nationality = :nationality" if nationality else ""
    params = {"nationality": nationality} if nationality else {}
    sql = f"""
        SELECT e.enrol_year, e.enrol_month, SUM(e.ytd_enrolments) AS total_enrolments,
               SUM(e.ytd_commencements) AS total_commencements
        FROM fact_student_enrolment e
        JOIN (
            SELECT enrol_year, MAX(enrol_month) AS latest_month
            FROM fact_student_enrolment
            WHERE 1=1 {where_nat}
            GROUP BY enrol_year
        ) latest
          ON e.enrol_year = latest.enrol_year AND e.enrol_month = latest.latest_month
        WHERE 1=1 {where_nat}
        GROUP BY e.enrol_year, e.enrol_month
        ORDER BY e.enrol_year
    """
    return run_query(sql, params)


@st.cache_data(ttl=CACHE_TTL)
def get_enrolment_by_country_at(year: int, month: int) -> pd.DataFrame:
    """Enrolment total per nationality at one specific (year, month) snapshot."""
    return run_query(
        """
        SELECT nationality, SUM(ytd_enrolments) AS total_enrolments
        FROM fact_student_enrolment
        WHERE enrol_year = :year AND enrol_month = :month
        GROUP BY nationality
        ORDER BY total_enrolments DESC
        """,
        {"year": year, "month": month},
    )


@st.cache_data(ttl=CACHE_TTL)
def get_country_growth(current_year: int, current_month: int, prior_year: int) -> pd.DataFrame:
    """
    Per-country YoY comparison at the SAME month in two different years --
    the only fair comparison for YTD-cumulative data. Returns current,
    previous, and both raw values so callers can apply their own minimum-
    volume threshold before ranking growth/decline.
    """
    return run_query(
        """
        SELECT
            cur.nationality,
            cur.total_enrolments AS current_total,
            COALESCE(prev.total_enrolments, 0) AS previous_total
        FROM (
            SELECT nationality, SUM(ytd_enrolments) AS total_enrolments
            FROM fact_student_enrolment
            WHERE enrol_year = :current_year AND enrol_month = :current_month
            GROUP BY nationality
        ) cur
        LEFT JOIN (
            SELECT nationality, SUM(ytd_enrolments) AS total_enrolments
            FROM fact_student_enrolment
            WHERE enrol_year = :prior_year AND enrol_month = :current_month
            GROUP BY nationality
        ) prev ON cur.nationality = prev.nationality
        """,
        {"current_year": current_year, "current_month": current_month, "prior_year": prior_year},
    )


@st.cache_data(ttl=CACHE_TTL)
def get_all_nationalities() -> list[str]:
    df = run_query(
        "SELECT DISTINCT nationality FROM fact_student_enrolment ORDER BY nationality"
    )
    return df["nationality"].tolist()


@st.cache_data(ttl=CACHE_TTL)
def get_sector_breakdown_at(year: int, month: int, nationality: str | None = None) -> pd.DataFrame:
    where_nat = "AND nationality = :nationality" if nationality else ""
    params = {"year": year, "month": month}
    if nationality:
        params["nationality"] = nationality
    return run_query(
        f"""
        SELECT sector, SUM(ytd_enrolments) AS total_enrolments
        FROM fact_student_enrolment
        WHERE enrol_year = :year AND enrol_month = :month {where_nat}
        GROUP BY sector
        ORDER BY total_enrolments DESC
        """,
        params,
    )


@st.cache_data(ttl=CACHE_TTL)
def get_sector_yearly_trend(sectors: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Yearly (latest-month-per-year) enrolment trend broken down by sector."""
    where_sector = "AND e.sector IN :sectors" if sectors else ""
    sql = f"""
        SELECT e.enrol_year, e.sector, SUM(e.ytd_enrolments) AS total_enrolments
        FROM fact_student_enrolment e
        JOIN (
            SELECT enrol_year, MAX(enrol_month) AS latest_month
            FROM fact_student_enrolment GROUP BY enrol_year
        ) latest
          ON e.enrol_year = latest.enrol_year AND e.enrol_month = latest.latest_month
        WHERE 1=1 {where_sector}
        GROUP BY e.enrol_year, e.sector
        ORDER BY e.enrol_year, e.sector
    """
    params = {"sectors": tuple(sectors)} if sectors else {}
    return run_query(sql, params)


@st.cache_data(ttl=CACHE_TTL)
def get_country_sector_comparison(nationalities: tuple[str, ...], year: int, month: int) -> pd.DataFrame:
    if not nationalities:
        return pd.DataFrame(columns=["nationality", "sector", "total_enrolments"])
    return run_query(
        """
        SELECT nationality, sector, SUM(ytd_enrolments) AS total_enrolments
        FROM fact_student_enrolment
        WHERE enrol_year = :year AND enrol_month = :month
          AND nationality IN :nationalities
        GROUP BY nationality, sector
        """,
        {"year": year, "month": month, "nationalities": tuple(nationalities)},
    )


@st.cache_data(ttl=CACHE_TTL)
def get_country_yearly_trend(nationalities: tuple[str, ...]) -> pd.DataFrame:
    if not nationalities:
        return pd.DataFrame(columns=["nationality", "enrol_year", "total_enrolments"])
    return run_query(
        """
        SELECT e.nationality, e.enrol_year, SUM(e.ytd_enrolments) AS total_enrolments
        FROM fact_student_enrolment e
        JOIN (
            SELECT enrol_year, MAX(enrol_month) AS latest_month
            FROM fact_student_enrolment GROUP BY enrol_year
        ) latest
          ON e.enrol_year = latest.enrol_year AND e.enrol_month = latest.latest_month
        WHERE e.nationality IN :nationalities
        GROUP BY e.nationality, e.enrol_year
        ORDER BY e.nationality, e.enrol_year
        """,
        {"nationalities": tuple(nationalities)},
    )


# ── Visa & migration ─────────────────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL)
def get_table_row_count(table_name: str) -> int:
    """Generic existence/emptiness check, used before rendering a section."""
    df = run_query(f"SELECT COUNT(*) AS n FROM `{table_name}`")  # noqa: S608 - table_name is from a fixed internal allowlist, never user input
    return int(df.iloc[0]["n"])


@st.cache_data(ttl=CACHE_TTL)
def get_student_visa_activity() -> pd.DataFrame:
    """National-level (no country breakdown exists in the source)."""
    return run_query(
        """
        SELECT applicant_type, sector,
               REPLACE(financial_year, '_', '-') AS financial_year,
               measure, value
        FROM fact_student_visa_activity
        ORDER BY financial_year, applicant_type, sector, measure
        """
    )


@st.cache_data(ttl=CACHE_TTL)
def get_skilled_migration() -> pd.DataFrame:
    return run_query(
        """
        SELECT REPLACE(financial_year, '_', '-') AS financial_year,
               visa_subclass, stream, state_code, measure, value
        FROM fact_skilled_migration
        ORDER BY financial_year
        """
    )


@st.cache_data(ttl=CACHE_TTL)
def get_skilled_migration_countries() -> list[str]:
    df = run_query(
        """
        SELECT DISTINCT country_name FROM ref_skilled_migration_by_cob_occupation
        WHERE country_name IS NOT NULL ORDER BY country_name
        """
    )
    return df["country_name"].tolist()


@st.cache_data(ttl=CACHE_TTL)
def get_skilled_migration_by_country(country: str | None = None) -> pd.DataFrame:
    where_c = "WHERE country_name = :country" if country else ""
    params = {"country": country} if country else {}
    return run_query(
        f"""
        SELECT REPLACE(financial_year, '_', '-') AS financial_year,
               country_name, anzsco_code, occupation_name, visa_subclass, measure, value
        FROM ref_skilled_migration_by_cob_occupation
        {where_c}
        ORDER BY financial_year
        """,
        params,
    )


# ── Competitor / CRICOS landscape ───────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL)
def get_provider_count() -> int:
    df = run_query("SELECT COUNT(*) AS n FROM dim_provider")
    return int(df.iloc[0]["n"])


@st.cache_data(ttl=CACHE_TTL)
def get_providers_by_state() -> pd.DataFrame:
    return run_query(
        """
        SELECT state_code, COUNT(*) AS provider_count
        FROM dim_provider
        WHERE state_code IS NOT NULL
        GROUP BY state_code
        ORDER BY provider_count DESC
        """
    )


@st.cache_data(ttl=CACHE_TTL)
def get_locations_by_state() -> pd.DataFrame:
    return run_query(
        """
        SELECT state_code, COUNT(*) AS location_count
        FROM dim_provider_location
        WHERE state_code IS NOT NULL
        GROUP BY state_code
        ORDER BY location_count DESC
        """
    )


@st.cache_data(ttl=CACHE_TTL)
def get_courses_by_state() -> pd.DataFrame:
    """Course count per state, via the course's delivering provider's state."""
    return run_query(
        """
        SELECT p.state_code, COUNT(*) AS course_count
        FROM dim_course c
        JOIN dim_provider p ON c.provider_id = p.provider_id
        WHERE p.state_code IS NOT NULL
        GROUP BY p.state_code
        ORDER BY course_count DESC
        """
    )


@st.cache_data(ttl=CACHE_TTL)
def get_courses_by_provider(top_n: int = 20) -> pd.DataFrame:
    return run_query(
        """
        SELECT p.provider_name, p.state_code, COUNT(*) AS course_count
        FROM dim_course c
        JOIN dim_provider p ON c.provider_id = p.provider_id
        GROUP BY p.provider_name, p.state_code
        ORDER BY course_count DESC
        LIMIT :top_n
        """,
        {"top_n": top_n},
    )


# ── Market drivers (macro context) ──────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL)
def get_yearly_exchange_rate(series_id: str = "FXRUSD") -> pd.DataFrame:
    return run_query(
        """
        SELECT YEAR(rate_date) AS year, AVG(value) AS avg_rate
        FROM fact_exchange_rate
        WHERE series_id = :series_id
        GROUP BY YEAR(rate_date)
        ORDER BY year
        """,
        {"series_id": series_id},
    )


# fact_cpi carries 27 series per period (per-capital-city index levels AND
# separate "percentage change" series) under a single blank cpi_group -- a
# blind AVG(value) mixes index levels with % changes. This is the one
# national "All Groups CPI, Australia" index-level series (verified against
# fact_cpi.title via direct query, not assumed).
_CPI_HEADLINE_SERIES = "A130393720C"

# fact_population_by_cob includes aggregate rows ("Total",
# "Total Australian-born(d)", "Australia", "Total overseas-born") alongside
# ~220 individual countries -- summing every row would multiply-count the
# same people. "Total" is the verified true national ERP figure.
_POPULATION_TOTAL_LABEL = "Total"


@st.cache_data(ttl=CACHE_TTL)
def get_yearly_cpi() -> pd.DataFrame:
    return run_query(
        """
        SELECT LEFT(cpi_period, 4) AS year, AVG(value) AS avg_cpi
        FROM fact_cpi
        WHERE series_id = :series_id
        GROUP BY LEFT(cpi_period, 4)
        ORDER BY year
        """,
        {"series_id": _CPI_HEADLINE_SERIES},
    )


@st.cache_data(ttl=CACHE_TTL)
def get_yearly_population_by_cob(country: str | None = None) -> pd.DataFrame:
    """
    Total resident population trend (country=None -> the 'Total' aggregate
    row), or one specific country's estimated resident population.
    """
    target = country or _POPULATION_TOTAL_LABEL
    return run_query(
        """
        SELECT LEFT(erp_period, 4) AS year, SUM(population) AS total_population
        FROM fact_population_by_cob
        WHERE country_name = :country
        GROUP BY LEFT(erp_period, 4)
        ORDER BY year
        """,
        {"country": target},
    )


@st.cache_data(ttl=CACHE_TTL)
def get_yearly_overseas_migration(direction: str = "net") -> pd.DataFrame:
    """
    National NOM trend (summed across the 8 states -- there is no national
    pseudo-state row in this table). Filtered to country_name='Total':
    each state's rows include aggregate labels ("Total",
    "Total overseas-born", "Australia") alongside ~220 individual
    countries; verified 'Total' = 'Total overseas-born' + 'Australia'
    (e.g. 429,120 = 450,280 + -21,150 for 2023-24), i.e. it is the true
    all-country-of-birth national total, not a further sum.
    """
    return run_query(
        """
        SELECT LEFT(nom_period, 4) AS year, SUM(value) AS total_value
        FROM fact_overseas_migration
        WHERE direction = :direction AND country_name = 'Total'
        GROUP BY LEFT(nom_period, 4)
        ORDER BY year
        """,
        {"direction": direction},
    )


@st.cache_data(ttl=CACHE_TTL)
def get_labour_force_measures() -> list[str]:
    df = run_query(
        """
        SELECT DISTINCT measure FROM fact_labour_force
        WHERE measure LIKE 'Employed total%' OR measure LIKE 'Unemployed total%'
           OR measure LIKE 'Unemployment rate%' OR measure LIKE '%Participation rate%'
        ORDER BY measure
        """
    )
    return df["measure"].tolist()


@st.cache_data(ttl=CACHE_TTL)
def get_yearly_labour_force(measure: str) -> pd.DataFrame:
    return run_query(
        """
        SELECT LEFT(lf_period, 4) AS year, AVG(value) AS avg_value
        FROM fact_labour_force
        WHERE measure = :measure
        GROUP BY LEFT(lf_period, 4)
        ORDER BY year
        """,
        {"measure": measure},
    )


# ── Opportunity Score inputs (Page 6) ───────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL)
def get_opportunity_score_inputs() -> pd.DataFrame:
    """
    One row per nationality with the three raw inputs the Opportunity Score
    is built from: current enrolment volume, YoY growth %, and total skilled
    migration grant volume (NULL where a country has no skilled-migration
    record at all -- left as NULL, never coerced to 0, so scoring.py can
    tell "genuinely zero" apart from "no data").
    """
    year, month = get_latest_period()
    prior_year = year - 1

    current = run_query(
        """
        SELECT nationality, SUM(ytd_enrolments) AS current_volume
        FROM fact_student_enrolment
        WHERE enrol_year = :year AND enrol_month = :month
        GROUP BY nationality
        """,
        {"year": year, "month": month},
    )
    prior = run_query(
        """
        SELECT nationality, SUM(ytd_enrolments) AS prior_volume
        FROM fact_student_enrolment
        WHERE enrol_year = :prior_year AND enrol_month = :month
        GROUP BY nationality
        """,
        {"prior_year": prior_year, "month": month},
    )
    visa = run_query(
        """
        SELECT country_name AS nationality, SUM(value) AS visa_volume
        FROM ref_skilled_migration_by_cob_occupation
        WHERE country_name IS NOT NULL
        GROUP BY country_name
        """
    )

    df = current.merge(prior, on="nationality", how="left")
    df = df.merge(visa, on="nationality", how="left")
    df["growth_pct"] = df.apply(
        lambda r: ((r["current_volume"] - r["prior_volume"]) / r["prior_volume"] * 100.0)
        if pd.notna(r["prior_volume"]) and r["prior_volume"] != 0 else None,
        axis=1,
    )
    return df[["nationality", "current_volume", "growth_pct", "visa_volume"]]
