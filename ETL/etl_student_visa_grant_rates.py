
"""
ETL Pipeline: data.gov.au Student Visa Program
Dataset: BP0015 Student visas lodged report

HOW IT WORKS:
1. Extract  — ดึง direct download URL จาก CKAN API (resource_show)
2. Transform — clean, normalise column names, parse dates
3. Load      — บันทึกลง SQLite database

USAGE:
    python etl_student_visa.py
"""

import requests
import pandas as pd
import sqlite3
import os
import re
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CKAN_BASE = "https://data.gov.au/data"
RESOURCE_ID    = "b4775919-d0f5-4beb-8901-6384342774c6"
PACKAGE_ID     = "324aa4f7-46bb-4d56-bc2d-772333a2317e"
DB_PATH        = "student_visa.db"
TABLE_NAME     = "visa_grant_rates_long"
DOWNLOAD_DIR   = Path("raw_data")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# STEP 1: EXTRACT — get direct URL from CKAN API
# ─────────────────────────────────────────────
def get_resource_url(resource_id: str) -> str:
    """
    ใช้ CKAN resource_show API เพื่อดึง direct download URL
    ไม่ใช้ datastore_search เพราะ resource นี้เป็น XLSX ไม่ใช่ tabular DataStore
    """
    api_url = f"{CKAN_BASE}/api/action/resource_show"
    
    resp = requests.get(api_url, params={"id": resource_id}, timeout=30)
    resp.raise_for_status()
    
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"CKAN API error: {data.get('error')}")
    
    resource = data["result"]
    download_url = resource["url"]
    file_format  = resource.get("format", "UNKNOWN")
    
    print(f"[EXTRACT] Resource name : {resource.get('name')}")
    print(f"[EXTRACT] Format        : {file_format}")
    print(f"[EXTRACT] Download URL  : {download_url}")
    print(f"[EXTRACT] Last modified : {resource.get('last_modified')}")
    
    return download_url


def download_file(url: str, dest_dir: Path) -> Path:
    """Download file และบันทึกลง dest_dir"""
    filename = url.split("/")[-1].split("?")[0]
    if not filename.endswith((".xlsx", ".xls", ".csv")):
        filename = "bp0015_visa_lodged.xlsx"
    
    dest_path = dest_dir / filename
    
    if dest_path.exists():
        print(f"[EXTRACT] File already exists, skipping download: {dest_path}")
        return dest_path
    
    print(f"[EXTRACT] Downloading to {dest_path} ...")
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    
    size_kb = dest_path.stat().st_size / 1024
    print(f"[EXTRACT] Downloaded {size_kb:.1f} KB")
    return dest_path


# ─────────────────────────────────────────────
# STEP 2: TRANSFORM — clean and normalise
# ─────────────────────────────────────────────
def clean_column_name(col: str) -> str:
    """Normalise column names: lowercase, spaces→underscore, remove special chars"""
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = col.strip("_")
    return col

def load_and_transform(file_path: Path) -> pd.DataFrame:
    print(f"[TRANSFORM] Loading {file_path.name} ...")

    xl = pd.ExcelFile(file_path)
    print(f"[TRANSFORM] Sheets found: {xl.sheet_names}")

    target_sheet = "Grant Rate (Month)"
    df_raw = pd.read_excel(file_path, sheet_name=target_sheet, header=None)

    print(f"[TRANSFORM] Raw shape: {df_raw.shape}")

    header_idx = df_raw[
        df_raw.apply(
            lambda row: row.astype(str).str.contains(
                "Applicant Type",
                case=False,
                na=False
            ).any(),
            axis=1
        )
    ].index[0]

    headers = df_raw.iloc[header_idx].tolist()

    df = df_raw.iloc[header_idx + 1:].copy()
    df.columns = headers

    df.dropna(how="all", inplace=True)
    df.dropna(axis=1, how="all", inplace=True)

    df.rename(
        columns={
            "Applicant Type": "applicant_type",
            "Sector": "sector"
        },
        inplace=True
    )

    df["applicant_type"] = df["applicant_type"].ffill()

    df = df[df["sector"].notna()]

    df = df[
        ~df["applicant_type"]
        .astype(str)
        .str.contains("Total", na=False)
    ]

    df = df[
        ~df["sector"]
        .astype(str)
        .str.contains("Total", na=False)
    ]

    year_cols = [
        c for c in df.columns
        if re.match(r"^\d{4}-\d{2}", str(c))
    ]

    df = df.melt(
        id_vars=["applicant_type", "sector"],
        value_vars=year_cols,
        var_name="financial_year",
        value_name="lodged_count"
    )

    df["lodged_count"] = pd.to_numeric(
        df["lodged_count"],
        errors="coerce"
    )

    df["_etl_source_file"] = file_path.name
    df["_etl_loaded_at"] = datetime.utcnow().isoformat()
    df["_etl_resource_id"] = RESOURCE_ID

    print(f"[TRANSFORM] Clean shape: {df.shape}")

    return df

def load_to_sqlite(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """Load transformed DataFrame into SQLite, replacing existing table"""
    
    print(f"[LOAD] Writing {len(df)} rows → {db_path} :: {table_name}")
    
    conn = sqlite3.connect(db_path)
    try:
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        
        # Verify
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        cols  = [r[1] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
        
        print(f"[LOAD] ✅ Loaded {count} rows, {len(cols)} columns")
        print(f"[LOAD] Columns in DB: {cols}")
        
    finally:
        conn.close()


def preview_db(db_path: str, table_name: str, n: int = 5) -> None:
    """Quick preview of what's in the DB"""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {table_name} LIMIT {n}", conn)
    conn.close()
    print(f"\n[PREVIEW] First {n} rows of '{table_name}':")
    print(df.to_string())


# ─────────────────────────────────────────────
# MAIN — run full ETL
# ─────────────────────────────────────────────
def run_etl():
    print("=" * 60)
    print("ETL START:", datetime.utcnow().isoformat())
    print("=" * 60)
    
    try:
        # E: Extract
        download_url = get_resource_url(RESOURCE_ID)
        file_path    = download_file(download_url, DOWNLOAD_DIR)
        
        # T: Transform
        df = load_and_transform(file_path)
        
        # L: Load
        load_to_sqlite(df, DB_PATH, TABLE_NAME)
        
        # Preview
        preview_db(DB_PATH, TABLE_NAME)
        
        print("\n" + "=" * 60)
        print("ETL COMPLETE ✅")
        print(f"Database : {os.path.abspath(DB_PATH)}")
        print(f"Table    : {TABLE_NAME}")
        print(f"Rows     : {len(df)}")
        print("=" * 60)
        
    except requests.exceptions.HTTPError as e:
        print(f"\n[ERROR] HTTP {e.response.status_code}: {e}")
        print("→ Check that the resource_id and CKAN base URL are correct")
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    run_etl()
