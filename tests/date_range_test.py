"""Quick date ranges on report pages: every report opens on this quarter, Last quarter / This FY /
... buttons, custom dates, ranges kept across tabs and filters, Excel export, Item Summary and Order Detail.
Read-only: uses restaurant 8 (reports) and 2 (online pages).
Run:  .venv\Scripts\python tests\date_range_test.py
"""
import re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from datetime import timedelta
from app import create_app
from app.date_ranges import presets
from app.security import CSRF_SESSION_KEY
from app.utils import today
app = create_app(); c = app.test_client()
with c.session_transaction() as s:
    s["user"] = {"id": 99, "name": "Admin", "email": "x", "user_type": 8, "role": "staff", "company_id": 8, "company_name": None}
    s[CSRF_SESSION_KEY] = "x"
ok = True
def check(label, cond, detail=""):
    global ok; ok &= bool(cond); print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  ({detail})"))
def dates(html):
    return re.search(r'name="date_from" value="([\d-]+)"', html).group(1), re.search(r'name="date_to" value="([\d-]+)"', html).group(1)
def active(html):
    m = re.search(r'class="range-chip active"[^>]*>([^<]+)<', html) or re.search(r'class="range-chip range-custom active"[^>]*>([^<]+)<', html)
    return m.group(1) if m else None
with app.app_context():
    P = presets(); t = today()
q = P["this_quarter"]; lq = P["last_quarter"]
for kind in ("daily", "daily_sale", "daily_consolidated", "items", "payments", "gst_monthly", "online_orders"):
    h = c.get(f"/reports/?type={kind}").get_data(as_text=True)
    check(f"{kind}: opens on this quarter", dates(h) == (q[0].isoformat(), q[1].isoformat()) and active(h) == "This quarter", (dates(h), active(h)))
h = c.get("/reports/?type=daily_sale&range=last_quarter").get_data(as_text=True)
check("Last quarter button", dates(h) == (lq[0].isoformat(), lq[1].isoformat()) and active(h) == "Last quarter", (dates(h), active(h)))
check("tabs keep the chosen range", "type=daily&amp;range=last_quarter" in h or "range=last_quarter&amp;type=daily" in h)
h = c.get(f"/reports/?type=daily_sale&date_from={lq[0]}&date_to={lq[1]}&source=online").get_data(as_text=True)
check("changing the source keeps Last quarter selected", active(h) == "Last quarter", active(h))
h = c.get("/reports/?type=daily_sale&range=custom&date_from=2026-05-03&date_to=2026-05-20").get_data(as_text=True)
check("custom dates", dates(h) == ("2026-05-03", "2026-05-20") and active(h) == "Custom", (dates(h), active(h)))
h = c.get("/reports/?type=daily_sale&date_from=2026-05-20&date_to=2026-05-03").get_data(as_text=True)
check("reversed dates swapped with a note", dates(h) == ("2026-05-03", "2026-05-20") and "wrong way round" in h)
for k in ("today", "yesterday", "this_month", "last_month", "this_fy", "last_fy"):
    h = c.get(f"/reports/?type=daily&range={k}").get_data(as_text=True)
    check(f"range {k}", dates(h) == (P[k][0].isoformat(), P[k][1].isoformat()), dates(h))
h = c.get("/reports/?type=period_sales").get_data(as_text=True)
check("Quarterly/yearly still opens on 3 financial years", dates(h)[0].endswith("-04-01") and active(h) == "Custom", (dates(h), active(h)))
r = c.get("/reports/export?type=daily_sale&range=last_quarter")
check("export honours the quick range", r.status_code == 200 and lq[0].strftime("%Y%m%d") in r.headers.get("Content-Disposition", ""), r.headers.get("Content-Disposition"))
with c.session_transaction() as ss:
    ss["user"]["company_id"] = 2; ss.modified = True
h = c.get("/online-orders/item-summary").get_data(as_text=True)
check("Item Summary opens on this quarter", dates(h) == (q[0].isoformat(), q[1].isoformat()), dates(h))
h = c.get("/online-orders/item-summary?range=last_quarter").get_data(as_text=True)
check("Item Summary last quarter", active(h) == "Last quarter")
h = c.get("/online-orders/").get_data(as_text=True)

check("Order Detail keeps yesterday+today and shows the quick ranges", dates(h) == ((t - timedelta(days=1)).isoformat(), t.isoformat()) and "Last quarter" in h, dates(h))
h = c.get("/online-orders/?range=this_fy").get_data(as_text=True)
check("Order Detail limits long ranges to 93 days with a note", "at most 93 days" in h or (t - P["this_fy"][0]).days <= 92)
with c.session_transaction() as ss:
    ss["user"]["company_id"] = 8; ss.modified = True
print("All checks passed" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
