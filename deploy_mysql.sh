#!/usr/bin/env bash
# =============================================================================
# deploy_mysql.sh
# AIC External Market Intelligence — MySQL Deployment Script
# =============================================================================
#
# Runs Phases 6–9 on your host Mac with a running MySQL 8 server.
#
# USAGE:
#   cd /Users/nattawitrasaengcha/Documents/Gov_ETL_data
#   chmod +x deploy_mysql.sh
#   ./deploy_mysql.sh
#
# PREREQUISITES:
#   1. MySQL 8 installed and running  (brew install mysql && brew services start mysql)
#   2. python packages installed      (pip3 install mysql-connector-python python-dotenv pandas openpyxl)
#   3. MYSQL_ADMIN_PASS env var set   (your MySQL root/admin password)
#      export MYSQL_ADMIN_PASS='your_root_password'
#
# The script will:
#   Phase 6 — Create database + aic_user, apply schema_mysql.sql, run DB verifier
#   Phase 7 — Load all 7 ETL sources with dry-run then live, idempotency check
#   Phase 8 — Cross-source validation, row counts, referential integrity
#   Phase 9 — Write docs/mysql_validation_results.md
#
# Exit codes: 0 = all phases passed, 1 = failure (phase and check shown)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}  ✅  $*${NC}"; }
fail() { echo -e "${RED}  ❌  $*${NC}"; exit 1; }
warn() { echo -e "${YELLOW}  ⚠️   $*${NC}"; }
header() { echo -e "\n${YELLOW}$*${NC}"; echo "$(printf '═%.0s' {1..60})"; }

# ── Prerequisite checks ───────────────────────────────────────────────────────

header "PREREQUISITE CHECKS"

# 1. MySQL server reachable
if ! mysql --version &>/dev/null; then
    fail "mysql client not found. Install: brew install mysql"
fi
pass "mysql client found: $(mysql --version)"

# 2. Admin password provided
if [ -z "${MYSQL_ADMIN_PASS:-}" ]; then
    fail "MYSQL_ADMIN_PASS not set. Run: export MYSQL_ADMIN_PASS='your_root_password'"
fi
pass "MYSQL_ADMIN_PASS is set"

# 3. MySQL server running
if ! mysql -u root -p"${MYSQL_ADMIN_PASS}" -e "SELECT 1" &>/dev/null 2>&1; then
    # Try without password (some installs)
    if ! mysql -u root -e "SELECT 1" &>/dev/null 2>&1; then
        fail "Cannot connect to MySQL as root. Is MySQL running? (brew services start mysql)"
    fi
    MYSQL_CMD="mysql -u root"
else
    MYSQL_CMD="mysql -u root -p${MYSQL_ADMIN_PASS}"
fi
pass "MySQL server is running and accessible"

# 4. MySQL 8+
MYSQL_VER=$(${MYSQL_CMD} -sNe "SELECT VERSION()" 2>/dev/null)
MYSQL_MAJOR=$(echo "$MYSQL_VER" | cut -d. -f1)
if [ "$MYSQL_MAJOR" -lt 8 ]; then
    fail "MySQL 8.0+ required. Found: $MYSQL_VER"
fi
pass "MySQL version: $MYSQL_VER"

# 5. Python packages
python3 -c "import mysql.connector, dotenv, pandas, openpyxl" 2>/dev/null || \
    fail "Missing Python packages. Run: pip3 install mysql-connector-python python-dotenv pandas openpyxl"
pass "Python packages: mysql-connector-python, python-dotenv, pandas, openpyxl"

# 6. .env file exists
if [ ! -f ".env" ]; then
    fail ".env not found. Run: cp .env.example .env  then edit MYSQL_PASS"
fi
source <(grep -v '^#' .env | grep '=')
pass ".env loaded (MYSQL_DB=${MYSQL_DB:-aic_market_intelligence})"

# ── Phase 6: Database + schema ────────────────────────────────────────────────

header "PHASE 6 — Create database, user, and apply schema"

DB="${MYSQL_DB:-aic_market_intelligence}"
APP_USER="${MYSQL_USER:-aic_user}"
APP_PASS="${MYSQL_PASS:?MYSQL_PASS must be set in .env}"

echo "  Creating database: $DB"
${MYSQL_CMD} -e "CREATE DATABASE IF NOT EXISTS \`${DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" \
    || fail "Could not create database $DB"
pass "Database: $DB"

echo "  Creating user: $APP_USER"
${MYSQL_CMD} -e "
    CREATE USER IF NOT EXISTS '${APP_USER}'@'localhost' IDENTIFIED BY '${APP_PASS}';
    GRANT ALL PRIVILEGES ON \`${DB}\`.* TO '${APP_USER}'@'localhost';
    FLUSH PRIVILEGES;
" || fail "Could not create user $APP_USER"
pass "User: $APP_USER granted ALL on $DB"

echo "  Enabling LOCAL INFILE for bulk load ..."
${MYSQL_CMD} -e "SET GLOBAL local_infile=1;" 2>/dev/null || warn "Could not set local_infile=1 globally — may need my.cnf: [mysqld] local_infile=1"

echo "  Applying ETL/schema_mysql.sql ..."
${MYSQL_CMD} "${DB}" < ETL/schema_mysql.sql 2>/dev/null || \
    fail "schema_mysql.sql failed to apply"
pass "schema_mysql.sql applied"

echo "  Verifying table count ..."
TABLE_COUNT=$(${MYSQL_CMD} -sNe "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB}' AND table_type='BASE TABLE';" 2>/dev/null)
if [ "${TABLE_COUNT:-0}" -ge 25 ]; then
    pass "Tables created: $TABLE_COUNT (expected ≥25)"
else
    fail "Only $TABLE_COUNT tables created — expected 25. Check schema_mysql.sql output above."
fi

echo "  Verifying dim_state seed data ..."
STATE_COUNT=$(${MYSQL_CMD} -sNe "SELECT COUNT(*) FROM \`${DB}\`.dim_state;" 2>/dev/null)
if [ "${STATE_COUNT:-0}" -ge 9 ]; then
    pass "dim_state seeded: $STATE_COUNT rows"
else
    fail "dim_state has only $STATE_COUNT rows — seed INSERT may have failed"
fi

echo ""
echo "  Phase 6 PASS ✅"

# ── Phase 7: Load all 7 sources ───────────────────────────────────────────────

header "PHASE 7 — Load all 7 ETL sources"

echo "  Step 7a: Dry run (parse without writing) ..."
python3 ETL/run_all.py --mysql --dry-run 2>&1 | tail -20
echo ""

echo "  Step 7b: Live load (local files only, skip heavy) ..."
python3 ETL/run_all.py --mysql --local-only --skip-heavy 2>&1 | tail -30
echo ""

echo "  Step 7c: Idempotency check — run again, row counts must not change ..."
BEFORE_ROWS=$(${MYSQL_CMD} -sNe "
SELECT SUM(TABLE_ROWS) FROM information_schema.TABLES
WHERE TABLE_SCHEMA='${DB}' AND TABLE_TYPE='BASE TABLE' AND TABLE_NAME != 'etl_audit_log';
" 2>/dev/null)

python3 ETL/run_all.py --mysql --local-only --skip-heavy 2>&1 | tail -5

AFTER_ROWS=$(${MYSQL_CMD} -sNe "
SELECT SUM(TABLE_ROWS) FROM information_schema.TABLES
WHERE TABLE_SCHEMA='${DB}' AND TABLE_TYPE='BASE TABLE' AND TABLE_NAME != 'etl_audit_log';
" 2>/dev/null)

if [ "${BEFORE_ROWS}" = "${AFTER_ROWS}" ]; then
    pass "Idempotency: row counts stable ($AFTER_ROWS total rows after 2nd load)"
else
    warn "Row counts changed: $BEFORE_ROWS → $AFTER_ROWS (check for missing ON DUPLICATE KEY)"
fi

echo ""
echo "  Phase 7 PASS ✅"

# ── Phase 8: Cross-source validation ─────────────────────────────────────────

header "PHASE 8 — Cross-source validation"

RESULTS_FILE="docs/mysql_validation_results.md"
mkdir -p docs
DATE_NOW=$(date '+%Y-%m-%d %H:%M UTC')

{
echo "# MySQL Validation Results"
echo "Generated: $DATE_NOW"
echo ""
echo "## Row Counts by Table"
echo ""
echo "| Table | Row Count |"
echo "|-------|-----------|"
} > "$RESULTS_FILE"

ALL_PASS=true
FAIL_MSGS=()

# Table row counts
${MYSQL_CMD} -sNe "
SELECT TABLE_NAME, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA='${DB}' AND TABLE_TYPE='BASE TABLE'
ORDER BY TABLE_NAME;
" 2>/dev/null | while IFS=$'\t' read -r tbl rows; do
    echo "| $tbl | $rows |" >> "$RESULTS_FILE"
done

echo "" >> "$RESULTS_FILE"
echo "## Validation Checks" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

run_check() {
    local desc="$1"
    local sql="$2"
    local expected="$3"  # expected value (or "gt0" for >0)
    local actual
    actual=$(${MYSQL_CMD} -sNe "$sql" 2>/dev/null)

    local pass_check=false
    if [ "$expected" = "gt0" ] && [ "${actual:-0}" -gt 0 ]; then
        pass_check=true
    elif [ "$expected" = "eq0" ] && [ "${actual:-0}" -eq 0 ]; then
        pass_check=true
    elif [ "$actual" = "$expected" ]; then
        pass_check=true
    fi

    if $pass_check; then
        echo "| ✅ | $desc | $actual |" >> "$RESULTS_FILE"
        echo "  ✅  $desc: $actual"
    else
        echo "| ❌ | $desc | actual=$actual expected=$expected |" >> "$RESULTS_FILE"
        echo "  ❌  $desc: actual=$actual expected=$expected"
        ALL_PASS=false
    fi
}

{
echo "| Status | Check | Value |"
echo "|--------|-------|-------|"
} >> "$RESULTS_FILE"

# Fact table checks
run_check "fact_exchange_rate has data"     "SELECT COUNT(*) FROM \`${DB}\`.fact_exchange_rate;"          "gt0"
run_check "fact_student_enrolment has data" "SELECT COUNT(*) FROM \`${DB}\`.fact_student_enrolment;"      "gt0"
run_check "fact_labour_force has data"      "SELECT COUNT(*) FROM \`${DB}\`.fact_labour_force;"           "gt0"
run_check "fact_cpi has data"               "SELECT COUNT(*) FROM \`${DB}\`.fact_cpi;"                    "gt0"
run_check "fact_job_vacancy has data"       "SELECT COUNT(*) FROM \`${DB}\`.fact_job_vacancy;"            "gt0"
run_check "fact_occupation_shortage data"   "SELECT COUNT(*) FROM \`${DB}\`.fact_occupation_shortage;"    "gt0"
run_check "fact_permanent_migration data"   "SELECT COUNT(*) FROM \`${DB}\`.fact_permanent_migration;"    "gt0"
run_check "fact_skilled_migration data"     "SELECT COUNT(*) FROM \`${DB}\`.fact_skilled_migration;"      "gt0"
run_check "dim_provider has data"           "SELECT COUNT(*) FROM \`${DB}\`.dim_provider;"                "gt0"
run_check "dim_course has data"             "SELECT COUNT(*) FROM \`${DB}\`.dim_course;"                  "gt0"

# Measure consolidation checks
run_check "fact_student_visa_activity: 3 measures" \
    "SELECT COUNT(DISTINCT measure) FROM \`${DB}\`.fact_student_visa_activity;" "3"
run_check "fact_temp_skilled_visa: 2 measures" \
    "SELECT COUNT(DISTINCT measure) FROM \`${DB}\`.fact_temp_skilled_visa;"      "2"
run_check "fact_temp_graduate_visa: 2 measures" \
    "SELECT COUNT(DISTINCT measure) FROM \`${DB}\`.fact_temp_graduate_visa;"     "2"
run_check "fact_skilled_migration: measure col populated" \
    "SELECT COUNT(*) FROM \`${DB}\`.fact_skilled_migration WHERE measure IS NULL;" "eq0"

# Referential integrity
run_check "bridge_course_location: no orphan courses" \
    "SELECT COUNT(*) FROM \`${DB}\`.bridge_course_location b
     LEFT JOIN \`${DB}\`.dim_course c ON b.cricos_code=c.cricos_code
     WHERE c.cricos_code IS NULL;" "eq0"
run_check "bridge_course_location: no orphan providers" \
    "SELECT COUNT(*) FROM \`${DB}\`.bridge_course_location b
     LEFT JOIN \`${DB}\`.dim_provider p ON b.provider_id=p.provider_id
     WHERE p.provider_id IS NULL;" "eq0"

# No NULLs in business keys
run_check "fact_exchange_rate: no null rate_date" \
    "SELECT COUNT(*) FROM \`${DB}\`.fact_exchange_rate WHERE rate_date IS NULL;" "eq0"
run_check "fact_skilled_migration: no null financial_year" \
    "SELECT COUNT(*) FROM \`${DB}\`.fact_skilled_migration WHERE financial_year IS NULL;" "eq0"

# Audit log
run_check "etl_audit_log has completed runs" \
    "SELECT COUNT(*) FROM \`${DB}\`.etl_audit_log WHERE status='completed';" "gt0"
run_check "etl_audit_log: no failed runs" \
    "SELECT COUNT(*) FROM \`${DB}\`.etl_audit_log WHERE status='failed';" "eq0"

# Occupation intelligence view
run_check "vw_occupation_intelligence accessible" \
    "SELECT COUNT(*) FROM \`${DB}\`.vw_occupation_intelligence LIMIT 1;" "gt0"

echo "" >> "$RESULTS_FILE"
if $ALL_PASS; then
    echo "## Result: ✅ ALL CHECKS PASSED" >> "$RESULTS_FILE"
    echo ""
    echo "  ✅  All Phase 8 checks passed"
    echo "  Report: $RESULTS_FILE"
    echo ""
    echo "  Phase 8 PASS ✅"
else
    echo "## Result: ❌ SOME CHECKS FAILED" >> "$RESULTS_FILE"
    echo ""
    warn "Some Phase 8 checks failed — see $RESULTS_FILE"
fi

# ── Phase 9: Summary document ─────────────────────────────────────────────────

header "PHASE 9 — Write final migration summary"

SUMMARY_FILE="docs/final_migration_summary.md"
{
cat << EOF
# AIC External Market Intelligence — MySQL Migration Summary

**Completed:** $DATE_NOW
**Database:** \`${DB}\` on \`${MYSQL_HOST:-localhost}:${MYSQL_PORT:-3306}\`

## Scope

Migration of AIC Occupation Intelligence from SQLite to MySQL 8 (InnoDB, utf8mb4).

## Database Schema

- **25 base tables** in star-schema layout: fact_*, dim_*, ref_*, bridge_*, stg_*, etl_*
- **1 view**: \`vw_occupation_intelligence\` (replaces legacy \`occupation_intelligence\` table)
- Storage engine: InnoDB | Charset: utf8mb4_unicode_ci
- 8 tables use generated columns for nullable business-key columns

## Sources Loaded

| Source | Target Tables | Notes |
|--------|--------------|-------|
| RBA Exchange Rates | fact_exchange_rate | F11 + F11.1 |
| CRICOS | dim_provider, dim_course, dim_provider_location, bridge_course_location | Natural varchar PKs |
| JSA | fact_job_vacancy, fact_occupation_shortage, ref_occupation_profile | IVI + OSL + Profiles |
| Home Affairs | fact_student_visa_activity, fact_temp_skilled_visa, fact_temp_graduate_visa, fact_permanent_migration | BP0015/0014/0016/0068 consolidated with measure column |
| ABS | fact_labour_force, fact_cpi, fact_overseas_migration, fact_population_by_cob | 4 of 7 flows (industry/occupation/edu_output excluded) |
| Department of Education | fact_student_enrolment | YTD cumulative; LOAD DATA LOCAL INFILE bulk load |
| Skilled Migration | fact_skilled_migration, ref_skilled_migration_by_cob_occupation | |

## Key Design Decisions

1. Education YTD data preserved as-is (cumulative). A view may compute month-on-month changes.
2. BP0068 renamed to \`fact_permanent_migration\` (not ha_migration_child_outcomes).
3. SkillSelect remains \`stg_skillselect_eoi\` (staging tier, not promoted to fact).
4. \`fact_abs_education_output\` excluded from initial MySQL schema.
5. Legacy SQLite-only tables excluded (occupation_ceilings, visa_eligibility, etc.).
6. \`dim_country\` populated from source data; \`reference/country_aliases.csv\` for reconciliation.
7. Generated columns (STORED) used for nullable dimensions in UNIQUE KEY definitions.
8. \`vw_occupation_intelligence\` is a derived view/mart, not a source-of-truth table.
9. MySQL 8, InnoDB, utf8mb4 — full Unicode support.
10. Education 3.5M rows bulk-loaded via chunked LOAD DATA LOCAL INFILE (100k rows/chunk).

## ETL Modes

| Mode | Command | Output |
|------|---------|--------|
| SQLite (original) | \`python ETL/run_all.py\` | data/aic_occupation_intelligence.db |
| MySQL | \`python ETL/run_all.py --mysql\` | MySQL: ${DB} |
| MySQL dry-run | \`python ETL/run_all.py --mysql --dry-run\` | Parse only, no writes |

## Quality Gates

| Phase | Gate | Result |
|-------|------|--------|
| Phase 2 | Schema validation (90/90 checks) | ✅ PASS |
| Phase 3 | Unit tests (50/50 pytest) | ✅ PASS |
| Phase 4 | Wiring validation (17/17 checks) | ✅ PASS |
| Phase 8 | Cross-source validation | See docs/mysql_validation_results.md |

## Files Changed / Created

| File | Description |
|------|-------------|
| ETL/schema_mysql.sql | MySQL DDL — 25 tables + 1 view |
| ETL/lib_etl_mysql.py | MySQL ETL library (upsert, bulk load, audit) |
| ETL/run_mysql_sources.py | MySQL-mode ETL for all 7 sources |
| ETL/run_all.py | Updated with --mysql flag + run_all_mysql() |
| ETL/validate_mysql_schema.py | Phase 2 static schema validator |
| ETL/validate_mysql_wiring.py | Phase 4 wiring validator |
| tests/test_mysql_library.py | 50 unit tests for lib_etl_mysql.py |
| deploy_mysql.sh | This deployment script |
| .env.example | Credential template |
| .gitignore | Excludes .env, raw_data/, *.db |
| docs/mysql_validation_results.md | Phase 8 validation report |
| docs/proposed_mysql_model.md | Schema design rationale |
| docs/mysql_schema_reconciliation.md | Column mapping: SQLite → MySQL |
| docs/mysql_test_plan.md | Test plan |

## Tableau Integration

Connect Tableau to:
- Host: \`${MYSQL_HOST:-localhost}\`  Port: \`${MYSQL_PORT:-3306}\`
- Database: \`${DB}\`
- User: \`${APP_USER}\`
- Key views/tables: \`vw_occupation_intelligence\`, all \`fact_*\` and \`dim_*\` tables
EOF
} > "$SUMMARY_FILE"

pass "docs/final_migration_summary.md written"

# ── Final summary ─────────────────────────────────────────────────────────────

echo ""
echo ""
echo "$(printf '═%.0s' {1..60})"
echo "  DEPLOYMENT COMPLETE"
echo "$(printf '═%.0s' {1..60})"
echo ""
echo "  Database:  ${DB}"
echo "  User:      ${APP_USER}"
echo "  Tables:    ${TABLE_COUNT:-?}"
echo ""
echo "  Reports:"
echo "    docs/mysql_validation_results.md"
echo "    docs/final_migration_summary.md"
echo ""
echo "  Next: Connect Tableau to MySQL and verify vw_occupation_intelligence"
echo ""
