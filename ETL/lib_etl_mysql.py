"""
lib_etl_mysql.py
=================
MySQL implementation of AIC ETL shared utilities.

Replaces the SQLite-based lib_etl.py for production MySQL loads.
Do NOT overwrite lib_etl.py — run both in parallel during migration.

Requirements:
    pip install mysql-connector-python python-dotenv pandas

Environment variables (see .env.example):
    MYSQL_HOST      default: localhost
    MYSQL_PORT      default: 3306
    MYSQL_USER      required
    MYSQL_PASS      required
    MYSQL_DB        required

Usage:
    from ETL.lib_etl_mysql import get_mysql_conn, upsert_df_mysql, bulk_load_csv
"""

import csv
import io
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mysql.connector
import mysql.connector.pooling
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass   # python-dotenv optional; env vars must be set externally


# ── Constants ──────────────────────────────────────────────────────────────────
CHUNK_SIZE      = 10_000    # rows per executemany batch
BULK_CHUNK_ROWS = 100_000   # rows per CSV chunk for LOAD DATA staging
MAX_RETRIES     = 3
RETRY_DELAY_S   = 5.0

# ── Logging ────────────────────────────────────────────────────────────────────
def get_logger(name: str = "AIC_ETL_MYSQL") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"
        ))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log

log = get_logger()


# ── MySQL Connection ───────────────────────────────────────────────────────────

def _mysql_config() -> dict:
    """Build MySQL connection config from environment variables."""
    host = os.environ.get("MYSQL_HOST", "localhost")
    port = int(os.environ.get("MYSQL_PORT", "3306"))
    user = os.environ.get("MYSQL_USER", "")
    password = os.environ.get("MYSQL_PASS", "")
    database = os.environ.get("MYSQL_DB", "")

    if not user or not database:
        raise EnvironmentError(
            "MYSQL_USER and MYSQL_DB environment variables are required. "
            "Copy .env.example → .env and fill in your credentials."
        )
    return {
        "host":     host,
        "port":     port,
        "user":     user,
        "password": password,
        "database": database,
        "charset":  "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
        "autocommit": False,
        "connection_timeout": 30,
        "allow_local_infile": True,   # needed for LOAD DATA LOCAL INFILE
    }


def get_mysql_conn(schema_file: Optional[Path] = None):
    """
    Open a MySQL connection. Optionally apply a schema SQL file.

    Returns: mysql.connector.MySQLConnection
    """
    cfg = _mysql_config()
    conn = mysql.connector.connect(**cfg)
    log.info(f"Connected to MySQL: {cfg['host']}:{cfg['port']}/{cfg['database']}")

    if schema_file and schema_file.exists():
        _apply_schema(conn, schema_file)

    return conn


def _apply_schema(conn, schema_file: Path):
    """Execute each statement in a SQL file."""
    sql = schema_file.read_text(encoding="utf-8")
    cursor = conn.cursor()
    # Split on semicolons, skip empty statements and comment-only blocks
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            try:
                cursor.execute(stmt)
            except mysql.connector.Error as e:
                # Warn on minor errors (e.g., IF NOT EXISTS — already exists)
                if e.errno not in (1050, 1060, 1061, 1062):  # table/column/index/dup exists
                    log.warning(f"Schema statement skipped: {e}")
    conn.commit()
    cursor.close()
    log.info(f"Schema applied: {schema_file.name}")


@contextmanager
def mysql_transaction(conn):
    """Context manager for a database transaction with automatic rollback on error."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Audit Logging ──────────────────────────────────────────────────────────────

class AuditRun:
    """
    Track a single ETL run in etl_audit_log.

    When conn is None (dry-run mode), all methods are no-ops — zero database
    writes occur. This is the mechanism that guarantees true zero-write dry-runs.
    """

    def __init__(self, conn, source: str, table_name: str):
        self.conn = conn          # None in dry-run → all methods become no-ops
        self.source = source
        self.table_name = table_name
        self.run_id: Optional[int] = None
        self.rows_read = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        self.rows_rejected = 0
        if self.conn is not None:
            self._start()

    def _start(self):
        if self.conn is None:
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO etl_audit_log
               (source, table_name, started_at, status)
               VALUES (%s, %s, %s, 'running')""",
            (self.source, self.table_name, datetime.now(timezone.utc))
        )
        self.conn.commit()
        self.run_id = cursor.lastrowid
        cursor.close()

    def complete(self):
        if self.conn is None:
            log.info(f"  [dry-run audit] {self.source}/{self.table_name}: "
                     f"{self.rows_read} rows parsed (no write)")
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """UPDATE etl_audit_log
               SET completed_at=%s, rows_read=%s, rows_inserted=%s,
                   rows_updated=%s, rows_rejected=%s, status='completed'
               WHERE run_id=%s""",
            (datetime.now(timezone.utc), self.rows_read, self.rows_inserted,
             self.rows_updated, self.rows_rejected, self.run_id)
        )
        self.conn.commit()
        cursor.close()
        log.info(f"  [audit] run_id={self.run_id} completed: "
                 f"{self.rows_inserted} inserted, {self.rows_updated} updated, "
                 f"{self.rows_rejected} rejected")

    def fail(self, error_msg: str):
        if self.conn is None:
            log.warning(f"  [dry-run audit] {self.source}/{self.table_name} failed: {error_msg}")
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """UPDATE etl_audit_log
               SET completed_at=%s, status='failed', error_message=%s
               WHERE run_id=%s""",
            (datetime.now(timezone.utc), str(error_msg)[:2000], self.run_id)
        )
        self.conn.commit()
        cursor.close()


# ── Upsert (INSERT … ON DUPLICATE KEY UPDATE) ─────────────────────────────────

def upsert_df_mysql(
    df: pd.DataFrame,
    table: str,
    conn,
    dry_run: bool = False,
    audit: Optional[AuditRun] = None,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """
    Upsert a DataFrame into a MySQL table using INSERT … ON DUPLICATE KEY UPDATE.

    - Processes in chunks of `chunk_size` rows to limit memory and transaction size.
    - NULL-safe: converts pandas NA/NaN to Python None.
    - Returns total rows processed.
    """
    if df.empty:
        log.warning(f"  ↳ Nothing to upsert into `{table}` (empty DataFrame)")
        return 0

    # Treat conn=None (dry-run mode) identically to dry_run=True — zero writes
    if dry_run or conn is None:
        log.info(f"  [dry-run] Would upsert {len(df):,} rows → `{table}`")
        if audit:
            audit.rows_read += len(df)
        return len(df)

    cols = list(df.columns)
    col_list     = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    updates      = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
    sql = (
        f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )

    total = 0
    cursor = conn.cursor()

    try:
        for chunk_start in range(0, len(df), chunk_size):
            chunk = df.iloc[chunk_start: chunk_start + chunk_size]
            rows = [
                tuple(None if (v is None or (not isinstance(v, str) and pd.isna(v))) else v
                      for v in r)
                for r in chunk.itertuples(index=False, name=None)
            ]
            cursor.executemany(sql, rows)
            conn.commit()
            total += len(rows)

            if audit:
                audit.rows_read += len(rows)
                audit.rows_inserted += cursor.rowcount  # 1 per insert, 2 per update in MySQL

            if chunk_start % (chunk_size * 10) == 0 and chunk_start > 0:
                log.info(f"  ... {total:,} rows upserted to `{table}`")

    except mysql.connector.Error as e:
        conn.rollback()
        if audit:
            audit.rows_rejected += len(df) - total
        raise RuntimeError(f"MySQL upsert failed at row {total}: {e}") from e
    finally:
        cursor.close()

    log.info(f"  ↳ Upserted {total:,} rows → `{table}`")
    return total


# ── Bulk Load via LOAD DATA LOCAL INFILE ──────────────────────────────────────

def bulk_load_csv(
    df: pd.DataFrame,
    table: str,
    conn,
    staging_dir: Optional[Path] = None,
    dry_run: bool = False,
    audit: Optional[AuditRun] = None,
    null_sentinel: str = "\\N",
) -> int:
    """
    Efficiently load a large DataFrame into MySQL using LOAD DATA LOCAL INFILE.

    Writes the DataFrame to a temporary CSV file, then issues LOAD DATA LOCAL INFILE.
    Dramatically faster than executemany() for 100k+ rows (50–100x speedup).

    Args:
        df:           DataFrame to load
        table:        Target MySQL table name
        conn:         MySQL connection (must have allow_local_infile=True)
        staging_dir:  Where to write the temp CSV (default: /tmp)
        dry_run:      If True, write CSV but do not execute LOAD DATA
        audit:        Optional AuditRun for tracking
        null_sentinel: String used for NULL values in CSV (default: \\N = MySQL NULL)

    Returns:
        Number of rows loaded
    """
    if df.empty:
        log.warning(f"  ↳ bulk_load_csv: empty DataFrame for `{table}`")
        return 0

    staging_dir = staging_dir or Path("/tmp")
    staging_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = staging_dir / f"aic_bulk_{table}_{ts}.csv"

    cols = list(df.columns)

    # Write CSV with NULL sentinel
    log.info(f"  ↳ Writing {len(df):,} rows to {csv_path.name} ...")
    df.to_csv(
        csv_path,
        index=False,
        header=True,
        encoding="utf-8",
        na_rep=null_sentinel,
        quoting=csv.QUOTE_MINIMAL,
    )

    # Treat conn=None (dry-run mode) identically to dry_run=True — zero writes
    if dry_run or conn is None:
        log.info(f"  [dry-run] Would LOAD DATA from {csv_path.name} → `{table}`")
        csv_path.unlink(missing_ok=True)
        if audit:
            audit.rows_read += len(df)
        return len(df)

    col_list = ", ".join(f"`{c}`" for c in cols)
    sql = f"""
        LOAD DATA LOCAL INFILE '{csv_path.as_posix()}'
        INTO TABLE `{table}`
        CHARACTER SET utf8mb4
        FIELDS TERMINATED BY ','
        OPTIONALLY ENCLOSED BY '"'
        LINES TERMINATED BY '\\n'
        IGNORE 1 LINES
        ({col_list})
    """

    log.info(f"  ↳ LOAD DATA LOCAL INFILE → `{table}` ...")
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        conn.commit()
        n = cursor.rowcount
        log.info(f"  ↳ Loaded {n:,} rows → `{table}`")
        if audit:
            audit.rows_read += len(df)
            audit.rows_inserted += n
        return n
    except mysql.connector.Error as e:
        conn.rollback()
        if audit:
            audit.rows_rejected += len(df)
        raise RuntimeError(f"LOAD DATA failed for `{table}`: {e}") from e
    finally:
        cursor.close()
        csv_path.unlink(missing_ok=True)  # clean up temp file


# ── Retry Wrapper ──────────────────────────────────────────────────────────────

def with_retry(fn, retries: int = MAX_RETRIES, delay: float = RETRY_DELAY_S, label: str = ""):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            log.warning(f"  ⚠️  {label or fn.__name__} attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
    raise last_err


# ── ETL Metadata Helper ────────────────────────────────────────────────────────

def add_etl_meta(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Add _etl_source and _etl_loaded_at to DataFrame."""
    df = df.copy()
    df["_etl_source"]    = source
    df["_etl_loaded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return df


# ── Column Normaliser ──────────────────────────────────────────────────────────

def norm_col(c: str) -> str:
    """Normalise column name to snake_case."""
    c = str(c).strip().lower()
    c = re.sub(r"[^a-z0-9]+", "_", c)
    return c.strip("_")


# ── Education Bulk Load (3.5M rows) ──────────────────────────────────────────

def load_education_enrolments(
    df: pd.DataFrame,
    conn,
    staging_dir: Optional[Path] = None,
    dry_run: bool = False,
    chunk_rows: int = BULK_CHUNK_ROWS,
) -> int:
    """
    Efficiently load education_enrolments (3.5M rows) using chunked LOAD DATA.

    Splits the DataFrame into chunks, stages each as a CSV, and issues
    LOAD DATA LOCAL INFILE per chunk. This avoids holding the full 3.5M rows
    in a single transaction.

    Args:
        df:          Full education enrolments DataFrame
        conn:        MySQL connection
        staging_dir: Temp CSV directory (default: /tmp)
        dry_run:     Parse only, don't write
        chunk_rows:  Rows per CSV chunk (default: 100,000)

    Returns:
        Total rows loaded
    """
    table = "fact_student_enrolment"
    staging_dir = staging_dir or Path("/tmp")
    audit = AuditRun(conn, "education", table)
    total = 0
    n_chunks = (len(df) + chunk_rows - 1) // chunk_rows

    log.info(f"  Loading {len(df):,} rows → `{table}` in {n_chunks} chunks of {chunk_rows:,}")

    try:
        for i in range(0, len(df), chunk_rows):
            chunk_num = i // chunk_rows + 1
            chunk = df.iloc[i: i + chunk_rows].copy()
            log.info(f"  Chunk {chunk_num}/{n_chunks}: {len(chunk):,} rows")

            n = bulk_load_csv(
                df=chunk,
                table=table,
                conn=conn,
                staging_dir=staging_dir,
                dry_run=dry_run,
                audit=audit,
            )
            total += n

        audit.complete()
        log.info(f"  ✅ Education enrolments: {total:,} rows loaded")

    except Exception as e:
        audit.fail(str(e))
        raise

    return total


# ── Convenience: run DDL ───────────────────────────────────────────────────────

def create_schema_if_missing(conn, schema_file: Path):
    """Apply schema_mysql.sql to the connected database if tables don't exist."""
    _apply_schema(conn, schema_file)


# ── Connection Test ────────────────────────────────────────────────────────────

def test_connection() -> bool:
    """Quick connection test. Returns True if successful."""
    try:
        conn = get_mysql_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        log.info(f"  MySQL connection OK — version: {version}")
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        log.error(f"  MySQL connection failed: {e}")
        return False


if __name__ == "__main__":
    # Quick connection test
    ok = test_connection()
    print("✅ Connection OK" if ok else "❌ Connection failed — check .env")
