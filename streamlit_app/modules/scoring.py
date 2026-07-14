"""
scoring.py
===========
Deterministic, rule-based scoring only. No machine learning, no predictive
modeling -- every number here is directly traceable to a documented formula
so it can be inspected line by line. Nothing in this module is a forecast;
it ranks *current and historical* data.

Opportunity Score
------------------
Components (each normalized to a 0-100 percentile rank across all countries
that have the relevant data):
  - growth        (40% target weight): YoY % change in enrolments
  - market_size   (35% target weight): current enrolment volume
  - visa_pathway  (25% target weight): skilled migration grant volume for
                    that country of birth (proxy for a visa outcome pathway
                    existing for that nationality)
  - competition   (0% weight, ALWAYS excluded): there is no country-level
                    link between CRICOS provider/course data and student
                    nationality in this schema, so a "competition" component
                    cannot be honestly computed per country. It is not
                    invented; the weight is documented as zero.

If a country is missing one of the three weighted components (most often
visa_pathway, since only ~220 of many nationalities have skilled-migration
records), that component's weight is redistributed proportionally across
the components that ARE available for that country -- not silently dropped,
not treated as zero. `data_confidence_pct` records how many of the three
weighted components were actually available for that country, so a user
can see at a glance how much of the score is "real" for any given row.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_WEIGHTS: dict[str, float] = {
    "growth": 0.40,
    "market_size": 0.35,
    "visa_pathway": 0.25,
}

COMPETITION_NOTE = (
    "Competition is intentionally excluded from the score (weight = 0): "
    "CRICOS provider/course data has no country-of-origin linkage in the "
    "current schema, so a per-country competition figure cannot be computed "
    "honestly. See the Competitor Analysis page for national/state-level "
    "competition context instead."
)


def percentile_rank(series: pd.Series) -> pd.Series:
    """0-100 percentile rank; NaN stays NaN (never coerced to 0)."""
    return series.rank(pct=True, na_option="keep") * 100


def compute_opportunity_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    df must contain: nationality, growth_pct, current_volume, visa_volume
    (any of the latter three may be NaN/None for a given row).
    Returns df + growth_score, market_size_score, visa_score,
    opportunity_score, data_confidence_pct, weights_used (dict per row).
    """
    out = df.copy()
    out["growth_score"] = percentile_rank(out["growth_pct"])
    out["market_size_score"] = percentile_rank(out["current_volume"])
    out["visa_score"] = percentile_rank(out["visa_volume"])

    scores, confidences, weights_used = [], [], []
    for _, row in out.iterrows():
        components = {}
        if pd.notna(row["growth_score"]):
            components["growth"] = row["growth_score"]
        if pd.notna(row["market_size_score"]):
            components["market_size"] = row["market_size_score"]
        if pd.notna(row["visa_score"]):
            components["visa_pathway"] = row["visa_score"]

        if not components:
            scores.append(None)
            confidences.append(0.0)
            weights_used.append({})
            continue

        total_weight = sum(DEFAULT_WEIGHTS[k] for k in components)
        redistributed = {k: DEFAULT_WEIGHTS[k] / total_weight for k in components}
        score = sum(components[k] * redistributed[k] for k in components)

        scores.append(round(score, 1))
        confidences.append(round(len(components) / len(DEFAULT_WEIGHTS) * 100, 1))
        weights_used.append({k: round(v, 3) for k, v in redistributed.items()})

    out["opportunity_score"] = scores
    out["data_confidence_pct"] = confidences
    out["weights_used"] = weights_used
    return out.sort_values("opportunity_score", ascending=False, na_position="last").reset_index(drop=True)


# ── Rule-based marketing recommendation (Country Analysis page) ────────────

GROWTH_THRESHOLD_STRONG = 10.0   # % YoY
GROWTH_THRESHOLD_DECLINE = -10.0  # % YoY


def marketing_recommendation(growth_pct: float | None, current_volume: float | None,
                              median_volume: float | None) -> str:
    """
    Deterministic, documented rule set -- not a model prediction:
      1. No growth figure available -> say so, no recommendation invented.
      2. Growth >= +10% AND volume >= market median -> established growth market.
      3. Growth >= +10% AND volume <  market median -> emerging growth market.
      4. Growth <= -10% -> declining market, flagged for review.
      5. Otherwise -> stable market.
    """
    if growth_pct is None or current_volume is None or median_volume is None:
        return ("Insufficient historical data for a rule-based recommendation "
                "(requires at least two comparable years of enrolment data).")
    if growth_pct >= GROWTH_THRESHOLD_STRONG and current_volume >= median_volume:
        return (f"Strong growth (+{growth_pct:.1f}% YoY) in an already large market -- "
                f"prioritize marketing investment and course capacity.")
    if growth_pct >= GROWTH_THRESHOLD_STRONG and current_volume < median_volume:
        return (f"Strong growth (+{growth_pct:.1f}% YoY) in an emerging market -- "
                f"consider early investment ahead of scale.")
    if growth_pct <= GROWTH_THRESHOLD_DECLINE:
        return (f"Declining market ({growth_pct:.1f}% YoY) -- monitor closely and "
                f"investigate cause before increasing investment.")
    return f"Stable market ({growth_pct:+.1f}% YoY) -- maintain current marketing effort."
