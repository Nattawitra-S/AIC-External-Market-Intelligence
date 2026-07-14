import math

from modules.formatting import fmt_number, fmt_pct, pct_change, period_label, safe_ratio


class TestFmtNumber:
    def test_basic(self):
        assert fmt_number(1234) == "1,234"

    def test_decimals(self):
        assert fmt_number(1234.5678, decimals=2) == "1,234.57"

    def test_none_is_na(self):
        assert fmt_number(None) == "N/A"

    def test_nan_is_na(self):
        assert fmt_number(float("nan")) == "N/A"


class TestFmtPct:
    def test_basic(self):
        assert fmt_pct(12.345) == "12.3%"

    def test_signed_positive(self):
        assert fmt_pct(5.0, signed=True) == "+5.0%"

    def test_signed_negative_no_double_sign(self):
        assert fmt_pct(-5.0, signed=True) == "-5.0%"

    def test_none_is_na(self):
        assert fmt_pct(None) == "N/A"


class TestPctChange:
    def test_increase(self):
        assert pct_change(110, 100) == 10.0

    def test_decrease(self):
        assert pct_change(90, 100) == -10.0

    def test_previous_zero_returns_none(self):
        # Division by zero must not raise or return inf -- callers render "N/A".
        assert pct_change(100, 0) is None

    def test_none_inputs_return_none(self):
        assert pct_change(None, 100) is None
        assert pct_change(100, None) is None


class TestSafeRatio:
    def test_basic(self):
        assert safe_ratio(10, 4) == 2.5

    def test_zero_denominator_returns_none(self):
        assert safe_ratio(10, 0) is None

    def test_none_returns_none(self):
        assert safe_ratio(None, 4) is None
        assert safe_ratio(10, None) is None


class TestPeriodLabel:
    def test_year_only(self):
        assert period_label(2026) == "2026"

    def test_year_month(self):
        assert period_label(2026, 2) == "2026-02"

    def test_month_zero_padded(self):
        assert period_label(2026, 9) == "2026-09"
