"""Market Drivers -- enrolment trend alongside macro indicators. Association, not causation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from modules import charts, common, queries, validation
from modules.theme import apply_page_config, inject_css

apply_page_config("Market Drivers")
inject_css()
common.render_sidebar_controls()

st.title("Market Drivers")
common.render_etl_freshness_banner()

st.error(
    "**These charts show association, not causation.** A shared trend "
    "between enrolments and an economic indicator does not prove the "
    "indicator *caused* the enrolment change -- treat every comparison "
    "below as a possible driver worth investigating, never a proven one."
)

try:
    enrolment = queries.get_yearly_snapshot_totals()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

enrolment = enrolment[["enrol_year", "total_enrolments"]].rename(columns={"enrol_year": "year"})
enrolment["year"] = enrolment["year"].astype(str)

INDICATORS = {
    "Exchange rate (AUD/USD)": ("get_yearly_exchange_rate", "avg_rate", {}),
    "CPI (All Groups, Australia)": ("get_yearly_cpi", "avg_cpi", {}),
    "Net overseas migration": ("get_yearly_overseas_migration", "total_value", {}),
    "Estimated resident population": ("get_yearly_population_by_cob", "total_population", {}),
}

labour_measures = queries.get_labour_force_measures()
if labour_measures:
    INDICATORS["Labour force"] = ("get_yearly_labour_force", "avg_value", {"measure": labour_measures[0]})

indicator_name = st.selectbox("Compare enrolments against", list(INDICATORS.keys()))
fn_name, value_col, extra_kwargs = INDICATORS[indicator_name]

if indicator_name == "Labour force":
    measure_choice = st.selectbox("Labour force measure", labour_measures,
                                   format_func=lambda m: m.replace(";", "").strip())
    extra_kwargs = {"measure": measure_choice}

fn = getattr(queries, fn_name)
indicator_df = fn(**extra_kwargs)

if validation.guard_empty(indicator_df, f"No data available for {indicator_name}."):
    st.stop()

indicator_df = indicator_df.rename(columns={indicator_df.columns[0]: "year"})
indicator_df["year"] = indicator_df["year"].astype(str)

merged = enrolment.merge(indicator_df, on="year", how="outer").sort_values("year")

# ── Coverage gap disclosure ──────────────────────────────────────────────────
enrol_years = set(enrolment["year"])
indicator_years = set(indicator_df["year"])
missing_for_indicator = sorted(enrol_years - indicator_years)
missing_for_enrolment = sorted(indicator_years - enrol_years)

if missing_for_indicator or missing_for_enrolment:
    msg = []
    if missing_for_indicator:
        msg.append(f"{indicator_name} has no data for enrolment year(s): {', '.join(missing_for_indicator)}")
    if missing_for_enrolment:
        msg.append(f"Enrolment data has no figure for {indicator_name} year(s): {', '.join(missing_for_enrolment)}")
    st.info("**Coverage note:** " + "; ".join(msg) + ". These years are shown as gaps below, not interpolated.")

st.subheader(f"Enrolments vs. {indicator_name}")
fig = charts.dual_axis_trend(
    merged, x="year", y1="total_enrolments", y2=value_col,
    y1_name="Total YTD enrolments", y2_name=indicator_name,
)
st.plotly_chart(fig, width="stretch")

# Correlation, explicitly labeled
paired = merged.dropna(subset=["total_enrolments", value_col])
if len(paired) >= 3:
    corr = paired["total_enrolments"].corr(paired[value_col])
    st.caption(
        f"**Correlation coefficient (association only): {corr:.2f}** "
        f"(computed on {len(paired)} overlapping years). "
        f"This measures how closely the two series move together historically -- "
        f"it is not evidence that one causes the other, and is not a forecast."
    )
else:
    st.caption("Not enough overlapping years to compute a meaningful correlation.")

common.download_csv_button(merged, f"market_drivers_{indicator_name.replace(' ', '_')}.csv")

common.page_footer([
    "fact_student_enrolment", "fact_exchange_rate", "fact_cpi",
    "fact_overseas_migration", "fact_population_by_cob", "fact_labour_force",
])
