# KhataShop (Python)

A Python rewrite of the KhataShop billing back office, built on the **Python standard library only**
(no Flask or other frameworks; nothing to `pip install`). It replaces the
Laravel API and the Angular screens for:

- **Dashboard**: sales, bills, average bill, GST, trend chart, payment modes, top items, recent bills
- **Menu Category**: list, add, edit, show/hide, delete, photo
- **Menu**: list with filters and paging, add, edit, show/hide, delete, photo
- **Upload PDF**: upload and preview the customer menu PDF
- **Bill Detail**: search and filter bills, view and print a bill, export to Excel
- **Generate Bill**: point-of-sale screen with GST, service and container charges, Save & Print

- **Reports**: daily, item-wise, category-wise, payment-mode and counter-wise sales, each exportable to Excel
- **Excel import/export** on Menu Category and Menu: download a template, export, edit, import
- **Super admin**: register restaurants, manage their payment options (Cash/Card/UPI) and staff logins, open any restaurant

It uses the **same MySQL database** as the PHP app, so both can run side by side.
Staff sign in with their existing email and password (`aspirant_registration` table).
There is no payment gateway. Cash, Card and UPI are only labels recorded on each bill.

## Super admin

Super admins are kept in their own table, `ks_super_admin`, which the app creates the first time it is needed.
No PHP table is changed. Create the first super admin from the command line:

```bash
python run.py --create-super-admin you@example.com --name "Your Name"     # prints a generated password
```

Super admins sign in on the normal login page and land on **Restaurants**, where they can:

- **Register a restaurant**: name, location, 2-letter bill prefix (bills are numbered `Kp01`, `Kp02` …), GSTIN, hours, logo.
  Cash, Card and UPI are added automatically.
- **Manage** a restaurant: its payment options, and staff logins (with the same role codes as the old app).
  Staff sign in with those logins and only see their own restaurant.
- **Open** a restaurant to use its Dashboard, Billing, Reports and Menu screens. The sidebar shows which
  restaurant you are in, with **Exit**. Bills a super admin saves are recorded as counter 0.
- **Super Admins** page: add more super admins, change passwords, deactivate accounts.

**Modules per restaurant.** When registering or managing a restaurant, tick which modules it may use:
Billing, Table service, Online orders, Inventory, Menu management, Staff logins, Attendance (Dashboard is
always on). A turned-off module disappears from the menu and its pages are blocked for every login of that
restaurant, including super admins working inside it. Stored in our own table `ks_company_modules`;
restaurants without a row have every module.

If a restaurant has no payment options, the Generate Bill screen shows a **Set up Cash, Card & UPI** button
to outlet admins (Coordinator, Admin, Manager) and super admins.

## Old app APIs (mobile apps, website, attendance machine, payments)

Every old URL (`/api/...`, `/payment/...`) answers on this server at the same path, so the apps need no change.
Endpoints rebuilt in Python are answered directly; all others are forwarded unchanged to the old Laravel backend
(`legacy_api.base_url` in db_config.json, which must keep running until everything is moved).
Full list: [docs/API_ENDPOINTS.md](docs/API_ENDPOINTS.md). What is converted: [docs/MIGRATION_STATUS.md](docs/MIGRATION_STATUS.md).
Check: `python tests/api_compat_test.py` (needs both servers running).

## Converted admin modules

| Old page | New page | Notes |
|---|---|---|
| Kitchen Order Ticket | Billing → Kitchen (KOT) | Live KOT cards, "Ready" button; table screen shows Ready |
| Order Detail / Online Orders | Online Orders → Order Detail | Status, driver, confirmed/ready, print. Written in the PHP format |
| Item Summary | Online Orders → Item Summary | Paid + delivered online orders |
| Driver | Online Orders → Drivers | Shared with the driver app (bcrypt passwords) |
| Daily Sale, Daily Sale Consolidated, Total Sale, Ingredient Purchase, Item Consumption | Reports tabs | Consolidated uses each restaurant's own payment modes; stock report works for any period |
| Procurement / Issuing / Consumption Detail | Inventory | Notes with ingredient lines, numbered like bills, A4 print |
| Consumption Formula | Inventory → Consumption Formula | Recipe per menu item, feeds Item Consumption |
| Ingredient, Ingredient Category, Vendor Detail | Inventory | |
| Discount, Delivery Charges | Staff & Settings | Shared tables (customer app) |
| Counter Manager, Executive, ... | Staff & Settings → Staff Logins | Same role codes; employee ID links attendance |
| Attendance | Staff & Settings → Attendance | Monthly grid from biometric punches, Excel export |

**Roles** (`app/roles.py`): each old role signs in to its own pages. Waiters land on Table View and can
send KOTs but not bill; kitchen staff land on the Kitchen screen; store managers get inventory; counter
managers get billing, issuing and reports; executives get online orders, menu and delivery settings.
Admin, Manager and Coordinator see everything for their restaurant.

**Separate data per restaurant.** Every page and action is limited to the signed-in restaurant (checked by
`tests/isolation_test.py`). Drivers belong to restaurants through our table `ks_driver_company` (filled from
delivery history). Each restaurant has its own menu PDF (`uploads/menu_pdf/<id>.pdf`); the old website's shared
`menupdf.pdf` is updated only by the restaurant set in `uploads.legacy_menu_pdf_company`. Photos, logos and PDFs
open only for their own restaurant. Discount and Delivery Charges are single app-wide settings in
the customer app, so restaurants can view them and only super admins can change them.

**Online orders vendor mapping**: `online_orders.vendor_by_company` in db_config.json maps a restaurant
to its `orders.vendor_id` (M. M. Continental = vendor 1). Other restaurants use their own id.

## Table service (Billing → Table View)

Dine-in billing by table, with kitchen order tickets (KOTs):

1. **Manage Tables** (outlet admins): add tables by area, e.g. area `AC` with names `AC1-AC6`, or `1-20`, or `T1, T2, Bar`.
2. **Table View** shows every table by area: white = free, blue = running (saved), yellow = KOT sent,
   green = bill printed. Each busy table shows minutes since the order started and the amount. Refreshes every 15 s.
3. Open a table and add items (a kitchen note can be added to each new item):
   - **Save**: keeps the items on the table without printing a KOT.
   - **KOT & Print** (Ctrl+Enter): prints a KOT with only the items not yet sent to the kitchen.
   - **Print Bill**: one bill for everything on the table (new items get a KOT first). Tick **It's paid** to settle at once.
   - **Settle**: after printing, choose Cash/Card/UPI when the customer pays. The table becomes free.
   - Items added after printing turn the table running again. Print the bill again: it keeps the same bill number.
   - Remove a sent item (×), **move** the order to a free table, or **cancel** it (outlet admins).
4. **Delivery** / **Pick Up** open the quick bill screen in that mode.

Printed but unpaid bills are not shown in Bill Detail, reports or the dashboard until they are settled.
Bill numbers are shared with quick bills, and the next one is always the highest number used plus one.

Storage uses the PHP app's `serving_table`, `table_order` (one row per KOT) and `table_order_detail` tables,
plus two small tables of our own: `ks_table_area` (area of each table) and `ks_kot_note` (kitchen notes).
Old PHP table orders (`kot_status = 2`) are ignored.

## Excel import / export (Menu Category and Menu)

Open the **Import / Export Excel** panel at the top of either page:

1. **Download template** (with an instructions sheet and dropdowns) or **Export current** data.
2. Edit it in Excel and **Import** (.xlsx or .csv).

- Existing rows are matched by name (Menu also uses the **Item ID** column from an export, so renaming is safe)
  and updated. New rows are added. Nothing is ever deleted.
- Every row is checked first. If any row has a problem, **nothing is imported** and each problem is listed with its row number.
- Menu import can create missing categories (tick **Create missing categories**).
- Hiding or showing a category through an import hides or shows its items, just like on the page.

## Project layout (MVC)

```
khatashop_python/
├── db_config.json          ← all settings (database, port, uploads, auto-restart, logging)
├── db_config.example.json  ← template to copy on a new server
├── run.py                  ← start the server (supervisor with auto-restart)
├── wsgi.py                 ← entry point for gunicorn or other WSGI servers
├── requirements.txt        ← empty: nothing to install
├── vendor/mysql/           ← bundled MySQL driver (pure Python), so no pip install is needed
├── app/
│   ├── models/             ← MODEL: all SQL; every query is limited to the user's company
│   │   ├── bill.py  dashboard.py  menu_category.py  menu_item.py  payment.py  company.py  user.py
│   ├── controllers/        ← CONTROLLER: one blueprint (group of URLs) per screen
│   │   ├── dashboard.py  menu_category.py  menu.py  menu_pdf.py  bills.py  auth.py  uploads.py
│   ├── templates/          ← VIEW: HTML pages ({{ }} / {% %} template syntax)
│   ├── static/css|js|img   ← VIEW: stylesheet and browser scripts (no CDN needed)
│   ├── services/           ← bill maths, file uploads, Excel export
│   ├── core/               ← own web layer on the standard library: framework, templates, web server,
│   │                          Excel (.xlsx), bcrypt passwords, image checks
│   ├── errors.py           ← exception types and global error handlers
│   ├── security.py         ← sign-in session, CSRF protection, login lockout
│   ├── db.py  config.py  utils.py  icons.py
├── tests/                  ← unit tests + end-to-end smoke test
└── logs/                   ← app.log and supervisor.log (rotated automatically)
```

## Setup

Requires Python 3.9 or newer (tested on 3.9 and 3.12) and access to the KhataShop MySQL/MariaDB database.
Nothing has to be installed: the app uses only Python's standard library plus the MySQL driver bundled in `vendor/`.

```bash
copy db_config.example.json db_config.json   # Linux: cp ...; then edit it (already filled in on this PC)
python3 run.py --check                        # checks config, database, tables and the password method
```

On Linux, sign-in uses the system's bcrypt (Python's built-in `crypt` module), which is fast. Where that is
missing (e.g. Windows), a pure-Python bcrypt is used: it works but takes several seconds per sign-in, and
`--check` says so.

## Running

| Command | What it does |
|---|---|
| `python run.py` | **Production.** Threaded web server under a supervisor that restarts it when code changes, or if it crashes. |
| `python run.py --dev` | Runs the web server directly, without the supervisor (no restart on change). |
| `python run.py --check` | Validates `db_config.json` and the database connection, then exits. |

Open http://localhost:5000 (the port is set by `app.port`).

### Automatic restart on code updates

When you run `python run.py`, the supervisor checks `app/` (`server.watch_dirs`) and `db_config.json` every second:

1. When a file changes, it test-loads the new code in a separate process first.
2. If the new code has an error (for example a syntax error or a bad config value), the error goes into
   `logs/supervisor.log` and **the running site stays up on the old code**.
3. If the new code loads, the server restarts on it within about 2 seconds.
4. If the server process crashes, it is started again. If it crashes repeatedly, the supervisor pauses briefly between attempts.

To keep it running after a reboot, start `python run.py` as a service: Windows Task Scheduler "At startup",
NSSM, or a systemd unit on Linux (`ExecStart=/usr/bin/python3 /path/run.py`, `Restart=always`).

## db_config.json

| Key | Meaning |
|---|---|
| `database.*` | MySQL host, port, user, password, database name, timeouts |
| `app.port` / `app.host` | Where the site listens (`0.0.0.0` = all network interfaces) |
| `app.secret_key` | Random string that signs sessions. Keep it secret; changing it signs everyone out. |
| `app.timezone` | Used for bill dates and the dashboard (default `Asia/Kolkata`) |
| `app.session_hours` | How long a sign-in lasts |
| `app.items_per_page` | Default page size for lists |
| `uploads.base_dir` | Folder with `category_img/`, `item_img/` and `menupdf.pdf`. It points at the Laravel `public/uploads` folder, so both apps share the same photos and PDF. |
| `uploads.max_upload_mb` | Largest file that can be uploaded |
| `server.auto_restart` | Turn the code watcher on or off |
| `server.watch_dirs` / `watch_extensions` | What the watcher looks at |
| `server.threads` | Worker threads of the web server (requests handled at the same time) |
| `logging.*` | Log level, file and rotation size |

The app checks every value at startup and names the exact setting that is wrong.

## Error handling

- **Expected problems** (bad form input, missing record, wrong company, bad upload) raise typed errors
  (`ValidationError`, `NotFoundError`, `UploadError`, ...). Forms are shown again with messages next to
  the fields that need fixing.
- **Database down or wrong credentials** show a clear "database unavailable" page (HTTP 503) instead of a crash.
- **Unexpected errors** are logged with a full traceback and a short reference code. The user sees a
  friendly page with that code, so a problem a user reports can be found in `logs/app.log`.
- API calls (the Save Bill button) always get JSON errors. The billing screen shows them next to the
  total, and the unsaved bill is kept in the browser tab if the page reloads.
- Saving a bill is one database transaction. Bill numbers are handed out under a database lock, so two
  counters can never get the same number.
- Prices and GST are always re-read from the database when a bill is saved. Totals sent by the browser are never used.
- Every form and API call is CSRF-protected. Repeated failed sign-ins lock that email/IP for 10 minutes.

## Compatibility with the PHP app

- Bill numbers continue the existing sequence (company abbreviation + next number, e.g. `Op16`).
- GST is added on top of the item price, per line, and split equally into CGST and SGST. This is the same
  as the current Angular billing screen; the unit tests check it against real bills Op13–Op15.
- `financial_session` and `financial_quarter` are stored the same way the PHP app stores them
  (Q1 = Apr–Jun … Q4 = Jan–Mar; the session is the calendar year).
- Showing or hiding a category also shows or hides its items, as before.
- Container charges: if the `bill` table has a `container_charges` column, they are saved there.
  Otherwise they are added to `service_cost` so the bill total is still right. To keep them separate, add the column:
  ```sql
  ALTER TABLE bill ADD container_charges DECIMAL(10,2) NOT NULL DEFAULT 0 AFTER service_cost;
  ```

### Recommended indexes

`bill_detail` has no index on `bill_id`, and `bill` has none on the company. The app works around this,
but these two indexes make lists and the dashboard much faster as data grows:

```sql
CREATE INDEX idx_bill_detail_bill ON bill_detail (bill_id);
CREATE INDEX idx_bill_company_date ON bill (company_detail_id, billing_date);
```

## Tests

```bash
python -m unittest discover tests     # bill maths, bill numbers, quarters
python tests/smoke_test.py 93         # every screen + saving a real bill (deleted afterwards), as user id 93
python tests/modules_smoke_test.py    # roles, kitchen, inventory, recipes, online orders, staff, attendance
python tests/module_permission_test.py # module on/off per restaurant
python tests/isolation_test.py        # restaurant A cannot see or change anything of restaurant B
python tests/admin_smoke_test.py      # super admin, new restaurant, staff login, Excel import, reports
                                      # (uses a throwaway test restaurant that is removed afterwards)
```
