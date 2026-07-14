import pandas as pd

from modules.validation import (
    KNOWN_EMPTY_TABLES,
    KNOWN_NULL_COLUMNS,
    guard_empty,
    is_empty,
)


class TestIsEmpty:
    def test_none_is_empty(self):
        assert is_empty(None) is True

    def test_empty_dataframe_is_empty(self):
        assert is_empty(pd.DataFrame()) is True

    def test_populated_dataframe_is_not_empty(self):
        assert is_empty(pd.DataFrame({"a": [1]})) is False


class TestGuardEmpty:
    def test_returns_true_and_does_not_raise_for_empty_df(self):
        # Must not raise even though st.info() has no real Streamlit session here.
        assert guard_empty(pd.DataFrame(), "no data") is True

    def test_returns_false_for_populated_df(self):
        assert guard_empty(pd.DataFrame({"a": [1]}), "no data") is False

    def test_known_empty_table_path_does_not_raise(self):
        assert guard_empty(pd.DataFrame(), "no data", table_name="fact_temp_skilled_visa") is True


class TestKnownGaps:
    def test_known_empty_tables_documented_with_reasons(self):
        for table, reason in KNOWN_EMPTY_TABLES.items():
            assert isinstance(table, str) and table
            assert isinstance(reason, str) and len(reason) > 10

    def test_known_null_columns_documented_with_reasons(self):
        for (table, col), reason in KNOWN_NULL_COLUMNS.items():
            assert table and col
            assert isinstance(reason, str) and len(reason) > 10

    def test_expected_empty_tables_present(self):
        """These 5 tables were confirmed empty during the live migration
        (docs/final_migration_summary.md) -- this test guards against the
        list silently going stale."""
        expected = {
            "fact_temp_skilled_visa", "fact_temp_graduate_visa",
            "fact_permanent_migration", "dim_occupation", "stg_skillselect_eoi",
        }
        assert expected.issubset(KNOWN_EMPTY_TABLES.keys())
