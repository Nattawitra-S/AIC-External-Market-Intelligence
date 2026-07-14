"""Visa Analysis -- every visa/migration measure that genuinely exists in the database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from modules import charts, common, queries, validation
from modules.theme import apply_page_config, inject_css

apply_page_config("Visa Analysis")
inject_css()
common.render_sidebar_controls()

st.title("Visa Analysis")
common.render_etl_freshness_banner()

st.warning(
    "**This page never shows visa refusal rates or processing times.** "
    "Neither measure exists anywhere in the database schema -- they are not "
    "collected by any of the 7 source pipelines. Only the measures listed "
    "below are genuinely present."
)

try:
    student_visa = queries.get_student_visa_activity()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

# ── Student visa activity (BP0015) ──────────────────────────────────────────
st.subheader("Student visa activity (lodged / granted / grant rate)")
st.caption(
    "National level only -- `fact_student_visa_activity` has no country/"
    "nationality column, so this cannot be broken down or filtered by country."
)
if not validation.guard_empty(student_visa, "No student visa activity data.", "fact_student_visa_activity"):
    measures = sorted(student_visa["measure"].unique())
    measure_choice = st.selectbox("Measure", measures, key="sva_measure")
    filtered = student_visa[student_visa["measure"] == measure_choice]
    sector_choice = st.multiselect(
        "Sector filter (optional)", sorted(filtered["sector"].unique()), key="sva_sector"
    )
    if sector_choice:
        filtered = filtered[filtered["sector"].isin(sector_choice)]
    trend = filtered.groupby("financial_year", as_index=False)["value"].sum()
    fig = charts.line_trend(trend, x="financial_year", y="value", y_title=measure_choice)
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(filtered, "student_visa_activity.csv")

st.divider()

# ── Skilled migration (national) ────────────────────────────────────────────
st.subheader("Skilled Migration Programme grants")
skilled_mig = queries.get_skilled_migration()
if not validation.guard_empty(skilled_mig, "No skilled migration data.", "fact_skilled_migration"):
    dims = st.multiselect(
        "Break down by", ["visa_subclass", "stream", "state_code", "measure"],
        default=["measure"], key="sm_dims",
    )
    group_cols = ["financial_year"] + dims if dims else ["financial_year"]
    trend = skilled_mig.groupby(group_cols, as_index=False)["value"].sum()
    if dims:
        color_col = dims[0]
        fig = charts.line_trend(trend, x="financial_year", y="value", color=color_col,
                                 y_title="Grants")
    else:
        fig = charts.line_trend(trend, x="financial_year", y="value", y_title="Grants")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(skilled_mig, "skilled_migration.csv")

st.divider()

# ── Skilled migration by country of birth ───────────────────────────────────
st.subheader("Skilled migration by country of birth")
countries = queries.get_skilled_migration_countries()
country_choice = st.selectbox("Country (optional filter)", ["All countries"] + countries, key="smc_country")
by_country = queries.get_skilled_migration_by_country(
    country=None if country_choice == "All countries" else country_choice
)
if not validation.guard_empty(by_country, "No records for this selection."):
    trend = by_country.groupby("financial_year", as_index=False)["value"].sum()
    fig = charts.line_trend(trend, x="financial_year", y="value", y_title="Grants")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(by_country, "skilled_migration_by_country.csv")

st.divider()

# ── Empty tables -- clearly labeled, not fabricated ─────────────────────────
st.subheader("Not currently available")
for table in ["fact_temp_skilled_visa", "fact_temp_graduate_visa", "fact_permanent_migration"]:
    row_count = queries.get_table_row_count(table)
    if row_count == 0:
        validation.notice_if_known_empty_table(table)

common.page_footer([
    "fact_student_visa_activity", "fact_skilled_migration",
    "ref_skilled_migration_by_cob_occupation",
])
