"""Country Analysis -- deep dive on one selected country."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from modules import charts, common, formatting, queries, scoring, validation
from modules.theme import apply_page_config, inject_css

apply_page_config("Country Analysis")
inject_css()
common.render_sidebar_controls()

st.title("Country Analysis")
common.render_etl_freshness_banner()

try:
    nationalities = queries.get_all_nationalities()
    year, month = queries.get_latest_period()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

default_idx = nationalities.index("China") if "China" in nationalities else 0
country = st.selectbox("Select a country", nationalities, index=default_idx)

st.caption(
    f"Country list ({len(nationalities)} countries) is drawn directly from "
    f"`fact_student_enrolment.nationality` -- full coverage, not limited to "
    f"the small curated `dim_country` reference list."
)

# ── Historical trend + YoY growth ───────────────────────────────────────────
st.subheader(f"{country} -- enrolment trend")
trend = queries.get_yearly_snapshot_totals(nationality=country)
if validation.guard_empty(trend, f"No enrolment history found for {country}."):
    st.stop()

fig = charts.line_trend(trend, x="enrol_year", y="total_enrolments", y_title="Total YTD enrolments")
st.plotly_chart(fig, width="stretch")
common.download_csv_button(trend, f"{country}_enrolment_trend.csv")

growth_df = queries.get_country_growth(year, month, year - 1)
row = growth_df[growth_df["nationality"] == country]
current_vol = float(row["current_total"].iloc[0]) if not row.empty else None
prior_vol = float(row["previous_total"].iloc[0]) if not row.empty else None
growth_pct = formatting.pct_change(current_vol, prior_vol)

col1, col2, col3 = st.columns(3)
col1.metric(f"Current ({formatting.period_label(year, month)})", formatting.fmt_number(current_vol))
col2.metric(f"Same month, {year - 1}", formatting.fmt_number(prior_vol))
col3.metric("Year-on-year change", formatting.fmt_pct(growth_pct, signed=True) if growth_pct is not None else "N/A")

st.divider()

# ── Sector demand ────────────────────────────────────────────────────────────
st.subheader("Sector demand")
sector_df = queries.get_sector_breakdown_at(year, month, nationality=country)
if not validation.guard_empty(sector_df, f"No sector breakdown available for {country} at this snapshot."):
    fig = charts.ranked_bar(sector_df, x="total_enrolments", y="sector", orientation="h")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(sector_df, f"{country}_sector_demand.csv")

st.divider()

# ── Course / study-area demand -- NOT AVAILABLE ─────────────────────────────
st.subheader("Course / study-area demand")
validation.unavailable_notice(
    "CRICOS course records (`dim_course`) have no field-of-education or "
    "broad-field classification populated in the current data load "
    "(`broad_field` and `field_of_education` are 100% NULL), and CRICOS "
    "course data is not linked to student nationality anywhere in the "
    "schema. Course/study-area demand cannot be shown per country. "
    "The sector demand chart above is the closest genuinely available proxy."
)

st.divider()

# ── Visa indicators ──────────────────────────────────────────────────────────
st.subheader("Visa indicators")
st.caption(
    "Student visa lodged/granted data (`fact_student_visa_activity`) has "
    "**no country or nationality column at all** -- it is only available "
    "at the national level (see the Visa Analysis page). The only "
    "genuinely country-linked visa data is Skilled Migration Programme "
    "grants by country of birth, shown below."
)
skilled_mig = queries.get_skilled_migration_by_country(country=country)
if validation.guard_empty(
    skilled_mig, f"No skilled migration grant records found for {country}."
):
    pass
else:
    yearly = skilled_mig.groupby("financial_year", as_index=False)["value"].sum()
    fig = charts.line_trend(yearly, x="financial_year", y="value",
                             y_title="Skilled migration grants")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(skilled_mig, f"{country}_skilled_migration.csv")

st.divider()

# ── Rule-based marketing recommendation ─────────────────────────────────────
st.subheader("Marketing recommendation")
median_volume = float(growth_df["current_total"].median()) if not growth_df.empty else None
recommendation = scoring.marketing_recommendation(growth_pct, current_vol, median_volume)
st.info(recommendation)
st.caption(
    f"Deterministic rule, not a prediction: strong growth threshold "
    f"= +{scoring.GROWTH_THRESHOLD_STRONG:.0f}% YoY, decline threshold = "
    f"{scoring.GROWTH_THRESHOLD_DECLINE:.0f}% YoY, market-size comparison "
    f"is against the median country's current enrolment volume "
    f"({formatting.fmt_number(median_volume)}). "
    f"See `modules/scoring.py::marketing_recommendation` for the exact rule."
)

common.page_footer([
    "fact_student_enrolment", "ref_skilled_migration_by_cob_occupation",
])
