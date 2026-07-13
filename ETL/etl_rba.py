"""
etl_rba.py
===========
ETL: Reserve Bank of Australia — Exchange Rates
Tables: F11 (all currencies) + F11.1 (selected AUD rates)

API:  Direct CSV download from rba.gov.au (permanent stable URLs)
Data: Daily / monthly exchange rates for AUD vs major currencies

USAGE:
    python ETL/etl_rba.py
    python ETL/etl_rba.py --force   # re-download even if file exists
    python ETL/etl_rba.py --dry-run
"""

import argparse
from pathlib import Path

from ETL.lib_etl import (
    add_etl_meta, download_file, get_db, get_logger, read_rba_csv, upsert_df
)

log = get_logger("ETL_RBA")

BASE_DIR  = Path(__file__).parent.parent
RAW_DIR   = BASE_DIR / "raw_data" / "rba"
DB_PATH   = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA    = Path(__file__).parent / "schema.sql"
TABLE     = "rba_exchange_rates"

# Stable permanent download URLs (RBA maintains these indefinitely)
SOURCES = [
    {
        "url":      "https://www.rba.gov.au/statistics/tables/csv/f11-data.csv",
        "filename": "f11-data.csv",
        "table_tag": "f11",
        "desc":     "F11 Exchange rates — all series",
    },
    {
        "url":      "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv",
        "filename": "f11.1-data.csv",
        "table_tag": "f11.1",
        "desc":     "F11.1 Selected exchange rates",
    },
]


def run(force: bool = False, dry_run: bool = False, db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0

    for src in SOURCES:
        log.info(f"\n{'─'*55}")
        log.info(f"[RBA] {src['desc']}")

        # 1. Download (skip if exists unless --force)
        local = download_file(src["url"], RAW_DIR, src["filename"], force=force)

        # 2. Parse RBA CSV format
        log.info(f"  Parsing {local.name} ...")
        df = read_rba_csv(local)
        df["source_table"] = src["table_tag"]
        df = add_etl_meta(df, f"rba/{src['filename']}")

        # 3. Normalise date to ISO string
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")

        # 4. Keep only rows with actual values
        df = df.dropna(subset=["value"])

        log.info(f"  {len(df):,} rows parsed")

        # 5. Load
        n = upsert_df(df, TABLE, conn, dry_run=dry_run)
        total += n

    log.info(f"\n✅ RBA ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: RBA Exchange Rates")
    ap.add_argument("--force",   action="store_true", help="Force re-download")
    ap.add_argument("--dry-run", action="store_true", help="Parse but don't write to DB")
    ap.add_argument("--db",      default=str(DB_PATH), help="SQLite DB path")
    args = ap.parse_args()
    run(force=args.force, dry_run=args.dry_run, db_path=Path(args.db))
