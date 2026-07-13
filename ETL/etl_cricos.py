"""
etl_cricos.py
==============
ETL: CRICOS — Commonwealth Register of Institutions and Courses for Overseas Students

API:  data.gov.au CKAN API (package: cricos-providers-courses-and-locations)
      Direct fallback: cricos.education.gov.au downloads

Tables:
  • cricos_institutions     — registered education providers
  • cricos_courses          — CRICOS-registered courses
  • cricos_locations        — provider campus locations
  • cricos_course_locations — which courses are offered at which locations

USAGE:
    python ETL/etl_cricos.py
    python ETL/etl_cricos.py --force
    python ETL/etl_cricos.py --local-only   # skip download, use raw_data/cricos/ files
"""

import argparse
import hashlib
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    add_etl_meta, ckan_package_resources, ckan_resource_url,
    download_file, get_db, get_logger, norm_col, upsert_df,
)

log = get_logger("ETL_CRICOS")

BASE_DIR  = Path(__file__).parent.parent
RAW_DIR   = BASE_DIR / "raw_data" / "cricos"
DB_PATH   = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA    = Path(__file__).parent / "schema.sql"

# data.gov.au CKAN package for CRICOS
CKAN_PACKAGE_ID = "7a8de5d2-c2d8-4c0e-a4de-9e7e1e4b5de8"  # fallback: search by name if this changes

# Fallback: direct download from cricos.education.gov.au
DIRECT_URL = "https://cricos.education.gov.au/Institution/Download.aspx?DownloadType=Providers"

# Local CSV files (already downloaded) as final fallback
LOCAL_FILES = {
    "institutions":     RAW_DIR / "cricos-institutions.csv",
    "courses":          RAW_DIR / "cricos-courses.csv",
    "locations":        RAW_DIR / "cricos-locations.csv",
    "course_locations": RAW_DIR / "cricos-course-locations.csv",
}

# XLSX all-in-one (most recent download, contains all 4 sheets)
LOCAL_XLSX = next(RAW_DIR.glob("cricos-providers-courses-and-locations*.xlsx"), None)


# ── Transform helpers ─────────────────────────────────────────────────────────

def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [norm_col(c) for c in df.columns]
    df = df.dropna(how="all")
    return df


def _synth_location_id(provider_id, location_name) -> str:
    """
    Neither the CSV nor XLSX CRICOS exports include a natural location
    identifier. Derive a stable one from (provider_id, location_name) so
    dim_provider_location and bridge_course_location agree on the same
    id for the same physical location.
    """
    key = f"{str(provider_id).strip().upper()}|{str(location_name).strip().lower()}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def transform_institutions(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm(df)
    # Standardise column names across CSV and XLSX variants
    rename = {
        "providerid": "provider_id",
        "cricos_provider_code": "provider_id",
        "provider_code": "provider_id",
        "tradingname": "provider_name",
        "provider_legal_name": "provider_name",
        "name": "provider_name",
        "providertype": "provider_type",
        "provider_type": "provider_type",
        "state_or_territory": "state",
        "webaddress": "website",
        "website_address": "website",
        "registered_from": "registration_start_date",
        "registered_to": "registration_end_date",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    # XLSX export fallbacks: "Trading Name" is often blank, so prefer the
    # always-populated "Institution Name" when no provider_name was set yet.
    fallbacks = {
        "provider_name": "institution_name",
        "provider_type": "institution_type",
        "state":         "postal_address_state",
    }
    for target, src in fallbacks.items():
        if target not in df.columns and src in df.columns:
            df = df.rename(columns={src: target})
    want = ["provider_id", "provider_name", "provider_type", "state",
            "website", "status", "registration_end_date"]
    return df[[c for c in want if c in df.columns]]


def transform_courses(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm(df)
    rename = {
        "cricos_course_code": "cricos_code",
        "coursecode": "cricos_code",
        "course_code": "cricos_code",
        "coursename": "course_name",
        "course_name_full": "course_name",
        "fieldofeducation": "field_of_education",
        "field_of_education_code_and_name": "field_of_education",
        "broadfieldofeducation": "broad_field",
        "durationfulltimeweeks": "duration_weeks",
        "duration_in_weeks": "duration_weeks",
        "minimumage": "min_age",
        "minimum_age": "min_age",
        "annualcostasinternationalstudent": "fees_aud",
        "total_cost_aud": "fees_aud",
        "providerid": "provider_id",
        "cricos_provider_code": "provider_id",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "duration_weeks" in df.columns:
        df["duration_weeks"] = pd.to_numeric(df["duration_weeks"], errors="coerce")
    if "fees_aud" in df.columns:
        df["fees_aud"] = pd.to_numeric(
            df["fees_aud"].astype(str).str.replace(r"[\$,]", "", regex=True),
            errors="coerce"
        )
    want = ["cricos_code", "course_name", "field_of_education", "broad_field",
            "duration_weeks", "min_age", "fees_aud", "provider_id"]
    return df[[c for c in want if c in df.columns]]


def transform_locations(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm(df)
    rename = {
        "locationid": "location_id",
        "location_id": "location_id",
        "providerid": "provider_id",
        "cricos_provider_code": "provider_id",
        "locationname": "location_name",
        "campus_name": "location_name",
        "addressline1": "address",
        "address_line1": "address",
        "suburb_or_town": "suburb",
        "suburb": "suburb",
        "city": "suburb",
        "state_or_territory": "state",
        "postcode": "postcode",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "location_id" not in df.columns and {"provider_id", "location_name"} <= set(df.columns):
        df["location_id"] = [
            _synth_location_id(p, n) for p, n in zip(df["provider_id"], df["location_name"])
        ]
    want = ["location_id", "provider_id", "location_name", "address",
            "suburb", "state", "postcode"]
    return df[[c for c in want if c in df.columns]]


def transform_course_locations(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm(df)
    rename = {
        "coursecode": "cricos_code",
        "cricos_course_code": "cricos_code",
        "locationid": "location_id",
        "providerid": "provider_id",
        "cricos_provider_code": "provider_id",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "location_id" not in df.columns and {"provider_id", "location_name"} <= set(df.columns):
        df["location_id"] = [
            _synth_location_id(p, n) for p, n in zip(df["provider_id"], df["location_name"])
        ]
    want = ["cricos_code", "location_id", "provider_id"]
    return df[[c for c in want if c in df.columns]]


# ── XLSX multi-sheet loader ───────────────────────────────────────────────────

def _find_xlsx_header_row(xlsx_path: Path, sheet: str, max_rows: int = 10) -> int:
    """
    Find the real header row in a CRICOS export sheet.

    Each sheet starts with a title row and a "Report generated ..." row
    before the actual column headers (e.g. "CRICOS Provider Code", ...).
    """
    raw = pd.read_excel(xlsx_path, sheet_name=sheet, header=None, nrows=max_rows)
    for i, row in raw.iterrows():
        row_str = " ".join(str(v) for v in row.dropna().tolist()).lower()
        if "cricos provider code" in row_str:
            return i
    return 0


def load_from_xlsx(xlsx_path: Path) -> dict[str, pd.DataFrame]:
    """Load CRICOS all-in-one XLSX (4 sheets)."""
    xl = pd.ExcelFile(xlsx_path)
    log.info(f"  XLSX sheets: {xl.sheet_names}")
    sheets = {}
    for sheet in xl.sheet_names:
        s = sheet.lower().strip()
        header_row = _find_xlsx_header_row(xlsx_path, sheet)
        if "institution" in s or "provider" in s:
            sheets["institutions"] = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)
        elif "course location" in s or "course-location" in s:
            sheets["course_locations"] = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)
        elif "course" in s:
            sheets["courses"] = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)
        elif "location" in s:
            sheets["locations"] = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)
    return sheets


# ── CKAN download ─────────────────────────────────────────────────────────────

def try_ckan_download(force: bool) -> Path | None:
    """Try to get latest XLSX from CKAN data.gov.au."""
    try:
        resources = ckan_package_resources(CKAN_PACKAGE_ID)
        xlsx_resources = [r for r in resources if r.get("format", "").upper() in ("XLSX", "XLS")]
        if xlsx_resources:
            r = xlsx_resources[0]
            url = r["url"]
            fname = r.get("name", "cricos-latest.xlsx").replace(" ", "-").lower() + ".xlsx"
            return download_file(url, RAW_DIR, fname, force=force)
    except Exception as e:
        log.warning(f"  CKAN download failed: {e} — falling back to local files")
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def run(force: bool = False, local_only: bool = False, dry_run: bool = False, db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0
    source_tag = "cricos/data.gov.au"

    # Step 1: Try CKAN download → XLSX
    xlsx = None
    if not local_only:
        xlsx = try_ckan_download(force)

    # If no XLSX from CKAN, try local all-in-one XLSX
    if xlsx is None and LOCAL_XLSX and LOCAL_XLSX.exists():
        xlsx = LOCAL_XLSX
        source_tag = f"cricos/{LOCAL_XLSX.name}"
        log.info(f"  Using local XLSX: {LOCAL_XLSX.name}")

    # Step 2: Load data
    if xlsx:
        sheets = load_from_xlsx(xlsx)
    else:
        # Fall back to individual CSV files
        log.info("  Loading from individual CSV files ...")
        sheets = {}
        for key, path in LOCAL_FILES.items():
            if path.exists():
                sheets[key] = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
            else:
                log.warning(f"  ⚠️  Missing local file: {path}")

    # Step 3: Transform + load each entity
    transformers = {
        "institutions":     (transform_institutions,    "cricos_institutions"),
        "courses":          (transform_courses,         "cricos_courses"),
        "locations":        (transform_locations,       "cricos_locations"),
        "course_locations": (transform_course_locations, "cricos_course_locations"),
    }

    for key, (transform_fn, table) in transformers.items():
        if key not in sheets:
            log.warning(f"  ⚠️  No data for {key} — skipping")
            continue

        log.info(f"\n[CRICOS] {key} → {table}")
        df = sheets[key]
        log.info(f"  Raw: {len(df):,} rows, {len(df.columns)} cols")
        df = transform_fn(df)
        df = add_etl_meta(df, source_tag)
        n = upsert_df(df, table, conn, dry_run=dry_run)
        total += n

    log.info(f"\n✅ CRICOS ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: CRICOS")
    ap.add_argument("--force",      action="store_true", help="Force re-download from CKAN")
    ap.add_argument("--local-only", action="store_true", help="Use local files only")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--db",         default=str(DB_PATH))
    args = ap.parse_args()
    run(force=args.force, local_only=args.local_only, dry_run=args.dry_run, db_path=Path(args.db))
