"""
run_mysql_sources.py
=====================
MySQL-mode ETL runner functions for all 7 AIC data sources.

Each `run_mysql_<source>()` function:
  1. Calls the same parse/download functions as the SQLite ETL
  2. Applies MySQL-specific column renames (per docs/mysql_schema_reconciliation.md)
  3. Loads data using upsert_df_mysql() or bulk_load_csv()

These functions do NOT touch the SQLite database.
They are called by run_all.py when the --mysql flag is active.

IMPORT NOTE: This module does NOT import mysql.connector at module level.
mysql-connector-python is only required at runtime when --mysql is used.
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger("RUN_MYSQL")

BASE_DIR = Path(__file__).parent.parent


# ── Column rename helpers ─────────────────────────────────────────────────────

def _rename(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Rename only columns that exist in df."""
    return df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})


def _keep(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Keep only columns that exist in df from the provided list."""
    return df[[c for c in cols if c in df.columns]]


# ─────────────────────────────────────────────────────────────────────────────
# 1.  RBA Exchange Rates  →  fact_exchange_rate
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_rba(conn, dry_run: bool = False, force: bool = False) -> int:
    """
    RBA ETL for MySQL.
    Column renames: date → rate_date, title → currency_pair
    Target table: fact_exchange_rate
    """
    from ETL.lib_etl import download_file, read_rba_csv, add_etl_meta
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun

    RAW_DIR = BASE_DIR / "raw_data" / "rba"
    SOURCES = [
        {"url":  "https://www.rba.gov.au/statistics/tables/csv/f11-data.csv",
         "filename": "f11-data.csv",  "table_tag": "f11"},
        {"url":  "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv",
         "filename": "f11.1-data.csv", "table_tag": "f11.1"},
    ]
    TABLE = "fact_exchange_rate"
    total = 0

    for src in SOURCES:
        audit = AuditRun(conn, "rba", TABLE)
        try:
            local = download_file(src["url"], RAW_DIR, src["filename"], force=force)
            df = read_rba_csv(local)
            df["source_table"] = src["table_tag"]
            df = add_etl_meta(df, f"rba/{src['filename']}")
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["value"])

            # MySQL column renames
            df = _rename(df, {
                "date":  "rate_date",
                "title": "currency_pair",
            })

            want = ["rate_date", "series_id", "currency_pair", "units",
                    "frequency", "value", "source_table", "_etl_source", "_etl_loaded_at"]
            df = _keep(df, want)

            n = upsert_df_mysql(df, TABLE, conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [RBA] {src['filename']}: {n:,} rows → {TABLE}")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [RBA] {src['filename']} failed: {e}")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 2.  CRICOS  →  dim_provider, dim_course, dim_provider_location, bridge_course_location
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_cricos(conn, dry_run: bool = False, force: bool = False,
                     local_only: bool = False) -> int:
    """
    CRICOS ETL for MySQL.
    Column renames:
      institutions: state → state_code, status → registration_status
      courses:      fees_aud → annual_fees_aud
      locations:    state → state_code
    Target tables: dim_provider, dim_course, dim_provider_location, bridge_course_location
    """
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun
    from ETL import etl_cricos

    sheets = {}
    source_tag = "cricos"

    if not local_only:
        xlsx = etl_cricos.try_ckan_download(force)
        if xlsx:
            sheets = etl_cricos.load_from_xlsx(xlsx)
            source_tag = f"cricos/{xlsx.name}"

    if not sheets:
        local_xlsx = etl_cricos.LOCAL_XLSX
        if local_xlsx and local_xlsx.exists():
            sheets = etl_cricos.load_from_xlsx(local_xlsx)
            source_tag = f"cricos/{local_xlsx.name}"

    if not sheets:
        from ETL.lib_etl import add_etl_meta
        import pandas as pd
        for key, path in etl_cricos.LOCAL_FILES.items():
            if path.exists():
                sheets[key] = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)

    # MySQL table mapping
    mysql_tasks = {
        "institutions": {
            "transform": etl_cricos.transform_institutions,
            "table":     "dim_provider",
            "renames":   {"state": "state_code", "status": "registration_status"},
            "want":      ["provider_id", "provider_name", "provider_type", "state_code",
                          "website", "registration_status", "registration_end_date",
                          "_etl_source", "_etl_loaded_at"],
        },
        "courses": {
            "transform": etl_cricos.transform_courses,
            "table":     "dim_course",
            "renames":   {"fees_aud": "annual_fees_aud"},
            "want":      ["cricos_code", "course_name", "field_of_education", "broad_field",
                          "duration_weeks", "min_age", "annual_fees_aud", "provider_id",
                          "_etl_source", "_etl_loaded_at"],
        },
        "locations": {
            "transform": etl_cricos.transform_locations,
            "table":     "dim_provider_location",
            "renames":   {"state": "state_code"},
            "want":      ["location_id", "provider_id", "location_name", "address",
                          "suburb", "state_code", "postcode",
                          "_etl_source", "_etl_loaded_at"],
        },
        "course_locations": {
            "transform": etl_cricos.transform_course_locations,
            "table":     "bridge_course_location",
            "renames":   {},
            "want":      ["cricos_code", "location_id", "provider_id",
                          "_etl_source", "_etl_loaded_at"],
        },
    }

    from ETL.lib_etl import add_etl_meta
    total = 0

    for key, cfg in mysql_tasks.items():
        if key not in sheets:
            log.warning(f"  [CRICOS] no data for {key} — skipping")
            continue

        audit = AuditRun(conn, "cricos", cfg["table"])
        try:
            df = cfg["transform"](sheets[key])
            df = add_etl_meta(df, source_tag)
            df = _rename(df, cfg["renames"])
            df = _keep(df, cfg["want"])

            n = upsert_df_mysql(df, cfg["table"], conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [CRICOS] {key}: {n:,} rows → {cfg['table']}")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [CRICOS] {key} failed: {e}")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 3.  JSA  →  fact_job_vacancy, fact_occupation_shortage, ref_occupation_profile
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_jsa(conn, dry_run: bool = False) -> int:
    """
    JSA ETL for MySQL.
    Column renames:
      IVI:      period → vacancy_period, state_territory → state_code, value → vacancy_count
      Profiles: measure → profile_measure
    Target tables: fact_job_vacancy, fact_occupation_shortage, ref_occupation_profile
    """
    from ETL.lib_etl import add_etl_meta
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun
    from ETL import etl_jsa

    total = 0

    # ── IVI → fact_job_vacancy ────────────────────────────────────────────────
    ivi_tasks = [
        ("ivi_anzsco4_state", etl_jsa.parse_ivi_anzsco4_state),
        ("ivi_anzsco2_state", etl_jsa.parse_ivi_anzsco2_state),
        ("ivi_skill_state",   etl_jsa.parse_ivi_skill_state),
    ]
    ivi_frames = []
    for key, parser in ivi_tasks:
        path = etl_jsa.LOCAL_FILES.get(key)
        if not path or not path.exists():
            log.warning(f"  [JSA] Missing {key} — skipping")
            continue
        try:
            df = parser(path)
            if not df.empty:
                df = add_etl_meta(df, f"jsa/{path.name}")
                ivi_frames.append(df)
        except Exception as e:
            log.warning(f"  [JSA] {key} parse failed: {e}")

    if ivi_frames:
        audit = AuditRun(conn, "jsa", "fact_job_vacancy")
        try:
            ivi_all = pd.concat(ivi_frames, ignore_index=True)
            # MySQL renames
            ivi_all = _rename(ivi_all, {
                "period":          "vacancy_period",
                "state_territory": "state_code",
                "value":           "vacancy_count",
            })
            # measure column: SA | Trend | Original (already in data as 'measure')
            want = ["vacancy_period", "anzsco_code", "anzsco_level", "state_code",
                    "measure", "vacancy_count", "_etl_source", "_etl_loaded_at"]
            ivi_all = _keep(ivi_all, want)
            n = upsert_df_mysql(ivi_all, "fact_job_vacancy", conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [JSA] IVI: {n:,} rows → fact_job_vacancy")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [JSA] IVI load failed: {e}")

    # ── OSL → fact_occupation_shortage ────────────────────────────────────────
    osl_tasks = [
        ("osl_6digit", etl_jsa.parse_osl_6digit),
        ("osl_4digit", etl_jsa.parse_osl_4digit),
    ]
    osl_frames = []
    for key, parser in osl_tasks:
        path = etl_jsa.LOCAL_FILES.get(key)
        if not path or not path.exists():
            log.warning(f"  [JSA] Missing {key} — skipping")
            continue
        try:
            df = parser(path)
            if not df.empty:
                df = add_etl_meta(df, f"jsa/{path.name}")
                osl_frames.append(df)
        except Exception as e:
            log.warning(f"  [JSA] {key} parse failed: {e}")

    if osl_frames:
        audit = AuditRun(conn, "jsa", "fact_occupation_shortage")
        try:
            osl_all = pd.concat(osl_frames, ignore_index=True)
            # state_territory → state_code; other cols already match MySQL schema
            osl_all = _rename(osl_all, {"state_territory": "state_code"})
            if "state_code" not in osl_all.columns:
                osl_all["state_code"] = "AUS"
            want = ["anzsco_code", "anzsco_level", "occupation_name",
                    "shortage_status", "osca_category", "assessment_year", "state_code",
                    "_etl_source", "_etl_loaded_at"]
            osl_all = _keep(osl_all, want)
            # fact_occupation_shortage is keyed on ANZSCO. The OSCA-2024
            # sheet uses a different, non-ANZSCO occupation code scheme and
            # has no anzsco_code — drop those rows rather than violate the
            # NOT NULL constraint (would need a code crosswalk to include).
            if "anzsco_code" in osl_all.columns:
                osl_all = osl_all.dropna(subset=["anzsco_code"])
            n = upsert_df_mysql(osl_all, "fact_occupation_shortage", conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [JSA] OSL: {n:,} rows → fact_occupation_shortage")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [JSA] OSL load failed: {e}")

    # ── Profiles → ref_occupation_profile ────────────────────────────────────
    path = etl_jsa.LOCAL_FILES.get("occ_profiles")
    if path and path.exists():
        audit = AuditRun(conn, "jsa", "ref_occupation_profile")
        try:
            df = etl_jsa.parse_occupation_profiles(path)
            if not df.empty:
                df = add_etl_meta(df, f"jsa/{path.name}")
                df = _rename(df, {"measure": "profile_measure"})
                want = ["anzsco_code", "occupation_name", "profile_measure",
                        "dimension", "value_num", "value_text", "profile_year",
                        "_etl_source", "_etl_loaded_at"]
                df = _keep(df, want)
                n = upsert_df_mysql(df, "ref_occupation_profile", conn, dry_run=dry_run, audit=audit)
                audit.complete()
                total += n
                log.info(f"  [JSA] Profiles: {n:,} rows → ref_occupation_profile")
            else:
                audit.fail("empty DataFrame")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [JSA] Profiles load failed: {e}")
    else:
        log.warning("  [JSA] Missing occ_profiles — skipping")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Home Affairs  →  fact_student_visa_activity, fact_temp_skilled_visa,
#                      fact_temp_graduate_visa, fact_permanent_migration
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_home_affairs(conn, dry_run: bool = False, force: bool = False,
                            local_only: bool = False) -> int:
    """
    Home Affairs ETL for MySQL.
    BP0015 (3 tables) → consolidated into fact_student_visa_activity (measure col)
    BP0014 (2 tables) → consolidated into fact_temp_skilled_visa (measure col)
    BP0016 (2 tables) → consolidated into fact_temp_graduate_visa (measure col)
    BP0068           → fact_permanent_migration (renamed from ha_migration_child_outcomes)
    """
    from ETL.lib_etl import add_etl_meta
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun
    from ETL import etl_home_affairs_extended as etl_ha

    total = 0

    def _get_file(key: str) -> Optional[Path]:
        cfg = etl_ha.RESOURCES.get(key, {})
        if not local_only:
            try:
                f = etl_ha._try_ckan_download(cfg, force)
                if f:
                    return f
            except Exception:
                pass
        return etl_ha._get_local_file(cfg)

    # ── BP0015 → fact_student_visa_activity ───────────────────────────────────
    bp0015_configs = [
        ("bp0015_lodged",  etl_ha.parse_bp0015_lodged,  "lodged",        "lodged_count"),
        ("bp0015_granted", etl_ha.parse_bp0015_granted, "granted",       "granted_count"),
        ("bp0015_rates",   etl_ha.parse_bp0015_rates,   "grant_rate_pct","grant_rate_pct"),
    ]
    sva_frames = []
    for key, parser, measure_label, count_col in bp0015_configs:
        f = _get_file(key)
        if not f or not f.exists():
            log.warning(f"  [HA] Missing {key} — skipping")
            continue
        try:
            df = parser(f)
            if df.empty:
                continue
            df = add_etl_meta(df, f"home_affairs/{f.name}")
            df = df.rename(columns={count_col: "value"})
            df["measure"] = measure_label
            sva_frames.append(df)
        except Exception as e:
            log.warning(f"  [HA] {key} parse failed: {e}")

    if sva_frames:
        audit = AuditRun(conn, "home_affairs", "fact_student_visa_activity")
        try:
            sva = pd.concat(sva_frames, ignore_index=True)
            want = ["applicant_type", "sector", "financial_year", "measure", "value",
                    "_etl_source", "_etl_loaded_at"]
            sva = _keep(sva, want)
            n = upsert_df_mysql(sva, "fact_student_visa_activity", conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [HA] BP0015: {n:,} rows → fact_student_visa_activity")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [HA] BP0015 load failed: {e}")

    # ── BP0014 → fact_temp_skilled_visa ───────────────────────────────────────
    bp0014_configs = [
        ("bp0014_granted", etl_ha.parse_bp0014_granted, "granted", "granted_count"),
        ("bp0014_holders", etl_ha.parse_bp0014_holders, "holders", "holder_count"),
    ]
    tsv_frames = []
    for key, parser, measure_label, count_col in bp0014_configs:
        f = _get_file(key)
        if not f or not f.exists():
            log.warning(f"  [HA] Missing {key} — skipping")
            continue
        try:
            df = parser(f)
            if df.empty:
                continue
            df = add_etl_meta(df, f"home_affairs/{f.name}")
            df = df.rename(columns={
                count_col:       "value",
                "state_territory": "state_code",
                "as_at_date":    "financial_year",  # holders parser renames fy→as_at_date
            })
            df["measure"] = measure_label
            tsv_frames.append(df)
        except Exception as e:
            log.warning(f"  [HA] {key} parse failed: {e}")

    if tsv_frames:
        audit = AuditRun(conn, "home_affairs", "fact_temp_skilled_visa")
        try:
            tsv = pd.concat(tsv_frames, ignore_index=True)
            if "state_code" not in tsv.columns:
                tsv["state_code"] = "AUS"
            want = ["visa_subclass", "nationality", "financial_year", "state_code",
                    "measure", "value", "_etl_source", "_etl_loaded_at"]
            tsv = _keep(tsv, want)
            n = upsert_df_mysql(tsv, "fact_temp_skilled_visa", conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [HA] BP0014: {n:,} rows → fact_temp_skilled_visa")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [HA] BP0014 load failed: {e}")

    # ── BP0016 → fact_temp_graduate_visa ──────────────────────────────────────
    bp0016_configs = [
        ("bp0016_lodged",  etl_ha.parse_bp0016_lodged,  "lodged",  "lodged_count"),
        ("bp0016_granted", etl_ha.parse_bp0016_granted, "granted", "granted_count"),
    ]
    tgv_frames = []
    for key, parser, measure_label, count_col in bp0016_configs:
        f = _get_file(key)
        if not f or not f.exists():
            log.warning(f"  [HA] Missing {key} — skipping")
            continue
        try:
            df = parser(f)
            if df.empty:
                continue
            df = add_etl_meta(df, f"home_affairs/{f.name}")
            df = df.rename(columns={count_col: "value"})
            df["measure"] = measure_label
            tgv_frames.append(df)
        except Exception as e:
            log.warning(f"  [HA] {key} parse failed: {e}")

    if tgv_frames:
        audit = AuditRun(conn, "home_affairs", "fact_temp_graduate_visa")
        try:
            tgv = pd.concat(tgv_frames, ignore_index=True)
            want = ["stream", "nationality", "financial_year", "measure", "value",
                    "_etl_source", "_etl_loaded_at"]
            tgv = _keep(tgv, want)
            n = upsert_df_mysql(tgv, "fact_temp_graduate_visa", conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [HA] BP0016: {n:,} rows → fact_temp_graduate_visa")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [HA] BP0016 load failed: {e}")

    # ── BP0068 → fact_permanent_migration ────────────────────────────────────
    f = _get_file("bp0068")
    if f and f.exists():
        audit = AuditRun(conn, "home_affairs", "fact_permanent_migration")
        try:
            df = etl_ha.parse_bp0068(f)
            if not df.empty:
                df = add_etl_meta(df, f"home_affairs/{f.name}")
                # Schema already uses: visa_type, birth_country, outcome_measure, period, value
                want = ["visa_type", "birth_country", "outcome_measure", "period", "value",
                        "_etl_source", "_etl_loaded_at"]
                df = _keep(df, want)
                n = upsert_df_mysql(df, "fact_permanent_migration", conn, dry_run=dry_run, audit=audit)
                audit.complete()
                total += n
                log.info(f"  [HA] BP0068: {n:,} rows → fact_permanent_migration")
            else:
                audit.fail("empty DataFrame")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [HA] BP0068 load failed: {e}")
    else:
        log.warning("  [HA] Missing bp0068 file — skipping")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 5.  ABS  →  fact_labour_force, fact_cpi, fact_overseas_migration,
#              fact_population_by_cob
# Note: lf_industry, lf_occupation, edu_output are EXCLUDED from MySQL schema
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_abs(conn, dry_run: bool = False, source: str = "api",
                  start_period: Optional[str] = None) -> int:
    """
    ABS ETL for MySQL.
    Only processes 4 of the 7 raw ABS flows: lf, cpi, nom, erp_cob.
    The remaining flows have no corresponding table in the approved
    MySQL schema and are intentionally not loaded here.

    Column renames:
      lf:      period → lf_period,  state → state_code
      cpi:     period → cpi_period, group_ → cpi_group
      nom:     period → nom_period, country_of_birth → country_name, state_territory → state_code
      erp_cob: period → erp_period, country_of_birth → country_name,
               state_territory → state_code, value → population
    """
    from ETL.lib_etl import abs_get_data, add_etl_meta
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun
    from ETL import etl_abs

    MYSQL_FLOWS = {
        "lf": {
            "table":   "fact_labour_force",
            "renames": {"period": "lf_period", "state": "state_code"},
            "want":    ["lf_period", "series_id", "measure", "sex", "adjustment_type",
                        "state_code", "value", "unit", "_etl_source", "_etl_loaded_at"],
        },
        "cpi": {
            "table":   "fact_cpi",
            "renames": {"period": "cpi_period", "group_": "cpi_group"},
            "want":    ["cpi_period", "series_id", "title", "cpi_group", "city",
                        "measure", "value", "_etl_source", "_etl_loaded_at"],
        },
        "nom": {
            "table":   "fact_overseas_migration",
            "renames": {
                "period":          "nom_period",
                "country_of_birth":"country_name",
                "state_territory": "state_code",
                "state":           "state_code",
            },
            "want":    ["nom_period", "country_name", "state_code", "direction",
                        "series_id", "value", "_etl_source", "_etl_loaded_at"],
        },
        "erp_cob": {
            "table":   "fact_population_by_cob",
            "renames": {
                "period":          "erp_period",
                "country_of_birth":"country_name",
                "state_territory": "state_code",
                "state":           "state_code",
                "value":           "population",
            },
            "want":    ["erp_period", "country_name", "state_code", "series_id",
                        "population", "_etl_source", "_etl_loaded_at"],
        },
    }

    RAW_DIR = BASE_DIR / "raw_data" / "abs"
    total = 0

    for key, cfg in MYSQL_FLOWS.items():
        abs_cfg = etl_abs.ABS_FLOWS[key]
        audit = AuditRun(conn, "abs", cfg["table"])
        df = pd.DataFrame()

        # Try ABS API
        if source in ("api", "both") and abs_cfg.get("flow"):
            try:
                df_api = abs_get_data(
                    flow_id=abs_cfg["flow"],
                    key=abs_cfg.get("key", "all"),
                    start_period=start_period or abs_cfg.get("start"),
                )
                if key == "lf":
                    df = etl_abs.transform_abs_api_lf(df_api)
                elif key == "cpi":
                    df = etl_abs.transform_abs_api_cpi(df_api)
                else:
                    df = etl_abs.transform_abs_api_generic(df_api)
                log.info(f"  [ABS] {key} API: {len(df):,} rows")
            except Exception as e:
                log.warning(f"  [ABS] {key} API failed: {e} — trying local")

        # Fallback to local
        if df.empty:
            matches = list(RAW_DIR.glob(abs_cfg.get("local_glob", "")))
            if matches:
                path = sorted(matches)[-1]
                parser = etl_abs.LOCAL_PARSERS.get(key,
                    lambda p: etl_abs._parse_abs_xlsx_generic(p, abs_cfg["table"]))
                try:
                    df = parser(path)
                    log.info(f"  [ABS] {key} local: {len(df):,} rows from {path.name}")
                except Exception as e:
                    log.error(f"  [ABS] {key} local parse failed: {e}")

        if df.empty:
            audit.fail("no data from API or local files")
            log.warning(f"  [ABS] {key}: no data — skipping")
            continue

        try:
            df = add_etl_meta(df, f"abs/{key}")
            df = _rename(df, cfg["renames"])
            df = _keep(df, cfg["want"])
            n = upsert_df_mysql(df, cfg["table"], conn, dry_run=dry_run, audit=audit)
            audit.complete()
            total += n
            log.info(f"  [ABS] {key}: {n:,} rows → {cfg['table']}")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [ABS] {key} load failed: {e}")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Education  →  fact_student_enrolment  (BULK LOAD — 3.5M rows)
# Note: parse_historical and parse_sa4 are EXCLUDED from MySQL (not in schema)
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_education(conn, dry_run: bool = False, force: bool = False,
                         local_only: bool = False,
                         staging_dir: Optional[Path] = None) -> int:
    """
    Education ETL for MySQL using LOAD DATA LOCAL INFILE (bulk load).
    Only pivot_basic and pivot_detailed parsers are run (not historical/SA4).

    Basic and Detailed are DIFFERENT GRAINS, not the same dataset at
    different completeness -- Detailed subdivides every Basic-grain
    combination further by region, field of education, and level of study.
    Empirically verified against the full Detailed dataset: 94.7% of its
    rows collide on Basic's business key. They are therefore loaded into two
    SEPARATE fact tables via two independent atomic loads, and must never be
    concatenated/unioned — doing so would either be rejected by the
    uniqueness check or, if that check were ever bypassed, silently
    double/multi-count enrolments in any downstream aggregation (e.g.
    Tableau) that sums both tables together.

      Basic    → fact_student_enrolment           (overall market trends)
      Detailed → fact_student_enrolment_detailed   (field of education /
                                                      level of study analysis)

    Each load is independent: if Detailed fails after Basic already
    succeeded, Basic's committed data is not affected (separate
    transactions) -- the failure still propagates so the caller/audit log
    sees it, but nothing already-committed is lost or rolled back.

    Normal production mode discovers the current publication-page release
    (see ETL/education_discovery.py) and downloads + extracts Basic and
    Detailed together, as a validated pair from the SAME reporting month --
    never independently, which could otherwise silently mix two different
    months' data. Any failure during discovery/download/validation/
    extraction stops the whole run before either table is touched.
    """
    from ETL.lib_etl import add_etl_meta
    from ETL.lib_etl_mysql import load_education_enrolments, DETAILED_ENROLMENT_KEY_COLUMNS
    from ETL import etl_education_v2 as etl_edu

    staging_dir = staging_dir or Path("/tmp/aic_edu_staging")

    total = 0
    basic_path = None
    detailed_path = None

    if local_only:
        basic_path = etl_edu._find_extracted_pivot_basic()
        if basic_path is None:
            raise RuntimeError(
                "  [Edu] --local-only: no valid raw-split file at "
                f"{etl_edu.PIVOT_BASIC_RAW_SPLIT}"
            )
        detailed_path = etl_edu._find_extracted_pivot_detailed()
        if detailed_path is None:
            raise RuntimeError(
                "  [Edu] --local-only: no valid raw-split file at "
                f"{etl_edu.PIVOT_DETAILED_RAW_SPLIT}"
            )
        # Release-consistency check: the workbooks are YTD-cumulative and
        # contain many historical years/months regardless of which release
        # produced them, so their CONTENTS cannot reveal which publication
        # month they came from. Verify the release manifest instead (no
        # network access) -- raises before any parsing/MySQL access on a
        # mismatch, and on missing-manifest-in-a-real-run.
        etl_edu.verify_local_only_release_manifest(dry_run=dry_run)
    elif dry_run:
        # True zero-write dry-run: no download, no extractor, no discovery.
        basic_path = etl_edu._find_extracted_pivot_basic()
        if basic_path is None:
            log.warning(
                "  [Edu] --dry-run: no local raw-split file yet "
                f"({etl_edu.PIVOT_BASIC_RAW_SPLIT.name}) — skipping Basic Pivot"
            )
        detailed_path = etl_edu._find_extracted_pivot_detailed()
        if detailed_path is None:
            log.warning(
                "  [Edu] --dry-run: no local raw-split file yet "
                f"({etl_edu.PIVOT_DETAILED_RAW_SPLIT.name}) — skipping Detailed Pivot"
            )
    else:
        # Normal production mode: discover the newest complete release,
        # download + validate both workbooks atomically, extract both in
        # one pass. Any failure here stops the whole education run before
        # any MySQL write is attempted -- both existing fact tables are
        # left completely untouched.
        basic_path, detailed_path = etl_edu.download_and_extract_release_pair(force=force)

    # ── Basic → fact_student_enrolment ─────────────────────────────────────────
    if basic_path is not None and basic_path.exists():
        log.info(f"  [Edu] Parsing Basic Pivot: {basic_path.name}")
        df_basic = etl_edu.parse_pivot_basic(basic_path)
        if df_basic.empty:
            log.warning("  [Edu] Basic Pivot: empty — skipping")
        else:
            df_basic = add_etl_meta(df_basic, f"education/{basic_path.name}")
            log.info(f"  [Edu] Basic Pivot: {len(df_basic):,} rows parsed")

            df_basic = _rename(df_basic, {
                "year":                    "enrol_year",
                "month":                   "enrol_month",
                "state":                   "state_code",
                "data_ytd_enrolments":     "ytd_enrolments",
                "data_ytd_commencements":  "ytd_commencements",
            })
            want_basic = ["enrol_year", "enrol_month", "nationality", "state_code",
                          "sector", "provider_type", "new_to_australia", "ends_this_year",
                          "ytd_enrolments", "ytd_commencements", "total",
                          "_etl_source", "_etl_loaded_at"]
            df_basic = _keep(df_basic, want_basic)

            total += load_education_enrolments(
                df_basic, conn, staging_dir=staging_dir, dry_run=dry_run,
                table="fact_student_enrolment",
            )
    else:
        log.warning("  [Edu] No file for Basic Pivot — skipping")

    # ── Detailed → fact_student_enrolment_detailed ─────────────────────────────
    if detailed_path is not None and detailed_path.exists():
        log.info(f"  [Edu] Parsing Detailed Pivot: {detailed_path.name}")
        df_detailed = etl_edu.parse_pivot_detailed(detailed_path)
        if df_detailed.empty:
            log.warning("  [Edu] Detailed Pivot: empty — skipping")
        else:
            df_detailed = add_etl_meta(df_detailed, f"education/{detailed_path.name}")
            log.info(f"  [Edu] Detailed Pivot: {len(df_detailed):,} rows parsed")

            df_detailed = _rename(df_detailed, {
                "year":                        "enrol_year",
                "month":                       "enrol_month",
                "state":                       "state_code",
                "data_ytd_enrolments":         "ytd_enrolments",
                "data_ytd_commencements":      "ytd_commencements",
                "data_as_at_1st_month":        "as_at_1st_month",
                "data_enrolments_for_month":   "monthly_enrolments",
                "data_commencements_for_month": "monthly_commencements",
            })
            want_detailed = [
                "enrol_year", "enrol_month", "region", "nationality", "state_code",
                "provider_type", "sector",
                "broad_field_of_education", "narrow_field_of_education",
                "detailed_field_of_education", "level_of_study", "foundation",
                "new_to_australia", "ends_this_year",
                "ytd_enrolments", "ytd_commencements",
                "as_at_1st_month", "monthly_enrolments", "monthly_commencements",
                "_etl_source", "_etl_loaded_at",
            ]
            df_detailed = _keep(df_detailed, want_detailed)

            total += load_education_enrolments(
                df_detailed, conn, staging_dir=staging_dir, dry_run=dry_run,
                table="fact_student_enrolment_detailed",
                key_columns=DETAILED_ENROLMENT_KEY_COLUMNS,
            )
    else:
        log.warning("  [Edu] No file for Detailed Pivot — skipping")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Skilled Migration  →  fact_skilled_migration,
#                           ref_skilled_migration_by_cob_occupation
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_skilled_migration(conn, dry_run: bool = False,
                                 skip_raw: bool = True) -> int:
    """
    Skilled Migration ETL for MySQL.
    Note: The 1.4M row raw CSV is SKIPPED by default in MySQL mode
    (it loads into fact_skilled_migration which already gets the summaries).

    Column renames:
      summaries:         state_territory → state_code
      country_occupation: country_of_birth → country_name
    """
    from ETL.lib_etl import add_etl_meta
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun
    from ETL import etl_skilled_migration as etl_sm

    total = 0

    # ── Summaries → fact_skilled_migration ────────────────────────────────────
    path = etl_sm.LOCAL_FILES["summaries"]
    if path.exists():
        audit = AuditRun(conn, "skilled_migration", "fact_skilled_migration")
        try:
            df = etl_sm.parse_summaries(path)
            if not df.empty:
                df = add_etl_meta(df, f"skilled_migration/{path.name}")
                df = _rename(df, {"state_territory": "state_code"})
                want = ["financial_year", "visa_subclass", "stream", "state_code",
                        "measure", "value", "_etl_source", "_etl_loaded_at"]
                df = _keep(df, want)
                n = upsert_df_mysql(df, "fact_skilled_migration", conn, dry_run=dry_run, audit=audit)
                audit.complete()
                total += n
                log.info(f"  [SM] Summaries: {n:,} rows → fact_skilled_migration")
            else:
                audit.fail("empty DataFrame")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [SM] Summaries failed: {e}")
    else:
        log.warning(f"  [SM] Missing: {path}")

    # ── Country × Occupation → ref_skilled_migration_by_cob_occupation ────────
    path = etl_sm.LOCAL_FILES["country_occupation"]
    if path.exists():
        audit = AuditRun(conn, "skilled_migration", "ref_skilled_migration_by_cob_occupation")
        try:
            df = etl_sm.parse_country_occupation(path)
            if not df.empty:
                df = add_etl_meta(df, f"skilled_migration/{path.name}")
                df = _rename(df, {"country_of_birth": "country_name"})
                want = ["financial_year", "country_name", "anzsco_code",
                        "occupation_name", "visa_subclass", "measure", "value",
                        "_etl_source", "_etl_loaded_at"]
                df = _keep(df, want)
                n = upsert_df_mysql(df, "ref_skilled_migration_by_cob_occupation",
                                    conn, dry_run=dry_run, audit=audit)
                audit.complete()
                total += n
                log.info(f"  [SM] Country×Occ: {n:,} rows → ref_skilled_migration_by_cob_occupation")
            else:
                audit.fail("empty DataFrame")
        except Exception as e:
            audit.fail(str(e))
            log.error(f"  [SM] Country×Occ failed: {e}")
    else:
        log.warning(f"  [SM] Missing: {path}")

    if skip_raw:
        log.info("  [SM] Skipping 1.4M raw CSV (skip_raw=True in MySQL mode)")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# 8.  dim_country — auto-derived from reference/country_aliases.csv
# ─────────────────────────────────────────────────────────────────────────────

def run_mysql_dim_country(conn, dry_run: bool = False) -> int:
    """
    Populate dim_country from reference/country_aliases.csv (approved
    decision #6: dim_country must be auto-derived from this file, not
    hand-maintained).
    """
    from ETL.lib_etl_mysql import upsert_df_mysql, AuditRun

    path = BASE_DIR / "reference" / "country_aliases.csv"
    if not path.exists():
        log.warning(f"  [dim_country] Missing: {path}")
        return 0

    audit = AuditRun(conn, "reference", "dim_country")
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        want = ["canonical_name", "iso_alpha2", "iso_alpha3", "name_education",
                "name_home_affairs", "name_abs", "name_skilled_mig"]
        df = _keep(df, want)
        n = upsert_df_mysql(df, "dim_country", conn, dry_run=dry_run, audit=audit)
        audit.complete()
        log.info(f"  [dim_country] {n:,} rows → dim_country")
        return n
    except Exception as e:
        audit.fail(str(e))
        log.error(f"  [dim_country] load failed: {e}")
        return 0
