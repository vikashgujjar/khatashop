-- OPTIONAL: module settings you chose locally for two restaurants.
-- Without this file every restaurant simply has all modules on; you can also set them
-- on the server under Super Admin > Restaurants > Manage instead of running this.
-- Run after 001_khatashop_python_tables.sql. Re-running it only updates these two rows.
--
--   2 = M. M. Continental        8 = Ogi kailash Parvatam

INSERT INTO ks_company_modules (company_detail_id, modules, updated) VALUES (2, '["attendance", "billing", "inventory", "menu", "online", "staff"]', NOW()) ON DUPLICATE KEY UPDATE modules = VALUES(modules), updated = NOW();
INSERT INTO ks_company_modules (company_detail_id, modules, updated) VALUES (8, '["billing", "menu", "tables"]', NOW()) ON DUPLICATE KEY UPDATE modules = VALUES(modules), updated = NOW();
