-- Recommended corrections to existing data on the live database.
-- Run after 001_khatashop_python_tables.sql. Safe to run more than once.
-- Take a backup first.

SET NAMES utf8mb4;

-- 1) Payment options for the three Kailash Parvatam restaurants (6, 8, 9).
--    They have none on the live database, so their bills cannot be settled.
--    Adds Cash, Card and UPI only where that option is missing (same as the
--    "Set up Cash, Card, UPI" button on Generate Bill). The old app is not affected.
INSERT INTO bill_payment_mode (company_detail_id, payment_mode, `order`)
SELECT c.company_detail_id, m.payment_mode, m.sort_order
FROM company_detail c
JOIN (SELECT 'Cash' AS payment_mode, 1 AS sort_order UNION ALL SELECT 'Card', 2 UNION ALL SELECT 'UPI', 3) m
WHERE c.company_detail_id IN (6, 8, 9)
  AND NOT EXISTS (SELECT 1 FROM bill_payment_mode p
                  WHERE p.company_detail_id = c.company_detail_id AND p.payment_mode = m.payment_mode);

-- 2) Sgi kailash Parvatam (9) uses bill prefix 'Op', the same as Ogi kailash Parvatam (8),
--    so both would print bills Op01, Op02 ... Neither has any bills yet, so it is safe to
--    change now. Pick the prefix you want, then remove the two dashes to run it:
-- UPDATE company_detail SET abbreviation = 'Sg' WHERE company_detail_id = 9 AND abbreviation = 'Op';

-- 3) OPTIONAL: keep container charges in their own column.
--    Without it the app adds container charges to bill.service_cost (totals stay correct,
--    reports just show them together). Adding a column with a default does not affect the
--    old Laravel app. Remove the two dashes to run it (run only once):
-- ALTER TABLE bill ADD COLUMN container_charges DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER service_cost;
