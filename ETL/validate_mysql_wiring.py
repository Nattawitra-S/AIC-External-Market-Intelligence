#!/usr/bin/env python3
"""
validate_mysql_wiring.py
=========================
Phase 4 quality gate — static validation that the MySQL-only pipeline is
correctly wired.

Verifies WITHOUT requiring a live MySQL server:
  1. run_mysql_sources.py exists
  2. All 7 run_mysql_*() functions are defined in it
  3. run_all.py is MySQL-only (has --dry-run, does NOT have --mysql or --db flags)
  4. run_all.py defines run_all() (the MySQL-only runner — not run_all_mysql)
  5. run_all.py CLI block calls run_all() directly (no conditional dispatch)
  6. run_mysql_sources.py can be imported with MySQL stubbed
  7. All 7 run_mysql_* functions callable after import
  8. Original 7 ETL source files are NOT importing lib_etl_mysql
     (they must remain SQLite-only — MySQL dispatch goes through run_mysql_sources)
  9. Column rename helpers _rename/_keep are defined in run_mysql_sources
 10. All 7 run_mysql functions reference the correct MySQL target tables

EXIT CODE: 0 = PASS, 1 = FAIL
"""

import ast
import importlib
import re
import sys
import types
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
ETL_DIR     = Path(__file__).parent
RUN_MYSQL   = ETL_DIR / "run_mysql_sources.py"
RUN_ALL     = ETL_DIR / "run_all.py"

ORIGINAL_ETL_SOURCES = [
    "etl_rba.py",
    "etl_cricos.py",
    "etl_jsa.py",
    "etl_home_affairs_extended.py",
    "etl_abs.py",
    "etl_education_v2.py",
    "etl_skilled_migration.py",
]

REQUIRED_MYSQL_FUNCTIONS = [
    "run_mysql_rba",
    "run_mysql_cricos",
    "run_mysql_jsa",
    "run_mysql_home_affairs",
    "run_mysql_abs",
    "run_mysql_education",
    "run_mysql_skilled_migration",
]

# MySQL target tables that should appear in the respective run_mysql_* function
EXPECTED_TABLE_REFS = {
    "run_mysql_rba":                  ["fact_exchange_rate"],
    "run_mysql_cricos":               ["dim_provider", "dim_course",
                                       "dim_provider_location", "bridge_course_location"],
    "run_mysql_jsa":                  ["fact_job_vacancy", "fact_occupation_shortage",
                                       "ref_occupation_profile"],
    "run_mysql_home_affairs":         ["fact_student_visa_activity", "fact_temp_skilled_visa",
                                       "fact_temp_graduate_visa", "fact_permanent_migration"],
    "run_mysql_abs":                  ["fact_labour_force", "fact_cpi",
                                       "fact_overseas_migration", "fact_population_by_cob"],
    "run_mysql_education":            ["fact_student_enrolment"],
    "run_mysql_skilled_migration":    ["fact_skilled_migration",
                                       "ref_skilled_migration_by_cob_occupation"],
}

# ─────────────────────────────────────────────────────────────────────────────

checks_passed = 0
checks_failed = 0
failures      = []


def ok(msg: str):
    global checks_passed
    checks_passed += 1
    print(f"  ✅  {msg}")


def fail(msg: str):
    global checks_failed
    failures.append(msg)
    checks_failed += 1
    print(f"  ❌  {msg}")


def get_top_level_functions(path: Path) -> set[str]:
    """Return set of top-level function names defined in a Python file (AST)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def run_checks():
    global checks_passed, checks_failed

    print(f"\n{'='*60}")
    print("Phase 4 — MySQL Wiring Validator")
    print(f"{'='*60}\n")

    # ── Check 1: run_mysql_sources.py exists ──────────────────────────────────
    print("Check 1: run_mysql_sources.py exists")
    if RUN_MYSQL.exists():
        ok("ETL/run_mysql_sources.py exists")
    else:
        fail("ETL/run_mysql_sources.py NOT FOUND")
        print("\n❌  Cannot continue without run_mysql_sources.py\n")
        sys.exit(1)
    print()

    # ── Check 2: All 7 run_mysql_*() functions defined ────────────────────────
    print(f"Check 2: All {len(REQUIRED_MYSQL_FUNCTIONS)} run_mysql_*() functions defined")
    defined_fns = get_top_level_functions(RUN_MYSQL)
    for fn in REQUIRED_MYSQL_FUNCTIONS:
        if fn in defined_fns:
            ok(f"{fn}() defined")
        else:
            fail(f"{fn}() MISSING in run_mysql_sources.py")
    print()

    # ── Check 3: run_all.py is MySQL-only (no --mysql / --db dual-mode flags) ──
    print("Check 3: run_all.py is MySQL-only — has --dry-run, no --mysql or --db flags")
    if not RUN_ALL.exists():
        fail("ETL/run_all.py NOT FOUND")
    else:
        raw = RUN_ALL.read_text(encoding="utf-8")
        has_dry_run = bool(re.search(r'add_argument\(["\']--dry-run["\']', raw))
        has_mysql_flag = bool(re.search(r'add_argument\(["\']--mysql["\']', raw))
        has_db_flag    = bool(re.search(r'add_argument\(["\']--db["\']',    raw))
        if has_dry_run and not has_mysql_flag and not has_db_flag:
            ok("run_all.py is MySQL-only: has --dry-run, no --mysql or --db flag ✓")
        else:
            if not has_dry_run:
                fail("--dry-run argument NOT found in run_all.py")
            if has_mysql_flag:
                fail("--mysql flag still present in run_all.py (must be removed — MySQL is the only target)")
            if has_db_flag:
                fail("--db flag still present in run_all.py (must be removed — MySQL is the only target)")
    print()

    # ── Check 4: run_all.py defines run_all() (the MySQL-only runner) ─────────
    print("Check 4: run_all.py defines run_all() (MySQL-only runner)")
    run_all_fns = get_top_level_functions(RUN_ALL)
    if "run_all" in run_all_fns:
        ok("run_all() defined in run_all.py ✓")
    else:
        fail("run_all() NOT found in run_all.py")
    if "run_all_mysql" in run_all_fns:
        fail("run_all_mysql() still present — dual-mode remnant must be removed")
    else:
        ok("run_all_mysql() absent (correct — MySQL-only architecture) ✓")
    print()

    # ── Check 5: run_all.py CLI block calls run_all() directly ────────────────
    print("Check 5: run_all.py CLI block calls run_all() directly (no conditional dispatch)")
    raw = RUN_ALL.read_text(encoding="utf-8")
    has_direct_call   = bool(re.search(r"results\s*=\s*run_all\s*\(", raw))
    has_mysql_dispatch = bool(re.search(r"if\s+args\.mysql", raw))
    if has_direct_call and not has_mysql_dispatch:
        ok("CLI calls run_all() directly — no conditional MySQL dispatch ✓")
    else:
        if not has_direct_call:
            fail("run_all() not called directly from CLI block")
        if has_mysql_dispatch:
            fail("'if args.mysql' dispatch block still present — dual-mode remnant")
    print()

    # ── Check 6: run_mysql_sources.py can be imported (with MySQL stubbed) ────
    print("Check 6: run_mysql_sources.py importable with MySQL stubbed")
    sys.path.insert(0, str(BASE_DIR))

    # Stub mysql.connector so import doesn't require the package
    mysql_stub = types.ModuleType("mysql")
    connector_stub = types.ModuleType("mysql.connector")

    class _MySQLError(Exception):
        def __init__(self, msg="", errno=0):
            super().__init__(msg)
            self.errno = errno

    connector_stub.Error = _MySQLError
    mysql_stub.connector = connector_stub
    sys.modules.setdefault("mysql", mysql_stub)
    sys.modules.setdefault("mysql.connector", connector_stub)
    sys.modules.setdefault("mysql.connector.pooling", types.ModuleType("mysql.connector.pooling"))

    # Also stub dotenv
    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")
        dotenv_stub.load_dotenv = lambda *a, **kw: None
        sys.modules["dotenv"] = dotenv_stub

    try:
        if "ETL.run_mysql_sources" in sys.modules:
            del sys.modules["ETL.run_mysql_sources"]
        if "ETL.lib_etl_mysql" in sys.modules:
            del sys.modules["ETL.lib_etl_mysql"]

        # lib_etl_mysql imports mysql.connector at top level; stub it, then import
        import ETL.lib_etl_mysql  # noqa: F401 — side-effect import to pre-load with stub
        import ETL.run_mysql_sources as rms   # noqa: F401

        # Check all 7 functions are callable after import
        for fn in REQUIRED_MYSQL_FUNCTIONS:
            if hasattr(rms, fn) and callable(getattr(rms, fn)):
                ok(f"{fn} importable and callable")
            else:
                fail(f"{fn} not callable after import")
    except Exception as e:
        fail(f"Import failed: {e}")
    print()

    # ── Check 7: Helper functions _rename/_keep present ──────────────────────
    print("Check 7: _rename() and _keep() helpers defined")
    helper_fns = get_top_level_functions(RUN_MYSQL)
    for fn in ["_rename", "_keep"]:
        if fn in helper_fns:
            ok(f"{fn}() defined")
        else:
            fail(f"{fn}() MISSING in run_mysql_sources.py")
    print()

    # ── Check 8: Original ETL source files do NOT import lib_etl_mysql ────────
    print("Check 8: Original ETL source files are SQLite-only (no lib_etl_mysql import)")
    for fname in ORIGINAL_ETL_SOURCES:
        path = ETL_DIR / fname
        if not path.exists():
            ok(f"{fname}: file not found (skip check)")
            continue
        src = path.read_text(encoding="utf-8")
        if "lib_etl_mysql" in src:
            fail(f"{fname}: imports lib_etl_mysql (must not — SQLite source files must remain unchanged)")
        else:
            ok(f"{fname}: no lib_etl_mysql import ✓")
    print()

    # ── Check 9: MySQL target table names referenced in each function ─────────
    print("Check 9: MySQL target tables referenced in each run_mysql_*() function body")
    rms_src = RUN_MYSQL.read_text(encoding="utf-8")

    # Parse function bodies from AST
    tree = ast.parse(rms_src)
    fn_src = {}
    lines  = rms_src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in EXPECTED_TABLE_REFS:
            # Extract function body source lines
            start = node.lineno - 1
            end   = max((n.end_lineno for n in ast.walk(node) if hasattr(n, "end_lineno")),
                        default=start + 1)
            fn_src[node.name] = "\n".join(lines[start:end])

    for fn_name, expected_tables in EXPECTED_TABLE_REFS.items():
        body = fn_src.get(fn_name, "")
        if not body:
            fail(f"{fn_name}: function body not found in AST")
            continue
        for tbl in expected_tables:
            if tbl in body:
                ok(f"{fn_name} → \"{tbl}\" referenced ✓")
            else:
                fail(f"{fn_name}: \"{tbl}\" NOT referenced in function body")
    print()

    # ── Check 10: ABS MySQL mode excludes forbidden flows ─────────────────────
    print("Check 10: run_mysql_abs() excludes lf_industry, lf_occupation, edu_output")
    abs_body = fn_src.get("run_mysql_abs", rms_src)
    for forbidden_flow in ["lf_industry", "lf_occupation", "edu_output"]:
        if forbidden_flow in abs_body:
            fail(f"run_mysql_abs() includes forbidden flow: {forbidden_flow}")
        else:
            ok(f"run_mysql_abs(): {forbidden_flow} excluded ✓")
    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 60)
    total = checks_passed + checks_failed
    print(f"Results: {checks_passed}/{total} checks PASSED")

    if checks_failed == 0:
        print("\n✅  QUALITY GATE 4 — PASS\n")
        return 0
    else:
        print(f"\n❌  QUALITY GATE 4 — FAIL  ({checks_failed} failures)\n")
        print("Failures:")
        for f in failures:
            print(f"  • {f}")
        return 1


if __name__ == "__main__":
    sys.exit(run_checks())
