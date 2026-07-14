"""Student Demand -- sector-level demand, trends, and country comparison."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from modules import charts, common, formatting, queries, validation
from modules.theme import apply_page_config, inject_css

apply_page_config("Student Demand")
inject_css()
common.render_sidebar_controls()

st.title("Student Demand")
common.render_etl_freshness_banner()

try:
    year, month = queries.get_latest_period()
    nationalities = queries.get_all_nationalities()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

# ── Course / study-area level -- NOT AVAILABLE ──────────────────────────────
st.subheader("Demand by sector and study area")
validation.unavailable_notice(
    "Study-area (course field-of-education) demand is not available: "
    "`dim_course.broad_field` and `dim_course.field_of_education` are 100% "
    "NULL in the current CRICOS data load, and CRICOS course records are "
    "not linked to student enrolment counts anywhere in the schema. "
    "Sector-level demand (Higher Education / VET / ELICOS / Schools / "
    "Non-award) is shown below as the available granularity."
)

sector_now = queries.get_sector_breakdown_at(year, month)
if not validation.guard_empty(sector_now, "No sector data at the latest snapshot."):
    fig = charts.ranked_bar(sector_now, x="total_enrolments", y="sector", orientation="h",
                            title=f"Sector demand -- {formatting.period_label(year, month)}")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(sector_now, "sector_demand_latest.csv")

st.divider()

# ── Historical sector trend ──────────────────────────────────────────────────
st.subheader("Sector demand trend over time")
sector_trend = queries.get_sector_yearly_trend()
if not validation.guard_empty(sector_trend, "No sector trend data available."):
    fig = charts.line_trend(sector_trend, x="enrol_year", y="total_enrolments", color="sector",
                             y_title="Total YTD enrolments")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(sector_trend, "sector_trend_yearly.csv")

st.divider()

# ── Increasing / declining sectors ──────────────────────────────────────────
st.subheader("Increasing and declining sectors")
st.caption(
    "Year-on-year % change per sector, comparing the latest reported year "
    "to the prior year at the same month. Reframed from 'study area' to "
    "'sector' because study-area-level demand data does not exist (see "
    "notice above)."
)
prior_sector = queries.get_sector_breakdown_at(year - 1, month) if month else None
if validation.is_empty(sector_now) or validation.is_empty(prior_sector):
    st.info("**Not available:** insufficient sector history to compute year-on-year change.")
else:
    merged = sector_now.merge(prior_sector, on="sector", how="outer", suffixes=("_current", "_prior")).fillna(0)
    merged["pct_change"] = merged.apply(
        lambda r: formatting.pct_change(r["total_enrolments_current"], r["total_enrolments_prior"]), axis=1
    )
    merged = merged.dropna(subset=["pct_change"]).sort_values("pct_change", ascending=False)
    fig = charts.growth_decline_bar(merged, "sector", "pct_change", "Sector YoY % change")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(merged, "sector_yoy_change.csv")

st.divider()

# ── Target-country comparison ────────────────────────────────────────────────
st.subheader("Target-country comparison")
default_countries = [c for c in ["China", "India", "Nepal"] if c in nationalities]
selected = st.multiselect("Select countries to compare", nationalities, default=default_countries)

if not selected:
    st.info("Select one or more countries above to compare their enrolment trends.")
else:
    trend_df = queries.get_country_yearly_trend(tuple(selected))
    if not validation.guard_empty(trend_df, "No trend data for the selected countries."):
        fig = charts.line_trend(trend_df, x="enrol_year", y="total_enrolments", color="nationality",
                                 y_title="Total YTD enrolments")
        st.plotly_chart(fig, width="stretch")
        common.download_csv_button(trend_df, "country_comparison_trend.csv")

    compare_sectors = queries.get_country_sector_comparison(tuple(selected), year, month)
    if not validation.guard_empty(compare_sectors, "No sector data for the selected countries."):
        st.markdown(f"**Sector split at {formatting.period_label(year, month)}**")
        fig = charts.stacked_bar(compare_sectors, x="nationality", y="total_enrolments", color="sector")
        st.plotly_chart(fig, width="stretch")
        common.download_csv_button(compare_sectors, "country_comparison_sectors.csv")

common.page_footer(["fact_student_enrolment"])
