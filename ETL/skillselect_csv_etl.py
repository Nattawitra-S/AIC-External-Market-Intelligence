"""
skillselect_csv_etl.py
=======================
Reads SkillSelect exported CSVs → transforms to long-format → loads to SQLite.
Australian Centre of English (AIC) - Market Intelligence Project

INPUT:  raw_data/skillselect_exports/*.csv
OUTPUT: data/aic_occupation_intelligence.db → table: skillselect_eoi_data

SCHEMA (long-format / tidy data):
    as_at_month      TEXT  — "06/2026" (from wizard context OR filename)
    visa_type        TEXT  — "189" | "190" | "491" | "All"
    eoi_status       TEXT  — "Submitted" | "Active" | "All"
    source_view      TEXT  — e.g. "Occupations_Points"
    dimension_1_name TEXT  — e.g. "Occupations"
    dimension_1_val  TEXT  — e.g. "Software Engineer"
    dimension_2_name TEXT  — e.g. "Points"
    dimension_2_val  TEXT  — e.g. "65-69"
    eoi_count        INTEGER
    captured_at      TEXT  — ISO 8601 when ETL ran

USAGE:
    python ETL/skillselect_csv_etl.py
    python ETL/skillselect_csv_etl.py --exports raw_data/skillselect_exports --db data/aic.db
    python ETL/skillselect_csv_etl.py --dry-run      # preview without writing to DB
"""

import argparse
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
EXPORTS_DIR = BASE_DIR / "raw_data" / "skillselect_exports"
DB_PATH     = BASE_DIR / "data" / "aic_occupation_intelligence.db"
TABLE_NAME  = "skillselect_eoi_data"


# ─────────────────────────────────────────────────────────────────────────────
# FILENAME PARSER
# Extract context (month, visa, status, source_view) from filename convention:
#   {month_safe}_{visa}_{status}_{source_view}.csv
# e.g. "06_2026_189_Submitted_Occupations_Points.csv"
# ─────────────────────────────────────────────────────────────────────────────

def parse_filename_context(path: Path) -> dict:
    """
    Extract ETL context from a standardised export filename.
    Returns dict with keys: as_at_month, visa_type, eoi_status, source_view.
    Falls back to None values if parsing fails.
    """
    stem = path.stem  # e.g. "06_2026_189_Submitted_Occupations_Points"

    ctx = {
        "as_at_month": None,
        "visa_type":   None,
        "eoi_status":  None,
        "source_view": stem,  # default: full stem
    }

    # Pattern: two-digit month + underscore + 4-digit year at start
    m = re.match(r"^(\d{1,2})[_-](\d{4})[_-](.+)$", stem)
    if m:
        month_num, year, remainder = m.groups()
        ctx["as_at_month"] = f"{month_num.zfill(2)}/{year}"
        # remainder: "189_Submitted_Occupations_Points"
        parts = remainder.split("_", 2)
        if len(parts) >= 1:
            ctx["visa_type"] = parts[0]
        if len(parts) >= 2:
            ctx["eoi_status"] = parts[1]
        if len(parts) >= 3:
            ctx["source_view"] = parts[2]
    else:
        # Alternative: try splitting on _ and guessing
        parts = stem.split("_")
        # Look for visa type token
        for i, p in enumerate(parts):
            if p in ("189", "190", "491", "All"):
                ctx["visa_type"] = p
                # month might be before, status after
                if i > 0:
                    ctx["as_at_month"] = parts[i-1]
                if i + 1 < len(parts):
                    ctx["eoi_status"] = parts[i+1]
                if i + 2 < len(parts):
                    ctx["source_view"] = "_".join(parts[i+2:])
                break

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# CSV TRANSFORMER
# ─────────────────────────────────────────────────────────────────────────────

def infer_dimensions_from_source_view(source_view: str) -> tuple[str, str]:
    """
    Guess dimension names from source_view string.
    e.g. "Occupations_Points" → ("Occupations", "Points")
    """
    # Map known abbreviations to full names
    EXPANSIONS = {
        "English":          "English Test Score",
        "State":            "Nominated State",
        "AustralianStudy":  "Australian Study",
        "RegionalStudy":    "Regional Study",
        "ProfYear":         "Professional Year",
        "CommunityLang":    "Community Language",
        "Groups":           "Occupation Groups",
    }

    parts = source_view.split("_", 1)
    dim1 = EXPANSIONS.get(parts[0], parts[0]) if parts else "Unknown"
    dim2 = EXPANSIONS.get(parts[1], parts[1]) if len(parts) > 1 else "Unknown"
    return dim1, dim2


def transform_csv(path: Path, captured_at: str) -> pd.DataFrame | None:
    """
    Read one exported CSV and convert to long-format rows.

    SkillSelect exports typically come in one of two layouts:
        Layout A (cross-tab):
            Row header = dimension 1 values, column headers = dimension 2 values,
            cells = EOI counts.

        Layout B (already long):
            Columns: dim1_name, dim1_val, dim2_name, dim2_val, eoi_count
            (or similar naming)

    This function handles both.
    """
    ctx = parse_filename_context(path)
    print(f"  Reading: {path.name}")
    print(f"    Context → month={ctx['as_at_month']}, visa={ctx['visa_type']}, "
          f"status={ctx['eoi_status']}, view={ctx['source_view']}")

    try:
        raw = pd.read_csv(path, header=0, skiprows=0, encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = pd.read_csv(path, header=0, skiprows=0, encoding="latin-1")

    if raw.empty:
        print(f"    ⚠️  Empty CSV — skipping")
        return None

    # ── Check if already long-format ──────────────────────────────────────
    lower_cols = {c.lower().strip() for c in raw.columns}
    long_markers = {"eoi_count", "count", "eoi count", "number of eois", "dimension_1", "dim1"}
    if lower_cols & long_markers:
        # Already long or close to it — just rename/normalise columns
        col_map = {}
        for c in raw.columns:
            cl = c.lower().strip()
            if "eoi" in cl and ("count" in cl or "number" in cl):
                col_map[c] = "eoi_count"
            elif "dimension" in cl and "1" in cl and "name" in cl:
                col_map[c] = "dimension_1_name"
            elif "dimension" in cl and "1" in cl and ("val" in cl or "value" in cl):
                col_map[c] = "dimension_1_val"
            elif "dimension" in cl and "2" in cl and "name" in cl:
                col_map[c] = "dimension_2_name"
            elif "dimension" in cl and "2" in cl and ("val" in cl or "value" in cl):
                col_map[c] = "dimension_2_val"
        df = raw.rename(columns=col_map)
    else:
        # ── Assume cross-tab layout ──────────────────────────────────────
        # Row 0 might be a label row — detect
        first_col = raw.columns[0]
        dim1_name_default, dim2_name_default = infer_dimensions_from_source_view(
            ctx["source_view"] or ""
        )

        # The first column is dimension 1 values; other columns are dimension 2 values
        id_col   = first_col
        val_cols = [c for c in raw.columns if c != first_col]

        df = raw.melt(
            id_vars=id_col,
            value_vars=val_cols,
            var_name="dimension_2_val",
            value_name="eoi_count",
        ).rename(columns={id_col: "dimension_1_val"})

        df["dimension_1_name"] = dim1_name_default
        df["dimension_2_name"] = dim2_name_default

    # ── Inject context columns ─────────────────────────────────────────────
    df["as_at_month"] = ctx["as_at_month"]
    df["visa_type"]   = ctx["visa_type"]
    df["eoi_status"]  = ctx["eoi_status"]
    df["source_view"] = ctx["source_view"]
    df["captured_at"] = captured_at

    # ── Clean up ───────────────────────────────────────────────────────────
    # Ensure required columns exist
    for col in ["dimension_1_name", "dimension_1_val", "dimension_2_name", "dimension_2_val"]:
        if col not in df.columns:
            df[col] = None

    # Convert eoi_count to numeric
    df["eoi_count"] = pd.to_numeric(
        df["eoi_count"].astype(str).str.replace(",", "").str.strip(),
        errors="coerce"
    )

    # Drop rows where eoi_count is null or zero (often padding rows)
    df = df.dropna(subset=["eoi_count"])
    df = df[df["eoi_count"] > 0]

    # Strip whitespace from string columns
    str_cols = ["as_at_month", "visa_type", "eoi_status", "source_view",
                "dimension_1_name", "dimension_1_val", "dimension_2_name", "dimension_2_val"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # Select final columns
    final_cols = [
        "as_at_month", "visa_type", "eoi_status", "source_view",
        "dimension_1_name", "dimension_1_val",
        "dimension_2_name", "dimension_2_val",
        "eoi_count", "captured_at",
    ]
    df = df[[c for c in final_cols if c in df.columns]]

    print(f"    ✅ {len(df):,} rows after transform")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE LOADER
# ─────────────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    as_at_month      TEXT,
    visa_type        TEXT,
    eoi_status       TEXT,
    source_view      TEXT,
    dimension_1_name TEXT,
    dimension_1_val  TEXT,
    dimension_2_name TEXT,
    dimension_2_val  TEXT,
    eoi_count        INTEGER,
    captured_at      TEXT,
    UNIQUE(as_at_month, visa_type, eoi_status, source_view,
           dimension_1_val, dimension_2_val)
);
"""

INDEXES_SQL = [
    f"CREATE INDEX IF NOT EXISTS idx_ss_month     ON {TABLE_NAME}(as_at_month);",
    f"CREATE INDEX IF NOT EXISTS idx_ss_visa      ON {TABLE_NAME}(visa_type);",
    f"CREATE INDEX IF NOT EXISTS idx_ss_dim1      ON {TABLE_NAME}(dimension_1_name, dimension_1_val);",
    f"CREATE INDEX IF NOT EXISTS idx_ss_view      ON {TABLE_NAME}(source_view);",
]


def load_to_db(df: pd.DataFrame, db_path: Path, dry_run: bool = False):
    """Upsert a DataFrame into skillselect_eoi_data."""
    if dry_run:
        print(f"    [dry-run] Would write {len(df):,} rows to {TABLE_NAME}")
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_TABLE_SQL)
        for idx_sql in INDEXES_SQL:
            conn.execute(idx_sql)

        # Use INSERT OR REPLACE so re-running is idempotent
        cols     = list(df.columns)
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        sql = (
            f"INSERT OR REPLACE INTO {TABLE_NAME} ({col_list}) "
            f"VALUES ({placeholders})"
        )
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        conn.executemany(sql, rows)
        conn.commit()
        print(f"    ✅ Loaded {len(df):,} rows → {TABLE_NAME}")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY
# ─────────────────────────────────────────────────────────────────────────────

def verify(db_path: Path):
    """Print a quick summary of what's in skillselect_eoi_data."""
    if not db_path.exists():
        print("DB not found.")
        return

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()
        total = row[0]
        print(f"\n{'='*50}")
        print(f"DB VERIFY: {TABLE_NAME}")
        print(f"{'='*50}")
        print(f"  Total rows: {total:,}")

        months = conn.execute(
            f"SELECT as_at_month, COUNT(*) FROM {TABLE_NAME} GROUP BY as_at_month ORDER BY as_at_month DESC LIMIT 5"
        ).fetchall()
        print(f"  Months: {[m[0] for m in months]}")

        views = conn.execute(
            f"SELECT source_view, COUNT(*) as n FROM {TABLE_NAME} GROUP BY source_view ORDER BY n DESC"
        ).fetchall()
        print("  Source views:")
        for v, n in views:
            print(f"    {v}: {n:,} rows")

        top = conn.execute(
            f"""SELECT dimension_1_val, SUM(eoi_count) as total
                FROM {TABLE_NAME}
                WHERE source_view LIKE 'Occupations%'
                GROUP BY dimension_1_val
                ORDER BY total DESC LIMIT 5"""
        ).fetchall()
        if top:
            print("  Top 5 occupations by EOI count:")
            for occ, cnt in top:
                print(f"    {occ}: {cnt:,}")
    except sqlite3.OperationalError as e:
        print(f"  ⚠️  {e}")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_etl(exports_dir: Path, db_path: Path, dry_run: bool = False):
    captured_at = datetime.now(timezone.utc).isoformat()
    csv_files   = sorted(exports_dir.glob("*.csv"))

    if not csv_files:
        print(f"No CSVs found in {exports_dir}")
        return

    print(f"\n{'='*60}")
    print(f"SkillSelect CSV ETL — {len(csv_files)} file(s)")
    print(f"{'='*60}")

    all_frames = []
    for path in csv_files:
        df = transform_csv(path, captured_at)
        if df is not None and not df.empty:
            all_frames.append(df)

    if not all_frames:
        print("No data to load.")
        return

    combined = pd.concat(all_frames, ignore_index=True)
    print(f"\nTotal rows to load: {len(combined):,}")

    load_to_db(combined, db_path, dry_run=dry_run)

    if not dry_run:
        verify(db_path)

    return combined


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC SkillSelect CSV ETL")
    ap.add_argument("--exports", default=str(EXPORTS_DIR),
                    help="Folder containing exported CSVs")
    ap.add_argument("--db",      default=str(DB_PATH),
                    help="SQLite DB path")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse CSVs and print rows without writing to DB")
    args = ap.parse_args()

    run_etl(
        exports_dir=Path(args.exports),
        db_path=Path(args.db),
        dry_run=args.dry_run,
    )
