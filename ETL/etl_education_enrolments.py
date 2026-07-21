# LEGACY — not imported or called by production. Kept for reference only.
# Production Education MySQL path is ETL.run_mysql_sources.run_mysql_education().
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime, UTC

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).parent.parent
RAW_DIR = BASE_DIR / "raw_data" / "File_extractor 2" / "output"

RAW_FILE = RAW_DIR / "Pivot_Basic_All_web_extracted_raw_split.xlsx"
DB_FILE = BASE_DIR / "student_visa.db"
TABLE_NAME = "education_enrolments"

EXPECTED_COLUMNS = [
    "year",
    "month",
    "nationality",
    "state",
    "sector",
    "new_to_australia",
    "ends_this_year",
    "data_ytd_enrolments",
    "data_ytd_commencements",
    "providertype",
    "total",
]

# ============================================================
# HELPERS
# ============================================================

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        str(c).strip().lower().replace(" ", "_")
        for c in df.columns
    ]
    return df


def add_metadata(df: pd.DataFrame, source_file: Path) -> pd.DataFrame:
    df["_etl_source_file"] = source_file.name
    df["_etl_loaded_at"] = datetime.now(UTC).isoformat()
    return df


def validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    print("[VALIDATION] Required columns found ✅")


def clean_types(df: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = [
        "year",
        "data_ytd_enrolments",
        "data_ytd_commencements",
        "total",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    text_cols = [
        "month",
        "nationality",
        "state",
        "sector",
        "new_to_australia",
        "ends_this_year",
        "providertype",
    ]

    for col in text_cols:
        df[col] = df[col].astype("string").str.strip()

    return df


# ============================================================
# EXTRACT + TRANSFORM
# ============================================================

def load_raw_excel(raw_file: Path) -> pd.DataFrame:
    if not raw_file.exists():
        raise FileNotFoundError(f"Raw file not found: {raw_file}")

    print("=" * 60)
    print("ETL START:", datetime.now(UTC).isoformat())
    print("=" * 60)

    print(f"[EXTRACT] Source file: {raw_file}")

    xls = pd.ExcelFile(raw_file)
    print(f"[EXTRACT] Sheets found: {xls.sheet_names}")

    frames = []

    for sheet in xls.sheet_names:
        print(f"[EXTRACT] Reading {sheet} ...")

        part = pd.read_excel(
            raw_file,
            sheet_name=sheet,
            dtype=str
        )

        print(f"[EXTRACT] Rows read: {len(part):,}")
        frames.append(part)

    df = pd.concat(frames, ignore_index=True)
    print(f"[TRANSFORM] Combined rows: {len(df):,}")

    df = clean_columns(df)
    validate_columns(df)
    df = clean_types(df)
    df = add_metadata(df, raw_file)

    print(f"[TRANSFORM] Final shape: {df.shape}")

    return df


# ============================================================
# LOAD
# ============================================================

def load_to_sqlite(df: pd.DataFrame, db_file: Path, table_name: str) -> None:
    print(f"[LOAD] Database: {db_file}")
    print(f"[LOAD] Table   : {table_name}")

    conn = sqlite3.connect(db_file)

    try:
        df.to_sql(
            table_name,
            conn,
            if_exists="replace",
            index=False
        )

        count = conn.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        print(f"[LOAD] Rows loaded: {count:,}")

        print("[LOAD] Creating indexes...")

        indexes = [
            ("idx_edu_year", "year"),
            ("idx_edu_month", "month"),
            ("idx_edu_nationality", "nationality"),
            ("idx_edu_state", "state"),
            ("idx_edu_sector", "sector"),
            ("idx_edu_provider_type", "providertype"),
        ]

        for idx_name, col in indexes:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON {table_name} ({col})"
            )

        conn.commit()

        print("[LOAD] Indexes created ✅")

    finally:
        conn.close()


def preview(db_file: Path, table_name: str) -> None:
    conn = sqlite3.connect(db_file)

    try:
        print("\n[PREVIEW] First 5 rows:")
        sample = pd.read_sql(
            f"SELECT * FROM {table_name} LIMIT 5",
            conn
        )
        print(sample.to_string())

        print("\n[VALIDATION] Row count by sector:")
        sector_counts = pd.read_sql(
            f"""
            SELECT sector, COUNT(*) AS rows
            FROM {table_name}
            GROUP BY sector
            ORDER BY rows DESC
            """,
            conn
        )
        print(sector_counts.to_string(index=False))

    finally:
        conn.close()


# ============================================================
# MAIN
# ============================================================

def run_etl():
    df = load_raw_excel(RAW_FILE)
    load_to_sqlite(df, DB_FILE, TABLE_NAME)
    preview(DB_FILE, TABLE_NAME)

    print("\n" + "=" * 60)
    print("ETL COMPLETE ✅")
    print(f"Database : {DB_FILE}")
    print(f"Table    : {TABLE_NAME}")
    print(f"Rows     : {len(df):,}")
    print("=" * 60)


if __name__ == "__main__":
    run_etl()
