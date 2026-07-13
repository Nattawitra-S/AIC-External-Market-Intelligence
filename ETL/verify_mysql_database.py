#!/usr/bin/env python3
"""
verify_mysql_database.py
=========================
Post-deployment database verifier for AIC MySQL schema.

Connects to MySQL using .env credentials and runs:
  1. Table existence checks (all 25 required tables)
  2. Row-count sanity (every fact table must have rows)
  3. Referential integrity spot-checks
  4. Measure-column value checks
  5. NULL-key checks on business columns
  6. ETL audit log status
  7. vw_occupation_intelligence accessibility

EXIT CODE: 0 = PASS, 1 = FAIL
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from ETL.lib_etl_mysql import get_mysql_conn, _mysql_config
except Exception as e:
    print(f"Import error: {e}")
    sys.exit(1)

checks_passed = 0
checks_failed = 0
failures = []


def ok(msg: str):
    global checks_passed
    checks_passed += 1
    print(f"  ✅  {msg}")


def fail(msg: str):
    global checks_failed
    failures.append(msg)
    checks_failed += 1
    print(f"  ❌  {msg}")


def q(conn, sql: str):
    cur = conn.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    cur.close()
    return row[0] if row else 0


REQUIRED_TABLES = [
    "etl_audit_log", "dim_country", "dim_state", "dim_occupation",
    "dim_visa_subclass", "dim_provider", "dim_course", "dim_provider_location",
    "fact_exchange_rate", "fact_student_enrolment", "fact_student_visa_activity",
    "fact_temp_skilled_visa", "fact_temp_graduate_visa", "fact_permanent_migration",
    "fact_skilled_migration", "fact_job_vacancy", "fact_occupation_shortage",
    "fact_labour_force", "fact_cpi", "fact_overseas_migration",
    "fact_population_by_cob", "ref_occupation_profile",
    "ref_skilled_migration_by_cob_occupation", "bridge_course_location",
    "stg_skillselect_eoi",
]

FACT_TABLES = [
    "fact_exchange_rate", "fact_student_enrolment", "fact_student_visa_activity",
    "fact_temp_skilled_visa", "fact_temp_graduate_visa", "fact_permanent_migration",
    "fact_skilled_migration", "fact_job_vacancy", "fact_occupation_shortage",
    "fact_labour_force", "fact_cpi", "fact_overseas_migration",
    "fact_population_by_cob",
]


def run_checks():
    cfg = _mysql_config()
    db = cfg["database"]

    print(f"\n{'='*60}")
    print(f"AIC MySQL Database Verifier")
    print(f"Database: {cfg['host']}:{cfg['port']}/{db}")
    print(f"{'='*60}\n")

    conn = get_mysql_conn()

    # ── Check 1: All 25 tables exist ──────────────────────────────────────────
    print("Check 1: All 25 required tables exist")
    existing = set(r[0] for r in conn.cursor().execute(
        f"SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{db}' AND TABLE_TYPE='BASE TABLE'"
    ) or [])
    # Use proper cursor
    cur = conn.cursor()
    cur.execute(
        f"SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'", (db,)
    )
    existing = {r[0] for r in cur.fetchall()}
    cur.close()

    for t in REQUIRED_TABLES:
        if t in existing:
            ok(f"{t}")
        else:
            fail(f"MISSING table: {t}")
    print()

    # ── Check 2: Row counts on fact tables ─────────────────────────────────────
    print("Check 2: Fact tables have data (row count > 0)")
    for t in FACT_TABLES:
        if t not in existing:
            fail(f"{t}: table missing")
            continue
        n = q(conn, f"SELECT COUNT(*) FROM `{t}`")
        if n > 0:
            ok(f"{t}: {n:,} rows")
        else:
            fail(f"{t}: EMPTY (0 rows) — ETL may not have run")
    print()

    # ── Check 3: dim_state seeded ─────────────────────────────────────────────
    print("Check 3: dim_state seed data")
    n = q(conn, "SELECT COUNT(*) FROM dim_state")
    if n >= 9:
        ok(f"dim_state: {n} rows seeded")
    else:
        fail(f"dim_state: only {n} rows — expected ≥9")
    print()

    # ── Check 4: Measure consolidation ────────────────────────────────────────
    print("Check 4: Measure column values")
    m = q(conn, "SELECT COUNT(DISTINCT measure) FROM fact_student_visa_activity")
    if m >= 3:
        ok(f"fact_student_visa_activity: {m} distinct measures (lodged, granted, grant_rate_pct)")
    else:
        fail(f"fact_student_visa_activity: only {m} distinct measures (expected 3)")

    m = q(conn, "SELECT COUNT(DISTINCT measure) FROM fact_temp_skilled_visa")
    if m >= 2:
        ok(f"fact_temp_skilled_visa: {m} distinct measures (granted, holders)")
    else:
        fail(f"fact_temp_skilled_visa: only {m} distinct measures (expected 2)")

    m = q(conn, "SELECT COUNT(DISTINCT measure) FROM fact_temp_graduate_visa")
    if m >= 2:
        ok(f"fact_temp_graduate_visa: {m} distinct measures (lodged, granted)")
    else:
        fail(f"fact_temp_graduate_visa: only {m} distinct measures (expected 2)")
    print()

    # ── Check 5: No NULLs in business key columns ─────────────────────────────
    print("Check 5: No NULLs in business key columns")
    null_checks = [
        ("fact_exchange_rate",     "rate_date"),
        ("fact_skilled_migration", "financial_year"),
        ("fact_labour_force",      "lf_period"),
        ("fact_cpi",               "cpi_period"),
        ("dim_provider",           "provider_id"),
        ("dim_course",             "cricos_code"),
    ]
    for tbl, col in null_checks:
        if tbl not in existing:
            continue
        n = q(conn, f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}` IS NULL")
        if n == 0:
            ok(f"{tbl}.{col}: no NULLs ✓")
        else:
            fail(f"{tbl}.{col}: {n} NULL values")
    print()

    # ── Check 6: Referential integrity ────────────────────────────────────────
    print("Check 6: Referential integrity spot-checks")
    if "bridge_course_location" in existing and "dim_course" in existing:
        n = q(conn, """
            SELECT COUNT(*) FROM bridge_course_location b
            LEFT JOIN dim_course c ON b.cricos_code = c.cricos_code
            WHERE c.cricos_code IS NULL
        """)
        if n == 0:
            ok("bridge_course_location: all cricos_codes in dim_course")
        else:
            fail(f"bridge_course_location: {n} orphan cricos_codes")

    if "bridge_course_location" in existing and "dim_provider" in existing:
        n = q(conn, """
            SELECT COUNT(*) FROM bridge_course_location b
            LEFT JOIN dim_provider p ON b.provider_id = p.provider_id
            WHERE p.provider_id IS NULL
        """)
        if n == 0:
            ok("bridge_course_location: all provider_ids in dim_provider")
        else:
            fail(f"bridge_course_location: {n} orphan provider_ids")
    print()

    # ── Check 7: ETL audit log ─────────────────────────────────────────────────
    print("Check 7: ETL audit log")
    n_comp = q(conn, "SELECT COUNT(*) FROM etl_audit_log WHERE status='completed'")
    n_fail = q(conn, "SELECT COUNT(*) FROM etl_audit_log WHERE status='failed'")
    if n_comp > 0:
        ok(f"etl_audit_log: {n_comp} completed runs")
    else:
        fail("etl_audit_log: no completed runs recorded")
    if n_fail == 0:
        ok("etl_audit_log: no failed runs")
    else:
        fail(f"etl_audit_log: {n_fail} failed run(s) — check error_message column")
    print()

    # ── Check 8: vw_occupation_intelligence view ───────────────────────────────
    print("Check 8: vw_occupation_intelligence view")
    try:
        n = q(conn, "SELECT COUNT(*) FROM vw_occupation_intelligence LIMIT 1")
        ok(f"vw_occupation_intelligence: accessible ({n:,} rows)")
    except Exception as e:
        fail(f"vw_occupation_intelligence: {e}")
    print()

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 60)
    total = checks_passed + checks_failed
    print(f"Results: {checks_passed}/{total} checks PASSED")
    if checks_failed == 0:
        print("\n✅  DATABASE VERIFICATION — PASS\n")
        return 0
    else:
        print(f"\n❌  DATABASE VERIFICATION — FAIL  ({checks_failed} failures)\n")
        for f in failures:
            print(f"  • {f}")
        return 1


if __name__ == "__main__":
    sys.exit(run_checks())
