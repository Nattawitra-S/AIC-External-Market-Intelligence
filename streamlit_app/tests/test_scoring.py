import pandas as pd
import pytest

from modules.scoring import (
    DEFAULT_WEIGHTS,
    GROWTH_THRESHOLD_DECLINE,
    GROWTH_THRESHOLD_STRONG,
    compute_opportunity_scores,
    marketing_recommendation,
    percentile_rank,
)


class TestPercentileRank:
    def test_higher_value_gets_higher_rank(self):
        s = pd.Series([10, 20, 30])
        ranks = percentile_rank(s)
        assert ranks.iloc[2] > ranks.iloc[1] > ranks.iloc[0]

    def test_nan_stays_nan(self):
        s = pd.Series([10, None, 30])
        ranks = percentile_rank(s)
        assert pd.isna(ranks.iloc[1])
        assert not pd.isna(ranks.iloc[0])


class TestComputeOpportunityScores:
    def _sample_df(self):
        return pd.DataFrame({
            "nationality": ["A", "B", "C"],
            "current_volume": [1000, 500, 100],
            "growth_pct": [20.0, 5.0, -10.0],
            "visa_volume": [300, None, 50],
        })

    def test_weights_sum_to_one_when_all_available(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_full_data_row_uses_all_three_weights(self):
        out = compute_opportunity_scores(self._sample_df())
        row_a = out[out["nationality"] == "A"].iloc[0]
        assert set(row_a["weights_used"].keys()) == {"growth", "market_size", "visa_pathway"}
        assert row_a["data_confidence_pct"] == 100.0

    def test_missing_visa_data_redistributes_not_drops(self):
        out = compute_opportunity_scores(self._sample_df())
        row_b = out[out["nationality"] == "B"].iloc[0]
        # B has no visa_volume -> only 2 of 3 weighted components
        assert "visa_pathway" not in row_b["weights_used"]
        assert set(row_b["weights_used"].keys()) == {"growth", "market_size"}
        # Redistributed weights must still sum to 1 (proportional to their target ratio)
        assert abs(sum(row_b["weights_used"].values()) - 1.0) < 1e-6
        assert row_b["data_confidence_pct"] == pytest.approx(2 / 3 * 100, abs=0.1)

    def test_higher_growth_and_volume_scores_higher(self):
        out = compute_opportunity_scores(self._sample_df())
        # Country A has the highest growth AND highest volume -> should rank first
        assert out.iloc[0]["nationality"] == "A"

    def test_empty_components_yields_none_score(self):
        df = pd.DataFrame({
            "nationality": ["X"], "current_volume": [None],
            "growth_pct": [None], "visa_volume": [None],
        })
        out = compute_opportunity_scores(df)
        assert out.iloc[0]["opportunity_score"] is None
        assert out.iloc[0]["data_confidence_pct"] == 0.0

    def test_never_fabricates_competition_component(self):
        """Competition must never appear as a scored component -- it is
        documented as permanently excluded (no country-level linkage exists)."""
        out = compute_opportunity_scores(self._sample_df())
        for weights in out["weights_used"]:
            assert "competition" not in weights


class TestMarketingRecommendation:
    def test_missing_data_returns_no_fabricated_recommendation(self):
        result = marketing_recommendation(None, None, None)
        assert "insufficient" in result.lower()

    def test_strong_growth_large_market(self):
        result = marketing_recommendation(GROWTH_THRESHOLD_STRONG + 5, 5000, 2000)
        assert "already large market" in result.lower() or "prioritize" in result.lower()

    def test_strong_growth_emerging_market(self):
        result = marketing_recommendation(GROWTH_THRESHOLD_STRONG + 5, 500, 2000)
        assert "emerging" in result.lower()

    def test_decline_triggers_monitor_message(self):
        result = marketing_recommendation(GROWTH_THRESHOLD_DECLINE - 5, 1000, 2000)
        assert "declining" in result.lower()

    def test_stable_market_between_thresholds(self):
        result = marketing_recommendation(2.0, 1000, 2000)
        assert "stable" in result.lower()
