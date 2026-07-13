"""
verify_db.py — Quick data quality check after ETL run.
Usage: python ETL/verify_db.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "aic_occupation_intelligence.db"

def check(label, query, conn):
    try:
        rows = conn.execute(query).fetchall()
        print(f"  ✅ {label}: {rows[0][0] if len(rows)==1 and len(rows[0])==1 else rows}")
    except Exception as e:
        print(f"  ❌ {label}: {e}")

def run():
    if not DB.exists():
        print(f"❌ Database not found: {DB}")
        return
    conn = sqlite3.connect(DB)
    print(f"\n{'='*55}\n  AIC DB Quality Check — {DB.name}\n{'='*55}")

    print("\n📊 Row counts:")
    for t in ["occupation_ceilings","occupation_shortage_ratings","visa_eligibility","occupation_intelligence"]:
        check(t, f"SELECT COUNT(*) FROM {t}", conn)

    print("\n📅 Data freshness:")
    check("Latest month", "SELECT MAX(data_month) FROM occupation_intelligence", conn)

    print("\n🔍 Data completeness:")
    check("With shortage data", "SELECT COUNT(*) FROM occupation_intelligence WHERE shortage_status IS NOT NULL", conn)
    check("Eligible for 189",   "SELECT COUNT(*) FROM occupation_intelligence WHERE eligible_189=1", conn)
    check("With ceiling data",  "SELECT COUNT(*) FROM occupation_intelligence WHERE ceiling_189 IS NOT NULL", conn)

    print("\n⭐ Top 5 occupations (189 fill rate):")
    rows = conn.execute("""
        SELECT occupation_name, fill_rate_189_pct, trend_189
        FROM occupation_intelligence
        WHERE eligible_189=1 AND fill_rate_189_pct IS NOT NULL
        ORDER BY fill_rate_189_pct DESC LIMIT 5
    """).fetchall()
    for r in rows:
        trend = {"UP":"⬆","DOWN":"⬇","STABLE":"→"}.get(r[2],"?")
        print(f"    {trend} {r[0]}: {r[1]:.1f}%")

    print(f"\n{'='*55}\n  ✅ Check complete\n{'='*55}\n")
    conn.close()

if __name__ == "__main__":
    run()
