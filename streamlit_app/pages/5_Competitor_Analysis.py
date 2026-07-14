"""Competitor Analysis -- CRICOS provider, location, and course landscape."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from modules import charts, common, formatting, queries, validation
from modules.theme import apply_page_config, inject_css

apply_page_config("Competitor Analysis")
inject_css()
common.render_sidebar_controls()

st.title("Competitor Analysis")
common.render_etl_freshness_banner()

try:
    provider_count = queries.get_provider_count()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

st.caption(
    "Based on CRICOS (Commonwealth Register of Institutions and Courses for "
    "Overseas Students) provider and course registrations -- not linked to "
    "student nationality, so this page describes the overall competitive "
    "landscape, not competition specific to any one source country."
)

col1, col2 = st.columns(2)
col1.metric("Registered CRICOS providers", formatting.fmt_number(provider_count))
locations = queries.get_locations_by_state()
col2.metric("Provider locations (campuses)", formatting.fmt_number(locations["location_count"].sum()) if not locations.empty else "N/A")

st.divider()

# ── Provider locations by state ─────────────────────────────────────────────
st.subheader("Provider locations by state")
providers_by_state = queries.get_providers_by_state()
if not validation.guard_empty(providers_by_state, "No provider-by-state data."):
    fig = charts.ranked_bar(providers_by_state, x="provider_count", y="state_code",
                            title="Registered providers by state")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(providers_by_state, "providers_by_state.csv")

if not validation.guard_empty(locations, "No location-by-state data."):
    fig = charts.ranked_bar(locations, x="location_count", y="state_code",
                            title="Campus/delivery locations by state")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(locations, "locations_by_state.csv")

st.divider()

# ── Course availability ──────────────────────────────────────────────────────
st.subheader("Course availability")
courses_by_state = queries.get_courses_by_state()
if not validation.guard_empty(courses_by_state, "No course-by-state data."):
    fig = charts.ranked_bar(courses_by_state, x="course_count", y="state_code",
                            title="Registered courses by state")
    st.plotly_chart(fig, width="stretch")
    common.download_csv_button(courses_by_state, "courses_by_state.csv")

st.markdown("**Top providers by course count**")
top_providers = queries.get_courses_by_provider(top_n=20)
if not validation.guard_empty(top_providers, "No provider course-count data."):
    st.dataframe(top_providers, width="stretch", hide_index=True)
    common.download_csv_button(top_providers, "top_providers_by_course_count.csv")

st.divider()

# ── Higher / lower competition ──────────────────────────────────────────────
st.subheader("Areas with higher and lower competition")
st.caption(
    "Competition here is defined transparently as provider density: states "
    "with more registered providers per course are treated as more "
    "competitive markets. This is a structural/registration-based measure, "
    "not a measure of student demand or market saturation relative to "
    "demand (no country-level demand-vs-supply linkage exists in this schema)."
)
if not validation.is_empty(providers_by_state) and not validation.is_empty(courses_by_state):
    merged = providers_by_state.merge(courses_by_state, on="state_code", how="outer").fillna(0)
    merged["courses_per_provider"] = merged.apply(
        lambda r: formatting.safe_ratio(r["course_count"], r["provider_count"]), axis=1
    )
    merged = merged.sort_values("provider_count", ascending=False)
    st.dataframe(
        merged.rename(columns={
            "state_code": "State", "provider_count": "Providers",
            "course_count": "Courses", "courses_per_provider": "Courses per provider",
        }),
        width="stretch", hide_index=True,
    )
    st.caption(
        f"**Highest competition (most providers):** {merged.iloc[0]['state_code']} "
        f"({formatting.fmt_number(merged.iloc[0]['provider_count'])} providers). "
        f"**Lowest competition:** {merged.iloc[-1]['state_code']} "
        f"({formatting.fmt_number(merged.iloc[-1]['provider_count'])} providers)."
    )
    common.download_csv_button(merged, "competition_by_state.csv")
else:
    st.info("**Not available:** insufficient provider/course data to rank competition.")

common.page_footer(["dim_provider", "dim_provider_location", "dim_course"])
