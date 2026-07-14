"""
db.py
======
MySQL connection layer for the AIC Market Intelligence Streamlit app.

Credentials are loaded ONLY from the project-root .env file (never
hardcoded, never displayed). Connection is cached as a SQLAlchemy Engine
for the lifetime of the Streamlit process; query results are cached
separately (see queries.py) with a short TTL so the app reflects newly
loaded ETL data without a full restart.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine

# Project root is two levels up from this file (streamlit_app/modules/db.py)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

REQUIRED_ENV_VARS = ["MYSQL_USER", "MYSQL_PASS", "MYSQL_DB"]


class MissingCredentialsError(RuntimeError):
    """Raised when required MySQL credentials are not present in .env."""


def _build_connection_url() -> str:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise MissingCredentialsError(
            f"Missing required .env variable(s): {', '.join(missing)}. "
            f"Copy .env.example to .env in the project root and fill in "
            f"MySQL credentials before running this app."
        )
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = os.environ.get("MYSQL_PORT", "3306")
    user = os.environ["MYSQL_USER"]
    password = os.environ["MYSQL_PASS"]
    db = os.environ["MYSQL_DB"]
    # mysql-connector-python driver via SQLAlchemy; password is never logged.
    return f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{db}?charset=utf8mb4"


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    """
    Return a cached SQLAlchemy Engine for the app's lifetime.

    st.cache_resource (not cache_data) is correct here: an Engine is a
    connection-pool object, not serializable data, and should be reused
    across reruns rather than rebuilt every time a widget changes.
    """
    url = _build_connection_url()
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """
    Execute a read-only SQL query and return a DataFrame.

    pool_pre_ping=True on the engine means a dropped connection is
    transparently reconnected on the next query rather than raising.

    Any parameter whose value is a list/tuple (used for an "IN :param"
    clause) is automatically marked as an expanding bind parameter --
    mysql-connector-python cannot bind a raw Python tuple/list directly,
    it must be expanded into individual placeholders by SQLAlchemy first.
    """
    engine = get_engine()
    params = params or {}
    stmt = text(sql)
    expanding = [k for k, v in params.items() if isinstance(v, (list, tuple, set))]
    if expanding:
        stmt = stmt.bindparams(*(bindparam(k, expanding=True) for k in expanding))
    with engine.connect() as conn:
        return pd.read_sql(stmt, conn, params=params)


def test_connection() -> tuple[bool, str]:
    """Lightweight connectivity check for a startup banner. Never raises."""
    try:
        df = run_query("SELECT 1 AS ok")
        if df.iloc[0]["ok"] == 1:
            return True, "Connected"
        return False, "Unexpected response from database"
    except MissingCredentialsError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 - surface any DB error to the UI banner
        return False, f"Database connection failed: {e}"
