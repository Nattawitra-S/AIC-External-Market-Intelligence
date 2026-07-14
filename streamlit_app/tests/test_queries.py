"""
test_queries.py
=================
Live, read-only integration tests against the actual MySQL database
configured in .env. These are the tests that would have caught the two
real bugs found while building this app:
  - a SQLAlchemy/mysql-connector "tuple cannot be converted" error on any
    IN-clause query with more than one value,
  - fact_cpi / fact_population_by_cob / fact_overseas_migration mixing
    aggregate rows with individual series, producing nonsensical totals.

If the database is unreachable, every test in this module is skipped
(not failed) -- this suite validates query correctness against live data,
it is not a substitute for the pure-logic unit tests in test_formatting.py
etc., which need no database at all.
"""

import pandas as pd
import pytest

from modules import queries as q
from modules.db import test_connection as _check_db_connection

_connected, _reason = _check_db_connection()
pytestmark = pytest.mark.skipif(
    not _connected, reason=f"MySQL not reachable, skipping live query tests: {_reason}"
)


class TestLatestPeriod:
    def test_returns_valid_year_month(self):
        year, month = q.get_latest_period()
        assert 2000 < year < 2100
        assert 1 <= month <= 12


class TestYearlySnapshotTotals:
    def test_years_are_non_decreasing_and_unique(self):
        df = q.get_yearly_snapshot_totals()
        years = df["enrol_year"].tolist()
        assert years == sorted(years)
        assert len(years) == len(set(years))

    def test_month_never_exceeds_twelve(self):
        df = q.get_yearly_snapshot_totals()
        assert df["enrol_month"].max() <= 12

    def test_totals_are_positive(self):
        df = q.get_yearly_snapshot_totals()
        assert (df["total_enrolments"] > 0).all()


class TestInClauseQueries:
    """Regression tests for the SQLAlchemy expanding-bindparam fix --
    these raised 'Python type tuple cannot be converted' before the fix
    in modules/db.py."""

    def test_country_yearly_trend_multiple_countries(self):
        df = q.get_country_yearly_trend(("China", "India", "Nepal"))
        assert not df.empty
        assert set(df["nationality"].unique()) <= {"China", "India", "Nepal"}

    def test_country_sector_comparison_multiple_countries(self):
        year, month = q.get_latest_period()
        df = q.get_country_sector_comparison(("China", "India"), year, month)
        assert not df.empty

    def test_sector_yearly_trend_with_sector_filter(self):
        df = q.get_sector_yearly_trend(("Higher Education", "VET"))
        assert not df.empty
        assert set(df["sector"].unique()) <= {"Higher Education", "VET"}

    def test_single_country_tuple_also_works(self):
        df = q.get_country_yearly_trend(("China",))
        assert not df.empty


class TestAggregateRowRegression:
    """Regression tests for the three queries that were silently summing
    ABS aggregate rows ("Total", "Australia", "Total overseas-born") on top
    of individual country/series breakdowns."""

    def test_cpi_is_a_single_index_series_not_a_blend(self):
        df = q.get_yearly_cpi()
        assert not df.empty
        # A genuine CPI index level is in the tens-to-low-hundreds range;
        # blending index levels with % change series (the original bug)
        # produced values as low as ~35, which is not a valid index level
        # for this series in this period.
        assert (df["avg_cpi"] > 50).all()

    def test_population_is_realistic_australia_scale(self):
        df = q.get_yearly_population_by_cob()
        assert not df.empty
        # Australia's real ERP is on the order of 20-30 million; the
        # original bug (summing every country_name row including "Total",
        # "Australia", "Total overseas-born") produced ~80 million.
        assert (df["total_population"] < 35_000_000).all()
        assert (df["total_population"] > 15_000_000).all()

    def test_net_overseas_migration_is_realistic_scale(self):
        df = q.get_yearly_overseas_migration()
        assert not df.empty
        # Real annual NOM has historically been well under 1 million;
        # the original bug (summing every country row across 8 states
        # including aggregate labels) produced multi-million-scale values.
        assert (df["total_value"].abs() < 1_000_000).all()


class TestKnownEmptyTables:
    def test_known_empty_tables_are_actually_empty(self):
        """If one of these ever gets populated by a future ETL run, this
        test should be updated (and the corresponding page's notice
        removed) -- it is a tripwire, not a permanent assertion."""
        for table in ["fact_temp_skilled_visa", "fact_temp_graduate_visa",
                      "fact_permanent_migration", "dim_occupation", "stg_skillselect_eoi"]:
            assert q.get_table_row_count(table) == 0

    def test_populated_tables_are_actually_populated(self):
        for table in ["fact_student_enrolment", "fact_exchange_rate", "dim_provider"]:
            assert q.get_table_row_count(table) > 0


class TestOpportunityScoreInputs:
    def test_returns_expected_columns(self):
        df = q.get_opportunity_score_inputs()
        assert {"nationality", "current_volume", "growth_pct", "visa_volume"} <= set(df.columns)

    def test_visa_volume_is_null_not_zero_when_no_skilled_migration_record(self):
        """A country genuinely absent from ref_skilled_migration_by_cob_occupation
        must show NaN, not a fabricated 0 -- 0 would incorrectly rank it as
        'worst visa pathway' instead of 'no data'."""
        df = q.get_opportunity_score_inputs()
        # At least one country (per live inspection) has no skilled migration record.
        assert df["visa_volume"].isna().any()


class TestEtlAuditLog:
    def test_last_etl_updates_not_empty(self):
        df = q.get_last_etl_updates()
        assert not df.empty
        assert "last_completed" in df.columns
