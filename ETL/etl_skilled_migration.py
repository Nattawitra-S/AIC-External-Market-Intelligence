"""
etl_skilled_migration.py
=========================
ETL: Skilled Migration Programme Reports

Source files in raw_data/skilled_migration/:
  • skilled_visas_summaries.xlsx           — annual summary by visa/stream/state
  • skilled_visas_country_occupation.xlsx  — by country of birth × occupation
  • skilled_visas_raw_all_1.4M_rows.csv    — full raw extract (1.4 million rows)

API:  data.gov.au CKAN API — search by "skilled migration programme report"
      Fallback: local files

Tables:
  • skilled_migration_summary              (from summaries xlsx)
  • skilled_migration_country_occupation   (from country_occupation xlsx)
  Note: The 1.4M CSV is loaded into skilled_migration_summary in chunks

USAGE:
    python ETL/etl_skilled_migration.py
    python ETL/etl_skilled_migration.py --skip-raw    # skip the 1.4M row CSV (slow)
    python ETL/etl_skilled_migration.py --local-only
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    add_etl_meta, ckan_search_packages, download_file,
    get_db, get_logger, norm_col, upsert_df,
)

log = get_logger("ETL_SKILLED")

BASE_DIR = Path(__file__).parent.parent
RAW_DIR  = BASE_DIR / "raw_data" / "skilled_migration"
DB_PATH  = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA   = Path(__file__).parent / "schema.sql"

LOCAL_FILES = {
    "summaries":          RAW_DIR / "skilled_visas_summaries.xlsx",
    "country_occupation": RAW_DIR / "skilled_visas_country_occupation.xlsx",
    "raw_csv":            RAW_DIR / "skilled_visas_raw_all_1.4M_rows.csv",
}

CHUNK_SIZE = 50_000  # rows per chunk for large CSV


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _find_header(raw: pd.DataFrame, hints: list[str]) -> int:
    for i, row in raw.head(15).iterrows():
        if row.astype(str).str.contains("|".join(hints), case=False, na=False, regex=True).any():
            return i
    return 0


def _melt_years(df: pd.DataFrame, id_cols: list[str], value_col: str) -> pd.DataFrame:
    year_cols = [c for c in df.columns
                 if re.match(r"^\d{4}", str(c).strip()) and c not in id_cols]
    if not year_cols:
        return df
    return df.melt(id_vars=id_cols, value_vars=year_cols,
                   var_name="financial_year", value_name=value_col)


# ── PARSERS ───────────────────────────────────────────────────────────────────

def parse_summaries(path: Path) -> pd.DataFrame:
    """
    skilled_visas_summaries.xlsx
    Expected: annual summary cross-tab by visa subclass / stream / state.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Summary sheets: {xl.sheet_names[:6]}")
    frames = []

    for sheet in xl.sheet_names:
        if any(x in sheet.lower() for x in ["note", "content", "source", "glossary"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Visa", "Subclass", "Stream", "State", "Territory"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            id_cols = []
            col_map = {}
            for c in df.columns:
                if "visa" in c and ("subclass" in c or "type" in c):
                    col_map[c] = "visa_subclass"
                    id_cols.append("visa_subclass")
                elif "stream" in c or "program" in c:
                    col_map[c] = "stream"
                    id_cols.append("stream")
                elif "state" in c or "territory" in c:
                    col_map[c] = "state_territory"
                    id_cols.append("state_territory")
            df = df.rename(columns=col_map)
            id_cols = list(dict.fromkeys(id_cols))  # deduplicate preserving order

            if not id_cols:
                continue

            # Forward-fill hierarchical labels
            for c in id_cols:
                if c in df.columns:
                    df[c] = df[c].ffill()

            long = _melt_years(df, id_cols, "value")
            if "value" not in long.columns:
                continue

            long["value"] = pd.to_numeric(
                long["value"].astype(str).str.replace(r"[^\d.-]", "", regex=True),
                errors="coerce"
            )
            long["measure"] = sheet.strip()
            long = long.dropna(subset=["value"])
            frames.append(long)

        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["financial_year", "visa_subclass", "stream",
                         "state_territory", "measure", "value"]
            if c in result.columns]
    return result[keep]


def parse_country_occupation(path: Path) -> pd.DataFrame:
    """
    skilled_visas_country_occupation.xlsx
    Cross-tab: country of birth × ANZSCO occupation × visa subclass.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  CountryOcc sheets: {xl.sheet_names[:6]}")
    frames = []

    for sheet in xl.sheet_names:
        if any(x in sheet.lower() for x in ["note", "content", "source"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Country", "Birth", "ANZSCO", "Occupation"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            id_cols = []
            col_map = {}
            for c in df.columns:
                if "country" in c or "birth" in c or "nationality" in c:
                    col_map[c] = "country_of_birth"
                    id_cols.append("country_of_birth")
                elif "anzsco" in c and "code" in c:
                    col_map[c] = "anzsco_code"
                    id_cols.append("anzsco_code")
                elif "anzsco" in c and "code" not in c:
                    pass  # skip anzsco name cols for now
                elif "occupation" in c or "title" in c:
                    col_map[c] = "occupation_name"
                    id_cols.append("occupation_name")
                elif "visa" in c and ("subclass" in c or "type" in c):
                    col_map[c] = "visa_subclass"
                    id_cols.append("visa_subclass")
            df = df.rename(columns=col_map)
            id_cols = list(dict.fromkeys(id_cols))

            if not id_cols:
                continue

            long = _melt_years(df, id_cols, "value")
            if "value" not in long.columns:
                continue

            long["value"] = pd.to_numeric(
                long["value"].astype(str).str.replace(r"[^\d.-]", "", regex=True),
                errors="coerce"
            )
            long["measure"] = sheet.strip()
            long = long.dropna(subset=["value"])
            frames.append(long)

        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["financial_year", "country_of_birth", "anzsco_code",
                         "occupation_name", "visa_subclass", "value", "measure"]
            if c in result.columns]
    return result[keep]


def load_raw_csv_chunks(path: Path, conn, dry_run: bool = False) -> int:
    """
    Stream the 1.4M row CSV in chunks into skilled_migration_summary.
    Uses minimal memory by processing one chunk at a time.
    """
    log.info(f"  Loading large CSV in chunks of {CHUNK_SIZE:,}: {path.name}")
    total = 0
    chunk_num = 0

    for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, low_memory=False, encoding="utf-8-sig"):
        chunk_num += 1
        # Normalise columns
        chunk.columns = [norm_col(c) for c in chunk.columns]

        # Map to summary table schema
        col_map = {}
        for c in chunk.columns:
            if re.match(r"\d{4}", str(c)[:4]) or "year" in c or "fy" in c:
                col_map[c] = "financial_year"
            elif "visa" in c and ("subclass" in c or "type" in c):
                col_map[c] = "visa_subclass"
            elif "stream" in c or "program" in c:
                col_map[c] = "stream"
            elif "state" in c or "territory" in c:
                col_map[c] = "state_territory"
            elif "count" in c or "number" in c or "total" in c or "value" in c:
                col_map[c] = "value"
            elif "measure" in c or "type" in c:
                col_map[c] = "measure"
        chunk = chunk.rename(columns=col_map)

        if "value" in chunk.columns:
            chunk["value"] = pd.to_numeric(chunk["value"], errors="coerce")
            chunk = chunk.dropna(subset=["value"])

        chunk = add_etl_meta(chunk, f"skilled_migration/{path.name}")
        n = upsert_df(chunk, "skilled_migration_summary", conn, dry_run=dry_run)
        total += n

        if chunk_num % 10 == 0:
            log.info(f"  ... chunk {chunk_num}, {total:,} rows so far")

    log.info(f"  ✅ CSV loaded: {total:,} rows in {chunk_num} chunks")
    return total


# ── Main ──────────────────────────────────────────────────────────────────────

def run(skip_raw: bool = False, local_only: bool = False,
        dry_run: bool = False, db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0

    # 1. Summaries XLSX
    log.info(f"\n{'─'*55}")
    log.info("[SkillMig] skilled_visas_summaries.xlsx")
    path = LOCAL_FILES["summaries"]
    if path.exists():
        try:
            df = parse_summaries(path)
            if not df.empty:
                df = add_etl_meta(df, f"skilled_migration/{path.name}")
                n = upsert_df(df, "skilled_migration_summary", conn, dry_run=dry_run)
                total += n
                log.info(f"  {n:,} rows → skilled_migration_summary")
        except Exception as e:
            log.error(f"  ❌ {e}")
    else:
        log.warning(f"  ⚠️  Not found: {path}")

    # 2. Country × Occupation XLSX
    log.info(f"\n{'─'*55}")
    log.info("[SkillMig] skilled_visas_country_occupation.xlsx")
    path = LOCAL_FILES["country_occupation"]
    if path.exists():
        try:
            df = parse_country_occupation(path)
            if not df.empty:
                df = add_etl_meta(df, f"skilled_migration/{path.name}")
                n = upsert_df(df, "skilled_migration_country_occupation", conn, dry_run=dry_run)
                total += n
                log.info(f"  {n:,} rows → skilled_migration_country_occupation")
        except Exception as e:
            log.error(f"  ❌ {e}")
    else:
        log.warning(f"  ⚠️  Not found: {path}")

    # 3. Raw CSV (1.4M rows) — optional, slow
    if not skip_raw:
        log.info(f"\n{'─'*55}")
        log.info("[SkillMig] skilled_visas_raw_all_1.4M_rows.csv (large file)")
        path = LOCAL_FILES["raw_csv"]
        if path.exists():
            try:
                n = load_raw_csv_chunks(path, conn, dry_run=dry_run)
                total += n
            except Exception as e:
                log.error(f"  ❌ CSV load failed: {e}")
        else:
            log.warning(f"  ⚠️  Not found: {path}")
    else:
        log.info("\n[SkillMig] Skipping 1.4M raw CSV (--skip-raw)")

    log.info(f"\n✅ Skilled Migration ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: Skilled Migration")
    ap.add_argument("--skip-raw",   action="store_true", help="Skip the 1.4M row CSV")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--db",         default=str(DB_PATH))
    args = ap.parse_args()
    run(skip_raw=args.skip_raw, local_only=args.local_only,
        dry_run=args.dry_run, db_path=Path(args.db))
