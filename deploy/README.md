# Database update for the live server

The Python app works on the **same database** as the live Laravel app. It does not change any
existing table; it only needs 8 new tables (all named `ks_...`).

**Do not import a copy of the local database on the server.** The live database has newer
orders and bills than any local copy, and the local copy contains test data.

## Steps

1. **Back up the live database first**

   ```
   mysqldump -u <db_user> -p --single-transaction <database_name> > backup_before_python_$(date +%F).sql
   ```

2. **Add the new tables** (safe to run more than once; creates only missing tables, changes nothing else)

   ```
   mysql -u <db_user> -p <database_name> < 001_khatashop_python_tables.sql
   ```

3. **Optional: module settings** for M. M. Continental (2) and Ogi kailash Parvatam (8).
   Skip it to leave every module on, or set modules later under *Super Admin › Restaurants › Manage*.

   ```
   mysql -u <db_user> -p <database_name> < 002_optional_module_settings.sql
   ```

   **Recommended fixes** (payment options for restaurants 6, 8, 9; read the file for the two optional fixes):

   ```
   mysql -u <db_user> -p <database_name> < 003_recommended_fixes.sql
   ```

4. **Create your super admin on the server** (from the `khatashop_python` folder; pick a new strong password)

   ```
   .venv/bin/python run.py --create-super-admin you@example.com --name "Your Name" --password "<new password>"
   ```

5. **Point the app at the live database** in `db_config.json` on the server (host, user, password,
   database name). This file is not in git; copy `db_config.example.json` if needed.

6. **Check**: `.venv/bin/python run.py --check` should print `Database OK` and the bill count.

## After going live

- Restaurant staff log in with their existing accounts (same `aspirant_registration` table as the old app).
- Waiter / kitchen logins and tables made on the local PC do not exist on the server; add them in
  *Staff Logins* and *Table View › Manage tables*.
- A restaurant without payment options gets a one-click *Set up Cash, Card, UPI* button on Generate Bill.

## Undo

The new tables are independent of the old app. To remove them:

```
DROP TABLE ks_activity_log, ks_bill_payment, ks_company_modules, ks_driver_company,
           ks_kot_note, ks_kot_ready, ks_super_admin, ks_table_area;
```
