"""
etl_abs.py
===========
ETL: Australian Bureau of Statistics — Macro & Labour Market Data

API:  ABS SDMX REST API (https://api.data.abs.gov.au)
      Format: CSV with labels (csvfilewithlabels)
      Fallback: local XLSX files in raw_data/abs/

Datasets:
  • Labour Force (LF) — employed/unemployed by sex, state, adjustment type
  • Employment by Industry (LF Table 4)
  • Employment by Occupation (LF Tables 7 & 12)
  • Consumer Price Index (CPI)
  • Net Overseas Migration (NOM)
  • Estimated Resident Population by Country of Birth (ERP_COB)
  • Education & Training Output (ANZSIC ABS 80 — Table 34)

ABS SDMX Flow IDs:
  LF      — Labour Force, Australia (monthly)
  CPI     — Consumer Price Index (quarterly)
  NOM     — Net Overseas Migration (annual)
  ERP_COB — ERP by Country of Birth (annual)

USAGE:
    python ETL/etl_abs.py
    python ETL/etl_abs.py --source api          # ABS API only
    python ETL/etl_abs.py --source local        # local XLSX only
    python ETL/etl_abs.py --datasets lf cpi
    python ETL/etl_abs.py --start-period 2015
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    abs_get_data, add_etl_meta, get_db, get_logger,
    norm_col, read_excel_autoheader, upsert_df,
)

log = get_logger("ETL_ABS")

BASE_DIR   = Path(__file__).parent.parent
RAW_DIR    = BASE_DIR / "raw_data" / "abs"
DB_PATH    = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA     = Path(__file__).parent / "schema.sql"

# ── ABS API configuration ─────────────────────────────────────────────────────
# Each entry: (flow_id, key, start_period_default, description)
ABS_FLOWS = {
    "lf": {
        "flow":       "LF",
        "key":        "all",
        "start":      "2015-01",
        "table":      "abs_labour_force",
        "desc":       "Labour Force Australia (monthly)",
        "local_glob": "Table 001. Labour force status*.xlsx",
    },
    "lf_industry": {
        "flow":       "LF",
        "key":        "4..AUS",          # Table 4 structure
        "start":      "2015-01",
        "table":      "abs_employment_by_industry",
        "desc":       "Employment by Industry (monthly)",
        "local_glob": "Table 04. Employed persons by Industry*.xlsx",
    },
    "lf_occupation": {
        "flow":       "LF",
        "key":        "7+12..AUS",
        "start":      "2015-01",
        "table":      "abs_employment_by_occupation",
        "desc":       "Employment by Occupation (monthly)",
        "local_glob": "Table 07. Employed persons by Occupation*.xlsx",
    },
    "cpi": {
        "flow":       "CPI",
        "key":        "all",
        "start":      "2015-Q1",
        "table":      "abs_cpi",
        "desc":       "Consumer Price Index (quarterly)",
        "local_glob": "TABLE 1. CPI All Groups*.xlsx",
    },
    "nom": {
        "flow":       "NOM",
        "key":        "all",
        "start":      "2015",
        "table":      "abs_net_overseas_migration",
        "desc":       "Net Overseas Migration",
        "local_glob": "Net overseas migration*.xlsx",
    },
    "erp_cob": {
        "flow":       "ERP_COB",
        "key":        "all",
        "start":      "2015",
        "table":      "abs_erp_country_of_birth",
        "desc":       "Estimated Resident Population by Country of Birth",
        "local_glob": "Estimated resident population by country of birth.xlsx",
    },
    "edu_output": {
        "flow":       None,   # No direct SDMX flow — local only
        "key":        None,
        "start":      None,
        "table":      "abs_education_output",
        "desc":       "Education & Training Output Index (Table 34)",
        "local_glob": "Table 34. Output of the Education*.xlsx",
    },
}


# ── ABS API TRANSFORM ─────────────────────────────────────────────────────────

def transform_abs_api_lf(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise ABS Labour Force API response."""
    df.columns = [norm_col(c) for c in df.columns]
    col_map = {
        "time_period": "period",
        "obs_value":   "value",
        "sex":         "sex",
        "adjustment_type": "adjustment_type",
        "region":      "state",
        "measure":     "measure",
        "unit_measure": "unit",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "series_id" not in df.columns and "series" in df.columns:
        df = df.rename(columns={"series": "series_id"})
    df["value"] = pd.to_numeric(df.get("value", pd.Series()), errors="coerce")
    return df.dropna(subset=["value"])


def transform_abs_api_cpi(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise ABS CPI API response."""
    df.columns = [norm_col(c) for c in df.columns]
    col_map = {
        "time_period": "period",
        "obs_value":   "value",
        "index_type":  "measure",
        "group":       "group_",
        "region":      "city",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "series_id" not in df.columns:
        df["series_id"] = "unknown"
    df["value"] = pd.to_numeric(df.get("value", pd.Series()), errors="coerce")
    return df.dropna(subset=["value"])


def transform_abs_api_generic(df: pd.DataFrame) -> pd.DataFrame:
    """Generic normalisation for any ABS API response."""
    df.columns = [norm_col(c) for c in df.columns]
    renames = {}
    for c in df.columns:
        if c in ("time_period", "time", "period_of_collection"):
            renames[c] = "period"
        elif c in ("obs_value", "observation_value", "value"):
            renames[c] = "value"
    df = df.rename(columns=renames)
    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
    return df


# ── LOCAL EXCEL PARSERS ───────────────────────────────────────────────────────

def _parse_abs_xlsx_generic(path: Path, table: str) -> pd.DataFrame:
    """
    Generic ABS Excel parser: detect header, melt date columns to long format.
    Handles ABS publication-style tables (Table 001, Table 04, etc.)
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Sheets: {xl.sheet_names[:5]}")
    frames = []

    # ABS tables usually have "Data" or numbered sheet(s)
    data_sheets = [s for s in xl.sheet_names if any(
        x in s.lower() for x in ["data", "table", "t0", "t1", "t2", "t3", "t4", "t5"]
    )] or xl.sheet_names

    for sheet in data_sheets[:5]:  # limit to first 5 relevant sheets
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            if raw.shape[0] < 5 or raw.shape[1] < 2:
                continue

            # ABS format: rows 1-8 are metadata, row 9+ is series ID / data
            # Find the first row that looks like it has dates
            data_start = None
            series_ids = None
            for i in range(min(20, len(raw))):
                row_vals = raw.iloc[i].dropna().tolist()
                # Series ID row: cell 0 is "Series ID" or the row is all-string codes
                if str(raw.iloc[i, 0]).strip().lower() in ("series id", "series_id"):
                    series_ids = raw.iloc[i].tolist()
                # Data rows start when first column parses as a date.
                # pd.to_datetime("nan", errors="raise") does NOT raise -- it
                # silently returns NaT -- so an empty/NaN cell in column A
                # must be excluded explicitly, or every sheet whose row 0
                # happens to be blank there mis-detects data_start=0.
                cell0 = raw.iloc[i, 0]
                if pd.isna(cell0):
                    continue
                try:
                    val = pd.to_datetime(str(cell0).strip(), errors="raise")
                    if pd.notna(val):
                        data_start = i
                        break
                except Exception:
                    pass

            if data_start is None:
                continue

            # Get headers from row above data_start or use series IDs
            if series_ids:
                headers = series_ids
            elif data_start > 0:
                headers = raw.iloc[data_start - 1].tolist()
            else:
                headers = [f"col_{j}" for j in range(raw.shape[1])]

            data = raw.iloc[data_start:].copy()
            data.columns = [str(h).strip() for h in headers]
            data = data.rename(columns={data.columns[0]: "period"})
            data["period"] = pd.to_datetime(data["period"], errors="coerce").dt.strftime("%Y-%m")
            data = data.dropna(subset=["period"])

            # Melt all series columns
            series_cols = [c for c in data.columns if c != "period"]
            long = data.melt(id_vars=["period"], value_vars=series_cols,
                             var_name="series_id", value_name="value")
            long["value"] = pd.to_numeric(long["value"], errors="coerce")
            long = long.dropna(subset=["value"])

            # Add metadata from top rows
            meta_rows = raw.iloc[:data_start]
            title_row = None
            unit_row  = None
            series_type_row = None
            for idx in range(len(meta_rows)):
                r = str(meta_rows.iloc[idx, 0]).lower()
                if "title" in r or "description" in r:
                    title_row = idx
                if "unit" in r:
                    unit_row  = idx
                if "series type" in r:
                    series_type_row = idx

            # Standard ABS layout has no explicit "Title" label in column A --
            # row 0 holds the series description directly (e.g. "Employed
            # total ;  Persons ;"), right above the "Unit" row. Fall back to
            # that position when no labeled title row was found.
            if title_row is None:
                title_row = 0

            if title_row is not None:
                title_map = dict(zip(raw.iloc[title_row].tolist()[1:], series_cols))
                long["title"] = long["series_id"].map({s: t for s, t in zip(series_cols, raw.iloc[title_row].tolist()[1:])})
            if unit_row is not None:
                long["unit"] = long["series_id"].map({s: u for s, u in zip(series_cols, raw.iloc[unit_row].tolist()[1:])})
            if series_type_row is not None:
                long["series_type"] = long["series_id"].map(
                    {s: t for s, t in zip(series_cols, raw.iloc[series_type_row].tolist()[1:])})

            long["_sheet"] = sheet
            frames.append(long)

        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def parse_local_lf(path: Path) -> pd.DataFrame:
    df = _parse_abs_xlsx_generic(path, "abs_labour_force")
    if df.empty:
        return df
    # LF table has adjustment type in a separate "Series Type" metadata row
    # (Trend | Seasonally Adjusted | Original), not embedded in the title text.
    if "series_type" in df.columns:
        df["adjustment_type"] = df["series_type"]
    else:
        df["adjustment_type"] = df.get("title", "").str.extract(r"(Trend|Seasonally adjusted|Original)", expand=False)
    df["sex"] = df.get("title", "").str.extract(r"(Persons|Males|Females)", expand=False)
    df["measure"] = df.get("title", "")
    df["state"] = "AUS"
    return df[["period", "measure", "sex", "adjustment_type", "state",
               "value", "unit", "series_id"] if all(c in df.columns for c in ["period", "value"]) else df.columns.tolist()]


def parse_local_cpi(path: Path) -> pd.DataFrame:
    df = _parse_abs_xlsx_generic(path, "abs_cpi")
    if df.empty:
        return df
    df["measure"] = "Index"
    df["group_"] = ""
    df["city"] = "Weighted Average of Eight Capital Cities"
    return df


_STATE_NAME_TO_CODE = {
    "new south wales": "NSW", "victoria": "VIC", "queensland": "QLD",
    "south australia": "SA", "western australia": "WA", "tasmania": "TAS",
    "northern territory": "NT", "australian capital territory": "ACT",
}


def _find_crosstab_header_row(raw: pd.DataFrame, max_rows: int = 20) -> int | None:
    """
    Find the header row of an ABS "SACC code / Country of birth x year"
    cross-tab sheet (a wide table of countries x years, one sheet per
    state) -- a different shape from the date-indexed time series that
    _parse_abs_xlsx_generic() handles.
    """
    for i in range(min(max_rows, len(raw))):
        row_str = " ".join(str(v) for v in raw.iloc[i].dropna().tolist()).lower()
        if "sacc code" in row_str and "country of birth" in row_str:
            return i
    return None


def _parse_country_year_crosstab(path: Path, sheet: str, state_code: str) -> pd.DataFrame:
    """Melt one ABS country-of-birth x year cross-tab sheet to long format."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    header_idx = _find_crosstab_header_row(raw)
    if header_idx is None:
        return pd.DataFrame()

    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [norm_col(h) for h in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")

    country_col = next((c for c in df.columns if "country" in c), None)
    if country_col is None:
        return pd.DataFrame()
    df = df.rename(columns={country_col: "country_name"})

    year_cols = [c for c in df.columns if re.match(r"^\d{4}", str(c))]
    if not year_cols:
        return pd.DataFrame()

    long = df.melt(id_vars=["country_name"], value_vars=year_cols,
                    var_name="period", value_name="value")
    # Column headers went through norm_col() (a column-NAME normaliser),
    # e.g. "2004-05" -> "2004_05" or numeric 1996.0 -> "1996_0". Recover a
    # clean "YYYY" or "YYYY-YY" period value.
    long["period"] = (
        long["period"].astype(str).str.extract(r"^(\d{4}(?:_\d{2})?)")[0]
        .str.replace("_", "-", regex=False)
    )
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["value", "country_name", "period"])
    long["state_territory"] = state_code
    return long


def parse_local_migration(path: Path) -> pd.DataFrame:
    """
    Parse ABS NOM XLSX: one country x financial-year cross-tab sheet per
    state (Table 1.2 = NSW, 1.3 = VIC, ... 1.9 = ACT). Table 1.1 is a
    notes-only sheet with no data table.
    """
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        if not sheet.lower().startswith("table 1.") or sheet == "Table 1.1":
            continue
        raw_head = pd.read_excel(path, sheet_name=sheet, header=None, nrows=15)
        title_text = " ".join(str(v) for v in raw_head.iloc[:15, 0].dropna().tolist()).lower()
        state_code = next(
            (code for name, code in _STATE_NAME_TO_CODE.items() if name in title_text),
            "AUS",
        )
        df = _parse_country_year_crosstab(path, sheet, state_code)
        if not df.empty:
            df["direction"] = "net"
            frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def parse_local_erp_cob(path: Path) -> pd.DataFrame:
    """
    Parse ABS ERP-by-country-of-birth XLSX. Table 1.1 has individual-country
    detail (Australia-wide, no state breakdown); Tables 1.2/1.3 are coarser
    minor/major-group aggregates of the same data and are skipped to avoid
    double-counting at a different grain.
    """
    df = _parse_country_year_crosstab(path, "Table 1.1", state_code="AUS")
    if df.empty:
        return df
    return df.rename(columns={"value": "population"})


def parse_local_edu_output(path: Path) -> pd.DataFrame:
    df = _parse_abs_xlsx_generic(path, "abs_education_output")
    if df.empty:
        return df
    df["industry_group"] = df.get("title", "")
    return df[["period", "series_id", "title", "industry_group", "value"]] if all(
        c in df.columns for c in ["period", "series_id", "value"]) else df


# ── LOCAL PARSERS MAP ─────────────────────────────────────────────────────────
LOCAL_PARSERS = {
    "lf":           parse_local_lf,
    "lf_industry":  lambda p: _parse_abs_xlsx_generic(p, "abs_employment_by_industry"),
    "lf_occupation":lambda p: _parse_abs_xlsx_generic(p, "abs_employment_by_occupation"),
    "cpi":          parse_local_cpi,
    "nom":          parse_local_migration,
    "erp_cob":      parse_local_erp_cob,
    "edu_output":   parse_local_edu_output,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def run(datasets: list[str] | None = None, source: str = "api",
        start_period: str | None = None, dry_run: bool = False,
        db_path: Path = DB_PATH):
    """
    Args:
        source: "api" (try ABS API first, fallback to local)
                "local" (local xlsx only)
                "both" (run both and combine)
    """
    conn = get_db(db_path, SCHEMA)
    total = 0
    keys = datasets if datasets else list(ABS_FLOWS.keys())

    for key in keys:
        cfg = ABS_FLOWS.get(key)
        if not cfg:
            log.warning(f"  Unknown dataset: {key}")
            continue

        log.info(f"\n{'─'*55}")
        log.info(f"[ABS] {cfg['desc']}")

        df = pd.DataFrame()

        # ── Try ABS SDMX API ──────────────────────────────────────────────
        if source in ("api", "both") and cfg.get("flow"):
            try:
                df_api = abs_get_data(
                    flow_id=cfg["flow"],
                    key=cfg.get("key", "all"),
                    start_period=start_period or cfg.get("start"),
                )
                # Apply appropriate transform
                if key.startswith("lf"):
                    df = transform_abs_api_lf(df_api)
                elif key == "cpi":
                    df = transform_abs_api_cpi(df_api)
                else:
                    df = transform_abs_api_generic(df_api)
                log.info(f"  ABS API: {len(df):,} rows")
            except Exception as e:
                log.warning(f"  ABS API failed: {e} — trying local file")

        # ── Fallback to local XLSX ─────────────────────────────────────────
        if df.empty and source in ("api", "local", "both"):
            matches = list(RAW_DIR.glob(cfg.get("local_glob", "")))
            if not matches:
                log.warning(f"  ⚠️  No local file matching: {cfg.get('local_glob')} — skipping")
                continue

            path = sorted(matches)[-1]
            log.info(f"  Local file: {path.name}")
            parser = LOCAL_PARSERS.get(key, lambda p: _parse_abs_xlsx_generic(p, cfg["table"]))
            try:
                df = parser(path)
                log.info(f"  Local parsed: {len(df):,} rows")
            except Exception as e:
                log.error(f"  ❌ Local parse failed: {e}")
                continue

            # Also check for additional related files (e.g. ABS has Table 04 AND Table 07 for occupation)
            if key == "lf_occupation":
                for extra_glob in ["Table 12. Employed persons by Occupation*.xlsx"]:
                    for extra_path in RAW_DIR.glob(extra_glob):
                        try:
                            df2 = _parse_abs_xlsx_generic(extra_path, cfg["table"])
                            df = pd.concat([df, df2], ignore_index=True)
                            log.info(f"  + {extra_path.name}: {len(df2):,} rows")
                        except Exception as e:
                            log.warning(f"  {extra_path.name}: {e}")

        if df.empty:
            log.warning(f"  ⚠️  No data — skipping {key}")
            continue

        df = add_etl_meta(df, f"abs/{key}")
        n = upsert_df(df, cfg["table"], conn, dry_run=dry_run)
        total += n

    log.info(f"\n✅ ABS ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: ABS Data")
    ap.add_argument("--datasets",     nargs="*", help=f"Datasets: {list(ABS_FLOWS.keys())}")
    ap.add_argument("--source",       default="api", choices=["api", "local", "both"])
    ap.add_argument("--start-period", default=None, help="e.g. '2015' or '2015-01'")
    ap.add_argument("--dry-run",      action="store_true")
    ap.add_argument("--db",           default=str(DB_PATH))
    args = ap.parse_args()
    run(datasets=args.datasets, source=args.source,
        start_period=args.start_period, dry_run=args.dry_run, db_path=Path(args.db))
