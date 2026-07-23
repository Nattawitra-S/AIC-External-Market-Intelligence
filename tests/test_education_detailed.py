"""
tests/test_education_detailed.py
=================================
Tests for the Detailed Education pipeline: parse_pivot_detailed()'s own
column set (separate from Basic's), the grain/key distinction between
fact_student_enrolment and fact_student_enrolment_detailed, and
run_mysql_education()'s routing of Basic -> fact_student_enrolment /
Detailed -> fact_student_enrolment_detailed as two independent loads
(never concatenated).

Runs WITHOUT a live MySQL connection: none of these tests ever call real
mysql.connector methods -- MySQL interaction is mocked at the function level
(load_education_enrolments, download_file), not at the connector-module
level. Deliberately does NOT install its own mysql.connector stub: doing so
previously collided with tests/test_mysql_library.py's own module-level
stub when both files run in the same pytest session (whichever file's
module code executes first "wins" the sys.modules slot via setdefault,
silently orphaning the other file's local stub reference and breaking its
patches). This file relies on whatever is already importable -- the real
mysql-connector-python package (installed in this project) or another
test module's stub -- without needing to control it.

Run:
    pytest tests/test_education_detailed.py -v
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from ETL import etl_education_v2 as etl_edu
from ETL import run_mysql_sources as rms
from ETL import lib_etl_mysql as lib


def _make_detailed_fixture_xlsx(path: Path) -> pd.DataFrame:
    """Write a tiny flat/tabular xlsx matching the real Detailed raw-split
    header layout (post-extraction shape, not the merged-cell pivot cache).
    Rows 0 and 1 deliberately share every Basic-grain column but differ on
    field of education -- this is the exact shape that proves Basic's key
    cannot be reused for Detailed."""
    df = pd.DataFrame({
        "Year": [2024, 2024, 2024],
        "Month": ["Dec", "Dec", "Dec"],
        "Region": ["Americas", "Americas", "South-East Asia"],
        "Nationality": ["Chile", "Chile", "Vietnam"],
        "State": ["SA", "SA", "NSW"],
        "ProviderType": ["Non Government", "Non Government", "Government"],
        "Sector": ["ELICOS", "ELICOS", "Higher Education"],
        "Broad_Field_Of_Education": ["Society and Culture", "Natural and Physical Sciences", "Information Technology"],
        "Narrow_Field_Of_Education": ["Language and Literature", "Other Natural and Physical Sciences", "Other Information Technology"],
        "Detailed_Field_Of_Education": ["ELICOS", "Medical Science", "Information Technology, n.e.c."],
        "Level_Of_Study": ["Non AQF Award", "Bachelor Degree", "Bachelor Degree"],
        "Foundation": ["No", "No", "No"],
        "New_to_Australia": ["No", "No", "Yes"],
        "Ends_This_Year": ["No", "No", "No"],
        "DATA_YTD_Enrolments": [8, 5, 12],
        "DATA_YTD_Commencements": [8, 2, 12],
        "DATA_As_at_1st_Month": [7, 5, 10],
        "DATA_Enrolments_for_Month": [8, 5, 12],
        "DATA_Commencements_for_Month": [1, 0, 2],
        "Total": [8, 5, 12],
    })
    df.to_excel(path, sheet_name="All Data 1", index=False)
    return df


class TestParsePivotDetailed(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_detailed_test_"))
        self.fixture_path = self.tmp / "detailed_fixture.xlsx"
        _make_detailed_fixture_xlsx(self.fixture_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retains_all_required_dimensions_and_measures(self):
        df = etl_edu.parse_pivot_detailed(self.fixture_path)
        self.assertEqual(len(df), 3)
        required = [
            "year", "month", "region", "nationality", "state", "provider_type", "sector",
            "broad_field_of_education", "narrow_field_of_education", "detailed_field_of_education",
            "level_of_study", "foundation", "new_to_australia", "ends_this_year",
            "data_ytd_enrolments", "data_ytd_commencements",
            "data_as_at_1st_month", "data_enrolments_for_month", "data_commencements_for_month",
        ]
        for col in required:
            self.assertIn(col, df.columns, f"missing required column: {col}")

    def test_total_column_excluded(self):
        """'total' must NOT appear in parse_pivot_detailed's output -- proven
        an exact duplicate of data_ytd_enrolments (100.00% match across the
        full 1,480,597-row production dataset)."""
        df = etl_edu.parse_pivot_detailed(self.fixture_path)
        self.assertNotIn("total", df.columns)

    def test_provider_type_correctly_renamed(self):
        """norm_col('ProviderType') -> 'providertype' (no separator to
        split on) -- must be explicitly renamed to 'provider_type' or it
        silently disappears from the natural key."""
        df = etl_edu.parse_pivot_detailed(self.fixture_path)
        self.assertIn("provider_type", df.columns)
        self.assertNotIn("providertype", df.columns)
        self.assertEqual(set(df["provider_type"]), {"Non Government", "Government"})

    def test_month_name_mapped_to_number(self):
        df = etl_edu.parse_pivot_detailed(self.fixture_path)
        self.assertTrue((df["month"] == 12).all())

    def test_distinct_field_of_education_rows_not_collapsed(self):
        """Rows 0/1 share year/month/nationality/state/sector/provider_type/
        new_to_australia/ends_this_year (Basic's whole key) but differ on
        field of education -- parse_pivot_detailed must keep them distinct,
        not collapse or average them."""
        df = etl_edu.parse_pivot_detailed(self.fixture_path)
        self.assertEqual(len(df), 3)
        self.assertEqual(len(df["detailed_field_of_education"].unique()), 3)


class TestDetailedGrainVsBasicKey(unittest.TestCase):
    """Regression guard: documents *why* Basic's key cannot be reused for
    Detailed, using load_education_enrolments' own validation logic."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_detailed_test_"))
        self.fixture_path = self.tmp / "detailed_fixture.xlsx"
        _make_detailed_fixture_xlsx(self.fixture_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parsed_and_renamed(self):
        df = etl_edu.parse_pivot_detailed(self.fixture_path)
        return rms._rename(df, {
            "year": "enrol_year", "month": "enrol_month", "state": "state_code",
            "data_ytd_enrolments": "ytd_enrolments", "data_ytd_commencements": "ytd_commencements",
            "data_as_at_1st_month": "as_at_1st_month",
            "data_enrolments_for_month": "monthly_enrolments",
            "data_commencements_for_month": "monthly_commencements",
        })

    def test_basic_key_would_reject_this_detailed_data(self):
        """Rows 0 and 1 share every Basic-key column but differ on field of
        education -- validating Detailed data against Basic's key must
        raise, proving Basic's key is genuinely wrong for this data."""
        df = self._parsed_and_renamed()
        with self.assertRaises(ValueError):
            lib.load_education_enrolments(
                df, conn=MagicMock(), dry_run=False,
                key_columns=lib.EDU_ENROLMENT_KEY_COLUMNS,
            )

    def test_detailed_key_accepts_the_same_data(self):
        """The same data, validated against DETAILED_ENROLMENT_KEY_COLUMNS
        (which includes field of education), must pass cleanly."""
        df = self._parsed_and_renamed()
        n = lib.load_education_enrolments(
            df, conn=None, dry_run=False,
            key_columns=lib.DETAILED_ENROLMENT_KEY_COLUMNS,
        )
        self.assertEqual(n, 3)


class TestRunMysqlEducationRouting(unittest.TestCase):
    """run_mysql_education() must route Basic and Detailed to two SEPARATE
    tables/loads, never concatenate them into one DataFrame/load call."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_routing_test_"))
        # Real (empty-content-irrelevant) placeholder files so Path.exists()
        # behaves naturally -- parse_pivot_basic/parse_pivot_detailed are
        # mocked, so their actual file content is never read.
        self.basic_path = self.tmp / "basic_raw_split.xlsx"
        self.detailed_path = self.tmp / "detailed_raw_split.xlsx"
        self.basic_path.write_bytes(b"placeholder")
        self.detailed_path.write_bytes(b"placeholder")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_basic_and_detailed_loaded_separately_not_concatenated(self):
        conn = MagicMock()
        basic_df = pd.DataFrame({
            "year": [2024], "month": [6], "nationality": ["China"], "state": ["NSW"],
            "sector": ["Higher Education"], "provider_type": ["University"],
            "new_to_australia": ["No"], "ends_this_year": ["No"],
            "data_ytd_enrolments": [100], "data_ytd_commencements": [50], "total": [100],
        })
        detailed_df = pd.DataFrame({
            "year": [2024, 2024], "month": [6, 6], "region": ["Asia", "Asia"],
            "nationality": ["China", "China"], "state": ["NSW", "NSW"],
            "provider_type": ["University", "University"], "sector": ["Higher Education", "Higher Education"],
            "broad_field_of_education": ["IT", "Health"],
            "narrow_field_of_education": ["IT", "Health"],
            "detailed_field_of_education": ["IT", "Health"],
            "level_of_study": ["Bachelor", "Bachelor"], "foundation": ["No", "No"],
            "new_to_australia": ["No", "No"], "ends_this_year": ["No", "No"],
            "data_ytd_enrolments": [60, 40], "data_ytd_commencements": [30, 20],
            "data_as_at_1st_month": [55, 35], "data_enrolments_for_month": [60, 40],
            "data_commencements_for_month": [5, 3],
        })

        calls = []

        def fake_load(df, conn, staging_dir=None, dry_run=False,
                       table="fact_student_enrolment", key_columns=None, **kw):
            calls.append({"table": table, "key_columns": key_columns, "n_rows": len(df)})
            return len(df)

        with patch.object(etl_edu, "download_and_extract_pivot_basic", return_value=self.basic_path), \
             patch.object(etl_edu, "parse_pivot_basic", return_value=basic_df), \
             patch("ETL.lib_etl.download_file", side_effect=Exception("no network in test")), \
             patch.object(etl_edu, "LOCAL_FILES", {"parse_pivot_detailed": self.detailed_path}), \
             patch.object(etl_edu, "parse_pivot_detailed", return_value=detailed_df), \
             patch("ETL.lib_etl_mysql.load_education_enrolments", side_effect=fake_load):
            total = rms.run_mysql_education(conn, dry_run=False, local_only=False)

        self.assertEqual(len(calls), 2, "expected exactly 2 separate load calls (Basic + Detailed)")
        tables_called = {c["table"] for c in calls}
        self.assertEqual(tables_called, {"fact_student_enrolment", "fact_student_enrolment_detailed"})

        basic_call = next(c for c in calls if c["table"] == "fact_student_enrolment")
        detailed_call = next(c for c in calls if c["table"] == "fact_student_enrolment_detailed")
        self.assertEqual(basic_call["n_rows"], 1, "Basic call must only carry Basic's own rows")
        self.assertIsNone(basic_call["key_columns"])  # uses load_education_enrolments' own default (EDU_ENROLMENT_KEY_COLUMNS)
        self.assertEqual(detailed_call["n_rows"], 2, "Detailed call must only carry Detailed's own rows, not 1+2=3 combined")
        self.assertEqual(detailed_call["key_columns"], lib.DETAILED_ENROLMENT_KEY_COLUMNS)
        self.assertEqual(total, 3)  # sum of both independent loads, not one concatenated load

    def test_detailed_failure_does_not_lose_basic_success(self):
        """If Detailed fails after Basic already succeeded, the exception
        must still propagate (visibility), but Basic's load call must have
        already happened and returned successfully -- it is a separate,
        already-committed transaction, unaffected by Detailed's failure."""
        conn = MagicMock()
        basic_df = pd.DataFrame({
            "year": [2024], "month": [6], "nationality": ["China"], "state": ["NSW"],
            "sector": ["Higher Education"], "provider_type": ["University"],
            "new_to_australia": ["No"], "ends_this_year": ["No"],
            "data_ytd_enrolments": [100], "data_ytd_commencements": [50], "total": [100],
        })
        detailed_df = pd.DataFrame({
            "year": [2024], "month": [6], "region": ["Asia"], "nationality": ["China"],
            "state": ["NSW"], "provider_type": ["University"], "sector": ["Higher Education"],
            "broad_field_of_education": ["IT"], "narrow_field_of_education": ["IT"],
            "detailed_field_of_education": ["IT"], "level_of_study": ["Bachelor"],
            "foundation": ["No"], "new_to_australia": ["No"], "ends_this_year": ["No"],
            "data_ytd_enrolments": [60], "data_ytd_commencements": [30],
            "data_as_at_1st_month": [55], "data_enrolments_for_month": [60],
            "data_commencements_for_month": [5],
        })

        basic_call_happened = []

        def fake_load(df, conn, staging_dir=None, dry_run=False,
                       table="fact_student_enrolment", key_columns=None, **kw):
            if table == "fact_student_enrolment":
                basic_call_happened.append(True)
                return len(df)
            raise RuntimeError("simulated Detailed load failure")

        with patch.object(etl_edu, "download_and_extract_pivot_basic", return_value=self.basic_path), \
             patch.object(etl_edu, "parse_pivot_basic", return_value=basic_df), \
             patch("ETL.lib_etl.download_file", side_effect=Exception("no network in test")), \
             patch.object(etl_edu, "LOCAL_FILES", {"parse_pivot_detailed": self.detailed_path}), \
             patch.object(etl_edu, "parse_pivot_detailed", return_value=detailed_df), \
             patch("ETL.lib_etl_mysql.load_education_enrolments", side_effect=fake_load):
            with self.assertRaises(RuntimeError):
                rms.run_mysql_education(conn, dry_run=False, local_only=False)

        self.assertEqual(basic_call_happened, [True], "Basic's load must have already run/succeeded before Detailed failed")


if __name__ == "__main__":
    unittest.main()
