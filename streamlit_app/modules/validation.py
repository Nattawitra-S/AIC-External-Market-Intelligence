"""
validation.py
==============
Guards for empty tables and unavailable measures. The project's core rule
(from the migration report and the Tableau data dictionary) is: several
tables are structurally complete but currently EMPTY (fact_temp_skilled_visa,
fact_temp_graduate_visa, fact_permanent_migration, dim_occupation,
stg_skillselect_eoi), and several columns exist but are 100% NULL
(dim_course.broad_field, dim_course.field_of_education). This module gives
every page one consistent way to detect that and render an honest
"not available" state instead of a blank or fabricated chart.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Tables known (as of the 2026-07-14 migration) to be structurally valid but
# empty. Kept as a named constant so pages can explain *why*, not just *that*,
# data is missing.
KNOWN_EMPTY_TABLES = {
    "fact_temp_skilled_visa": "Home Affairs BP0014 source file is an Excel "
        "PivotTable export with no flat header row -- not yet parseable.",
    "fact_temp_graduate_visa": "Home Affairs BP0016 source file is an Excel "
        "PivotTable export with no flat header row -- not yet parseable.",
    "fact_permanent_migration": "Home Affairs BP0068 source file is an Excel "
        "PivotTable export with no flat header row -- not yet parseable.",
    "dim_occupation": "No ETL step currently populates this dimension "
        "(unlike dim_country, there is no small curated source file to "
        "derive it from).",
    "stg_skillselect_eoi": "SkillSelect capture is a separate staging "
        "pipeline not yet considered stable; intentionally not promoted.",
}

# Columns known to exist but be entirely NULL in the current data load.
KNOWN_NULL_COLUMNS = {
    ("dim_course", "broad_field"): "CRICOS course exports do not populate "
        "a field-of-education category in this data load.",
    ("dim_course", "field_of_education"): "CRICOS course exports do not "
        "populate a field-of-education category in this data load.",
}


def is_empty(df: pd.DataFrame | None) -> bool:
    return df is None or df.empty


def notice_if_known_empty_table(table_name: str) -> None:
    """Render a clear st.info() explaining a known-empty table, if applicable."""
    if table_name in KNOWN_EMPTY_TABLES:
        st.info(
            f"**Not currently available:** `{table_name}` has no rows yet. "
            f"{KNOWN_EMPTY_TABLES[table_name]}"
        )


def guard_empty(df: pd.DataFrame, message: str, table_name: str | None = None) -> bool:
    """
    If df is empty, render an honest notice and return True (caller should
    stop rendering that section). Otherwise returns False.
    """
    if is_empty(df):
        if table_name and table_name in KNOWN_EMPTY_TABLES:
            notice_if_known_empty_table(table_name)
        else:
            st.info(f"**Not available:** {message}")
        return True
    return False


def unavailable_notice(message: str) -> None:
    """Standard 'clearly labeled unavailable' banner for indicators that do
    not exist in the schema at all (not just empty right now)."""
    st.info(f"**Not available from current data:** {message}")
