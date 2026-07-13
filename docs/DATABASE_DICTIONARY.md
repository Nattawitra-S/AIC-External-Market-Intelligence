# AIC Occupation Intelligence — Database Dictionary
**File:** `data/aic_occupation_intelligence.db`  
**Engine:** SQLite 3  
**Last updated:** 2026-07-09

> ⚠️ **Scope note (2026-07-14):** This dictionary describes the original
> SkillSelect-only SQLite prototype, preserved unchanged. It is a separate
> database from the production MySQL pipeline. For the current MySQL 8
> schema (25 tables + `vw_occupation_intelligence`), live row counts, and
> Tableau connection details, see `docs/final_migration_summary.md` and
> `ETL/schema_mysql.sql`.

---

## Table: `occupation_ceilings`
Raw SkillSelect data pulled monthly from the Department of Home Affairs (Qlik Engine backend). One row per occupation × visa subclass × state × month.

| Column | Type | Example | Description |
|--------|------|---------|-------------|
| `id` | INTEGER PK | 1 | Auto-increment |
| `anzsco_code` | TEXT | "261313" | 6-digit ANZSCO occupation code |
| `occupation_name` | TEXT | "Software Engineer" | Human-readable occupation label |
| `visa_subclass` | TEXT | "189" | Visa subclass: 189 / 190 / 491 |
| `state` | TEXT | "NSW" | State/territory or "National" |
| `ceiling` | INTEGER | 1000 | Annual allocation cap for this occupation+visa combination |
| `invitations_issued` | INTEGER | 743 | Invitations sent YTD |
| `fill_rate_pct` | REAL | 74.3 | `invitations_issued / ceiling × 100` |
| `trend` | TEXT | "UP" | Direction vs prior period: UP / DOWN / STABLE |
| `data_month` | TEXT | "2026-07" | YYYY-MM of the data snapshot |
| `extracted_at` | TEXT | "2026-07-09T10:00:00" | ISO 8601 timestamp when ETL ran |
| `source_url` | TEXT | "https://immi..." | Source page URL |

**Unique constraint:** `(anzsco_code, visa_subclass, state, data_month)`  
**Indexes:** `anzsco_code`, `data_month`, `visa_subclass`

---

## Table: `occupation_shortage_ratings`
Occupation Shortage List (OSL) ratings from Jobs & Skills Australia. Updated annually. One row per occupation × state × year.

| Column | Type | Example | Description |
|--------|------|---------|-------------|
| `id` | INTEGER PK | 1 | Auto-increment |
| `anzsco_code` | TEXT | "261313" | 6-digit ANZSCO code |
| `occupation_name` | TEXT | "Software Engineer" | Occupation label from OSL |
| `shortage_status` | TEXT | "National Shortage" | National Shortage / Regional Shortage / Balanced / Surplus |
| `shortage_level` | TEXT | "Strong" | Strong / Moderate / Slight |
| `state` | TEXT | NULL | State code or NULL = national |
| `osl_year` | INTEGER | 2025 | Publication year of OSL |
| `source` | TEXT | "JSA OSL 2025" | Data source label |
| `extracted_at` | TEXT | "2026-07-09T10:00:00" | ETL run timestamp |

**Unique constraint:** `(anzsco_code, state, osl_year)`  
**Source:** [Jobs & Skills Australia — Occupation Shortage Data](https://www.jobsandskills.gov.au/data/occupation-shortages)

---

## Table: `visa_eligibility`
Skilled occupation list eligibility from Home Affairs — which occupations are on MLTSSL, STSOL, or ROL and which visa subclasses they qualify for.

| Column | Type | Example | Description |
|--------|------|---------|-------------|
| `id` | INTEGER PK | 1 | Auto-increment |
| `anzsco_code` | TEXT | "261313" | 6-digit ANZSCO code |
| `occupation_name` | TEXT | "Software Engineer" | Occupation label |
| `list_type` | TEXT | "MLTSSL" | MLTSSL / STSOL / ROL |
| `visa_subclass` | TEXT | "189, 190, 491" | Comma-separated eligible subclasses |
| `assessing_body` | TEXT | "Engineers Australia" | Skills assessment authority |
| `effective_date` | TEXT | "2026-03-01" | Date list was last updated |
| `source` | TEXT | "Home Affairs SOL" | Source label |
| `extracted_at` | TEXT | "2026-07-09T10:00:00" | ETL run timestamp |

**Unique constraint:** `(anzsco_code, list_type, visa_subclass)`  
**Source:** [Home Affairs — Skilled Occupation Lists](https://immi.homeaffairs.gov.au/visas/working-in-australia/skill-occupation-list)

---

## Table: `occupation_intelligence` ⭐ Main Fact Table
Denormalized view joining ceilings + shortage + visa eligibility. **This is the table Tableau connects to.** One row per occupation × month.

| Column | Type | Example | Description |
|--------|------|---------|-------------|
| `id` | INTEGER PK | 1 | Auto-increment |
| `anzsco_code` | TEXT | "261313" | 6-digit ANZSCO code |
| `occupation_name` | TEXT | "Software Engineer" | Occupation name |
| `shortage_status` | TEXT | "National Shortage" | From OSL |
| `shortage_level` | TEXT | "Strong" | From OSL |
| `shortage_national` | INTEGER | 1 | 1 if National Shortage, 0 otherwise |
| `eligible_189` | INTEGER | 1 | 1 if on list for subclass 189 |
| `eligible_190` | INTEGER | 1 | 1 if on list for subclass 190 |
| `eligible_491` | INTEGER | 1 | 1 if on list for subclass 491 |
| `list_type` | TEXT | "MLTSSL" | MLTSSL / STSOL / ROL |
| `assessing_body` | TEXT | "Engineers Australia" | Skills assessment body |
| `ceiling_189` | INTEGER | 1000 | Annual cap for 189 |
| `ceiling_190` | INTEGER | 2500 | Annual cap for 190 |
| `ceiling_491` | INTEGER | 3000 | Annual cap for 491 |
| `invitations_189` | INTEGER | 743 | 189 invitations YTD |
| `invitations_190` | INTEGER | 1823 | 190 invitations YTD |
| `invitations_491` | INTEGER | 2100 | 491 invitations YTD |
| `fill_rate_189_pct` | REAL | 74.3 | 189 fill rate % |
| `fill_rate_190_pct` | REAL | 72.9 | 190 fill rate % |
| `fill_rate_491_pct` | REAL | 70.0 | 491 fill rate % |
| `trend_189` | TEXT | "UP" | 189 trend: UP / DOWN / STABLE |
| `trend_190` | TEXT | "STABLE" | 190 trend |
| `trend_491` | TEXT | "DOWN" | 491 trend |
| `median_salary_aud` | INTEGER | 130000 | Median salary AUD (manual/ABS enrichment) |
| `data_month` | TEXT | "2026-07" | Data snapshot month YYYY-MM |
| `last_updated` | TEXT | "2026-07-09T10:00:00" | ETL run timestamp |

**Unique constraint:** `(anzsco_code, data_month)`  
**Indexes:** `anzsco_code`, `shortage_status`

---

## Key Queries

### Top in-demand occupations (national shortage + visa 189 eligible)
```sql
SELECT anzsco_code, occupation_name, shortage_level,
       ceiling_189, invitations_189, fill_rate_189_pct, trend_189,
       median_salary_aud
FROM occupation_intelligence
WHERE shortage_national = 1
  AND eligible_189 = 1
  AND data_month = '2026-07'
ORDER BY fill_rate_189_pct DESC;
```

### Marketing copy source data for a given occupation
```sql
SELECT * FROM occupation_intelligence
WHERE anzsco_code = '261313'
ORDER BY data_month DESC
LIMIT 1;
```

### State-by-state demand for an occupation
```sql
SELECT state, visa_subclass, ceiling, invitations_issued, fill_rate_pct, trend
FROM occupation_ceilings
WHERE anzsco_code = '261313'
  AND data_month = '2026-07'
ORDER BY state, visa_subclass;
```

### Trend over time for an occupation
```sql
SELECT data_month, ceiling_189, invitations_189, fill_rate_189_pct, trend_189
FROM occupation_intelligence
WHERE anzsco_code = '261313'
ORDER BY data_month;
```
