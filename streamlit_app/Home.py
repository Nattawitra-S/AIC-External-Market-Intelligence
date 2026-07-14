"""
Home.py
========
Entry point for the AIC External Market Intelligence Streamlit application.

Run with:  streamlit run streamlit_app/Home.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from modules import common, db
from modules.theme import apply_page_config, inject_css

apply_page_config("Home")
inject_css()

st.title("AIC External Market Intelligence Database")
st.caption("Production dashboard -- live MySQL 8 source, Tableau-equivalent view built in Streamlit")

connected, message = db.test_connection()
if not connected:
    st.error(
        f"**Cannot connect to the database.** {message}\n\n"
        f"Check that MySQL is running and that `.env` (project root) contains "
        f"MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASS / MYSQL_DB."
    )
    st.stop()

common.render_sidebar_controls()
common.render_etl_freshness_banner()

st.markdown(
    """
### What this application does

Reads live data directly from the `aic_market_intelligence` MySQL database --
the same production database documented in `docs/final_migration_summary.md`
and `docs/tableau_data_dictionary.md`. Every number on every page is a live
query result, not a static export.

Use the navigation panel on the left to open a page:

| Page | Purpose |
|---|---|
| **Market Overview** | Total enrolments, growth/decline, largest and fastest-moving source countries |
| **Country Analysis** | Deep-dive on one country: trend, sectors, visa indicators, marketing recommendation |
| **Student Demand** | Sector-level demand trends and country comparisons |
| **Visa Analysis** | Every visa/migration measure that genuinely exists in the database |
| **Competitor Analysis** | CRICOS provider, location, and course landscape |
| **Opportunity Score** | Transparent, rule-based country ranking (not a prediction) |
| **Market Drivers** | Enrolment trend alongside exchange rate, CPI, population, migration, and labour indicators (association, not causation) |

### Honesty-by-design rules this app follows

- **No fabricated data.** If a measure (e.g. visa refusal rates, processing
  times, per-country course demand) does not exist in the database, the page
  says so explicitly instead of inventing a number.
- **YTD-aware.** Enrolment figures are year-to-date cumulative; every trend
  and comparison here uses the same calendar month across years so growth
  figures are genuinely comparable.
- **Deterministic scoring only.** The Opportunity Score is a documented,
  inspectable formula -- never presented as a forecast or prediction.
- **Not real-time.** Source publication cadence ranges from monthly to
  annual; the banner above shows exactly when each source was last
  successfully loaded.
"""
)

common.page_footer(["etl_audit_log"])
