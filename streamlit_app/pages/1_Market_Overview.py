"""Market Overview -- total enrolments, growth/decline, largest and fastest-moving countries."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from modules import charts, common, formatting, queries
from modules.theme import apply_page_config, inject_css

apply_page_config("Market Overview")
inject_css()
common.render_sidebar_controls()

st.title("Market Overview")
common.render_etl_freshness_banner()

try:
    year, month = queries.get_latest_period()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

prior_year = year - 1
is_partial_year = month < 12

st.markdown(
    f"**Latest available snapshot: {formatting.period_label(year, month)}** "
    + ("_(partial year -- only months up to this point have been reported)_"
       if is_partial_year else "")
)

# ── KPIs ─────────────────────────────────────────────────────────────────────
snapshot = queries.get_yearly_snapshot_totals()
growth_df = queries.get_country_growth(year, month, prior_year)

current_total = float(snapshot.loc[snapshot["enrol_year"] == year, "total_enrolments"].sum())
# NOTE: prior_total is computed via its own direct query, not by summing
# growth_df["previous_total"] -- that column only covers countries that
# ALSO have current-year data (it's built as current LEFT JOIN prior), so
# any country that had 2025 enrolments but zero 2026 enrolments would be
# silently excluded from that sum, undercounting the true prior-year total.
prior_total_same_month = float(
    queries.get_enrolment_by_country_at(prior_year, month)["total_enrolments"].sum()
)
yoy_pct = formatting.pct_change(current_total, prior_total_same_month)

col1, col2, col3 = st.columns(3)
col1.metric(
    f"Total enrolments ({formatting.period_label(year, month)})",
    formatting.fmt_number(current_total),
)
col2.metric(
    f"Same month, {prior_year}",
    formatting.fmt_number(prior_total_same_month),
)
col3.metric(
    "Year-on-year change",
    formatting.fmt_pct(yoy_pct, signed=True) if yoy_pct is not None else "N/A",
    delta=f"{yoy_pct:.1f}%" if yoy_pct is not None else None,
)
st.caption(
    "Enrolment figures are YTD-cumulative, so the year-on-year comparison above "
    f"uses the same month ({formatting.period_label(1, month).split('-')[1]}) in "
    "both years -- comparing different months within the year would not be a fair comparison."
)

st.divider()

# ── Long-run trend ───────────────────────────────────────────────────────────
st.subheader("Enrolment trend (latest reported month each year)")
if is_partial_year:
    st.caption(
        f"⚠️ {year} shows a partial-year figure (through month {month} only) and "
        f"is not directly comparable to the full-year totals for earlier years."
    )
fig = charts.line_trend(snapshot, x="enrol_year", y="total_enrolments",
                         title="", y_title="Total YTD enrolments")
st.plotly_chart(fig, width="stretch")
common.download_csv_button(snapshot, "market_overview_yearly_trend.csv")

st.divider()

# ── Largest source countries ─────────────────────────────────────────────────
st.subheader(f"Largest source countries -- {formatting.period_label(year, month)}")
top_countries = queries.get_enrolment_by_country_at(year, month).head(15)
fig = charts.ranked_bar(top_countries, x="total_enrolments", y="nationality",
                        title="")
st.plotly_chart(fig, width="stretch")
common.download_csv_button(top_countries, "largest_source_countries.csv")

st.divider()

# ── Fastest growing / declining ──────────────────────────────────────────────
st.subheader("Fastest-growing and fastest-declining countries")
MIN_VOLUME = 100
st.caption(
    f"Ranked by year-on-year % change, {formatting.period_label(1, month).split('-')[1]} "
    f"{prior_year} → {formatting.period_label(1, month).split('-')[1]} {year}. "
    f"Countries with fewer than {MIN_VOLUME} students in the base period are excluded "
    f"to avoid small-base percentage swings dominating the ranking."
)

growth_df = growth_df[growth_df["previous_total"] >= MIN_VOLUME].copy()
growth_df["pct_change"] = growth_df.apply(
    lambda r: formatting.pct_change(r["current_total"], r["previous_total"]), axis=1
)
growth_df = growth_df.dropna(subset=["pct_change"])

col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Fastest growing**")
    fastest_growing = growth_df.sort_values("pct_change", ascending=False).head(10)
    fig = charts.growth_decline_bar(fastest_growing, "nationality", "pct_change", "")
    st.plotly_chart(fig, width="stretch")
with col_b:
    st.markdown("**Fastest declining**")
    fastest_declining = growth_df.sort_values("pct_change", ascending=True).head(10)
    fig = charts.growth_decline_bar(fastest_declining, "nationality", "pct_change", "")
    st.plotly_chart(fig, width="stretch")

common.download_csv_button(growth_df, "country_growth_full.csv", "Download full growth/decline table as CSV")

common.page_footer(["fact_student_enrolment"])
