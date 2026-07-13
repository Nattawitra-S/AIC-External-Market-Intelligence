# AIC Occupation Intelligence — Dashboard User Guide
**For:** Marketing Team & Senior Management  
**Last updated:** 2026-07-09

> ⚠️ **Scope note (2026-07-14):** This guide describes the original
> SkillSelect-only SQLite dashboard (`occupation_intelligence` table in
> `data/aic_occupation_intelligence.db`), preserved unchanged. The
> production dashboard now runs on **MySQL 8**, with
> **`vw_occupation_intelligence`** as the primary Tableau entry point —
> see `docs/final_migration_summary.md` for connection details and current
> data coverage.

---

## What is this dashboard?

The AIC Occupation Intelligence Dashboard shows **live data** about which occupations are in demand in Australia, which visa pathways are available, and how competitive those visas are. Data updates automatically every month from the Department of Home Affairs.

---

## Connecting Tableau to the Database

1. Open Tableau Desktop
2. **Connect → To a File → SQLite**
3. Navigate to: `Documents/Gov_ETL_data/data/aic_occupation_intelligence.db`
4. Select table: `occupation_intelligence`
5. Click **Update Now** to load data

> If you don't see SQLite in the connector list, download the SQLite ODBC driver from http://www.ch-werner.de/sqliteodbc/

---

## Dashboard Sheets

### Sheet 1: Occupation Finder
Filter `shortage_status` = "National Shortage" + `eligible_189` = 1, sort by `fill_rate_189_pct` to find most in-demand occupations.

### Sheet 2: Visa Pathway Summary
Select an occupation → see MLTSSL/STSOL/ROL status, which visas apply, assessing body, fill rates.

### Sheet 3: State Demand Heatmap
Connect `occupation_ceilings` table. Rows = occupations, columns = states, color = `fill_rate_pct`.

### Sheet 4: Marketing Copy Generator

Create calculated field `marketing_copy`:
```
"Study " + [occupation_name] + " with AIC.
" +
IF [shortage_national] = 1 THEN "✅ NATIONAL SHORTAGE — high employer demand" ELSE "Regional demand available" END
+ IF [eligible_189] = 1 THEN "
✅ Visa 189 (Skilled Independent) pathway available" ELSE "" END
+ IF [eligible_190] = 1 THEN "
✅ Visa 190 (State Sponsored) — check state lists" ELSE "" END
+ IF [eligible_491] = 1 THEN "
✅ Visa 491 (Regional) pathway available" ELSE "" END
+ "
📊 " + STR(ROUND([fill_rate_189_pct],0)) + "% of annual invitations issued this month"
+ IF [trend_189] = "UP" THEN " — TRENDING UP ⬆"
  ELSEIF [trend_189] = "DOWN" THEN " — trending down ⬇"
  ELSE " — stable" END
+ IF NOT ISNULL([median_salary_aud]) THEN "
💰 Median salary: $" + STR([median_salary_aud]) + " AUD/year" ELSE "" END
```

**Example output:**
```
Study Software Engineering with AIC.
✅ NATIONAL SHORTAGE — high employer demand
✅ Visa 189 (Skilled Independent) pathway available
✅ Visa 190 (State Sponsored) — check state lists
📊 74% of annual invitations issued this month — TRENDING UP ⬆
💰 Median salary: $130,000 AUD/year
```

---

## Common Questions

**Q: How often is the data updated?**  
A: 1st of every month. Check `data_month` column.

**Q: What does "fill rate" mean?**  
A: ceiling=1,000 and invitations_issued=743 → fill rate=74.3%. At ~90%+, occupation may stop receiving invitations for the year.

**Q: Difference between 189, 190, 491?**  

| Visa | Type | Points requirement |
|------|------|--------------------|
| 189 | Skilled Independent | Highest (~85–95+) |
| 190 | State Sponsored | Lower (~75–85) |
| 491 | Regional (3 yrs regional) | Lowest (~65–75) |

**Q: MLTSSL vs STSOL vs ROL?**  

| List | 189 | 190 | 491 |
|------|:---:|:---:|:---:|
| MLTSSL | ✅ | ✅ | ✅ |
| STSOL | ❌ | ✅ | ✅ |
| ROL | ❌ | ❌ | ✅ |

**Q: Data looks old?**  
A: Tell Mild to run the monthly ETL → Tableau: Data → Refresh All Extracts.
