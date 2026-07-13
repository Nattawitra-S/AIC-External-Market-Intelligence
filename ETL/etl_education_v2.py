"""
etl_education_v2.py
====================
ETL: Department of Education — International Student Data

API:  Direct download from education.gov.au (stable URLs for Pivot files)
      Fallback: local XLSX files in raw_data/department_of_education/

Datasets:
  1. Pivot_Basic_All_web.xlsx      — YTD enrolments/commencements by nationality/sector/state
  2. Pivot_Detailed_Latest_web.xlsx — More detailed breakdown (same dimensions + sub-sector)
  3. International students 2005-2025.xlsx — Historical annual data
  4. SA4 enrolments by SA4/remoteness/field — spatial breakdown

Tables:
  • education_enrolments                (detailed current-year pivot)
  • education_int_students_historical   (2005-2025 annual)
  • education_sa4_enrolments            (SA4 spatial breakdown)

USAGE:
    python ETL/etl_education_v2.py
    python ETL/etl_education_v2.py --force   # force re-download
    python ETL/etl_education_v2.py --local-only
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    add_etl_meta, download_file, get_db, get_logger, norm_col, upsert_df,
)

log = get_logger("ETL_EDUCATION")

_MONTH_NAME_TO_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _month_name_to_num(v):
    return _MONTH_NAME_TO_NUM.get(str(v).strip().lower())


BASE_DIR = Path(__file__).parent.parent
RAW_DIR  = BASE_DIR / "raw_data" / "department_of_education"
DB_PATH  = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA   = Path(__file__).parent / "schema.sql"

# ── Direct download URLs (stable) ─────────────────────────────────────────────
# These are the permanent data download links from education.gov.au
SOURCES = [
    {
        "url":      "https://www.education.gov.au/sites/default/files/documents/Pivot_Basic_All_web.xlsx",
        "filename": "Pivot_Basic_All_web_latest.xlsx",
        "desc":     "Basic Pivot — YTD enrolments/commencements",
        "table":    "education_enrolments",
        "parser":   "parse_pivot_basic",
    },
    {
        "url":      "https://www.education.gov.au/sites/default/files/documents/Pivot_Detailed_Latest_web.xlsx",
        "filename": "Pivot_Detailed_Latest_web_latest.xlsx",
        "desc":     "Detailed Pivot — extended breakdown",
        "table":    "education_enrolments",
        "parser":   "parse_pivot_detailed",
    },
    {
        "url":      None,  # Historical file — no public direct URL, use local
        "filename": None,
        "desc":     "International students 2005-2025 (historical)",
        "table":    "education_int_students_historical",
        "parser":   "parse_historical",
        "local_glob": "International students studying in Australia*.xlsx",
    },
    {
        "url":      None,
        "filename": None,
        "desc":     "SA4 enrolments (spatial)",
        "table":    "education_sa4_enrolments",
        "parser":   "parse_sa4",
        "local_glob": "SA4_International Student Enrolments*.xlsx",
    },
]

# ── Fallback local files ───────────────────────────────────────────────────────
# Pivot_Basic_All_web.xlsx from the website is a true Excel pivot (merged cells, no tabular data).
# The pre-extracted flat file lives under "Zz Extracted files/" and is used by etl_education_enrolments.py.
EXTRACTED_DIR = BASE_DIR / "raw_data" / "Zz Extracted files"

def _find_extracted_pivot_basic() -> Path | None:
    """Locate the pre-extracted flat version of Pivot_Basic_All_web."""
    candidates = sorted(EXTRACTED_DIR.glob("Pivot_Basic_All_web*extracted*.xlsx"))
    if candidates:
        return candidates[-1]   # most recent / alphabetically last
    # Also check raw_data root
    candidates = sorted(BASE_DIR.glob("raw_data/**/Pivot_Basic_All_web*extracted*.xlsx"))
    return candidates[-1] if candidates else None

LOCAL_FILES = {
    "parse_pivot_basic":    _find_extracted_pivot_basic(),   # pre-extracted flat file
    "parse_pivot_detailed": RAW_DIR / "Pivot_Detailed_Latest_web.xlsx",
}


# ── PARSERS ───────────────────────────────────────────────────────────────────

def _find_header(raw: pd.DataFrame, hints: list[str]) -> int:
    for i, row in raw.head(15).iterrows():
        if row.astype(str).str.contains("|".join(hints), case=False, na=False, regex=True).any():
            return i
    return 0


def parse_pivot_basic(path: Path) -> pd.DataFrame:
    """
    Parse the pre-extracted flat version of Pivot_Basic_All_web.xlsx.

    The downloaded Pivot_Basic_All_web.xlsx from education.gov.au is a true
    Excel pivot table with merged cells — it is NOT tabular.  The local
    fallback is the pre-extracted file:
        raw_data/Zz Extracted files/Pivot_Basic_All_web*extracted*.xlsx
    which is already flat / tabular and may span multiple sheets.

    Expected columns (post-normalise):
        year, month, nationality, state, sector, new_to_australia,
        ends_this_year, data_ytd_enrolments, data_ytd_commencements,
        providertype / provider_type, total
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Sheets ({len(xl.sheet_names)}): {xl.sheet_names[:8]}")

    # Column name standardisation map
    rename = {
        "ytd_enrolments":         "data_ytd_enrolments",
        "enrolments":             "data_ytd_enrolments",
        "ytd_commencements":      "data_ytd_commencements",
        "commencements":          "data_ytd_commencements",
        "providertype":           "provider_type",
    }

    frames = []
    for sheet in xl.sheet_names:
        # Skip obvious non-data sheets
        if any(x in sheet.lower() for x in ["note", "content", "glossary", "source", "readme"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Year", "Month", "Nationality", "Sector", "State"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            if "year" not in df.columns:
                log.debug(f"  Sheet '{sheet}' — no 'year' column, skipping")
                continue

            df = df.rename(columns=rename)

            if "month" in df.columns:
                # Month is an abbreviated name ("Jul", "Jan"), not a number.
                # pd.to_numeric(..., errors="coerce") would silently turn
                # every value into NaN (which MySQL then forces to 0 on
                # insert into a NOT NULL column) -- map name -> number first.
                df["month"] = df["month"].map(_month_name_to_num)

            # Convert numeric columns
            for col in ["year", "month", "data_ytd_enrolments", "data_ytd_commencements", "total"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=[c for c in ["year", "month"] if c in df.columns])
            if df.empty:
                continue

            log.info(f"  Sheet '{sheet}': {len(df):,} rows")
            frames.append(df)
        except Exception as e:
            log.warning(f"  Sheet '{sheet}': {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in [
        "year", "month", "nationality", "state", "sector", "provider_type",
        "new_to_australia", "ends_this_year",
        "data_ytd_enrolments", "data_ytd_commencements", "total",
    ] if c in result.columns]
    return result[keep]


def parse_pivot_detailed(path: Path) -> pd.DataFrame:
    """Same as basic but may have more columns — reuse same parser."""
    return parse_pivot_basic(path)


def parse_historical(path: Path) -> pd.DataFrame:
    """
    'International students studying in Australia (2005-2025).xlsx'
    Expected: annual data by nationality/state/sector.
    May be wide-format with years as columns.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Historical sheets: {xl.sheet_names[:5]}")
    frames = []

    for sheet in xl.sheet_names:
        if any(x in sheet.lower() for x in ["note", "content", "glossary", "source"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Nationality", "Sector", "State", "Year", "Country"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            id_cols = [c for c in df.columns if any(x in c for x in
                ["nationality", "country", "state", "sector", "measure", "type"])]
            year_cols = [c for c in df.columns if re.match(r"^\d{4}$", str(c).strip())]

            if year_cols and id_cols:
                long = df.melt(id_vars=id_cols, value_vars=year_cols,
                               var_name="year", value_name="value")
                long["year"] = pd.to_numeric(long["year"], errors="coerce")
                long["value"] = pd.to_numeric(long["value"], errors="coerce")
                long = long.dropna(subset=["value", "year"])
                long["measure"] = sheet.strip()

                # Standardise names
                col_map = {}
                for c in long.columns:
                    if "nationality" in c or "country" in c:
                        col_map[c] = "nationality"
                    elif "state" in c or "territory" in c:
                        col_map[c] = "state"
                    elif "sector" in c:
                        col_map[c] = "sector"
                long = long.rename(columns=col_map)
                frames.append(long)
        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["year", "nationality", "state", "sector", "measure", "value"]
            if c in result.columns]
    return result[keep]


def parse_sa4(path: Path) -> pd.DataFrame:
    """
    SA4 enrolments by SA4 location, remoteness, sector, broad field.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  SA4 sheets: {xl.sheet_names[:5]}")
    frames = []

    for sheet in xl.sheet_names:
        if any(x in sheet.lower() for x in ["note", "content", "source"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["SA4", "Remoteness", "Sector", "Field", "Year"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            id_cols = [c for c in df.columns if any(x in c for x in
                ["sa4", "remoteness", "sector", "field", "year", "month"])]
            val_cols = [c for c in df.columns if c not in id_cols and
                        df[c].dtype in ["float64", "int64"]]

            if val_cols and id_cols:
                long = df.melt(id_vars=id_cols, value_vars=val_cols,
                               var_name="measure", value_name="value")
                long["value"] = pd.to_numeric(long["value"], errors="coerce")
                long = long.dropna(subset=["value"])

                col_map = {}
                for c in long.columns:
                    if "sa4" in c:
                        col_map[c] = "sa4_name"
                    elif "remoteness" in c:
                        col_map[c] = "remoteness"
                    elif "sector" in c:
                        col_map[c] = "sector"
                    elif "field" in c:
                        col_map[c] = "broad_field"
                    elif "year" in c:
                        col_map[c] = "year"
                    elif "month" in c:
                        col_map[c] = "month"
                long = long.rename(columns=col_map)
                frames.append(long)
        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["year", "month", "sa4_name", "remoteness",
                         "sector", "broad_field", "measure", "value"]
            if c in result.columns]
    return result[keep]


PARSERS = {
    "parse_pivot_basic":    parse_pivot_basic,
    "parse_pivot_detailed": parse_pivot_detailed,
    "parse_historical":     parse_historical,
    "parse_sa4":            parse_sa4,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def run(force: bool = False, local_only: bool = False, dry_run: bool = False,
        db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0

    for src in SOURCES:
        log.info(f"\n{'─'*55}")
        log.info(f"[Education] {src['desc']}")

        # Find file
        path = None
        if not local_only and src.get("url"):
            try:
                path = download_file(src["url"], RAW_DIR, src["filename"], force=force)
            except Exception as e:
                log.warning(f"  Download failed: {e}")

        if path is None or not path.exists():
            # Try local glob
            if src.get("local_glob"):
                matches = sorted(RAW_DIR.glob(src["local_glob"]))
                if matches:
                    path = matches[-1]
                    log.info(f"  Local: {path.name}")
            elif src["parser"] in LOCAL_FILES:
                path = LOCAL_FILES[src["parser"]]

        if path is None or not path.exists():
            log.warning(f"  ⚠️  No file found — skipping")
            continue

        # Parse
        parser = PARSERS.get(src["parser"])
        if not parser:
            log.error(f"  No parser: {src['parser']}")
            continue

        try:
            df = parser(path)
        except Exception as e:
            log.error(f"  ❌ Parse failed: {e}")
            continue

        if df.empty:
            log.warning(f"  ⚠️  Empty result")
            continue

        log.info(f"  Parsed {len(df):,} rows")
        df = add_etl_meta(df, f"education/{path.name}")
        n = upsert_df(df, src["table"], conn, dry_run=dry_run)
        total += n

    log.info(f"\n✅ Education ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: Department of Education")
    ap.add_argument("--force",      action="store_true")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--db",         default=str(DB_PATH))
    args = ap.parse_args()
    run(force=args.force, local_only=args.local_only, dry_run=args.dry_run, db_path=Path(args.db))
