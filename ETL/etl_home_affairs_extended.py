"""
etl_home_affairs_extended.py
=============================
ETL: Department of Home Affairs — Extended Migration Program Reports

Covers datasets NOT already in the existing etl_student_visa*.py scripts:
  • BP0014 — Temporary Resident Skilled Visas (granted + holders)
  • BP0016 — Temporary Graduate Visas (lodged + granted)
  • BP0068 — Migration and Child Outcomes

Also consolidates the BP0015 student visa ETL into one module.

API:  data.gov.au CKAN API (resource_show) for all Home Affairs datasets.
      Fallback: local XLSX files in raw_data/home_affairs/

CKAN Package IDs (data.gov.au):
  BP0015 (student):          324aa4f7-46bb-4d56-bc2d-772333a2317e
  BP0014 (temp skilled):     Search by "bp0014" or "temporary resident skilled"
  BP0016 (temp graduate):    Search by "bp0016" or "temporary graduate"
  BP0068 (migration child):  Search by "bp0068"

USAGE:
    python ETL/etl_home_affairs_extended.py
    python ETL/etl_home_affairs_extended.py --datasets bp0014 bp0016 bp0068
    python ETL/etl_home_affairs_extended.py --local-only
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    add_etl_meta, ckan_resource_url, ckan_search_packages,
    download_file, get_db, get_logger, norm_col, upsert_df,
)

log = get_logger("ETL_HA_EXT")

BASE_DIR = Path(__file__).parent.parent
RAW_DIR  = BASE_DIR / "raw_data" / "home_affairs"
DB_PATH  = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA   = Path(__file__).parent / "schema.sql"

# ── CKAN Resource IDs ─────────────────────────────────────────────────────────
# These are the resource IDs found on data.gov.au for each report.
# If they expire, the script falls back to searching by package name.

RESOURCES = {
    # BP0015 — Student Visa Program
    "bp0015_lodged": {
        "resource_id": "ef31b2b4-a894-484b-99bc-e35d62ace777",
        "package_id":  "324aa4f7-46bb-4d56-bc2d-772333a2317e",
        "search_hint": "bp0015 student visas lodged",
        "local_glob":  "bp0015l-student-visas-lodged*.xlsx",
        "table":       "ha_student_visa_lodged",
        "parser":      "parse_bp0015_lodged",
    },
    "bp0015_granted": {
        "resource_id": "dfc7a893-0523-4b8e-bc5a-829e35bec90f",
        "package_id":  "324aa4f7-46bb-4d56-bc2d-772333a2317e",
        "search_hint": "bp0015 student visas granted",
        "local_glob":  "bp0015l-student-visas-granted*.xlsx",
        "table":       "ha_student_visa_granted",
        "parser":      "parse_bp0015_granted",
    },
    "bp0015_rates": {
        "resource_id": "b4775919-d0f5-4beb-8901-6384342774c6",
        "package_id":  "324aa4f7-46bb-4d56-bc2d-772333a2317e",
        "search_hint": "bp0015 student visa grant rates",
        "local_glob":  "bp0015l-student-visa-grant-rates*.xlsx",
        "table":       "ha_student_visa_grant_rates",
        "parser":      "parse_bp0015_rates",
    },
    # BP0014 — Temporary Resident Skilled Visas
    "bp0014_granted": {
        "resource_id": None,   # Look up via package search
        "package_id":  None,
        "search_hint": "bp0014 temporary resident skilled visas granted",
        "local_glob":  "bp0014l*granted*.xlsx",
        "table":       "ha_temp_skilled_visa_granted",
        "parser":      "parse_bp0014_granted",
    },
    "bp0014_holders": {
        "resource_id": None,
        "package_id":  None,
        "search_hint": "bp0014 temporary resident skilled visa holders",
        "local_glob":  "bp0014l*holders*.xlsx",
        "table":       "ha_temp_skilled_visa_holders",
        "parser":      "parse_bp0014_holders",
    },
    # BP0016 — Temporary Graduate Visas
    "bp0016_lodged": {
        "resource_id": None,
        "package_id":  None,
        "search_hint": "bp0016 temporary graduate visas lodged",
        "local_glob":  "bp0016l*lodged*.xlsx",
        "table":       "ha_temp_graduate_visa_lodged",
        "parser":      "parse_bp0016_lodged",
    },
    "bp0016_granted": {
        "resource_id": None,
        "package_id":  None,
        "search_hint": "bp0016 temporary graduate visa granted",
        "local_glob":  "bp0016l*granted*.xlsx",
        "table":       "ha_temp_graduate_visa_granted",
        "parser":      "parse_bp0016_granted",
    },
    # BP0068 — Migration and Child Outcomes
    "bp0068": {
        "resource_id": None,
        "package_id":  None,
        "search_hint": "bp0068 migration child outcome",
        "local_glob":  "bp0068*.xlsx",
        "table":       "ha_migration_child_outcomes",
        "parser":      "parse_bp0068",
    },
}


# ── SHARED HELPERS ────────────────────────────────────────────────────────────

def _find_header(raw: pd.DataFrame, hints: list[str], max_rows: int = 20) -> int:
    for i, row in raw.head(max_rows).iterrows():
        row_str = " ".join(row.dropna().astype(str)).lower()
        if any(h.lower() in row_str for h in hints):
            return i
    return 0


def _melt_wide(df: pd.DataFrame, id_cols: list[str], count_col: str) -> pd.DataFrame:
    """Melt a wide cross-tab (year/period columns) to long format."""
    year_cols = [c for c in df.columns if re.match(r"\d{4}", str(c)[:4]) and c not in id_cols]
    if not year_cols:
        return df
    return df.melt(id_vars=id_cols, value_vars=year_cols,
                   var_name="financial_year", value_name=count_col)


def _clean_count(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df[col] = pd.to_numeric(
        df[col].astype(str).str.replace(r"[^\d.-]", "", regex=True),
        errors="coerce"
    )
    return df.dropna(subset=[col])


# ── BP0015 PARSERS ────────────────────────────────────────────────────────────

def _parse_bp0015_generic(path: Path, sheet_hint: str, count_col: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    target = next((s for s in xl.sheet_names if sheet_hint.lower() in s.lower()), xl.sheet_names[0])
    raw = pd.read_excel(path, sheet_name=target, header=None)
    header_idx = _find_header(raw, ["Applicant Type", "Sector"])
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")

    # Standardise key columns
    for alias in ["applicant_type", "type", "category"]:
        if alias in df.columns:
            df = df.rename(columns={alias: "applicant_type"})
            break
    for alias in ["sector", "programme", "program"]:
        if alias in df.columns:
            df = df.rename(columns={alias: "sector"})
            break

    if "applicant_type" in df.columns:
        df["applicant_type"] = df["applicant_type"].ffill()

    df = df[df.get("sector", pd.Series(dtype=str)).notna()]
    df = df[~df.get("applicant_type", pd.Series(dtype=str)).astype(str).str.contains("Total", na=False)]

    long = _melt_wide(df, ["applicant_type", "sector"], count_col)
    return _clean_count(long, count_col)


def parse_bp0015_lodged(path: Path) -> pd.DataFrame:
    return _parse_bp0015_generic(path, "lodged", "lodged_count")

def parse_bp0015_granted(path: Path) -> pd.DataFrame:
    return _parse_bp0015_generic(path, "granted", "granted_count")

def parse_bp0015_rates(path: Path) -> pd.DataFrame:
    return _parse_bp0015_generic(path, "rate", "grant_rate_pct")


# ── BP0014 PARSERS ────────────────────────────────────────────────────────────

def parse_bp0014_granted(path: Path) -> pd.DataFrame:
    """Temp Resident Skilled Visas Granted — by visa subclass × nationality × year."""
    xl = pd.ExcelFile(path)
    target = next((s for s in xl.sheet_names if "grant" in s.lower()), xl.sheet_names[0])
    raw = pd.read_excel(path, sheet_name=target, header=None)
    header_idx = _find_header(raw, ["Visa", "Subclass", "Nationality", "Country"])
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")

    col_map = {}
    for c in df.columns:
        if "visa" in c and ("subclass" in c or "type" in c):
            col_map[c] = "visa_subclass"
        elif "nationality" in c or "country" in c or "citizenship" in c:
            col_map[c] = "nationality"
    df = df.rename(columns=col_map)

    if "visa_subclass" in df.columns:
        df["visa_subclass"] = df["visa_subclass"].ffill()

    long = _melt_wide(df, [c for c in ["visa_subclass", "nationality"] if c in df.columns], "granted_count")
    return _clean_count(long, "granted_count")


def parse_bp0014_holders(path: Path) -> pd.DataFrame:
    """Temp Resident Skilled Visa Holders — snapshot by subclass × nationality × state."""
    xl = pd.ExcelFile(path)
    target = next((s for s in xl.sheet_names if "holder" in s.lower()), xl.sheet_names[0])
    raw = pd.read_excel(path, sheet_name=target, header=None)
    header_idx = _find_header(raw, ["Visa", "Subclass", "State", "Territory", "Nationality"])
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")

    col_map = {}
    for c in df.columns:
        if "visa" in c and ("subclass" in c or "type" in c):
            col_map[c] = "visa_subclass"
        elif "nationality" in c or "country" in c or "citizenship" in c:
            col_map[c] = "nationality"
        elif "state" in c or "territory" in c:
            col_map[c] = "state_territory"

    df = df.rename(columns=col_map)
    if "visa_subclass" in df.columns:
        df["visa_subclass"] = df["visa_subclass"].ffill()

    # Try to find a date/count column
    count_candidates = [c for c in df.columns if re.search(r"\d{4}", str(c)[:4])
                        or "count" in c or "total" in c or "number" in c]
    if count_candidates:
        id_cols = [c for c in ["visa_subclass", "nationality", "state_territory"]
                   if c in df.columns]
        long = _melt_wide(df, id_cols, "holder_count")
        # Treat the var_name (financial_year) as as_at_date
        if "financial_year" in long.columns:
            long = long.rename(columns={"financial_year": "as_at_date"})
        return _clean_count(long, "holder_count")

    return df


# ── BP0016 PARSERS ────────────────────────────────────────────────────────────

def parse_bp0016_lodged(path: Path) -> pd.DataFrame:
    """Temp Graduate Visas Lodged — by stream × nationality × year."""
    xl = pd.ExcelFile(path)
    target = next((s for s in xl.sheet_names if "lodged" in s.lower()), xl.sheet_names[0])
    raw = pd.read_excel(path, sheet_name=target, header=None)
    header_idx = _find_header(raw, ["Stream", "Nationality", "Country", "Lodged"])
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")

    col_map = {}
    for c in df.columns:
        if "stream" in c or "subclass" in c or "visa" in c:
            col_map[c] = "stream"
        elif "nationality" in c or "country" in c or "citizenship" in c:
            col_map[c] = "nationality"
    df = df.rename(columns=col_map)

    if "stream" in df.columns:
        df["stream"] = df["stream"].ffill()

    long = _melt_wide(df, [c for c in ["stream", "nationality"] if c in df.columns], "lodged_count")
    return _clean_count(long, "lodged_count")


def parse_bp0016_granted(path: Path) -> pd.DataFrame:
    """Temp Graduate Visas Granted."""
    xl = pd.ExcelFile(path)
    target = next((s for s in xl.sheet_names if "grant" in s.lower()), xl.sheet_names[0])
    raw = pd.read_excel(path, sheet_name=target, header=None)
    header_idx = _find_header(raw, ["Stream", "Nationality", "Country", "Granted"])
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")

    col_map = {}
    for c in df.columns:
        if "stream" in c or "subclass" in c or "visa" in c:
            col_map[c] = "stream"
        elif "nationality" in c or "country" in c or "citizenship" in c:
            col_map[c] = "nationality"
    df = df.rename(columns=col_map)

    if "stream" in df.columns:
        df["stream"] = df["stream"].ffill()

    long = _melt_wide(df, [c for c in ["stream", "nationality"] if c in df.columns], "granted_count")
    return _clean_count(long, "granted_count")


# ── BP0068 PARSER ─────────────────────────────────────────────────────────────

def parse_bp0068(path: Path) -> pd.DataFrame:
    """
    Migration and Child Outcomes — complex multi-sheet report.
    Strategy: iterate all sheets, pick up any numeric data with visa/country identifiers.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  BP0068 sheets: {xl.sheet_names}")
    frames = []

    for sheet in xl.sheet_names:
        if sheet.lower() in ("contents", "notes", "glossary", "introduction"):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["visa", "country", "birth", "outcome"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            # Find id and value columns
            id_cols = []
            for c in df.columns:
                if any(x in c for x in ["visa", "type", "country", "birth"]):
                    id_cols.append(c)
            if not id_cols:
                continue

            val_cols = [c for c in df.columns if c not in id_cols]
            long = df.melt(id_vars=id_cols, value_vars=val_cols,
                           var_name="outcome_measure", value_name="value")
            long["value"] = pd.to_numeric(long["value"], errors="coerce")
            long = long.dropna(subset=["value"])

            # Standardise column names
            col_map = {}
            for c in long.columns:
                if "visa" in c and ("type" in c or "class" in c):
                    col_map[c] = "visa_type"
                elif "country" in c or "birth" in c:
                    col_map[c] = "birth_country"
                elif "period" in c or "year" in c:
                    col_map[c] = "period"
            long = long.rename(columns=col_map)
            long["_sheet"] = sheet
            frames.append(long)

        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["visa_type", "birth_country", "outcome_measure", "period", "value"]
            if c in result.columns]
    return result[keep]


# ── DOWNLOAD HELPER ───────────────────────────────────────────────────────────

def _get_local_file(cfg: dict) -> Path | None:
    """Find a matching local file using glob pattern."""
    matches = list(RAW_DIR.glob(cfg["local_glob"]))
    if matches:
        return sorted(matches)[-1]  # most recent if multiple
    return None


def _try_ckan_download(cfg: dict, force: bool) -> Path | None:
    """Try CKAN resource_show to get download URL."""
    # Method 1: known resource ID
    if cfg.get("resource_id"):
        try:
            resource = ckan_resource_url(cfg["resource_id"])
            url = resource["url"]
            fname = url.split("/")[-1].split("?")[0] or cfg["local_glob"].replace("*", "latest")
            return download_file(url, RAW_DIR, fname, force=force)
        except Exception as e:
            log.warning(f"  CKAN resource_id failed: {e}")

    # Method 2: search by package name
    if cfg.get("search_hint"):
        try:
            packages = ckan_search_packages(cfg["search_hint"], rows=3)
            for pkg in packages:
                resources = pkg.get("resources", [])
                for r in resources:
                    if r.get("format", "").upper() in ("XLSX", "XLS"):
                        url = r["url"]
                        fname = url.split("/")[-1].split("?")[0] or "ha_latest.xlsx"
                        return download_file(url, RAW_DIR, fname, force=force)
        except Exception as e:
            log.warning(f"  CKAN search failed: {e}")

    return None


# ── PARSER REGISTRY ───────────────────────────────────────────────────────────

PARSERS = {
    "parse_bp0015_lodged":  parse_bp0015_lodged,
    "parse_bp0015_granted": parse_bp0015_granted,
    "parse_bp0015_rates":   parse_bp0015_rates,
    "parse_bp0014_granted": parse_bp0014_granted,
    "parse_bp0014_holders": parse_bp0014_holders,
    "parse_bp0016_lodged":  parse_bp0016_lodged,
    "parse_bp0016_granted": parse_bp0016_granted,
    "parse_bp0068":         parse_bp0068,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def run(datasets: list[str] | None = None, force: bool = False,
        local_only: bool = False, dry_run: bool = False, db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0

    keys = datasets if datasets else list(RESOURCES.keys())

    for key in keys:
        if key not in RESOURCES:
            log.warning(f"  Unknown dataset key: {key}")
            continue

        cfg = RESOURCES[key]
        log.info(f"\n{'─'*55}")
        log.info(f"[HA] {key} → {cfg['table']}")

        # 1. Find file
        local_file = None
        if not local_only:
            local_file = _try_ckan_download(cfg, force)
        if local_file is None:
            local_file = _get_local_file(cfg)
        if local_file is None or not local_file.exists():
            log.warning(f"  ⚠️  No file found for {key} — skipping")
            continue

        log.info(f"  File: {local_file.name}")

        # 2. Parse
        parser_fn = PARSERS.get(cfg["parser"])
        if parser_fn is None:
            log.error(f"  No parser: {cfg['parser']}")
            continue

        try:
            df = parser_fn(local_file)
        except Exception as e:
            log.error(f"  ❌ Parse failed: {e}")
            continue

        if df.empty:
            log.warning(f"  ⚠️  Parser returned empty DataFrame")
            continue

        log.info(f"  Parsed {len(df):,} rows")

        # 3. Add metadata + load
        df = add_etl_meta(df, f"home_affairs/{local_file.name}")
        n = upsert_df(df, cfg["table"], conn, dry_run=dry_run)
        total += n

    log.info(f"\n✅ Home Affairs ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: Home Affairs Extended")
    ap.add_argument("--datasets",   nargs="*", help="Which datasets to run (default: all)")
    ap.add_argument("--force",      action="store_true")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--db",         default=str(DB_PATH))
    args = ap.parse_args()
    run(datasets=args.datasets, force=args.force, local_only=args.local_only,
        dry_run=args.dry_run, db_path=Path(args.db))
