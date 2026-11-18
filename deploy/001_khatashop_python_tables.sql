-- KhataShop Python app: new tables (structure only, no data)
--
-- Adds the 8 tables the Python app uses next to the existing database.
-- Safe to run on the live database:
--   * it only creates tables that do not exist yet (CREATE TABLE IF NOT EXISTS)
--   * it does not change, drop or delete anything in the existing tables
--   * running it twice does nothing the second time
--
-- Run (take a backup first):
--   mysql -u <db_user> -p <database_name> < 001_khatashop_python_tables.sql
--
--   ks_super_admin      super admin logins
--   ks_company_modules  modules switched on per restaurant (no row = all modules on)
--   ks_driver_company   which restaurant each driver belongs to
--   ks_table_area       dining area of each table (AC, Garden ...)
--   ks_kot_note         kitchen note on a KOT line ("less spicy")
--   ks_kot_ready        KOTs marked ready by the kitchen (table turns green)
--   ks_bill_payment     part payments (one bill paid by several modes)
--   ks_activity_log     super admin activity log

SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS `ks_activity_log` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `created` datetime NOT NULL,
  `user_id` int(11) DEFAULT NULL,
  `user_name` varchar(120) DEFAULT NULL,
  `user_email` varchar(190) DEFAULT NULL,
  `role` varchar(10) DEFAULT NULL,
  `user_type` int(11) DEFAULT NULL,
  `company_detail_id` int(11) DEFAULT NULL,
  `action` varchar(60) NOT NULL,
  `category` varchar(30) NOT NULL,
  `label` varchar(120) NOT NULL,
  `target` varchar(190) DEFAULT NULL,
  `details` text DEFAULT NULL,
  `status` varchar(10) NOT NULL DEFAULT 'ok',
  `ip` varchar(45) DEFAULT NULL,
  `user_agent` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_created` (`created`),
  KEY `idx_company` (`company_detail_id`,`created`),
  KEY `idx_user` (`user_email`,`created`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_bill_payment` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `bill_id` int(11) NOT NULL,
  `company_detail_id` int(11) NOT NULL,
  `payment_mode_id` int(11) NOT NULL,
  `amount` decimal(10,2) NOT NULL,
  `created_by` int(11) NOT NULL DEFAULT 0,
  `created` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_bill` (`bill_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_company_modules` (
  `company_detail_id` int(11) NOT NULL,
  `modules` text NOT NULL,
  `updated` datetime NOT NULL,
  PRIMARY KEY (`company_detail_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_driver_company` (
  `driver_id` int(11) NOT NULL,
  `company_detail_id` int(11) NOT NULL,
  PRIMARY KEY (`driver_id`,`company_detail_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_kot_note` (
  `table_order_detail_id` int(11) NOT NULL,
  `note` varchar(200) NOT NULL,
  PRIMARY KEY (`table_order_detail_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_kot_ready` (
  `table_order_id` int(11) NOT NULL,
  `ready_at` datetime NOT NULL,
  PRIMARY KEY (`table_order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_super_admin` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `email` varchar(150) NOT NULL,
  `password` varchar(200) NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT 1,
  `last_login` datetime DEFAULT NULL,
  `created` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ks_super_admin_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE IF NOT EXISTS `ks_table_area` (
  `serving_table_id` int(11) NOT NULL,
  `area` varchar(40) NOT NULL,
  `sort_order` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`serving_table_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
