"""
common.py
==========
Shared page boilerplate: sidebar refresh control, ETL-freshness banner,
CSV download helper. Used by every page so behaviour is identical everywhere.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import queries


def render_sidebar_controls() -> None:
    with st.sidebar:
        st.markdown("### Data Controls")
        if st.button("Refresh Data", width="stretch", type="primary"):
            st.cache_data.clear()
            st.rerun()
        st.caption(
            "Query results are cached for 15 minutes. Click Refresh Data to "
            "clear the cache and re-read the latest rows from MySQL immediately."
        )
        st.divider()
        st.markdown("### About this data")
        st.caption(
            "Source data is government-published on a monthly, quarterly, or "
            "annual cadence depending on dataset (RBA monthly, ABS CPI "
            "quarterly, Dept. of Education monthly YTD, etc.). This is **not** "
            "a real-time feed -- it reflects the latest successfully completed "
            "ETL load, shown below."
        )


def render_etl_freshness_banner() -> None:
    try:
        df = queries.get_last_etl_updates()
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not reach the database: {e}")
        st.stop()

    if df.empty:
        st.warning("No ETL audit history found in `etl_audit_log`.")
        return

    latest = pd.to_datetime(df["last_completed"]).max()
    st.markdown(
        f'<div class="aic-banner">Database last updated by ETL: '
        f'<b>{latest.strftime("%Y-%m-%d %H:%M")}</b> &nbsp;|&nbsp; '
        f'{len(df)} source tables tracked &nbsp;|&nbsp; '
        f'Not real-time -- reflects the latest completed ETL load, at each '
        f'source\'s own publication frequency.</div>',
        unsafe_allow_html=True,
    )


def download_csv_button(df: pd.DataFrame, filename: str, label: str = "Download data as CSV") -> None:
    if df is None or df.empty:
        return
    st.download_button(
        label=label,
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
    )


def page_footer(data_sources: list[str]) -> None:
    st.divider()
    st.caption(f"**Data sources used on this page:** {', '.join(data_sources)}")
    st.caption(
        "AIC External Market Intelligence Database -- MySQL 8 production source. "
        "See docs/tableau_data_dictionary.md and docs/final_migration_summary.md "
        "in the project repository for full data lineage and known limitations."
    )
