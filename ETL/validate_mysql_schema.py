#!/usr/bin/env python3
"""
validate_mysql_schema.py
=========================
Static validation of ETL/schema_mysql.sql without requiring a live MySQL server.

Checks:
  1. No SQLite-only syntax (AUTOINCREMENT, PRAGMA, .executescript)
  2. No duplicate CREATE TABLE names
  3. Expected table count (25 base tables)
  4. Required tables present (fact_permanent_migration, stg_skillselect_eoi, etc.)
  5. Forbidden tables absent (fact_abs_education_output, legacy SQLite tables)
  6. Expected UNIQUE KEY / PRIMARY KEY patterns
  7. Audit table (etl_audit_log) present with required columns
  8. Generated columns present for nullable key tables
  9. No inline COALESCE() in UNIQUE KEY definitions (invalid syntax)
 10. Expected seed INSERT statements

EXIT CODE: 0 = PASS, 1 = FAIL
"""

import re
import sys
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
SCHEMA_FILE = Path(__file__).parent / "schema_mysql.sql"

# ─── Expected configuration ───────────────────────────────────────────────────

EXPECTED_TABLE_COUNT = 25

REQUIRED_TABLES = [
    "etl_audit_log",
    "dim_country",
    "dim_state",
    "dim_occupation",
    "dim_visa_subclass",
    "dim_provider",
    "dim_course",
    "dim_provider_location",
    "fact_exchange_rate",
    "fact_student_enrolment",
    "fact_student_visa_activity",
    "fact_temp_skilled_visa",
    "fact_temp_graduate_visa",
    "fact_permanent_migration",
    "fact_skilled_migration",
    "fact_job_vacancy",
    "fact_occupation_shortage",
    "fact_labour_force",
    "fact_cpi",
    "fact_overseas_migration",
    "fact_population_by_cob",
    "ref_occupation_profile",
    "ref_skilled_migration_by_cob_occupation",
    "bridge_course_location",
    "stg_skillselect_eoi",
]

FORBIDDEN_TABLES = [
    "fact_abs_education_output",   # removed per decision #4
    "occupation_ceilings",         # legacy SQLite
    "occupation_shortage_ratings", # legacy SQLite
    "visa_eligibility",            # legacy SQLite
    "occupation_intelligence",     # decision #8 — view only
    "ha_migration_child_outcomes", # renamed to fact_permanent_migration
    "ha_student_visa_lodged",      # consolidated into fact_student_visa_activity
    "ha_student_visa_granted",     # consolidated
    "ha_student_visa_grant_rates", # consolidated
    "ha_temp_skilled_visa_granted",# consolidated into fact_temp_skilled_visa
    "ha_temp_skilled_visa_holders",# consolidated
    "ha_temp_graduate_visa_lodged",# consolidated into fact_temp_graduate_visa
    "ha_temp_graduate_visa_granted",
    "cricos_institutions",         # renamed to dim_provider
    "cricos_courses",              # renamed to dim_course
    "cricos_locations",            # renamed to dim_provider_location
    "cricos_course_locations",     # renamed to bridge_course_location
    "rba_exchange_rates",          # renamed to fact_exchange_rate
    "abs_labour_force",            # renamed to fact_labour_force
    "abs_cpi",                     # renamed to fact_cpi
    "abs_net_overseas_migration",  # renamed to fact_overseas_migration
    "abs_erp_country_of_birth",    # renamed to fact_population_by_cob
    "abs_education_output",        # excluded
    "abs_employment_by_industry",  # not in MySQL schema (ABS sub-dataset)
    "abs_employment_by_occupation",# not in MySQL schema
    "jsa_internet_vacancies",      # renamed to fact_job_vacancy
    "jsa_occupation_shortage",     # renamed to fact_occupation_shortage
    "jsa_occupation_profiles",     # renamed to ref_occupation_profile
    "education_int_students_historical", # excluded from MySQL initial load
    "education_sa4_enrolments",    # excluded from MySQL initial load
    "skilled_migration_summary",   # renamed to fact_skilled_migration
    "skilled_migration_country_occupation", # renamed to ref_skilled_migration_by_cob_occupation
    "skillselect_eoi_data",        # renamed to stg_skillselect_eoi
]

SQLITE_FORBIDDEN_PATTERNS = [
    (r"\bAUTOINCREMENT\b",    "SQLite AUTOINCREMENT — use AUTO_INCREMENT"),
    (r"\bPRAGMA\b",           "SQLite PRAGMA"),
    (r"executescript",        "SQLite executescript"),
    (r"\.executemany\s*\(",   "SQLite executemany (use MySQL executemany)"),
    (r"INTEGER PRIMARY KEY(?!\s+AUTO)", "SQLite INTEGER PRIMARY KEY (missing AUTO_INCREMENT)"),
]

REQUIRED_GENERATED_COLS = {
    # table → [list of generated column name patterns]
    "fact_skilled_migration":           ["visa_subclass_k", "stream_k", "state_k"],
    "ref_skilled_migration_by_cob_occupation": ["country_name_k", "anzsco_code_k", "visa_subclass_k"],
    "fact_job_vacancy":                 ["anzsco_code_k"],
    "fact_occupation_shortage":         ["assessment_year_k"],
    "fact_overseas_migration":          ["country_name_k"],
    "fact_population_by_cob":          ["country_name_k"],
    "ref_occupation_profile":           ["profile_year_k"],
    "stg_skillselect_eoi":             ["dim1_val_k", "dim2_val_k"],
}

REQUIRED_SEED_TABLES = ["dim_state", "dim_visa_subclass"]

REQUIRED_AUDIT_COLUMNS = ["run_id", "source", "table_name", "started_at", "status", "rows_inserted"]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def strip_comments(sql: str) -> str:
    """Remove -- and /* */ comments."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def extract_tables(sql: str) -> list[str]:
    """Return list of table names from CREATE TABLE statements."""
    return re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?",
        sql, flags=re.IGNORECASE
    )


def extract_views(sql: str) -> list[str]:
    return re.findall(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+`?(\w+)`?",
        sql, flags=re.IGNORECASE
    )


def find_table_block(sql: str, table_name: str) -> str:
    """Return the CREATE TABLE ... ; block for a given table."""
    pattern = rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?\s*\(.*?\)\s*[^;]*;"
    m = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    return m.group(0) if m else ""


# ─── Check functions ─────────────────────────────────────────────────────────

checks_passed = 0
checks_failed = 0
failures = []

def ok(msg: str):
    global checks_passed
    checks_passed += 1
    print(f"  ✅  {msg}")

def fail(msg: str):
    global checks_failed
    checks_failed += 1
    failures.append(msg)
    print(f"  ❌  {msg}")


def run_checks():
    if not SCHEMA_FILE.exists():
        print(f"FATAL: Schema file not found: {SCHEMA_FILE}")
        sys.exit(1)

    raw_sql = SCHEMA_FILE.read_text(encoding="utf-8")
    clean_sql = strip_comments(raw_sql)
    tables = extract_tables(clean_sql)
    views  = extract_views(clean_sql)

    print(f"\n{'='*60}")
    print(f"Validating: {SCHEMA_FILE}")
    print(f"Tables found: {len(tables)}  |  Views found: {len(views)}")
    print(f"{'='*60}\n")

    # ── Check 1: No SQLite-only syntax ────────────────────────────────────────
    print("Check 1: No SQLite-only syntax")
    for pattern, label in SQLITE_FORBIDDEN_PATTERNS:
        if re.search(pattern, raw_sql, re.IGNORECASE):
            fail(f"Found forbidden pattern: {label}  [{pattern}]")
        else:
            ok(f"Not found: {label}")
    print()

    # ── Check 2: No duplicate table names ─────────────────────────────────────
    print("Check 2: No duplicate CREATE TABLE names")
    seen = {}
    for t in tables:
        seen[t] = seen.get(t, 0) + 1
    dups = {t: c for t, c in seen.items() if c > 1}
    if dups:
        fail(f"Duplicate table names: {list(dups.keys())}")
    else:
        ok(f"No duplicates — {len(tables)} unique CREATE TABLE statements")
    print()

    # ── Check 3: Expected table count ─────────────────────────────────────────
    print(f"Check 3: Expected {EXPECTED_TABLE_COUNT} base tables")
    if len(tables) == EXPECTED_TABLE_COUNT:
        ok(f"Table count = {len(tables)} ✓")
    else:
        fail(f"Table count = {len(tables)}, expected {EXPECTED_TABLE_COUNT}")
        missing = set(REQUIRED_TABLES) - set(tables)
        extra   = set(tables) - set(REQUIRED_TABLES)
        if missing:
            print(f"      Missing: {sorted(missing)}")
        if extra:
            print(f"      Extra:   {sorted(extra)}")
    print()

    # ── Check 4: Required tables present ─────────────────────────────────────
    print(f"Check 4: All {len(REQUIRED_TABLES)} required tables present")
    for t in REQUIRED_TABLES:
        if t in tables:
            ok(f"{t}")
        else:
            fail(f"MISSING table: {t}")
    print()

    # ── Check 5: Forbidden tables absent ─────────────────────────────────────
    print("Check 5: Forbidden tables absent")
    for t in FORBIDDEN_TABLES:
        if t in tables:
            fail(f"Forbidden table still present: {t}")
        else:
            ok(f"Absent: {t}")
    print()

    # ── Check 6: No raw COALESCE() in UNIQUE KEY inline expressions ──────────
    print("Check 6: No COALESCE() expressions inside UNIQUE KEY definitions")
    # Look for pattern: UNIQUE KEY ... COALESCE in same context
    # Valid: GENERATED ALWAYS AS (COALESCE(...)) STORED
    # Invalid: UNIQUE KEY uk (col1, COALESCE(col2,''), col3)
    # Pattern: UNIQUE KEY followed by ( containing COALESCE before the UNIQUE KEY closes
    uk_blocks = re.findall(r"UNIQUE\s+KEY\s+\w+\s*\([^)]+\)", clean_sql, re.IGNORECASE)
    coalesce_in_uk = [b for b in uk_blocks if "COALESCE" in b.upper()]
    if coalesce_in_uk:
        for b in coalesce_in_uk:
            fail(f"COALESCE() in UNIQUE KEY: {b[:80]}...")
    else:
        ok("No raw COALESCE() expressions in UNIQUE KEY definitions")
    print()

    # ── Check 7: Audit table has required columns ─────────────────────────────
    print("Check 7: etl_audit_log has required columns")
    audit_block = find_table_block(clean_sql, "etl_audit_log")
    if not audit_block:
        fail("etl_audit_log CREATE TABLE block not found")
    else:
        for col in REQUIRED_AUDIT_COLUMNS:
            if re.search(rf"\b{col}\b", audit_block, re.IGNORECASE):
                ok(f"etl_audit_log.{col} present")
            else:
                fail(f"etl_audit_log.{col} MISSING")
    print()

    # ── Check 8: Generated columns present in tables that need them ───────────
    print("Check 8: Generated columns present for nullable key tables")
    for tbl, gen_cols in REQUIRED_GENERATED_COLS.items():
        block = find_table_block(clean_sql, tbl)
        if not block:
            fail(f"Table block not found: {tbl}")
            continue
        for gc in gen_cols:
            if re.search(rf"\b{gc}\b", block, re.IGNORECASE):
                if re.search(r"GENERATED\s+ALWAYS\s+AS", block, re.IGNORECASE):
                    ok(f"{tbl}.{gc} (GENERATED)")
                else:
                    fail(f"{tbl}.{gc} present but not GENERATED")
            else:
                fail(f"{tbl}.{gc} MISSING — required generated column")
    print()

    # ── Check 9: fact_permanent_migration present (not ha_migration_child_outcomes) ──
    print("Check 9: Correct BP0068 table name")
    if "fact_permanent_migration" in tables:
        ok("fact_permanent_migration exists ✓")
    else:
        fail("fact_permanent_migration NOT FOUND")
    if "ha_migration_child_outcomes" in tables:
        fail("Old name ha_migration_child_outcomes still present — must be removed")
    else:
        ok("ha_migration_child_outcomes absent ✓")
    print()

    # ── Check 10: Seed data present ──────────────────────────────────────────
    print("Check 10: Seed INSERT statements present")
    for tbl in REQUIRED_SEED_TABLES:
        if re.search(rf"INSERT\s+(?:IGNORE\s+)?INTO\s+{tbl}", raw_sql, re.IGNORECASE):
            ok(f"INSERT IGNORE INTO {tbl} found")
        else:
            fail(f"Missing seed INSERT for {tbl}")
    print()

    # ── Check 11: stg_skillselect_eoi present (staging, not promoted) ─────────
    print("Check 11: stg_skillselect_eoi present")
    if "stg_skillselect_eoi" in tables:
        ok("stg_skillselect_eoi present ✓")
    else:
        fail("stg_skillselect_eoi MISSING")
    if "fact_skillselect_eoi" in tables:
        fail("fact_skillselect_eoi should not exist — staging only")
    else:
        ok("fact_skillselect_eoi absent ✓ (staging only)")
    print()

    # ── Check 12: Education table uses YTD naming ─────────────────────────────
    print("Check 12: Education YTD table naming consistent")
    if "fact_student_enrolment" in tables:
        ok("fact_student_enrolment present ✓")
        edu_block = find_table_block(clean_sql, "fact_student_enrolment")
        if "ytd_enrolments" in edu_block:
            ok("ytd_enrolments column present ✓")
        else:
            fail("ytd_enrolments column missing in fact_student_enrolment")
    else:
        fail("fact_student_enrolment MISSING")
    print()

    # ── Check 13: AUTO_INCREMENT used (not AUTOINCREMENT) ────────────────────
    # Note: dim_provider, dim_course, dim_provider_location use natural varchar PKs
    # (CRICOS codes), so 3 of the 25 tables legitimately omit AUTO_INCREMENT.
    MIN_AUTO_INCREMENT = EXPECTED_TABLE_COUNT - 3  # = 22
    print("Check 13: AUTO_INCREMENT syntax (MySQL, not SQLite)")
    ai_count = len(re.findall(r"\bAUTO_INCREMENT\b", raw_sql, re.IGNORECASE))
    if ai_count >= MIN_AUTO_INCREMENT:
        ok(f"AUTO_INCREMENT found {ai_count} times (≥{MIN_AUTO_INCREMENT} required — 3 tables use natural varchar PKs)")
    else:
        fail(f"Only {ai_count} AUTO_INCREMENT — expected at least {MIN_AUTO_INCREMENT}")
    print()

    # ── Check 14: InnoDB ENGINE used ─────────────────────────────────────────
    print("Check 14: ENGINE=InnoDB on all tables")
    innodb_count = len(re.findall(r"ENGINE\s*=\s*InnoDB", raw_sql, re.IGNORECASE))
    if innodb_count >= EXPECTED_TABLE_COUNT:
        ok(f"ENGINE=InnoDB on {innodb_count} tables")
    else:
        fail(f"ENGINE=InnoDB found {innodb_count} times — expected {EXPECTED_TABLE_COUNT}")
    print()

    # ── Check 15: utf8mb4 charset ────────────────────────────────────────────
    print("Check 15: utf8mb4 charset")
    utf8_count = len(re.findall(r"utf8mb4", raw_sql, re.IGNORECASE))
    if utf8_count >= EXPECTED_TABLE_COUNT:
        ok(f"utf8mb4 referenced {utf8_count} times")
    else:
        fail(f"utf8mb4 only {utf8_count} times — expected {EXPECTED_TABLE_COUNT}")
    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 60)
    total = checks_passed + checks_failed
    print(f"Results: {checks_passed}/{total} checks PASSED")

    if checks_failed == 0:
        print("\n✅  QUALITY GATE 2 — PASS\n")
        return 0
    else:
        print(f"\n❌  QUALITY GATE 2 — FAIL  ({checks_failed} failures)\n")
        print("Failures:")
        for f in failures:
            print(f"  • {f}")
        return 1


if __name__ == "__main__":
    sys.exit(run_checks())
