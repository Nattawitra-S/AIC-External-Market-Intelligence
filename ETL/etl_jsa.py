"""
etl_jsa.py
===========
ETL: Jobs and Skills Australia (JSA)

Datasets:
  1. Internet Vacancy Index (IVI) — monthly online job ads by occupation/state
       → ANZSCO4 by State/Territory (most granular)
       → ANZSCO2 by State/Territory (for trend analysis)
       → ANZSCO Skill Level by State/Territory
  2. Occupation Shortage List (OSL)
       → 6-digit ANZSCO + OSCA category
       → 4-digit (Unit Group) shortage determination
  3. Occupation Profiles data

API: Direct XLSX download from jobsandskills.gov.au
     Stable URL patterns: https://www.jobsandskills.gov.au/sites/default/files/{year}-{month}/{filename}
     Fallback: local files in raw_data/jobs_and_skills_australia/

USAGE:
    python ETL/etl_jsa.py
    python ETL/etl_jsa.py --force
    python ETL/etl_jsa.py --local-only
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    add_etl_meta, download_file, get_db, get_logger, norm_col, upsert_df,
)

log = get_logger("ETL_JSA")

BASE_DIR = Path(__file__).parent.parent
RAW_DIR  = BASE_DIR / "raw_data" / "jobs_and_skills_australia"
DB_PATH  = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA   = Path(__file__).parent / "schema.sql"

# ── Download sources ──────────────────────────────────────────────────────────
# JSA uses dated filenames. The most stable approach is to fetch the index page
# and extract the download link. As a fallback, use local files.

JSA_IVI_BASE = "https://www.jobsandskills.gov.au"

# Known local files (latest downloaded)
LOCAL_FILES = {
    "ivi_anzsco4_state":  RAW_DIR / "Internet Vacancies, ANZSCO4 Occupations, States and Territories.xlsx",
    "ivi_anzsco2_state":  RAW_DIR / "Internet Vacancies, ANZSCO2 Occupations, States and Territories.xlsx",
    "ivi_skill_state":    RAW_DIR / "Internet Vacancies, ANZSCO Skill Level, States and Territories.xlsx",
    "osl_6digit":         RAW_DIR / "Occupation Shortage List - 6 digit ANZSCO and OSCA.xlsx",
    "osl_4digit":         RAW_DIR / "Unit Group Shortage List - 4 digit ANZSCO.xlsx",
    "osl_report":         RAW_DIR / "Occupation Shortage Report - March 2026 - Charts and Tables.xlsx",
    "occ_profiles":       RAW_DIR / "Occupation profiles data.xlsx",
    "industry_data":      RAW_DIR / "Industry data.xlsx",
}


# ── IVI PARSERS ───────────────────────────────────────────────────────────────

def _detect_header_row(df_raw: pd.DataFrame, hint: str) -> int:
    """Find the row index where hint text appears."""
    for i, row in df_raw.head(10).iterrows():
        if row.astype(str).str.contains(hint, case=False, na=False).any():
            return i
    return 0


def parse_ivi_anzsco4_state(path: Path) -> pd.DataFrame:
    """
    IVI ANZSCO4 × State — typically sheet 'SA' or 'Trend'.
    Format: wide cross-tab: rows = ANZSCO4 codes + names, cols = State × Period.
    OR: long format with columns [ANZSCO Code, Occupation, State, Period, SA, Trend, Original]
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Sheets: {xl.sheet_names}")

    frames = []
    for sheet_name in xl.sheet_names:
        sl = sheet_name.lower()
        if any(x in sl for x in ["sa", "trend", "data", "original"]):
            try:
                raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
                header_idx = _detect_header_row(raw, "anzsco")
                df = raw.iloc[header_idx + 1:].copy()
                df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
                df = df.dropna(how="all").reset_index(drop=True)

                # Detect layout: wide (dates as columns) vs long
                date_cols = [c for c in df.columns if re.search(r"\d{4}", str(c))]
                id_cols = [c for c in df.columns if c in (
                    "anzsco_code", "anzsco", "occupation", "occupation_name",
                    "state", "state_territory", "state_or_territory",
                )]

                if date_cols and id_cols:
                    # Wide → melt
                    long = df.melt(id_vars=id_cols, value_vars=date_cols,
                                   var_name="period", value_name="value")
                    long["measure"] = sheet_name.strip()
                    long["anzsco_level"] = 4
                    frames.append(long)
                elif all(c in df.columns for c in ["period", "value"]):
                    df["measure"] = sheet_name.strip()
                    df["anzsco_level"] = 4
                    frames.append(df)
            except Exception as e:
                log.warning(f"  Sheet {sheet_name}: {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    return _normalise_ivi(result)


def parse_ivi_anzsco2_state(path: Path) -> pd.DataFrame:
    return _parse_ivi_generic(path, anzsco_level=2)


def parse_ivi_skill_state(path: Path) -> pd.DataFrame:
    return _parse_ivi_generic(path, anzsco_level=None, is_skill_level=True)


def _parse_ivi_generic(path: Path, anzsco_level: int | None = None,
                        is_skill_level: bool = False) -> pd.DataFrame:
    """Generic IVI parser for ANZSCO2 and Skill Level files."""
    xl = pd.ExcelFile(path)
    frames = []

    for sheet_name in xl.sheet_names:
        sl = sheet_name.lower()
        if any(x in sl for x in ["sa", "trend", "data", "original", "seasonally"]):
            try:
                raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
                header_idx = _detect_header_row(raw, "state|anzsco|skill")
                df = raw.iloc[header_idx + 1:].copy()
                df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
                df = df.dropna(how="all").reset_index(drop=True)

                id_cols = [c for c in df.columns if any(x in c for x in
                    ["anzsco", "occupation", "skill", "state", "territory"])]
                date_cols = [c for c in df.columns if re.search(r"\d{4}", str(c))]

                if date_cols and id_cols:
                    long = df.melt(id_vars=id_cols, value_vars=date_cols,
                                   var_name="period", value_name="value")
                    long["measure"] = sheet_name.strip()
                    if anzsco_level:
                        long["anzsco_level"] = anzsco_level
                    frames.append(long)
            except Exception as e:
                log.warning(f"  Sheet {sheet_name}: {e}")

    if not frames:
        return pd.DataFrame()
    return _normalise_ivi(pd.concat(frames, ignore_index=True))


def _normalise_ivi(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise column names for IVI data."""
    # Map various column name patterns to standard names
    col_map = {}
    for c in df.columns:
        if c in ("anzsco_code", "anzsco", "code"):
            col_map[c] = "anzsco_code"
        elif c in ("occupation", "occupation_name", "anzsco_description"):
            col_map[c] = "occupation_name"
        elif c in ("state", "state_territory", "state_or_territory", "geography"):
            col_map[c] = "state_territory"
    df = df.rename(columns=col_map)

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])

    keep = [c for c in ["period", "anzsco_code", "occupation_name",
                         "anzsco_level", "state_territory", "measure", "value"]
            if c in df.columns]
    return df[keep]


# ── OSL PARSERS ───────────────────────────────────────────────────────────────

def parse_osl_6digit(path: Path) -> pd.DataFrame:
    """
    Occupation Shortage List — 6-digit ANZSCO + OSCA category.
    Sheet: 'OSL' or first data sheet.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  OSL6 sheets: {xl.sheet_names}")

    for sheet in xl.sheet_names:
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _detect_header_row(raw, "anzsco|shortage")
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            if len(df.columns) < 2:
                continue

            col_map = {}
            for c in df.columns:
                if "anzsco" in c and ("code" in c or c == "anzsco"):
                    col_map[c] = "anzsco_code"
                elif "occupation" in c or "title" in c:
                    col_map[c] = "occupation_name"
                elif "shortage" in c and "status" in c:
                    col_map[c] = "shortage_status"
                elif "shortage" in c and not col_map.get("shortage_status"):
                    col_map[c] = "shortage_status"
                elif "osca" in c or "category" in c:
                    col_map[c] = "osca_category"
                elif "state" in c or "territory" in c:
                    col_map[c] = "state_territory"
                elif "year" in c or "assessment" in c:
                    col_map[c] = "assessment_year"
            df = df.rename(columns=col_map)

            if "anzsco_code" in df.columns or "occupation_name" in df.columns:
                df["anzsco_level"] = 6
                keep = [c for c in ["anzsco_code", "anzsco_level", "occupation_name",
                                     "shortage_status", "osca_category",
                                     "assessment_year", "state_territory"]
                        if c in df.columns]
                return df[keep].dropna(subset=["shortage_status"])
        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    return pd.DataFrame()


def parse_osl_4digit(path: Path) -> pd.DataFrame:
    """Unit Group Shortage List — 4-digit ANZSCO."""
    df = parse_osl_6digit(path)  # Same format, different granularity
    if not df.empty and "anzsco_level" in df.columns:
        df["anzsco_level"] = 4
    return df


def parse_occupation_profiles(path: Path) -> pd.DataFrame:
    """
    Occupation Profiles — wide format per ANZSCO code with various dimensions.
    Melt to long format: (anzsco_code, measure, dimension, value).
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Profiles sheets: {xl.sheet_names}")
    frames = []

    for sheet in xl.sheet_names:
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _detect_header_row(raw, "anzsco|occupation")
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            # Find ANZSCO code column
            anzsco_col = next((c for c in df.columns if "anzsco" in c and "code" in c), None)
            if anzsco_col is None:
                anzsco_col = next((c for c in df.columns if "anzsco" in c), None)
            if anzsco_col is None:
                continue

            occ_col = next((c for c in df.columns if "occupation" in c or "title" in c), None)
            id_vars = [anzsco_col] + ([occ_col] if occ_col else [])
            val_cols = [c for c in df.columns if c not in id_vars]

            long = df.melt(id_vars=id_vars, value_vars=val_cols,
                           var_name="dimension", value_name="value")
            long = long.rename(columns={anzsco_col: "anzsco_code"})
            if occ_col:
                long = long.rename(columns={occ_col: "occupation_name"})
            long["measure"] = sheet.strip()
            long["value_text"] = long["value"].astype(str)
            long["value"] = pd.to_numeric(long["value"], errors="coerce")
            frames.append(long)
        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(force: bool = False, local_only: bool = False, dry_run: bool = False,
        db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0
    src_tag = "jsa/local"

    tasks = [
        # (local_key, parser_fn, table_name, anzsco_level_label)
        ("ivi_anzsco4_state", parse_ivi_anzsco4_state, "jsa_internet_vacancies",   "ANZSCO4 × State IVI"),
        ("ivi_anzsco2_state", parse_ivi_anzsco2_state, "jsa_internet_vacancies",   "ANZSCO2 × State IVI"),
        ("ivi_skill_state",   parse_ivi_skill_state,   "jsa_internet_vacancies",   "Skill Level × State IVI"),
        ("osl_6digit",        parse_osl_6digit,         "jsa_occupation_shortage",  "OSL 6-digit"),
        ("osl_4digit",        parse_osl_4digit,         "jsa_occupation_shortage",  "OSL 4-digit (Unit Group)"),
        ("occ_profiles",      parse_occupation_profiles,"jsa_occupation_profiles",  "Occupation Profiles"),
    ]

    for key, parser, table, label in tasks:
        path = LOCAL_FILES.get(key)
        if not path or not path.exists():
            log.warning(f"  ⚠️  Missing: {key} ({path}) — skipping")
            continue

        log.info(f"\n{'─'*55}")
        log.info(f"[JSA] {label}")
        log.info(f"  File: {path.name}")

        try:
            df = parser(path)
            if df.empty:
                log.warning(f"  ⚠️  Parser returned empty DataFrame")
                continue
            df = add_etl_meta(df, f"{src_tag}/{path.name}")
            n = upsert_df(df, table, conn, dry_run=dry_run)
            total += n
        except Exception as e:
            log.error(f"  ❌ {label}: {e}")

    log.info(f"\n✅ JSA ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: Jobs and Skills Australia")
    ap.add_argument("--force",      action="store_true")
    ap.add_argument("--local-only", action="store_true", default=True)
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--db",         default=str(DB_PATH))
    args = ap.parse_args()
    run(force=args.force, local_only=args.local_only, dry_run=args.dry_run, db_path=Path(args.db))
