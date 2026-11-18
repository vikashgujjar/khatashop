"""Monthly GST and Quarterly / Yearly sale reports: pages, tabs, sidebar, default ranges, Excel
export, waiter access, and GST totals equal to the CGST / SGST printed on the bills.
Uses restaurant 8's real data read-only (nothing is written).
Run:  .venv\Scripts\python tests\gst_period_reports_test.py
"""
import io, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from openpyxl import load_workbook
from app import create_app
from app.security import CSRF_SESSION_KEY
app = create_app()
c = app.test_client()
with c.session_transaction() as s:
    s["user"] = {"id": 99, "name": "Admin", "email": "x", "user_type": 8, "role": "staff", "company_id": 8, "company_name": None}
    s[CSRF_SESSION_KEY] = "x"
ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("  ok   " if cond else "  FAIL ") + label)
for q, name in (("type=gst_monthly", "gst"), ("type=period_sales", "quarter"), ("type=period_sales&period=year", "year")):
    r = c.get("/reports/?" + q); html = r.get_data(as_text=True)
    check(f"{q} page 200", r.status_code == 200)
    check(f"{q} sidebar link active", 'Monthly GST Report' in html and 'Quarterly / Yearly Sale' in html)
    check(f"{q} has tabs", "Monthly GST (CGST / SGST)" in html and "Quarterly / yearly sales" in html)
html = c.get("/reports/?type=gst_monthly").get_data(as_text=True)
from datetime import date as _d
q_start = _d(2026, 10, 1) if _d.today() >= _d(2026, 10, 1) and _d.today() < _d(2027, 1, 1) else None
check("GST opens on this quarter", 'class="range-chip active"' in html and 'This quarter' in html and (q_start is None or f'value="{q_start.isoformat()}"' in html))
html = c.get("/reports/?type=period_sales").get_data(as_text=True)
check("Quarterly opens on 3 financial years (from 1 Apr 2024)", 'value="2024-04-01"' in html and "Q1 · Apr-Jun" in html)
check("Yearly switch present", 'name="period" value="year"' in html)
html = c.get("/reports/?type=period_sales&period=year").get_data(as_text=True)
check("Yearly shows FY rows", "FY 2026-27" in html and "Q1 · Apr-Jun" not in html)
for q in ("type=gst_monthly", "type=period_sales&period=year"):
    r = c.get("/reports/export?" + q)
    wb = load_workbook(io.BytesIO(r.data)); ws = wb.active
    check(f"export {q} is a workbook with rows ({ws.max_row})", r.status_code == 200 and ws.max_row > 3)
# waiter must not see reports
with c.session_transaction() as s:
    s["user"]["user_type"] = 4; s.modified = True
check("waiter blocked from GST report", c.get("/reports/?type=gst_monthly").status_code in (302, 403))
from datetime import date
from app import db
from app.models import report
with app.app_context():
    for cid in (1, 2, 3, 8):
        s, e = date(2023, 4, 1), date(2026, 11, 16)
        g = report.build("gst_monthly", cid, s, e, "offline")
        h = db.fetch_one("SELECT SUM(CAST(cgst AS DECIMAL(12,2))) c, SUM(CAST(sgst AS DECIMAL(12,2))) s FROM bill WHERE company_detail_id=%s AND payment_mode_id<>0 AND billing_date BETWEEN %s AND %s", (cid, s, e))
        check(f"restaurant {cid}: GST report CGST/SGST = printed bills", float(g["totals"].get("cgst", 0)) == float(h["c"] or 0) and float(g["totals"].get("sgst", 0)) == float(h["s"] or 0))
        y = report.build("period_sales", cid, s, e, "offline", "year")
        t = db.fetch_value("SELECT COALESCE(SUM(total), 0) FROM bill WHERE company_detail_id=%s AND payment_mode_id<>0 AND billing_date BETWEEN %s AND %s", (cid, s, e))
        check(f"restaurant {cid}: yearly total = bills total", float(y["totals"].get("total", 0)) == float(t))
print("All checks passed" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
