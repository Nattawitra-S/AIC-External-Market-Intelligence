"""Opportunity Score -- transparent, rule-based country ranking. Not a prediction."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from modules import charts, common, formatting, queries, scoring, validation
from modules.theme import apply_page_config, inject_css

apply_page_config("Opportunity Score")
inject_css()
common.render_sidebar_controls()

st.title("Opportunity Score")
common.render_etl_freshness_banner()

st.error(
    "**This is not a prediction or forecast.** The Opportunity Score is a "
    "transparent, deterministic ranking built from current and historical "
    "data only, using the fixed rules documented below and in "
    "`modules/scoring.py`. It describes relative standing today, not future "
    "performance."
)

try:
    inputs = queries.get_opportunity_score_inputs()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the database: {e}")
    st.stop()

if validation.guard_empty(inputs, "No data available to compute opportunity scores."):
    st.stop()

scored = scoring.compute_opportunity_scores(inputs)

with st.expander("How this score is calculated (read before using this page)", expanded=True):
    st.markdown(
        f"""
Each component below is converted to a **0-100 percentile rank** across all
countries with that data (e.g. a growth percentile of 90 means this
country's YoY growth is higher than 90% of other countries -- not that
growth is "90%").

| Component | Target weight | Source |
|---|---|---|
| Student growth (YoY %) | {scoring.DEFAULT_WEIGHTS['growth']:.0%} | `fact_student_enrolment` |
| Market size (current volume) | {scoring.DEFAULT_WEIGHTS['market_size']:.0%} | `fact_student_enrolment` |
| Visa pathway (skilled migration grant volume) | {scoring.DEFAULT_WEIGHTS['visa_pathway']:.0%} | `ref_skilled_migration_by_cob_occupation` |
| Competition | 0% (always excluded) | — |

{scoring.COMPETITION_NOTE}

**When a country is missing a component** (most often visa pathway data),
that weight is redistributed proportionally across the components that
*are* available for that country -- never dropped silently, never treated
as zero. The **Data confidence %** column shows how many of the three
weighted components were actually available for each country.
        """
    )

# ── Ranking table ────────────────────────────────────────────────────────────
st.subheader("Country ranking")
top_n = st.slider("Show top N countries", min_value=10, max_value=len(scored), value=min(25, len(scored)))
display_df = scored.head(top_n).copy()
display_df["weights_used"] = display_df["weights_used"].apply(
    lambda d: ", ".join(f"{k}={v:.0%}" for k, v in d.items()) if d else "N/A"
)
st.dataframe(
    display_df.rename(columns={
        "nationality": "Country", "current_volume": "Current enrolments",
        "growth_pct": "YoY growth %", "visa_volume": "Skilled migration grants",
        "opportunity_score": "Opportunity score", "data_confidence_pct": "Data confidence %",
        "weights_used": "Weights actually applied",
    })[[
        "Country", "Current enrolments", "YoY growth %", "Skilled migration grants",
        "Opportunity score", "Data confidence %", "Weights actually applied",
    ]],
    width="stretch", hide_index=True,
)
common.download_csv_button(scored, "opportunity_scores_full.csv", "Download full ranking as CSV")

st.divider()

fig = charts.ranked_bar(
    display_df.sort_values("opportunity_score"), x="opportunity_score", y="nationality",
    title=f"Top {top_n} countries by Opportunity Score",
)
st.plotly_chart(fig, width="stretch")

st.divider()

# ── Per-country inspection ──────────────────────────────────────────────────
st.subheader("Inspect one country's score")
country_choice = st.selectbox("Country", scored["nationality"].tolist())
row = scored[scored["nationality"] == country_choice].iloc[0]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Opportunity score", formatting.fmt_number(row["opportunity_score"], 1))
col2.metric("Growth percentile", formatting.fmt_number(row["growth_score"], 1) if pd.notna(row["growth_score"]) else "N/A")
col3.metric("Market size percentile", formatting.fmt_number(row["market_size_score"], 1) if pd.notna(row["market_size_score"]) else "N/A")
col4.metric("Visa pathway percentile", formatting.fmt_number(row["visa_score"], 1) if pd.notna(row["visa_score"]) else "N/A")

st.markdown(f"**Data confidence:** {row['data_confidence_pct']:.0f}% of weighted components available for {country_choice}.")
st.markdown(f"**Weights actually applied:** {', '.join(f'{k}: {v:.1%}' for k, v in row['weights_used'].items()) if row['weights_used'] else 'None -- no components available'}")
st.markdown(
    f"**Raw inputs:** current enrolments = {formatting.fmt_number(row['current_volume'])}, "
    f"YoY growth = {formatting.fmt_pct(row['growth_pct'], signed=True) if pd.notna(row['growth_pct']) else 'N/A'}, "
    f"skilled migration grants = {formatting.fmt_number(row['visa_volume']) if pd.notna(row['visa_volume']) else 'N/A (no skilled migration record for this country)'}"
)

common.page_footer(["fact_student_enrolment", "ref_skilled_migration_by_cob_occupation"])
