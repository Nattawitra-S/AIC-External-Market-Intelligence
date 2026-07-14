"""
formatting.py
==============
Pure, dependency-free formatting helpers. No I/O — safe to unit test directly.
"""

from __future__ import annotations

import math


def fmt_number(value, decimals: int = 0) -> str:
    """Format a number with thousands separators. Returns 'N/A' for None/NaN."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value:,.{decimals}f}"


def fmt_pct(value, decimals: int = 1, signed: bool = False) -> str:
    """Format a fraction-or-percent-scale number as a percentage string."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    sign = "+" if (signed and value > 0) else ""
    return f"{sign}{value:,.{decimals}f}%"


def pct_change(current, previous) -> float | None:
    """
    Percentage change from `previous` to `current`.
    Returns None (not 0 or inf) when previous is missing or zero, so callers
    can render "N/A" instead of a fabricated/misleading number.
    """
    if current is None or previous is None:
        return None
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def safe_ratio(numerator, denominator) -> float | None:
    """Division that returns None instead of raising or returning inf/NaN."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def period_label(year, month: int | None = None) -> str:
    """Human-readable YYYY or YYYY-MM label."""
    if month is None:
        return str(int(year))
    return f"{int(year)}-{int(month):02d}"
