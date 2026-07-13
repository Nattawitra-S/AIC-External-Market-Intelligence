# Pre-MySQL Migration Backup Manifest
**Created:** 2026-07-13  
**Purpose:** Safety snapshot before MySQL migration phases begin

## What is backed up here

### Text files (copied to this directory)
| File | Status |
|------|--------|
| ETL/schema.sql | Preserved as ETL/schema_sqlite.sql in main project |
| ETL/lib_etl.py | Unchanged — SQLite shared utilities |
| ETL/run_all.py | Will be modified (add --mysql flag) |
| ETL/schema_mysql.sql | Will be significantly modified |
| ETL/lib_etl_mysql.py | Will be modified |
| ETL/etl_rba.py | Will be modified (MySQL wiring) |
| ETL/etl_cricos.py | Will be modified (MySQL wiring) |
| ETL/etl_jsa.py | Will be modified (MySQL wiring) |
| ETL/etl_home_affairs_extended.py | Will be modified (MySQL wiring) |
| ETL/etl_abs.py | Will be modified (MySQL wiring) |
| ETL/etl_skilled_migration.py | Will be modified (MySQL wiring) |
| ETL/etl_education_v2.py | Will be modified (MySQL wiring + bulk load) |

### Binary databases (cannot be text-copied — require manual backup)
| File | Note |
|------|------|
| data/aic_occupation_intelligence.db | PRIMARY SQLite DB — contains all loaded data |
| raw_data/student_visa.db | Legacy student visa DB |

## ⚠️ User action required for binary backups
Before any live MySQL load, manually copy the SQLite database:
```bash
cd ~/Documents/Gov_ETL_data
cp data/aic_occupation_intelligence.db data/aic_occupation_intelligence_backup_2026_07_13.db
```

## Key SQLite schema location
`ETL/schema_sqlite.sql` — complete DDL for all 24 SQLite tables  
`ETL/schema.sql` — same file (original, not modified)

## Recovery procedure
If MySQL migration fails:
1. `data/aic_occupation_intelligence.db` remains intact
2. `ETL/run_all.py` without `--mysql` flag still uses SQLite
3. `ETL/schema_sqlite.sql` documents the original schema
4. No SQLite files are deleted in the MySQL migration
