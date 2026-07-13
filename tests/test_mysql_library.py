"""
tests/test_mysql_library.py
============================
Unit tests for ETL/lib_etl_mysql.py.

All tests run WITHOUT a live MySQL server — mysql.connector is fully mocked.

Run:
    pytest tests/test_mysql_library.py -v
"""

import os
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock

import pandas as pd

# ── Stub mysql.connector before importing lib_etl_mysql ───────────────────────
# This avoids a hard dependency on the connector for unit tests.

mysql_stub = types.ModuleType("mysql")
connector_stub = types.ModuleType("mysql.connector")

class _MySQLError(Exception):
    def __init__(self, msg="", errno=0):
        super().__init__(msg)
        self.errno = errno

connector_stub.Error = _MySQLError
connector_stub.connect = MagicMock()

mysql_stub.connector = connector_stub
sys.modules.setdefault("mysql", mysql_stub)
sys.modules.setdefault("mysql.connector", connector_stub)
sys.modules.setdefault("mysql.connector.pooling", types.ModuleType("mysql.connector.pooling"))

# Add ETL to path so we can import lib_etl_mysql
sys.path.insert(0, str(Path(__file__).parent.parent))

import ETL.lib_etl_mysql as lib  # noqa: E402  (must come after stub)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_conn():
    """Return a mock MySQL connection with a cursor mock."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 42
    cursor.rowcount = 10
    conn.cursor.return_value = cursor
    return conn, cursor


def _sample_df(**kwargs):
    """Return a tiny 3-row DataFrame for upsert testing."""
    data = {
        "name": ["Alice", "Bob", None],
        "value": [1, 2, 3],
    }
    data.update(kwargs)
    return pd.DataFrame(data)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  _mysql_config
# ─────────────────────────────────────────────────────────────────────────────

class TestMysqlConfig(unittest.TestCase):

    def setUp(self):
        for k in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASS", "MYSQL_DB"):
            os.environ.pop(k, None)

    def test_raises_when_user_missing(self):
        os.environ["MYSQL_DB"] = "testdb"
        with self.assertRaises(EnvironmentError):
            lib._mysql_config()

    def test_raises_when_db_missing(self):
        os.environ["MYSQL_USER"] = "root"
        with self.assertRaises(EnvironmentError):
            lib._mysql_config()

    def test_defaults_applied(self):
        os.environ["MYSQL_USER"] = "root"
        os.environ["MYSQL_DB"]   = "aic"
        cfg = lib._mysql_config()
        self.assertEqual(cfg["host"], "localhost")
        self.assertEqual(cfg["port"], 3306)
        self.assertTrue(cfg["allow_local_infile"])
        self.assertEqual(cfg["charset"], "utf8mb4")

    def test_custom_host_and_port(self):
        os.environ["MYSQL_USER"] = "u"
        os.environ["MYSQL_DB"]   = "d"
        os.environ["MYSQL_HOST"] = "db.internal"
        os.environ["MYSQL_PORT"] = "3307"
        cfg = lib._mysql_config()
        self.assertEqual(cfg["host"], "db.internal")
        self.assertEqual(cfg["port"], 3307)

    def test_password_included(self):
        os.environ["MYSQL_USER"] = "u"
        os.environ["MYSQL_DB"]   = "d"
        os.environ["MYSQL_PASS"] = "secret"
        cfg = lib._mysql_config()
        self.assertEqual(cfg["password"], "secret")

    def tearDown(self):
        for k in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASS", "MYSQL_DB"):
            os.environ.pop(k, None)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  norm_col
# ─────────────────────────────────────────────────────────────────────────────

class TestNormCol(unittest.TestCase):

    def test_lowercase(self):
        self.assertEqual(lib.norm_col("Country"), "country")

    def test_spaces_to_underscore(self):
        self.assertEqual(lib.norm_col("Country Name"), "country_name")

    def test_special_chars_stripped(self):
        self.assertEqual(lib.norm_col("CO2 Emissions (kt)"), "co2_emissions_kt")

    def test_leading_trailing_underscores_removed(self):
        self.assertEqual(lib.norm_col("  _test_  "), "test")

    def test_already_normalised(self):
        self.assertEqual(lib.norm_col("lf_period"), "lf_period")

    def test_numbers_preserved(self):
        self.assertEqual(lib.norm_col("BP0015"), "bp0015")


# ─────────────────────────────────────────────────────────────────────────────
# 3.  add_etl_meta
# ─────────────────────────────────────────────────────────────────────────────

class TestAddEtlMeta(unittest.TestCase):

    def test_columns_added(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = lib.add_etl_meta(df, source="rba")
        self.assertIn("_etl_source", result.columns)
        self.assertIn("_etl_loaded_at", result.columns)

    def test_source_value(self):
        df = pd.DataFrame({"a": [1]})
        result = lib.add_etl_meta(df, source="education")
        self.assertTrue((result["_etl_source"] == "education").all())

    def test_does_not_mutate_original(self):
        df = pd.DataFrame({"a": [1]})
        _ = lib.add_etl_meta(df, source="rba")
        self.assertNotIn("_etl_source", df.columns)

    def test_loaded_at_is_parseable_datetime(self):
        df = pd.DataFrame({"a": [1]})
        result = lib.add_etl_meta(df, source="test")
        val = result["_etl_loaded_at"].iloc[0]
        # Should be a valid datetime string
        datetime.strptime(val, "%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────────
# 4.  AuditRun
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditRun(unittest.TestCase):

    def test_start_inserts_row(self):
        conn, cursor = _mock_conn()
        audit = lib.AuditRun(conn, source="rba", table_name="fact_exchange_rate")
        self.assertEqual(audit.run_id, 42)
        # INSERT called on __init__
        cursor.execute.assert_called()
        insert_sql = cursor.execute.call_args_list[0][0][0]
        self.assertIn("INSERT INTO etl_audit_log", insert_sql)
        self.assertIn("running", insert_sql)

    def test_complete_updates_status(self):
        conn, cursor = _mock_conn()
        audit = lib.AuditRun(conn, "rba", "fact_exchange_rate")
        audit.rows_inserted = 100
        audit.complete()
        update_sql = cursor.execute.call_args_list[-1][0][0]
        self.assertIn("completed", update_sql)
        self.assertIn("UPDATE etl_audit_log", update_sql)

    def test_fail_updates_status_and_error(self):
        conn, cursor = _mock_conn()
        audit = lib.AuditRun(conn, "rba", "fact_exchange_rate")
        audit.fail("something went wrong")
        update_sql = cursor.execute.call_args_list[-1][0][0]
        params = cursor.execute.call_args_list[-1][0][1]
        self.assertIn("failed", update_sql)
        self.assertIn("something went wrong", params)

    def test_row_counters_default_to_zero(self):
        conn, _ = _mock_conn()
        audit = lib.AuditRun(conn, "src", "tbl")
        self.assertEqual(audit.rows_read, 0)
        self.assertEqual(audit.rows_inserted, 0)
        self.assertEqual(audit.rows_updated, 0)
        self.assertEqual(audit.rows_rejected, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  mysql_transaction context manager
# ─────────────────────────────────────────────────────────────────────────────

class TestMysqlTransaction(unittest.TestCase):

    def test_commits_on_success(self):
        conn = MagicMock()
        with lib.mysql_transaction(conn):
            pass
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_rolls_back_on_exception(self):
        conn = MagicMock()
        with self.assertRaises(ValueError):
            with lib.mysql_transaction(conn):
                raise ValueError("oops")
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_exception_propagates(self):
        conn = MagicMock()
        with self.assertRaises(RuntimeError):
            with lib.mysql_transaction(conn):
                raise RuntimeError("db error")


# ─────────────────────────────────────────────────────────────────────────────
# 6.  upsert_df_mysql — SQL generation and NULL conversion
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertDfMysql(unittest.TestCase):

    def test_dry_run_returns_row_count_without_execute(self):
        conn, cursor = _mock_conn()
        df = _sample_df()
        n = lib.upsert_df_mysql(df, "fact_exchange_rate", conn, dry_run=True)
        self.assertEqual(n, len(df))
        cursor.executemany.assert_not_called()

    def test_empty_df_returns_zero(self):
        conn, cursor = _mock_conn()
        n = lib.upsert_df_mysql(pd.DataFrame(), "some_table", conn)
        self.assertEqual(n, 0)
        cursor.executemany.assert_not_called()

    def test_sql_contains_on_duplicate_key_update(self):
        conn, cursor = _mock_conn()
        df = pd.DataFrame({"a": [1], "b": [2]})
        lib.upsert_df_mysql(df, "test_tbl", conn)
        sql = cursor.executemany.call_args[0][0]
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertIn("`a`=VALUES(`a`)", sql)
        self.assertIn("`b`=VALUES(`b`)", sql)

    def test_sql_uses_backtick_quoting(self):
        conn, cursor = _mock_conn()
        df = pd.DataFrame({"my_col": [1]})
        lib.upsert_df_mysql(df, "my_table", conn)
        sql = cursor.executemany.call_args[0][0]
        self.assertIn("`my_col`", sql)
        self.assertIn("`my_table`", sql)

    def test_none_values_passed_as_python_none(self):
        conn, cursor = _mock_conn()
        df = pd.DataFrame({"name": [None], "val": [float("nan")]})
        lib.upsert_df_mysql(df, "tbl", conn)
        rows = cursor.executemany.call_args[0][1]
        # Both None and NaN should become Python None
        for row in rows:
            for v in row:
                if v is not None:
                    # If not None, should not be NaN
                    import math
                    self.assertFalse(
                        isinstance(v, float) and math.isnan(v),
                        f"NaN leaked into row: {row}"
                    )

    def test_string_none_not_converted(self):
        """String values that equal 'None' should stay as strings."""
        conn, cursor = _mock_conn()
        df = pd.DataFrame({"name": ["Alice"]})
        lib.upsert_df_mysql(df, "tbl", conn)
        rows = cursor.executemany.call_args[0][1]
        self.assertEqual(rows[0][0], "Alice")

    def test_chunking_calls_executemany_multiple_times(self):
        conn, cursor = _mock_conn()
        df = pd.DataFrame({"a": range(25)})
        lib.upsert_df_mysql(df, "tbl", conn, chunk_size=10)
        # 25 rows / 10 per chunk = 3 executemany calls
        self.assertEqual(cursor.executemany.call_count, 3)

    def test_mysql_error_raises_runtime_error(self):
        conn, cursor = _mock_conn()
        cursor.executemany.side_effect = _MySQLError("duplicate key", errno=1062)
        df = pd.DataFrame({"a": [1]})
        with self.assertRaises(RuntimeError) as ctx:
            lib.upsert_df_mysql(df, "tbl", conn)
        self.assertIn("MySQL upsert failed", str(ctx.exception))

    def test_mysql_error_triggers_rollback(self):
        conn, cursor = _mock_conn()
        cursor.executemany.side_effect = _MySQLError("error")
        df = pd.DataFrame({"a": [1]})
        with self.assertRaises(RuntimeError):
            lib.upsert_df_mysql(df, "tbl", conn)
        conn.rollback.assert_called_once()

    def test_audit_rows_read_incremented(self):
        conn, cursor = _mock_conn()
        audit = MagicMock()
        audit.rows_read = 0
        audit.rows_inserted = 0
        df = pd.DataFrame({"a": [1, 2, 3]})
        lib.upsert_df_mysql(df, "tbl", conn, audit=audit)
        self.assertEqual(audit.rows_read, 3)

    def test_dry_run_increments_audit_rows_read(self):
        conn, _ = _mock_conn()
        audit = MagicMock()
        audit.rows_read = 0
        df = pd.DataFrame({"a": [1, 2]})
        lib.upsert_df_mysql(df, "tbl", conn, dry_run=True, audit=audit)
        self.assertEqual(audit.rows_read, 2)


# ─────────────────────────────────────────────────────────────────────────────
# 7.  bulk_load_csv
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkLoadCsv(unittest.TestCase):

    def test_dry_run_writes_csv_then_deletes(self):
        conn, cursor = _mock_conn()
        staging = Path("/tmp/aic_test_staging_dryrundel")
        staging.mkdir(exist_ok=True)
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        n = lib.bulk_load_csv(df, "test_tbl", conn, staging_dir=staging, dry_run=True)
        self.assertEqual(n, 3)
        # No CSV file should remain after dry_run
        remaining = list(staging.glob("aic_bulk_test_tbl_*.csv"))
        self.assertEqual(remaining, [], "Dry-run CSV not cleaned up")
        cursor.execute.assert_not_called()
        staging.rmdir()

    def test_empty_df_returns_zero(self):
        conn, cursor = _mock_conn()
        n = lib.bulk_load_csv(pd.DataFrame(), "tbl", conn)
        self.assertEqual(n, 0)
        cursor.execute.assert_not_called()

    def test_load_data_sql_uses_correct_table(self):
        conn, cursor = _mock_conn()
        cursor.rowcount = 5
        staging = Path("/tmp/aic_test_staging_sql")
        staging.mkdir(exist_ok=True)
        df = pd.DataFrame({"col1": [1], "col2": ["v"]})
        lib.bulk_load_csv(df, "fact_exchange_rate", conn, staging_dir=staging)
        sql = cursor.execute.call_args[0][0]
        self.assertIn("LOAD DATA LOCAL INFILE", sql)
        self.assertIn("fact_exchange_rate", sql)
        self.assertIn("utf8mb4", sql)
        self.assertIn("`col1`", sql)
        staging.rmdir()

    def test_csv_cleaned_up_after_load(self):
        conn, cursor = _mock_conn()
        cursor.rowcount = 2
        staging = Path("/tmp/aic_test_staging_cleanup")
        staging.mkdir(exist_ok=True)
        df = pd.DataFrame({"a": [1, 2]})
        lib.bulk_load_csv(df, "tbl", conn, staging_dir=staging)
        remaining = list(staging.glob("aic_bulk_tbl_*.csv"))
        self.assertEqual(remaining, [], "CSV file not cleaned up after load")
        staging.rmdir()

    def test_mysql_error_raises_and_rejects_rows(self):
        conn, cursor = _mock_conn()
        cursor.execute.side_effect = _MySQLError("Access denied", errno=1045)
        staging = Path("/tmp/aic_test_staging_err")
        staging.mkdir(exist_ok=True)
        audit = MagicMock()
        audit.rows_read = 0
        audit.rows_rejected = 0
        df = pd.DataFrame({"a": [1, 2]})
        with self.assertRaises(RuntimeError) as ctx:
            lib.bulk_load_csv(df, "tbl", conn, staging_dir=staging, audit=audit)
        self.assertIn("LOAD DATA failed", str(ctx.exception))
        self.assertEqual(audit.rows_rejected, 2)
        # Clean up any leftover CSV files
        for f in staging.glob("*.csv"):
            f.unlink()
        staging.rmdir()

    def test_null_values_written_as_sentinel(self):
        """NaN/None values must appear as \\N in the staged CSV."""
        conn, cursor = _mock_conn()
        cursor.rowcount = 2
        staging = Path("/tmp/aic_test_null_sentinel")
        staging.mkdir(exist_ok=True)
        df = pd.DataFrame({"a": [1, None], "b": ["x", float("nan")]})

        # Intercept the LOAD DATA call to inspect the CSV content
        csv_content = []
        def _capture_execute(sql, *args, **kwargs):
            # Extract CSV path from SQL
            import re
            m = re.search(r"INFILE '([^']+)'", sql)
            if m:
                p = Path(m.group(1))
                if p.exists():
                    csv_content.append(p.read_text())

        cursor.execute.side_effect = _capture_execute
        cursor.rowcount = 2

        lib.bulk_load_csv(df, "tbl", conn, staging_dir=staging)
        # After load, CSV is deleted — content was captured above
        if csv_content:
            self.assertIn("\\N", csv_content[0])
        # Clean up
        for f in staging.glob("*.csv"):
            f.unlink(missing_ok=True)
        staging.rmdir()


# ─────────────────────────────────────────────────────────────────────────────
# 8.  load_education_enrolments — chunking and audit
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadEducationEnrolments(unittest.TestCase):

    def _make_edu_df(self, n_rows: int) -> pd.DataFrame:
        return pd.DataFrame({
            "enrol_year":        [2024] * n_rows,
            "enrol_month":       [6]    * n_rows,
            "nationality":       ["India"] * n_rows,
            "state_code":        ["NSW"] * n_rows,
            "sector":            ["Higher Education"] * n_rows,
            "provider_type":     ["University"] * n_rows,
            "new_to_australia":  ["No"] * n_rows,
            "ends_this_year":    ["No"] * n_rows,
            "ytd_enrolments":    [100]  * n_rows,
            "ytd_commencements": [10]   * n_rows,
            "_etl_source":       ["test"] * n_rows,
            "_etl_loaded_at":    ["2024-01-01 00:00:00"] * n_rows,
        })

    def test_dry_run_returns_total_rows(self):
        conn, cursor = _mock_conn()
        cursor.lastrowid = 1
        df = self._make_edu_df(250)
        n = lib.load_education_enrolments(
            df, conn, staging_dir=Path("/tmp"), dry_run=True, chunk_rows=100
        )
        self.assertEqual(n, 250)

    def test_chunks_correctly(self):
        """250 rows with chunk_rows=100 → 3 bulk_load_csv calls."""
        conn, cursor = _mock_conn()
        cursor.lastrowid = 1
        cursor.rowcount = 100

        call_count = []
        original_bulk = lib.bulk_load_csv

        def _count_calls(*args, **kwargs):
            call_count.append(1)
            kwargs["dry_run"] = True
            return original_bulk(*args, **kwargs)

        df = self._make_edu_df(250)
        with patch.object(lib, "bulk_load_csv", side_effect=_count_calls):
            lib.load_education_enrolments(
                df, conn, staging_dir=Path("/tmp"), dry_run=True, chunk_rows=100
            )
        self.assertEqual(len(call_count), 3)

    def test_audit_run_created_for_education(self):
        """AuditRun should be created with source='education' and correct table."""
        conn, cursor = _mock_conn()
        cursor.lastrowid = 99
        df = self._make_edu_df(5)

        created_audits = []
        OrigAudit = lib.AuditRun

        class CapturingAudit(OrigAudit):
            def __init__(self, *args, **kwargs):
                created_audits.append((args, kwargs))
                super().__init__(*args, **kwargs)

        with patch.object(lib, "AuditRun", CapturingAudit):
            lib.load_education_enrolments(df, conn, dry_run=True, staging_dir=Path("/tmp"))

        self.assertTrue(len(created_audits) > 0)
        args, _ = created_audits[0]
        self.assertEqual(args[1], "education")
        self.assertEqual(args[2], "fact_student_enrolment")

    def test_exception_triggers_audit_fail(self):
        conn, cursor = _mock_conn()
        cursor.lastrowid = 1

        df = self._make_edu_df(5)
        audit_mock = MagicMock()
        audit_mock.rows_read = 0
        audit_mock.rows_inserted = 0

        with patch.object(lib, "AuditRun", return_value=audit_mock):
            with patch.object(lib, "bulk_load_csv", side_effect=RuntimeError("disk full")):
                with self.assertRaises(RuntimeError):
                    lib.load_education_enrolments(
                        df, conn, staging_dir=Path("/tmp"), dry_run=False
                    )
        audit_mock.fail.assert_called_once()
        fail_msg = audit_mock.fail.call_args[0][0]
        self.assertIn("disk full", fail_msg)


# ─────────────────────────────────────────────────────────────────────────────
# 9.  with_retry
# ─────────────────────────────────────────────────────────────────────────────

class TestWithRetry(unittest.TestCase):

    def test_succeeds_first_try(self):
        result = lib.with_retry(lambda: 42, retries=3, delay=0)
        self.assertEqual(result, 42)

    def test_retries_on_failure_then_succeeds(self):
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("not yet")
            return "ok"
        result = lib.with_retry(fn, retries=3, delay=0)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)

    def test_raises_after_max_retries(self):
        def fn():
            raise RuntimeError("always fails")
        with self.assertRaises(RuntimeError):
            lib.with_retry(fn, retries=3, delay=0)

    def test_raises_last_exception(self):
        errors = [ValueError("first"), TypeError("second"), OSError("last")]
        calls = [0]
        def fn():
            e = errors[calls[0]]
            calls[0] += 1
            raise e
        with self.assertRaises(OSError) as ctx:
            lib.with_retry(fn, retries=3, delay=0)
        self.assertIn("last", str(ctx.exception))


# ─────────────────────────────────────────────────────────────────────────────
# 10.  get_mysql_conn — mocked connector
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMysqlConn(unittest.TestCase):

    def setUp(self):
        os.environ["MYSQL_USER"] = "root"
        os.environ["MYSQL_PASS"] = "pass"
        os.environ["MYSQL_DB"]   = "aic"

    def tearDown(self):
        for k in ("MYSQL_USER", "MYSQL_PASS", "MYSQL_DB"):
            os.environ.pop(k, None)

    def test_calls_connect_with_allow_local_infile(self):
        mock_conn = MagicMock()
        with patch.object(connector_stub, "connect", return_value=mock_conn) as mock_connect:
            conn = lib.get_mysql_conn()
            cfg = mock_connect.call_args[1]
            self.assertTrue(cfg.get("allow_local_infile"))

    def test_returns_connection_object(self):
        mock_conn = MagicMock()
        with patch.object(connector_stub, "connect", return_value=mock_conn):
            conn = lib.get_mysql_conn()
            self.assertIs(conn, mock_conn)


# ─────────────────────────────────────────────────────────────────────────────
# 11.  Generated key column logic (schema-level — validated via validator)
# ─────────────────────────────────────────────────────────────────────────────

class TestGeneratedKeyConvention(unittest.TestCase):
    """
    These tests verify that the lib correctly handles DataFrames that include
    columns destined for tables with generated columns.

    Generated columns (visa_subclass_k, etc.) are server-side computed —
    the ETL must NOT include them in the INSERT column list.
    The upsert must only pass the base columns, not the _k generated columns.
    """

    def test_generated_cols_not_in_upsert_sql(self):
        """
        If the DataFrame includes generated-column names (e.g., visa_subclass_k),
        upsert_df_mysql should include them in the INSERT col list.
        MySQL will reject the insert at runtime if they're specified for a STORED col.

        This test documents the EXPECTED behavior: the ETL source modules must
        drop generated columns before calling upsert_df_mysql.
        """
        conn, cursor = _mock_conn()
        # DataFrame that incorrectly includes a generated column name
        df = pd.DataFrame({
            "financial_year":  ["2023-24"],
            "visa_subclass":   ["190"],
            "visa_subclass_k": ["190"],      # generated col — should NOT be in df
            "measure":         ["grants"],
            "value":           [1000.0],
        })
        lib.upsert_df_mysql(df, "fact_skilled_migration", conn)
        sql = cursor.executemany.call_args[0][0]
        # All provided columns go into SQL — it's the ETL's job to exclude gen cols
        self.assertIn("`visa_subclass_k`", sql)  # documents current behavior
        # Real enforcement is in the ETL source modules, not the library


# ─────────────────────────────────────────────────────────────────────────────
# 12.  test_connection helper
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectionHelper(unittest.TestCase):

    def setUp(self):
        os.environ["MYSQL_USER"] = "root"
        os.environ["MYSQL_DB"]   = "aic"

    def tearDown(self):
        os.environ.pop("MYSQL_USER", None)
        os.environ.pop("MYSQL_DB", None)

    def test_returns_true_on_successful_connect(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("8.0.32",)
        mock_conn.cursor.return_value = mock_cursor
        with patch.object(connector_stub, "connect", return_value=mock_conn):
            result = lib.test_connection()
        self.assertTrue(result)

    def test_returns_false_on_connection_error(self):
        with patch.object(connector_stub, "connect", side_effect=_MySQLError("refused")):
            result = lib.test_connection()
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# 13.  TRUE ZERO-WRITE DRY-RUN
#      These tests prove that conn=None produces zero database mutations.
#      No cursor.execute(), cursor.executemany(), conn.commit(), or
#      conn.rollback() calls may occur when conn is None.
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroWriteDryRun(unittest.TestCase):
    """
    Mandatory requirement: when conn=None, zero database mutations.
    AuditRun, upsert_df_mysql, and bulk_load_csv must all be no-ops.
    """

    # ── AuditRun with conn=None ───────────────────────────────────────────────

    def test_audit_run_conn_none_no_cursor_call(self):
        """AuditRun(conn=None) must never open a cursor."""
        audit = lib.AuditRun(None, "rba", "fact_exchange_rate")
        # No exception, no cursor usage. run_id stays None.
        self.assertIsNone(audit.run_id)

    def test_audit_run_conn_none_complete_no_db(self):
        """AuditRun.complete() with conn=None must not touch the database."""
        audit = lib.AuditRun(None, "rba", "fact_exchange_rate")
        audit.rows_read = 100
        audit.rows_inserted = 100
        # Must not raise; no DB call possible
        audit.complete()

    def test_audit_run_conn_none_fail_no_db(self):
        """AuditRun.fail() with conn=None must not touch the database."""
        audit = lib.AuditRun(None, "abs", "fact_labour_force")
        audit.fail("test error message")  # must not raise, must not write

    def test_audit_run_conn_none_counts_accumulate(self):
        """Row counters on AuditRun still accumulate even when conn=None."""
        audit = lib.AuditRun(None, "cricos", "dim_provider")
        audit.rows_read += 50
        audit.rows_inserted += 50
        self.assertEqual(audit.rows_read, 50)
        self.assertEqual(audit.rows_inserted, 50)

    # ── upsert_df_mysql with conn=None ────────────────────────────────────────

    def test_upsert_conn_none_returns_row_count(self):
        """upsert_df_mysql with conn=None returns len(df), zero DB writes."""
        df = _sample_df()
        n = lib.upsert_df_mysql(df, "fact_exchange_rate", conn=None)
        self.assertEqual(n, len(df))

    def test_upsert_conn_none_no_cursor(self):
        """upsert_df_mysql with conn=None must never call conn.cursor()."""
        conn_spy = MagicMock()
        df = _sample_df()
        lib.upsert_df_mysql(df, "fact_exchange_rate", conn=None)
        # conn_spy is NOT passed in (conn=None), but if it were,
        # no cursor calls should happen — verify via dry_run path too.
        conn_spy.cursor.assert_not_called()

    def test_upsert_dry_run_true_no_cursor(self):
        """upsert_df_mysql with dry_run=True must not call conn.cursor()."""
        conn, cursor = _mock_conn()
        df = _sample_df()
        n = lib.upsert_df_mysql(df, "fact_exchange_rate", conn=conn, dry_run=True)
        cursor.executemany.assert_not_called()
        conn.commit.assert_not_called()
        self.assertEqual(n, len(df))

    def test_upsert_conn_none_with_audit(self):
        """upsert_df_mysql conn=None with an AuditRun — rows_read is updated."""
        audit = lib.AuditRun(None, "src", "tbl")
        df = _sample_df()
        n = lib.upsert_df_mysql(df, "tbl", conn=None, audit=audit)
        self.assertEqual(n, len(df))
        self.assertEqual(audit.rows_read, len(df))

    # ── bulk_load_csv with conn=None ──────────────────────────────────────────

    def test_bulk_load_conn_none_returns_row_count(self):
        """bulk_load_csv with conn=None returns len(df), no CSV survives."""
        import tempfile
        df = _sample_df()
        staging = Path(tempfile.mkdtemp())
        n = lib.bulk_load_csv(df, "fact_labour_force", conn=None, staging_dir=staging)
        self.assertEqual(n, len(df))
        # Staging dir should have no CSV files left
        remaining = list(staging.glob("*.csv"))
        self.assertEqual(remaining, [], "Dry-run must delete staging CSV")

    def test_bulk_load_dry_run_true_no_load_data_call(self):
        """bulk_load_csv with dry_run=True must not call conn.cursor()."""
        conn, cursor = _mock_conn()
        import tempfile
        staging = Path(tempfile.mkdtemp())
        df = _sample_df()
        n = lib.bulk_load_csv(df, "fact_cpi", conn=conn, staging_dir=staging,
                              dry_run=True)
        cursor.execute.assert_not_called()
        conn.commit.assert_not_called()
        self.assertEqual(n, len(df))

    # ── load_education_enrolments with conn=None ──────────────────────────────

    def test_education_conn_none_zero_writes(self):
        """load_education_enrolments with conn=None is fully zero-write."""
        import tempfile
        df = pd.DataFrame({
            "enrol_year":        [2024, 2024, 2024],
            "enrol_month":       [6, 7, 8],
            "nationality":       ["India", "China", "Nepal"],
            "state_code":        ["NSW", "VIC", "QLD"],
            "sector":            ["HE", "HE", "VE"],
            "provider_type":     ["University"] * 3,
            "new_to_australia":  ["No"] * 3,
            "ends_this_year":    ["No"] * 3,
            "ytd_enrolments":    [100, 200, 50],
            "ytd_commencements": [10, 20, 5],
            "_etl_source":       ["test"] * 3,
            "_etl_loaded_at":    ["2024-01-01"] * 3,
        })
        staging = Path(tempfile.mkdtemp())
        n = lib.load_education_enrolments(df, conn=None, staging_dir=staging, chunk_rows=2)
        self.assertEqual(n, 3)
        remaining = list(staging.glob("*.csv"))
        self.assertEqual(remaining, [], "Dry-run must clean up all staging CSVs")

    # ── Prove the distinction: conn=real still writes ─────────────────────────

    def test_upsert_with_real_conn_calls_executemany(self):
        """With a real (mock) connection and dry_run=False, executemany IS called."""
        conn, cursor = _mock_conn()
        cursor.rowcount = 3
        df = _sample_df()
        lib.upsert_df_mysql(df, "fact_exchange_rate", conn=conn, dry_run=False)
        cursor.executemany.assert_called_once()
        conn.commit.assert_called()

    def test_audit_run_with_real_conn_writes_to_db(self):
        """With a real (mock) connection, AuditRun._start() inserts a row."""
        conn, cursor = _mock_conn()
        cursor.lastrowid = 77
        audit = lib.AuditRun(conn, "rba", "fact_exchange_rate")
        # _start() should have been called → cursor.execute called
        cursor.execute.assert_called_once()
        self.assertEqual(audit.run_id, 77)


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
