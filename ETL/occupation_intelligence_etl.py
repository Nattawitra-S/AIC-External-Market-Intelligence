"""
occupation_intelligence_etl.py
================================
Phase 2: Main ETL Pipeline — SkillSelect → SQLite
Australian Centre of English (AIC) - Market Intelligence Project

USAGE:
    python ETL/occupation_intelligence_etl.py \
        --ws-payload captures/ws_payload_20260701.json \
        --osl-file   raw_data/jobs_and_skills_australia/Occupation\ Shortage\ List\ -\ 6\ digit\ ANZSCO\ and\ OSCA.xlsx \
        --visa-list  raw_data/home_affairs/skilled_occupation_lists.xlsx \
        --db         data/aic_occupation_intelligence.db
"""

import argparse
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from ETL.skillselect_qlik_parser import parse_ws_payload

DEFAULT_DB  = Path(__file__).parent.parent / "data" / "aic_occupation_intelligence.db"
SCHEMA_FILE = Path(__file__).parent / "schema.sql"
LOG_DIR     = Path(__file__).parent.parent / "logs"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("AIC_ETL")


def init_database(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_FILE) as f:
        conn.executescript(f.read())
    conn.commit()
    logger.info(f"Database initialized: {db_path}")
    return conn


def extract_skillselect(ws_payload_path: Path, data_month: str) -> pd.DataFrame:
    # TODO: Update COLUMN_LABELS after running notebook Cell 3
    COLUMN_LABELS = [
        "anzsco_code", "occupation_name", "visa_subclass", "state",
        "ceiling", "invitations_issued", "fill_rate_pct", "trend",
    ]
    logger.info(f"Extracting SkillSelect: {ws_payload_path}")
    df = parse_ws_payload(ws_payload_path, COLUMN_LABELS, data_month)
    if df.empty:
        return df
    df["ceiling"]            = pd.to_numeric(df.get("ceiling"), errors="coerce").astype("Int64")
    df["invitations_issued"] = pd.to_numeric(df.get("invitations_issued"), errors="coerce").astype("Int64")
    df["fill_rate_pct"]      = pd.to_numeric(df.get("fill_rate_pct"), errors="coerce")
    before = len(df)
    df = df[df["anzsco_code"].str.match(r"^\d{6}$", na=False)]
    logger.info(f"  {before} → {len(df)} rows after quality filter")
    return df


def extract_osl(osl_file: Path | None) -> pd.DataFrame:
    if not osl_file or not osl_file.exists():
        logger.warning(f"OSL file not found: {osl_file}")
        return pd.DataFrame()
    logger.info(f"Extracting OSL: {osl_file}")
    df = pd.read_excel(osl_file, sheet_name=0, dtype=str)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    col_map = {"anzsco": "anzsco_code", "occupation": "occupation_name",
               "shortage": "shortage_status", "shortage_level": "shortage_level", "state": "state"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["osl_year"]     = 2025
    df["source"]       = "JSA OSL 2025"
    df["extracted_at"] = datetime.now().isoformat()
    logger.info(f"  OSL: {len(df)} rows")
    return df


def extract_visa_eligibility(visa_list_file: Path | None) -> pd.DataFrame:
    if not visa_list_file or not visa_list_file.exists():
        logger.warning(f"Visa list not found: {visa_list_file}")
        return pd.DataFrame()
    logger.info(f"Extracting visa eligibility: {visa_list_file}")
    dfs = []
    for lst in ["MLTSSL", "STSOL", "ROL"]:
        try:
            s = pd.read_excel(visa_list_file, sheet_name=lst, dtype=str)
            s["list_type"] = lst
            dfs.append(s)
        except: pass
    if not dfs:
        dfs = [pd.read_excel(visa_list_file, dtype=str)]
    df = pd.concat(dfs, ignore_index=True)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    col_map = {"anzsco": "anzsco_code", "occupation": "occupation_name",
               "assessing_authority": "assessing_body", "visa_subclasses": "visa_subclass"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["extracted_at"] = datetime.now().isoformat()
    df["source"]       = "Home Affairs SOL"
    logger.info(f"  Visa eligibility: {len(df)} rows")
    return df


def build_fact_table(df_ceilings, df_osl, df_visa, data_month) -> pd.DataFrame:
    if df_ceilings.empty:
        return pd.DataFrame()
    subclasses = ["189", "190", "491"]
    records = {}
    for _, row in df_ceilings.iterrows():
        code = row.get("anzsco_code", "")
        if not code: continue
        if code not in records:
            records[code] = {"anzsco_code": code, "occupation_name": row.get("occupation_name", "")}
        vs    = str(row.get("visa_subclass", "")).strip()
        state = str(row.get("state", "")).strip().upper()
        if vs in subclasses and state in ("NATIONAL", "ALL", "", "NAN"):
            records[code][f"ceiling_{vs}"]       = row.get("ceiling")
            records[code][f"invitations_{vs}"]    = row.get("invitations_issued")
            records[code][f"fill_rate_{vs}_pct"]  = row.get("fill_rate_pct")
            records[code][f"trend_{vs}"]          = row.get("trend")
    fact = pd.DataFrame(list(records.values()))
    if not df_osl.empty:
        osl_nat = df_osl[df_osl.get("state", pd.Series([""])).isin(["", None, "National"])][
            ["anzsco_code", "shortage_status", "shortage_level"]].drop_duplicates("anzsco_code")
        fact = fact.merge(osl_nat, on="anzsco_code", how="left")
        fact["shortage_national"] = fact.get("shortage_status", pd.Series([""])).str.contains("National", na=False).astype(int)
    else:
        fact["shortage_status"] = fact["shortage_level"] = None
        fact["shortage_national"] = 0
    if not df_visa.empty:
        for vs in subclasses:
            eligible = df_visa[df_visa.get("visa_subclass", pd.Series([""])).str.contains(vs, na=False)]["anzsco_code"].unique()
            fact[f"eligible_{vs}"] = fact["anzsco_code"].isin(eligible).astype(int)
        meta = df_visa[["anzsco_code", "list_type", "assessing_body"]].drop_duplicates("anzsco_code")
        fact = fact.merge(meta, on="anzsco_code", how="left")
    else:
        for vs in subclasses: fact[f"eligible_{vs}"] = 0
        fact["list_type"] = fact["assessing_body"] = None
    fact["median_salary_aud"] = None
    fact["data_month"]   = data_month
    fact["last_updated"] = datetime.now().isoformat()
    logger.info(f"Fact table: {len(fact)} occupations")
    return fact


def load_table(conn, df, table):
    if df.empty:
        return
    df.to_sql(f"_tmp_{table}", conn, if_exists="replace", index=False)
    cols = ", ".join(df.columns)
    conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM _tmp_{table}")
    conn.execute(f"DROP TABLE IF EXISTS _tmp_{table}")
    conn.commit()
    logger.info(f"  Loaded {len(df)} rows → {table}")


def run_etl(ws_payload_path, osl_file, visa_list_file, db_path, data_month):
    logger.info("=" * 60)
    logger.info(f"AIC SkillSelect ETL — {data_month}")
    logger.info("=" * 60)
    conn         = init_database(db_path)
    df_ceilings  = extract_skillselect(ws_payload_path, data_month) if ws_payload_path else pd.DataFrame()
    df_osl       = extract_osl(osl_file)
    df_visa      = extract_visa_eligibility(visa_list_file)
    df_fact      = build_fact_table(df_ceilings, df_osl, df_visa, data_month)
    if not df_ceilings.empty:
        df_ceilings["data_month"] = data_month
        df_ceilings["extracted_at"] = datetime.now().isoformat()
        load_table(conn, df_ceilings, "occupation_ceilings")
    if not df_osl.empty:  load_table(conn, df_osl,  "occupation_shortage_ratings")
    if not df_visa.empty: load_table(conn, df_visa, "visa_eligibility")
    if not df_fact.empty: load_table(conn, df_fact, "occupation_intelligence")
    conn.close()
    logger.info("✅ ETL complete — DB: %s", db_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws-payload", type=Path)
    ap.add_argument("--osl-file",   type=Path)
    ap.add_argument("--visa-list",  type=Path)
    ap.add_argument("--db",         type=Path, default=DEFAULT_DB)
    ap.add_argument("--month",      type=str,  default=datetime.now().strftime("%Y-%m"))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"etl_run_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"
    logging.getLogger().addHandler(logging.FileHandler(log_file))
    args = ap.parse_args()
    run_etl(args.ws_payload, args.osl_file, args.visa_list, args.db, args.month)
