# Migration status: old Laravel + Angular app → KhataShop Python

Source of the old page list: the `field_side_bar` table (the old Angular menu, per role) and the Laravel routes.
API URLs and how each is served: see [API_ENDPOINTS.md](API_ENDPOINTS.md).

## Admin role (your screenshot)

| Old menu | Old page | New app | Status |
|---|---|---|---|
| — | Dashboard | Dashboard | Done (redesigned) |
| Bill Detail | Bill Detail | Billing → Bill Detail | Done |
| Report | Item Consumption | Report → Item Consumption | Done (was never built in the old app) |
| Report | Ingredient Purchase | Report → Ingredient Purchase | Done (old one was fixed to July 2023) |
| Report | Daily Sale Consolidated | Report → Daily Sale Consolidated | Done (old one had Cash/UPI ids of one restaurant) |
| Report | Daily Sale | Report → Daily Sale | Done |
| Report | Order Detail | Report → Order Detail | Done |
| Report | Item Summary | Report → Item Summary | Done |
| Report | Online Orders | Report → Online Orders | Done |
| Procurement | Procurement Detail | Inventory → Procurement | Done |
| Issuing | Issuing Detail | Inventory → Issuing | Done |
| consumption | Consumption Detail | Inventory → Consumption | Done |
| Consumption Formula | Consumption Formula | Inventory → Consumption Formula | Done |
| Define Menu | Menu Revision / Menu Category / Menu | Define Menu | Done |
| Define Ingredient | Ingredient / Ingredient Category | Inventory | Done |
| Vendor Detail | Vendor Detail | Inventory → Vendor Detail | Done |
| Settings | Discount / Delivery Charges | Staff & Settings | Done (app-wide; super admin edits) |
| Executive / Counter Manager | Executive / Counter Manager | Staff & Settings → Staff Logins | Done |
| Driver | Driver1 | Online Orders → Drivers | Done (per restaurant) |
| Serving Table | Serving Table | Table View → Manage Tables | Done |
| Attendance | Attendance | Staff & Settings → Attendance | Done |

## Other roles

| Role | Old page | New app | Status |
|---|---|---|---|
| Coordinator | Menu Category, Menu, Upload PDF, Order Detail | same pages | Done |
| Coordinator | Feedback Users | Customers → Feedback Users | Done |
| Coordinator | Users | Customers → Users | Done |
| Counter Manager | Daily Sale (Consolidated), Total Sale, Online Orders, Consumption, Issuing, Order Detail, Bill Detail, Attendance | same pages (role-limited) | Done |
| Counter Manager | Table Wise Billing | Table View (+ Kitchen) | Done |
| Store Manager | Issuing, Procurement, Bill Detail, Ingredient Purchase, Daily Sale (Consolidated), Ingredient (Category), Vendor, Attendance | same pages (role-limited) | Done |
| Kitchen | Kitchen Order Ticket, Order Detail, Attendance | Kitchen (KOT), Order Detail, Attendance | Done |
| Waiter | Dashboard, Attendance, table orders | Table View (KOT only), Attendance | Done |
| Executive | Order Detail, Order History, Discount, Delivery Charges, Menu Category, Menu | same pages | Done |
| Executive | Guest Detail, Executive Visit Detail | — | **Not converted** (hotel / sales module) |
| Executive | Notification Templates, Send Broadcast, Notification Reports | — | **Not converted** (push notifications to customers) |
| Executive | Coupon code, Cashback | — | **Not converted** (customer app offers) |
| Manager | Guest Detail, Event Detail, Room Occupancy, Guest Comment Card | — | **Not converted** (hotel module) |
| Manager | Privilege Card Type, Privilege Card Detail | — | **Not converted** (hotel loyalty cards) |
| Manager | Leave Applied, Employee Chart, Create Task | — | **Not converted** (HR / tasks module) |
| Employee | Event Detail | — | **Not converted** (hotel module) |

The not-converted pages belong to the hotel, HR/tasks and customer-marketing modules, which were not part of
the admin menu in the requested screenshot. Their old screens and APIs keep working through the old app.

## Mobile app / website APIs

All 363 old URLs answer on the new server at the same paths (see API_ENDPOINTS.md). Rebuilt in Python so far:
the website menu (`/api/getmenuitems`), the attendance machine sync id (`/api/get_lastid_attendances`) and
`/api/mb_api/menu_category`, each verified identical to Laravel by `tests/api_compat_test.py`. Every other URL is
forwarded unchanged to the old Laravel backend.

Why the customer / driver endpoints are still forwarded: they use JWT tokens issued by Laravel, and Laravel's
logout puts tokens on a blacklist in its own cache. To move them, token issuing, checking and logout must move
together (otherwise a logged-out phone could still be accepted). Payments (HDFC / Juspay) also use bank keys and
signed callbacks and must be moved and tested with the bank's test mode.
