"""
lib_etl.py
===========
Shared utilities for all AIC ETL scripts.

Covers:
  • CKAN API (data.gov.au) — resource_show → download URL
  • Direct URL download with resume / skip-if-exists
  • ABS SDMX REST API — data fetch → DataFrame
  • Flexible Excel / CSV parser (header auto-detect)
  • SQLite upsert (INSERT OR REPLACE)
  • Standard column normaliser
  • Logging setup

Australian Centre of English (AIC) – Market Intelligence Project
"""

import io
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

# ── Constants ─────────────────────────────────────────────────────────────────
CKAN_BASE    = "https://data.gov.au/data"
ABS_API_BASE = "https://api.data.abs.gov.au"
DEFAULT_TIMEOUT = 60      # seconds for API calls
DOWNLOAD_TIMEOUT = 300    # seconds for large file downloads
CHUNK_SIZE = 65536        # 64 KB chunks


# ── Logging ───────────────────────────────────────────────────────────────────
def get_logger(name: str = "AIC_ETL") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


log = get_logger()


# ══════════════════════════════════════════════════════════════════════════════
# CKAN API (data.gov.au)
# ══════════════════════════════════════════════════════════════════════════════

def ckan_resource_url(resource_id: str) -> dict:
    """
    Call CKAN resource_show and return the resource dict.
    Keys: url, name, format, last_modified, package_id, ...
    """
    url = f"{CKAN_BASE}/api/action/resource_show"
    resp = requests.get(url, params={"id": resource_id}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"CKAN error for {resource_id}: {data.get('error')}")
    return data["result"]


def ckan_package_resources(package_id: str) -> list[dict]:
    """Return list of all resources in a CKAN package."""
    url = f"{CKAN_BASE}/api/action/package_show"
    resp = requests.get(url, params={"id": package_id}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"CKAN package error {package_id}: {data.get('error')}")
    return data["result"]["resources"]


def ckan_find_resource(package_id: str, name_contains: str) -> Optional[dict]:
    """Find first resource in a package whose name contains the given string (case-insensitive)."""
    for r in ckan_package_resources(package_id):
        if name_contains.lower() in r.get("name", "").lower():
            return r
    return None


def ckan_search_packages(query: str, rows: int = 5) -> list[dict]:
    """Search data.gov.au for packages matching a query string."""
    url = f"{CKAN_BASE}/api/action/package_search"
    resp = requests.get(url, params={"q": query, "rows": rows}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {}).get("results", [])


# ══════════════════════════════════════════════════════════════════════════════
# FILE DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

def download_file(
    url: str,
    dest_dir: Path,
    filename: Optional[str] = None,
    force: bool = False,
    headers: Optional[dict] = None,
) -> Path:
    """
    Download a file to dest_dir.
    - Skips download if file already exists and force=False.
    - Streams in chunks for large files.
    Returns the local Path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = url.split("/")[-1].split("?")[0]
        if "." not in filename:
            filename = "download.xlsx"
    dest = dest_dir / filename

    if dest.exists() and not force:
        log.info(f"  ↳ Already exists, skipping: {dest.name}")
        return dest

    log.info(f"  ↳ Downloading {url}")
    h = {"User-Agent": "Mozilla/5.0 AIC-ETL/1.0", **(headers or {})}
    resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True, headers=h)
    resp.raise_for_status()

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            f.write(chunk)

    log.info(f"  ↳ Saved {dest.stat().st_size / 1024:.1f} KB → {dest.name}")
    return dest


# ══════════════════════════════════════════════════════════════════════════════
# ABS SDMX REST API
# ══════════════════════════════════════════════════════════════════════════════

def abs_get_data(
    flow_id: str,
    key: str = "all",
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
    detail: str = "full",
) -> pd.DataFrame:
    """
    Download data from ABS SDMX REST API as a CSV-labelled DataFrame.

    Args:
        flow_id:      ABS dataflow ID, e.g. "LF", "CPI", "NOM", "ERP_COB"
        key:          SDMX key string, e.g. "1.M.10+20.30.AUS" or "all"
        start_period: ISO period string, e.g. "2015-Q1" or "2015-01"
        end_period:   ISO period string
        detail:       "full" | "dataonly" | "serieskeysonly"

    Returns:
        DataFrame with labelled columns (using csvfilewithlabels format).
    """
    url = f"{ABS_API_BASE}/data/{flow_id}/{key}"
    params = {"format": "csvfilewithlabels", "detail": detail}
    if start_period:
        params["startPeriod"] = start_period
    if end_period:
        params["endPeriod"] = end_period

    log.info(f"  ↳ ABS API: {flow_id}/{key}  start={start_period}")
    resp = requests.get(url, params=params, timeout=DOWNLOAD_TIMEOUT)

    if resp.status_code == 404:
        raise ValueError(f"ABS dataflow not found: {flow_id}")
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    log.info(f"  ↳ ABS returned {len(df):,} rows, {len(df.columns)} cols")
    return df


def abs_list_dataflows(agency: str = "ABS") -> pd.DataFrame:
    """Return a DataFrame of available ABS dataflows (flow_id, name, description)."""
    url = f"{ABS_API_BASE}/dataflow/{agency}"
    resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers={"Accept": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for df_entry in data.get("data", {}).get("dataflows", []):
        rows.append({
            "id": df_entry.get("id"),
            "name": df_entry.get("name", {}).get("en", ""),
            "version": df_entry.get("version"),
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# FLEXIBLE EXCEL / CSV PARSER
# ══════════════════════════════════════════════════════════════════════════════

def norm_col(c: str) -> str:
    """Normalise column name to snake_case."""
    c = str(c).strip().lower()
    c = re.sub(r"[^a-z0-9]+", "_", c)
    return c.strip("_")


def read_excel_autoheader(
    path: Path,
    sheet: str | int = 0,
    header_hint: str = "",
    max_scan_rows: int = 20,
    skipfooter: int = 0,
) -> pd.DataFrame:
    """
    Read an Excel sheet, auto-detecting the header row.
    The header row is the first row where any cell contains header_hint (if given),
    or the first non-empty row.
    Returns DataFrame with normalised snake_case column names.
    """
    raw = pd.read_excel(path, sheet_name=sheet, header=None, skipfooter=skipfooter)

    # Find header row
    header_idx = 0
    if header_hint:
        for i, row in raw.head(max_scan_rows).iterrows():
            if row.astype(str).str.contains(header_hint, case=False, na=False).any():
                header_idx = i
                break
    else:
        for i, row in raw.head(max_scan_rows).iterrows():
            if row.notna().sum() > 2:
                header_idx = i
                break

    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
    df.dropna(how="all", inplace=True)
    df.dropna(axis=1, how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def read_rba_csv(path: Path) -> pd.DataFrame:
    """
    Parse RBA statistical table CSVs.

    Actual file structure (verified from f11-data.csv / f11.1-data.csv):
      Row 0:  "F11 EXCHANGE RATES"            ← single-field table name
      Row 1:  "Title, A$1=USD, TWI, ..."      ← 26 fields
      Row 2:  "Description, AUD/USD..., ..."
      Row 3:  "Frequency, Monthly, ..."
      Row 4:  "Type, Indicative, ..."
      Row 5:  "Units, USD, Index, ..."
      Row 6:  (blank)
      Row 7:  (blank)
      Row 8:  "Source, WM/Reuters, ..."
      Row 9:  "Publication date, ..."
      Row 10: "Series ID, FXRUSD, FXRTWI, ..." ← column IDs
      Row 11+: "29-Jan-2010, 0.8909, ..."       ← data rows

    CRITICAL: Row 0 has 1 field; rows 1+ have 26 fields.
    pd.read_csv(header=None) raises "Expected 1 fields in line 2, saw 26"
    because it fixes column count from the first row.

    Fix: use csv.reader which reads each row independently (variable-width).
    Returns long-format DataFrame: date, series_id, value, title, units, frequency.
    """
    import csv

    # ── Read ALL rows with csv.reader (handles variable column count) ──────────
    with open(path, newline="", encoding="utf-8-sig") as f:
        all_rows = list(csv.reader(f))

    # ── Scan for metadata labels in column 0 ──────────────────────────────────
    meta = {}
    series_id_row_idx = None

    for i, row in enumerate(all_rows[:20]):
        if not row:
            continue
        label = row[0].strip().lower()
        vals  = row[1:]   # everything after the label cell

        if label == "title":
            meta["title"] = vals
        elif label == "description":
            meta["description"] = vals
        elif label == "frequency":
            meta["frequency"] = vals
        elif label == "type":
            meta["type"] = vals
        elif label == "units":
            meta["units"] = vals
        elif label == "source":
            meta["source"] = vals
        elif label in ("series id", "series_id"):
            series_id_row_idx = i
            break

    if series_id_row_idx is None:
        first_labels = [r[0] if r else "" for r in all_rows[:8]]
        raise ValueError(
            f"Could not find 'Series ID' row in {path.name}. "
            f"First col-0 values: {first_labels}"
        )

    # ── Extract series IDs (strip empty trailing cells) ────────────────────────
    series_ids = [
        s.strip() for s in all_rows[series_id_row_idx][1:]
        if s.strip()
    ]
    n = len(series_ids)

    # ── Collect data rows (date parseable in col 0) ────────────────────────────
    data_rows = []
    for row in all_rows[series_id_row_idx + 1:]:
        if not row or not row[0].strip():
            continue
        date_str = row[0].strip()
        try:
            pd.to_datetime(date_str, dayfirst=True)
            # Pad or trim to exactly n+1 columns
            padded = (row + [""] * (n + 1))[: n + 1]
            data_rows.append(padded)
        except Exception:
            pass

    if not data_rows:
        raise ValueError(f"No data rows found in {path.name}")

    # ── Build DataFrame ────────────────────────────────────────────────────────
    data = pd.DataFrame(data_rows, columns=["date"] + series_ids)
    data["date"] = pd.to_datetime(data["date"], dayfirst=True, errors="coerce")
    data = data.dropna(subset=["date"])

    # ── Melt to long format ────────────────────────────────────────────────────
    long = data.melt(id_vars=["date"], var_name="series_id", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")

    # ── Annotate with metadata ─────────────────────────────────────────────────
    for meta_key, col_name in [("title", "title"), ("units", "units"), ("frequency", "frequency")]:
        raw_vals = meta.get(meta_key, [])
        clean    = [v.strip() for v in raw_vals if v.strip()][:n]
        if len(clean) == n:
            long[col_name] = long["series_id"].map(dict(zip(series_ids, clean)))
        else:
            long[col_name] = None

    return long


# ══════════════════════════════════════════════════════════════════════════════
# SQLITE UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def get_db(db_path: Path, schema_file: Optional[Path] = None) -> sqlite3.Connection:
    """Open (or create) a SQLite DB, optionally applying a schema SQL file."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if schema_file and schema_file.exists():
        conn.executescript(schema_file.read_text())
        conn.commit()
    return conn


def upsert_df(
    df: pd.DataFrame,
    table: str,
    conn: sqlite3.Connection,
    create_if_missing: bool = True,
    dry_run: bool = False,
) -> int:
    """
    Upsert a DataFrame into a SQLite table using INSERT OR REPLACE.
    If the table doesn't exist and create_if_missing=True, creates it from the DataFrame schema.
    Returns number of rows written.
    """
    if df.empty:
        log.warning(f"  ↳ Nothing to upsert into {table} (empty DataFrame)")
        return 0

    if dry_run:
        log.info(f"  [dry-run] Would upsert {len(df):,} rows → {table}")
        return len(df)

    if create_if_missing:
        df.head(0).to_sql(table, conn, if_exists="ignore", index=False)

    cols = list(df.columns)
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    sql = f'INSERT OR REPLACE INTO "{table}" ({col_list}) VALUES ({placeholders})'
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    conn.executemany(sql, rows)
    conn.commit()
    log.info(f"  ↳ Upserted {len(rows):,} rows → {table}")
    return len(rows)


def add_etl_meta(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Add standard ETL metadata columns to a DataFrame."""
    df = df.copy()
    df["_etl_source"] = source
    df["_etl_loaded_at"] = datetime.now(timezone.utc).isoformat()
    return df


# ══════════════════════════════════════════════════════════════════════════════
# RETRY WRAPPER
# ══════════════════════════════════════════════════════════════════════════════

def with_retry(fn, retries: int = 3, delay: float = 5.0, label: str = ""):
    """Call fn() up to retries times, sleeping delay seconds between attempts."""
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
