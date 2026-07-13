import pandas as pd
import sqlite3
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

RAW_FILE = "/Users/nattawitrasaengcha/Documents/Placement/File_extractor 2/output/Pivot_Basic_All_web (YTD Feb 2026)_extracted_raw_split.xlsx"

DB_FILE = "/Users/nattawitrasaengcha/Documents/Gov_ETL_data /student_visa.db"

TABLE_NAME = "education_enrolments"

# ============================================================
# EXTRACT
# ============================================================

print("=" * 60)
print("LOADING RAW EDUCATION DATA")
print("=" * 60)

xls = pd.ExcelFile(RAW_FILE)

print("Sheets found:")
print(xls.sheet_names)

frames = []

for sheet in xls.sheet_names:

    print(f"\nReading {sheet} ...")

    df = pd.read_excel(
        RAW_FILE,
        sheet_name=sheet
    )

    print(f"Rows: {len(df):,}")

    frames.append(df)

# ============================================================
# TRANSFORM
# ============================================================

df = pd.concat(frames, ignore_index=True)

print("\nCombined rows:", f"{len(df):,}")

df.columns = [
    c.strip().lower()
    for c in df.columns
]

# ============================================================
# LOAD
# ============================================================

print("\nConnecting SQLite...")

conn = sqlite3.connect(DB_FILE)

df.to_sql(
    TABLE_NAME,
    conn,
    if_exists="replace",
    index=False
)

conn.close()

print("\n" + "=" * 60)
print("ETL COMPLETE")
print("Database :", DB_FILE)
print("Table    :", TABLE_NAME)
print("Rows     :", len(df))
print("=" * 60)
