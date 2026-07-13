"""
run_all.py
===========
AIC Market Intelligence ETL — MySQL Pipeline Runner

Target database: MySQL 8 (InnoDB, utf8mb4)
Credentials:     MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASS / MYSQL_DB (see .env)

USAGE:
    python ETL/run_all.py                          # run all 7 sources
    python ETL/run_all.py --dry-run                # parse, no writes
    python ETL/run_all.py --sources rba cricos     # selected sources
    python ETL/run_all.py --local-only             # use local files, no downloads
    python ETL/run_all.py --local-only --skip-heavy  # fast run, skip 1.4M CSV

SOURCES:
    rba               RBA exchange rates (F11, F11.1)
    cricos            CRICOS institutions, courses, locations
    jsa               JSA internet vacancies + occupation shortage
    home_affairs      Dept of Home Affairs (BP0015/0014/0016/0068)
    abs               ABS labour force, CPI, migration, population
    education         Dept of Education international student data (3.5M rows)
    skilled_migration Skilled migration programme reports
"""

import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("RUN_ALL")


# ── MYSQL SOURCE REGISTRY ─────────────────────────────────────────────────────

SOURCE_DESCS = {
    "rba":               "RBA Exchange Rates (F11, F11.1)",
    "cricos":            "CRICOS Institutions, Courses, Locations",
    "jsa":               "JSA Internet Vacancies + Occupation Shortage",
    "home_affairs":      "Home Affairs BP0015/0014/0016/0068",
    "abs":               "ABS Labour Force, CPI, Migration, Population",
    "education":         "Dept of Education International Student Data (3.5M rows)",
    "skilled_migration": "Skilled Migration Programme Reports",
}

ALL_SOURCE_KEYS = list(SOURCE_DESCS.keys())


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run_all(
    sources: list[str] | None = None,
    dry_run: bool = False,
    local_only: bool = False,
    force: bool = False,
    skip_heavy: bool = False,
    staging_dir: Path | None = None,
    abs_source: str = "local",
) -> dict:
    """
    Run all (or selected) ETL sources into MySQL.

    Connects once, applies schema if tables are missing, then calls
    each run_mysql_*() function in dependency order.

    Returns: {source_key: {status, rows, elapsed}}
    """
    from ETL.lib_etl_mysql import get_mysql_conn
    import ETL.run_mysql_sources as rms

    SCHEMA_FILE = Path(__file__).parent / "schema_mysql.sql"

    started_at = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info("AIC Market Intelligence — MySQL ETL Pipeline")
    log.info(f"Started:    {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log.info(f"Dry run:    {dry_run}")
    log.info(f"Local only: {local_only}")
    log.info("=" * 60)

    if dry_run:
        # TRUE ZERO-WRITE: do not connect to MySQL, do not apply schema,
        # do not insert/update/delete/alter/commit anything.
        # conn=None propagates through all run_mysql_* functions and
        # causes AuditRun, upsert_df_mysql, and bulk_load_csv to be no-ops.
        log.info("  [DRY-RUN] No MySQL connection will be opened. Parse-only mode.")
        conn = None
    else:
        conn = get_mysql_conn(schema_file=SCHEMA_FILE)

    # Source → callable mapping
    source_fns = {
        "rba": lambda: rms.run_mysql_rba(
            conn, dry_run=dry_run, force=force),
        "cricos": lambda: rms.run_mysql_cricos(
            conn, dry_run=dry_run, force=force, local_only=local_only),
        "jsa": lambda: rms.run_mysql_jsa(
            conn, dry_run=dry_run),
        "home_affairs": lambda: rms.run_mysql_home_affairs(
            conn, dry_run=dry_run, force=force, local_only=local_only),
        "abs": lambda: rms.run_mysql_abs(
            conn, dry_run=dry_run, source=abs_source),
        "education": lambda: rms.run_mysql_education(
            conn, dry_run=dry_run, force=force, local_only=local_only,
            staging_dir=staging_dir),
        "skilled_migration": lambda: rms.run_mysql_skilled_migration(
            conn, dry_run=dry_run, skip_raw=skip_heavy),
    }

    run_keys = sources if sources else ALL_SOURCE_KEYS
    results: dict = {}

    for key in run_keys:
        if key not in source_fns:
            log.warning(f"Unknown source: {key!r}  (valid: {', '.join(ALL_SOURCE_KEYS)})")
            continue

        log.info(f"\n{'═'*60}")
        log.info(f"  {SOURCE_DESCS[key]}")
        log.info(f"{'═'*60}")

        t0 = time.time()
        try:
            n = source_fns[key]()
            elapsed = time.time() - t0
            results[key] = {"status": "ok", "rows": n or 0, "elapsed": elapsed}
            log.info(f"  ✅ {key}: {n:,} rows in {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - t0
            results[key] = {"status": "error", "error": str(e), "elapsed": elapsed}
            log.error(f"  ❌ {key}: {e}")
            traceback.print_exc()

    try:
        conn.close()
    except Exception:
        pass

    # ── Summary ───────────────────────────────────────────────────────────────
    finished_at = datetime.now(timezone.utc)
    total_elapsed = (finished_at - started_at).total_seconds()
    ok_keys   = [k for k, r in results.items() if r["status"] == "ok"]
    fail_keys = [k for k, r in results.items() if r["status"] == "error"]
    total_rows = sum(r.get("rows", 0) for r in results.values())

    log.info(f"\n{'='*60}")
    log.info("PIPELINE SUMMARY")
    log.info(f"{'='*60}")
    for k in ok_keys:
        r = results[k]
        log.info(f"  ✅ {k:<25} {r['rows']:>12,} rows   {r['elapsed']:5.1f}s")
    for k in fail_keys:
        r = results[k]
        log.info(f"  ❌ {k:<25} ERROR: {r['error'][:60]}")
    log.info(f"{'─'*60}")
    log.info(f"  Total rows:  {total_rows:,}")
    log.info(f"  Success:     {len(ok_keys)}/{len(results)}")
    log.info(f"  Total time:  {total_elapsed:.1f}s")
    log.info(f"  Finished:    {finished_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if fail_keys:
        log.info(f"\n⚠️  {len(fail_keys)} source(s) failed — check MYSQL_* env vars and logs above")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="AIC Market Intelligence — MySQL ETL Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Sources: rba  cricos  jsa  home_affairs  abs  education  skilled_migration

Examples:
  python ETL/run_all.py                                # run all 7 sources
  python ETL/run_all.py --dry-run                      # parse only, no writes
  python ETL/run_all.py --sources rba cricos           # selected sources
  python ETL/run_all.py --local-only                   # no network downloads
  python ETL/run_all.py --local-only --skip-heavy      # fast run

Credentials (required in .env or shell environment):
  MYSQL_HOST  MYSQL_PORT  MYSQL_USER  MYSQL_PASS  MYSQL_DB
        """,
    )
    ap.add_argument("--sources",      nargs="*",          help="Sources to run (default: all 7)")
    ap.add_argument("--dry-run",      action="store_true", help="Parse but do not write to MySQL")
    ap.add_argument("--local-only",   action="store_true", help="Use local files only (no downloads)")
    ap.add_argument("--force",        action="store_true", help="Force re-download of all source files")
    ap.add_argument("--skip-heavy",   action="store_true", help="Skip the 1.4M-row skilled migration CSV")
    args = ap.parse_args()

    results = run_all(
        sources=args.sources,
        dry_run=args.dry_run,
        local_only=args.local_only,
        force=args.force,
        skip_heavy=args.skip_heavy,
    )

    failed = [k for k, r in results.items() if r["status"] == "error"]
    sys.exit(1 if failed else 0)
