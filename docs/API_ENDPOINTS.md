# API endpoints

Base URL for every endpoint: the new server, e.g. `https://api.khatashop.com` (same paths as before).
Apps do not need any URL change.

* **Python**: answered by the new Python server (checked identical to Laravel by `tests/api_compat_test.py`).
* **Forwarded to Laravel**: the new server passes the request unchanged to the old Laravel backend
  (`legacy_api.base_url` in db_config.json) and returns its answer. Same URL, same behaviour.

Regenerate: `python tools/api_inventory.py ../backend/project`

## Summary

| Used by | Endpoints | Python | Forwarded |
|---|---:|---:|---:|
| Customer mobile app | 36 | 0 | 36 |
| Driver mobile app | 14 | 0 | 14 |
| Website (rooms, menu, feedback, site orders) | 11 | 1 | 10 |
| Payment gateway (HDFC / Juspay callbacks) | 14 | 0 | 14 |
| Biometric attendance machine | 2 | 1 | 1 |
| Mobile menu API | 1 | 1 | 0 |
| Old admin panel - login | 15 | 0 | 15 |
| Old admin panel (Angular) - screen data | 270 | 0 | 270 |
| **Total** | **363** | **3** | **360** |

## Customer mobile app

Login by OTP, menu, addresses, orders, tracking, notifications, Juspay payments

| Method | URL | Served by | Old handler |
|---|---|---|---|
| POST | `/api/customer/add_address` | Forwarded to Laravel | CustomerController@AddAddress |
| POST | `/api/customer/all_notifications` | Forwarded to Laravel | CustomerController@all_notifications |
| POST | `/api/customer/assign_driver` | Forwarded to Laravel | CustomerController@AssignDriver |
| POST | `/api/customer/cancel_order` | Forwarded to Laravel | CustomerController@cancel_order |
| POST | `/api/customer/deleteaccount` | Forwarded to Laravel | CustomerController@deleteAccount |
| POST | `/api/customer/delivery_charges` | Forwarded to Laravel | CustomerController@delivery_charges |
| GET | `/api/customer/failed_payments` | Forwarded to Laravel | JuspayController@failed_payments |
| GET | `/api/customer/get_all_menu` | Forwarded to Laravel | CustomerController@GetMenu |
| POST | `/api/customer/get_distance` | Forwarded to Laravel | CustomerController@get_distance |
| POST | `/api/customer/get_menu_by_category_id` | Forwarded to Laravel | CustomerController@GetMenuByCategoryId |
| GET | `/api/customer/get_menu_category` | Forwarded to Laravel | CustomerController@GetMenuCategory |
| GET | `/api/customer/get_transaction_status` | Forwarded to Laravel | CustomerController@get_transaction_status |
| GET | `/api/customer/handleJuspayResponse` | Forwarded to Laravel | JuspayController@handleResponse |
| POST | `/api/customer/handleJuspayResponse` | Forwarded to Laravel | JuspayController@handleResponse |
| GET | `/api/customer/handleJuspayResponse1` | Forwarded to Laravel | JuspayController@handleResponse1 |
| POST | `/api/customer/handleJuspayResponse1` | Forwarded to Laravel | JuspayController@handleResponse1 |
| POST | `/api/customer/initiatePayment` | Forwarded to Laravel | JuspayController@initiatePayment |
| POST | `/api/customer/juspayWebhook` | Forwarded to Laravel | JuspayController@juspayWebhook |
| POST | `/api/customer/list_address` | Forwarded to Laravel | CustomerController@ListAddress |
| POST | `/api/customer/login_verify_otp` | Forwarded to Laravel | CustomerController@LoginVerifyOtp |
| POST | `/api/customer/login_with_otp` | Forwarded to Laravel | CustomerController@LoginWithOtp |
| POST | `/api/customer/logout` | Forwarded to Laravel | CustomerController@Logout |
| GET | `/api/customer/offers` | Forwarded to Laravel | CustomerController@offers |
| POST | `/api/customer/order_details` | Forwarded to Laravel | CustomerController@OrderDetails |
| POST | `/api/customer/order_history` | Forwarded to Laravel | CustomerController@OrderHistory |
| POST | `/api/customer/place_order` | Forwarded to Laravel | CustomerController@PlaceOrder |
| POST | `/api/customer/resend_otp` | Forwarded to Laravel | CustomerController@ResendOtp |
| GET | `/api/customer/restaurants` | Forwarded to Laravel | CustomerController@restaurants |
| POST | `/api/customer/search_items` | Forwarded to Laravel | CustomerController@SearchItems |
| GET | `/api/customer/success_payments` | Forwarded to Laravel | JuspayController@success_payments |
| POST | `/api/customer/testsms` | Forwarded to Laravel | CustomerController@testsms |
| POST | `/api/customer/track_order` | Forwarded to Laravel | CustomerController@TrackOrder |
| POST | `/api/customer/update_address` | Forwarded to Laravel | CustomerController@UpdateAddress |
| POST | `/api/customer/update_image` | Forwarded to Laravel | CustomerController@UpdateImage |
| POST | `/api/customer/update_profile` | Forwarded to Laravel | CustomerController@UpdateProfile |
| POST | `/api/customer/user_profile` | Forwarded to Laravel | CustomerController@UserProfile |

## Driver mobile app

Login by OTP, assigned orders, accept / pick up / deliver

| Method | URL | Served by | Old handler |
|---|---|---|---|
| POST | `/api/driver/accept_order` | Forwarded to Laravel | DriverController@AcceptOrder |
| POST | `/api/driver/confirm_pickup` | Forwarded to Laravel | DriverController@ConfirmPickup |
| POST | `/api/driver/deliver_order` | Forwarded to Laravel | DriverController@DeliverOrder |
| GET | `/api/driver/get_menu` | Forwarded to Laravel | DriverController@GetMenu |
| GET | `/api/driver/get_menu_category` | Forwarded to Laravel | DriverController@GetMenuCategory |
| POST | `/api/driver/get_orders` | Forwarded to Laravel | DriverController@GetOrders |
| POST | `/api/driver/login_verify_otp` | Forwarded to Laravel | DriverController@LoginVerifyOtp |
| POST | `/api/driver/login_with_otp` | Forwarded to Laravel | DriverController@LoginWithOtp |
| POST | `/api/driver/logout` | Forwarded to Laravel | DriverController@Logout |
| POST | `/api/driver/order_details` | Forwarded to Laravel | DriverController@OrderDetails |
| GET | `/api/driver/profile` | Forwarded to Laravel | DriverController@Profile |
| POST | `/api/driver/reject_order` | Forwarded to Laravel | DriverController@RejectOrder |
| POST | `/api/driver/resend_otp` | Forwarded to Laravel | DriverController@ResendOtp |
| POST | `/api/driver/update_profile` | Forwarded to Laravel | DriverController@UpdateProfile |

## Website (rooms, menu, feedback, site orders)

Called by the public website / QR menu pages

| Method | URL | Served by | Old handler |
|---|---|---|---|
| POST | `/api/book` | Forwarded to Laravel | RoonsController@book |
| GET | `/api/check-availability` | Forwarded to Laravel | RoonsController@checkAvailability |
| GET | `/api/copyimage` | Forwarded to Laravel | RoonsController@copyimage |
| POST | `/api/getmenuitems` | Python | RoonsController@getmenuitems |
| POST | `/api/place_site_order` | Forwarded to Laravel | RoonsController@place_site_order |
| POST | `/api/roomDetails` | Forwarded to Laravel | RoonsController@roomDetails |
| GET | `/api/rooms` | Forwarded to Laravel | RoonsController@rooms |
| POST | `/api/submitfeedback` | Forwarded to Laravel | RoonsController@submitfeedback |
| POST | `/api/submituser` | Forwarded to Laravel | RoonsController@submituser |
| POST | `/api/verify_site_order` | Forwarded to Laravel | RoonsController@verify_site_order |
| POST | `/api/verifyotp` | Forwarded to Laravel | RoonsController@LoginVerifyOtp |

## Payment gateway (HDFC / Juspay callbacks)

Called by the bank / payment gateway, not by people

| Method | URL | Served by | Old handler |
|---|---|---|---|
| GET | `/api/UpdateOrderStatus` | Forwarded to Laravel | JuspayController@UpdateOrderStatus |
| POST | `/api/UpdateOrderStatus` | Forwarded to Laravel | JuspayController@UpdateOrderStatus |
| POST | `/api/payment/initiate` | Forwarded to Laravel | PaymentController@initiate |
| POST | `/api/payment/response` | Forwarded to Laravel | PaymentController@handleResponse |
| GET | `/api/payment/response/{order_id}` | Forwarded to Laravel | PaymentController@paymentResponse |
| POST | `/api/payment/response/{order_id}` | Forwarded to Laravel | PaymentController@paymentResponse |
| GET | `/api/payment/response1/{order_id}` | Forwarded to Laravel | PaymentController@paymentResponse1 |
| POST | `/api/payment/response1/{order_id}` | Forwarded to Laravel | PaymentController@paymentResponse1 |
| GET | `/api/payment/status/{order_id}` | Forwarded to Laravel | PaymentController@status |
| POST | `/payment/initiate` | Forwarded to Laravel | PaymentController@initiate |
| GET | `/payment/response` | Forwarded to Laravel | PaymentController@handleResponse |
| POST | `/payment/response` | Forwarded to Laravel | PaymentController@handleResponse |
| GET | `/payment/response/{order_id}` | Forwarded to Laravel | PaymentController@handleResponse |
| POST | `/payment/response/{order_id}` | Forwarded to Laravel | PaymentController@handleResponse |

## Biometric attendance machine

The machine pushes punches and asks for the last id

| Method | URL | Served by | Old handler |
|---|---|---|---|
| GET | `/api/get_lastid_attendances` | Python | AttendancePullApiController@getLastInsertedId |
| POST | `/api/pull_Data` | Forwarded to Laravel | AttendancePullApiController@pullData |

## Mobile menu API

| Method | URL | Served by | Old handler |
|---|---|---|---|
| POST | `/api/mb_api/menu_category` | Python | MobileAPIController@menu_category |

## Old admin panel - login

Replaced by the new /login page

| Method | URL | Served by | Old handler |
|---|---|---|---|
| POST | `/api/auth/forgot_password` | Forwarded to Laravel | AuthController@forgotPassword |
| POST | `/api/auth/forgot_password2/{p1?}` | Forwarded to Laravel | AuthController@forgot_password |
| POST | `/api/auth/get_form_option` | Forwarded to Laravel | AuthController@get_form_option |
| POST | `/api/auth/login` | Forwarded to Laravel | AuthController@login |
| POST | `/api/auth/login/{p1?}` | Forwarded to Laravel | AuthController@login |
| POST | `/api/auth/logout` | Forwarded to Laravel | AuthController@logout |
| POST | `/api/auth/refresh` | Forwarded to Laravel | AuthController@refresh |
| POST | `/api/auth/register` | Forwarded to Laravel | AuthController@register |
| POST | `/api/auth/register_form` | Forwarded to Laravel | AuthController@register_form |
| POST | `/api/auth/request_otp` | Forwarded to Laravel | AuthController@requestOtp |
| POST | `/api/auth/reset_password` | Forwarded to Laravel | AuthController@resetPassword |
| POST | `/api/auth/user-profile` | Forwarded to Laravel | AuthController@userProfile |
| POST | `/api/auth/user_dashboard` | Forwarded to Laravel | AuthController@get_user_dashboard |
| POST | `/api/auth/user_status` | Forwarded to Laravel | AuthController@get_user_status |
| POST | `/api/auth/verify_otp` | Forwarded to Laravel | AuthController@verifyOtp |

## Old admin panel (Angular) - screen data

Replaced by the new web pages; still forwarded so the old panel keeps working

| Method | URL | Served by | Old handler |
|---|---|---|---|
| POST | `/api/admin/assign_special_task` | Forwarded to Laravel | AdminController@assignSpecialTask |
| POST | `/api/admin/attendance` | Forwarded to Laravel | AdminController@getMonthlyAttendance |
| POST | `/api/admin/create_bill` | Forwarded to Laravel | AdminController@create_bill |
| POST | `/api/admin/create_consumption_formula` | Forwarded to Laravel | AdminController@create_consumption_formula |
| POST | `/api/admin/create_consumption_note` | Forwarded to Laravel | AdminController@create_consumption_note |
| POST | `/api/admin/create_issuing_note` | Forwarded to Laravel | AdminController@create_issuing_note |
| POST | `/api/admin/create_procurement_note` | Forwarded to Laravel | AdminController@create_procurement_note |
| POST | `/api/admin/create_procurement_note_search` | Forwarded to Laravel | AdminController@create_procurement_note_search |
| POST | `/api/admin/delete_data` | Forwarded to Laravel | AdminController@delete_data |
| GET | `/api/admin/drivers` | Forwarded to Laravel | AdminController@drivers |
| POST | `/api/admin/edit_consumption_formula` | Forwarded to Laravel | AdminController@edit_consumption_formula |
| POST | `/api/admin/edit_consumption_note` | Forwarded to Laravel | AdminController@edit_consumption_note |
| POST | `/api/admin/edit_issuing_note` | Forwarded to Laravel | AdminController@edit_issuing_note |
| POST | `/api/admin/edit_procurement_note` | Forwarded to Laravel | AdminController@edit_procurement_note |
| POST | `/api/admin/edit_procurement_note_search` | Forwarded to Laravel | AdminController@edit_procurement_note_search |
| POST | `/api/admin/employee_list` | Forwarded to Laravel | AdminController@getEmployeeList |
| POST | `/api/admin/get_admission_form` | Forwarded to Laravel | AdminController@get_admission_form |
| POST | `/api/admin/get_button_info` | Forwarded to Laravel | AdminController@get_button_info |
| POST | `/api/admin/get_dashboard_data` | Forwarded to Laravel | AdminController@get_dashboard_data |
| POST | `/api/admin/get_form_field/{p1?}` | Forwarded to Laravel | AdminController@get_form_field |
| POST | `/api/admin/get_form_option` | Forwarded to Laravel | AdminController@get_form_option |
| POST | `/api/admin/get_monthly_attendance_report` | Forwarded to Laravel | AdminController@getMonthlyAttendanceReport |
| POST | `/api/admin/get_option/{p1?}` | Forwarded to Laravel | AdminController@get_option |
| POST | `/api/admin/get_page_env` | Forwarded to Laravel | AdminController@get_page_env |
| POST | `/api/admin/get_page_info` | Forwarded to Laravel | AdminController@get_page_info |
| POST | `/api/admin/get_profile_status` | Forwarded to Laravel | AdminController@get_profile_status |
| GET | `/api/admin/get_search_bar_data/{p1?}` | Forwarded to Laravel | AdminController@get_search_bar_data |
| POST | `/api/admin/get_side_bar` | Forwarded to Laravel | AdminController@get_sidebar |
| POST | `/api/admin/get_side_bar1` | Forwarded to Laravel | AdminController@get_sidebar1 |
| POST | `/api/admin/get_table_data_a` | Forwarded to Laravel | AdminController@get_table_data |
| POST | `/api/admin/ingredient_search` | Forwarded to Laravel | AdminController@ingredient_search |
| POST | `/api/admin/newOrders` | Forwarded to Laravel | AdminController@newOrders |
| POST | `/api/admin/page_action_button` | Forwarded to Laravel | AdminController@page_action_button |
| POST | `/api/admin/page_action_button_confirm` | Forwarded to Laravel | AdminController@page_action_button_confirm |
| POST | `/api/admin/report_pdf` | Forwarded to Laravel | AdminController@report_pdf |
| POST | `/api/admin/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | AdminController@store_data |
| POST | `/api/admin/update_bill` | Forwarded to Laravel | AdminController@update_bill |
| POST | `/api/admin/update_consumption_formula` | Forwarded to Laravel | AdminController@update_consumption_formula |
| POST | `/api/admin/update_consumption_note` | Forwarded to Laravel | AdminController@update_consumption_note |
| POST | `/api/admin/update_issuing_note` | Forwarded to Laravel | AdminController@update_issuing_note |
| POST | `/api/admin/update_order` | Forwarded to Laravel | AdminController@updateOrder |
| POST | `/api/admin/update_order_status` | Forwarded to Laravel | AdminController@update_order_status |
| POST | `/api/admin/update_procurement_note` | Forwarded to Laravel | AdminController@update_procurement_note |
| POST | `/api/admin/update_profile_status` | Forwarded to Laravel | AdminController@update_profile_status |
| POST | `/api/admin/update_routine_task_status` | Forwarded to Laravel | AdminController@updateTaskStatus |
| GET | `/api/admin/user-profile` | Forwarded to Laravel | AdminController@userProfile |
| POST | `/api/admin/vendor_status` | Forwarded to Laravel | AdminController@vendor_status |
| POST | `/api/coordinator/attendance` | Forwarded to Laravel | CoordinatorController@getMonthlyAttendance |
| POST | `/api/coordinator/delete_data` | Forwarded to Laravel | CoordinatorController@delete_data |
| GET | `/api/coordinator/drivers` | Forwarded to Laravel | CoordinatorController@drivers |
| POST | `/api/coordinator/employee_list` | Forwarded to Laravel | CoordinatorController@getEmployeeList |
| POST | `/api/coordinator/get_admission_form` | Forwarded to Laravel | CoordinatorController@get_admission_form |
| POST | `/api/coordinator/get_button_info` | Forwarded to Laravel | CoordinatorController@get_button_info |
| POST | `/api/coordinator/get_dashboard_data` | Forwarded to Laravel | CoordinatorController@get_dashboard_data |
| POST | `/api/coordinator/get_form_field/{p1?}` | Forwarded to Laravel | CoordinatorController@get_form_field |
| POST | `/api/coordinator/get_form_option` | Forwarded to Laravel | CoordinatorController@get_form_option |
| POST | `/api/coordinator/get_option/{p1?}` | Forwarded to Laravel | CoordinatorController@get_option |
| POST | `/api/coordinator/get_page_env` | Forwarded to Laravel | CoordinatorController@get_page_env |
| POST | `/api/coordinator/get_page_info` | Forwarded to Laravel | CoordinatorController@get_page_info |
| GET | `/api/coordinator/get_search_bar_data/{p1?}` | Forwarded to Laravel | CoordinatorController@get_search_bar_data |
| POST | `/api/coordinator/get_side_bar` | Forwarded to Laravel | CoordinatorController@get_sidebar |
| POST | `/api/coordinator/get_side_bar1` | Forwarded to Laravel | CoordinatorController@get_sidebar1 |
| POST | `/api/coordinator/get_table_data_a` | Forwarded to Laravel | CoordinatorController@get_table_data |
| POST | `/api/coordinator/newOrders` | Forwarded to Laravel | CoordinatorController@newOrders |
| POST | `/api/coordinator/page_action_button` | Forwarded to Laravel | CoordinatorController@page_action_button |
| POST | `/api/coordinator/page_action_button_confirm` | Forwarded to Laravel | CoordinatorController@page_action_button_confirm |
| POST | `/api/coordinator/report_pdf` | Forwarded to Laravel | CoordinatorController@report_pdf |
| GET | `/api/coordinator/status` | Forwarded to Laravel | CoordinatorController@status |
| POST | `/api/coordinator/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | CoordinatorController@store_data |
| POST | `/api/coordinator/update_order` | Forwarded to Laravel | CoordinatorController@updateOrder |
| POST | `/api/coordinator/update_order_status` | Forwarded to Laravel | CoordinatorController@update_order_status |
| GET | `/api/coordinator/user-profile` | Forwarded to Laravel | CoordinatorController@userProfile |
| POST | `/api/counter_manager/attendance` | Forwarded to Laravel | CounterManagerController@getMonthlyAttendance |
| POST | `/api/counter_manager/create_bill` | Forwarded to Laravel | CounterManagerController@create_bill |
| POST | `/api/counter_manager/create_consumption_note` | Forwarded to Laravel | CounterManagerController@create_consumption_note |
| POST | `/api/counter_manager/create_issuing_note` | Forwarded to Laravel | CounterManagerController@create_issuing_note |
| POST | `/api/counter_manager/create_table_order` | Forwarded to Laravel | CounterManagerController@create_table_order |
| POST | `/api/counter_manager/create_table_wise_billing` | Forwarded to Laravel | CounterManagerController@create_table_wise_billing |
| POST | `/api/counter_manager/delete_data` | Forwarded to Laravel | CounterManagerController@delete_data |
| GET | `/api/counter_manager/drivers` | Forwarded to Laravel | CounterManagerController@drivers |
| POST | `/api/counter_manager/edit_consumption_note` | Forwarded to Laravel | CounterManagerController@edit_consumption_note |
| POST | `/api/counter_manager/edit_issuing_note` | Forwarded to Laravel | CounterManagerController@edit_issuing_note |
| POST | `/api/counter_manager/employee_list` | Forwarded to Laravel | CounterManagerController@getEmployeeList |
| POST | `/api/counter_manager/get_admission_form` | Forwarded to Laravel | CounterManagerController@get_admission_form |
| POST | `/api/counter_manager/get_button_info` | Forwarded to Laravel | CounterManagerController@get_button_info |
| POST | `/api/counter_manager/get_dashboard_data` | Forwarded to Laravel | CounterManagerController@get_dashboard_data |
| POST | `/api/counter_manager/get_form_field/{p1?}` | Forwarded to Laravel | CounterManagerController@get_form_field |
| POST | `/api/counter_manager/get_form_option` | Forwarded to Laravel | CounterManagerController@get_form_option |
| POST | `/api/counter_manager/get_option/{p1?}` | Forwarded to Laravel | CounterManagerController@get_option |
| POST | `/api/counter_manager/get_page_env` | Forwarded to Laravel | CounterManagerController@get_page_env |
| POST | `/api/counter_manager/get_page_info` | Forwarded to Laravel | CounterManagerController@get_page_info |
| GET | `/api/counter_manager/get_search_bar_data/{p1?}` | Forwarded to Laravel | CounterManagerController@get_search_bar_data |
| POST | `/api/counter_manager/get_service_charges` | Forwarded to Laravel | CounterManagerController@get_service_charges |
| POST | `/api/counter_manager/get_side_bar` | Forwarded to Laravel | CounterManagerController@get_sidebar |
| POST | `/api/counter_manager/get_side_bar1` | Forwarded to Laravel | CounterManagerController@get_sidebar1 |
| POST | `/api/counter_manager/get_table_data_a` | Forwarded to Laravel | CounterManagerController@get_table_data |
| POST | `/api/counter_manager/get_table_wise_billing` | Forwarded to Laravel | CounterManagerController@get_table_wise_billing |
| POST | `/api/counter_manager/newOrders` | Forwarded to Laravel | CounterManagerController@newOrders |
| POST | `/api/counter_manager/order_details` | Forwarded to Laravel | CounterManagerController@orderDetails |
| POST | `/api/counter_manager/page_action_button` | Forwarded to Laravel | CounterManagerController@page_action_button |
| POST | `/api/counter_manager/page_action_button_confirm` | Forwarded to Laravel | CounterManagerController@page_action_button_confirm |
| POST | `/api/counter_manager/report_pdf` | Forwarded to Laravel | CounterManagerController@report_pdf |
| GET | `/api/counter_manager/status` | Forwarded to Laravel | CounterManagerController@status |
| POST | `/api/counter_manager/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | CounterManagerController@store_data |
| POST | `/api/counter_manager/update_bill` | Forwarded to Laravel | CounterManagerController@update_bill |
| POST | `/api/counter_manager/update_consumption_note` | Forwarded to Laravel | CounterManagerController@update_consumption_note |
| POST | `/api/counter_manager/update_issuing_note` | Forwarded to Laravel | CounterManagerController@update_issuing_note |
| POST | `/api/counter_manager/update_order` | Forwarded to Laravel | CounterManagerController@updateOrder |
| POST | `/api/counter_manager/update_order_status` | Forwarded to Laravel | CounterManagerController@update_order_status |
| POST | `/api/counter_manager/update_table_order` | Forwarded to Laravel | CounterManagerController@update_table_order |
| GET | `/api/counter_manager/user-profile` | Forwarded to Laravel | CounterManagerController@userProfile |
| POST | `/api/employee/attendance` | Forwarded to Laravel | AspirantController@getMonthlyAttendance |
| POST | `/api/employee/delete_data` | Forwarded to Laravel | AspirantController@delete_data |
| POST | `/api/employee/employee_list` | Forwarded to Laravel | AspirantController@getEmployeeList |
| POST | `/api/employee/get_admission_form` | Forwarded to Laravel | AspirantController@get_admission_form |
| POST | `/api/employee/get_dashboard_data` | Forwarded to Laravel | AspirantController@get_dashboard_data |
| POST | `/api/employee/get_form_field/{p1?}` | Forwarded to Laravel | AspirantController@get_form_field |
| POST | `/api/employee/get_form_option` | Forwarded to Laravel | AspirantController@get_form_option |
| GET | `/api/employee/get_leave_type` | Forwarded to Laravel | AspirantController@getLeaveType |
| POST | `/api/employee/get_option/{p1?}` | Forwarded to Laravel | AspirantController@get_option |
| POST | `/api/employee/get_page_env` | Forwarded to Laravel | AspirantController@get_page_env |
| POST | `/api/employee/get_page_info` | Forwarded to Laravel | AspirantController@get_page_info |
| GET | `/api/employee/get_search_bar_data/{p1?}` | Forwarded to Laravel | AspirantController@get_search_bar_data |
| POST | `/api/employee/get_side_bar` | Forwarded to Laravel | AspirantController@get_sidebar |
| POST | `/api/employee/get_side_bar1` | Forwarded to Laravel | AspirantController@get_sidebar1 |
| POST | `/api/employee/get_table_data_a` | Forwarded to Laravel | AspirantController@get_table_data |
| POST | `/api/employee/leave_apply` | Forwarded to Laravel | AspirantController@leave_apply |
| POST | `/api/employee/routine_task_btn_status` | Forwarded to Laravel | AspirantController@routineTaskBtnStatus |
| POST | `/api/employee/select_search_option` | Forwarded to Laravel | AspirantController@getGuests |
| POST | `/api/employee/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | AspirantController@store_data |
| POST | `/api/employee/store_module_data/{p1?}/{p2?}` | Forwarded to Laravel | AspirantController@store_module_data |
| GET | `/api/employee/user-profile` | Forwarded to Laravel | AuthController@userProfile |
| POST | `/api/executive/attendance` | Forwarded to Laravel | ExecutiveController@getMonthlyAttendance |
| POST | `/api/executive/delete_data` | Forwarded to Laravel | ExecutiveController@delete_data |
| GET | `/api/executive/drivers` | Forwarded to Laravel | AuthController@drivers |
| POST | `/api/executive/employee_list` | Forwarded to Laravel | ExecutiveController@getEmployeeList |
| POST | `/api/executive/get_admission_form` | Forwarded to Laravel | ExecutiveController@get_admission_form |
| POST | `/api/executive/get_button_info` | Forwarded to Laravel | ExecutiveController@get_button_info |
| POST | `/api/executive/get_dashboard_data` | Forwarded to Laravel | ExecutiveController@get_dashboard_data |
| POST | `/api/executive/get_form_field/{p1?}` | Forwarded to Laravel | ExecutiveController@get_form_field |
| POST | `/api/executive/get_form_option` | Forwarded to Laravel | ExecutiveController@get_form_option |
| POST | `/api/executive/get_option/{p1?}` | Forwarded to Laravel | ExecutiveController@get_option |
| POST | `/api/executive/get_page_env` | Forwarded to Laravel | ExecutiveController@get_page_env |
| POST | `/api/executive/get_page_info` | Forwarded to Laravel | ExecutiveController@get_page_info |
| GET | `/api/executive/get_search_bar_data/{p1?}` | Forwarded to Laravel | ExecutiveController@get_search_bar_data |
| POST | `/api/executive/get_side_bar` | Forwarded to Laravel | ExecutiveController@get_sidebar |
| POST | `/api/executive/get_side_bar1` | Forwarded to Laravel | ExecutiveController@get_sidebar1 |
| POST | `/api/executive/get_table_data_a` | Forwarded to Laravel | ExecutiveController@get_table_data |
| POST | `/api/executive/newOrders` | Forwarded to Laravel | ExecutiveController@newOrders |
| POST | `/api/executive/page_action_button` | Forwarded to Laravel | ExecutiveController@page_action_button |
| POST | `/api/executive/page_action_button_confirm` | Forwarded to Laravel | ExecutiveController@page_action_button_confirm |
| POST | `/api/executive/report_pdf` | Forwarded to Laravel | ExecutiveController@report_pdf |
| POST | `/api/executive/select_search_option` | Forwarded to Laravel | ExecutiveController@getGuests |
| POST | `/api/executive/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | ExecutiveController@store_data |
| POST | `/api/executive/update_order` | Forwarded to Laravel | AuthController@updateOrder |
| POST | `/api/executive/update_order_status` | Forwarded to Laravel | ExecutiveController@update_order_status |
| GET | `/api/executive/user-profile` | Forwarded to Laravel | ExecutiveController@userProfile |
| POST | `/api/kitchen/attendance` | Forwarded to Laravel | kitchenController@getMonthlyAttendance |
| POST | `/api/kitchen/delete_data` | Forwarded to Laravel | kitchenController@delete_data |
| POST | `/api/kitchen/download_pdf` | Forwarded to Laravel | kitchenController@download_pdf |
| GET | `/api/kitchen/drivers` | Forwarded to Laravel | kitchenController@drivers |
| POST | `/api/kitchen/employee_list` | Forwarded to Laravel | kitchenController@getEmployeeList |
| POST | `/api/kitchen/get_button_info` | Forwarded to Laravel | kitchenController@get_button_info |
| POST | `/api/kitchen/get_dashboard_data` | Forwarded to Laravel | kitchenController@get_dashboard_data |
| POST | `/api/kitchen/get_form_field/{p1?}` | Forwarded to Laravel | kitchenController@get_form_field |
| POST | `/api/kitchen/get_form_option` | Forwarded to Laravel | kitchenController@get_form_option |
| POST | `/api/kitchen/get_kot` | Forwarded to Laravel | kitchenController@get_kot |
| POST | `/api/kitchen/get_option/{p1?}` | Forwarded to Laravel | kitchenController@get_option |
| POST | `/api/kitchen/get_page_env` | Forwarded to Laravel | kitchenController@get_page_env |
| POST | `/api/kitchen/get_page_info` | Forwarded to Laravel | kitchenController@get_page_info |
| GET | `/api/kitchen/get_search_bar_data/{p1?}` | Forwarded to Laravel | kitchenController@get_search_bar_data |
| POST | `/api/kitchen/get_side_bar` | Forwarded to Laravel | kitchenController@get_sidebar |
| POST | `/api/kitchen/get_side_bar1` | Forwarded to Laravel | kitchenController@get_sidebar1 |
| POST | `/api/kitchen/get_table_data_a` | Forwarded to Laravel | kitchenController@get_table_data |
| POST | `/api/kitchen/kitchen_order_ticket_current` | Forwarded to Laravel | kitchenController@kitchen_order_ticket_current |
| POST | `/api/kitchen/newOrders` | Forwarded to Laravel | kitchenController@newOrders |
| POST | `/api/kitchen/store_data/{p1?}/{p2?}` | Forwarded to Laravel | kitchenController@store_data |
| POST | `/api/kitchen/update_order` | Forwarded to Laravel | kitchenController@updateOrder |
| POST | `/api/kitchen/update_order_status` | Forwarded to Laravel | kitchenController@update_order_status |
| POST | `/api/manager/assign_special_task` | Forwarded to Laravel | ManagerController@assignSpecialTask |
| POST | `/api/manager/attendance` | Forwarded to Laravel | ManagerController@getMonthlyAttendance |
| POST | `/api/manager/create_task` | Forwarded to Laravel | ManagerController@create_task |
| POST | `/api/manager/create_task_message` | Forwarded to Laravel | ManagerController@create_task_message |
| POST | `/api/manager/create_task_report` | Forwarded to Laravel | ManagerController@create_task_report |
| POST | `/api/manager/create_task_team` | Forwarded to Laravel | ManagerController@createTaskTeam2 |
| POST | `/api/manager/delete_data` | Forwarded to Laravel | ManagerController@delete_data |
| POST | `/api/manager/delete_task/{p1?}` | Forwarded to Laravel | ManagerController@deletetask |
| POST | `/api/manager/employee_list` | Forwarded to Laravel | ManagerController@getEmployeeList |
| POST | `/api/manager/employee_pyramid` | Forwarded to Laravel | ManagerController@employee_pyramid |
| POST | `/api/manager/employee_select_search` | Forwarded to Laravel | ManagerController@employeeSelectSearch |
| POST | `/api/manager/get_admission_form` | Forwarded to Laravel | ManagerController@get_admission_form |
| POST | `/api/manager/get_back_task/{p1?}` | Forwarded to Laravel | ManagerController@getBacktask |
| POST | `/api/manager/get_button_info` | Forwarded to Laravel | ManagerController@get_button_info |
| POST | `/api/manager/get_dashboard_data` | Forwarded to Laravel | ManagerController@get_dashboard_data |
| POST | `/api/manager/get_form_field/{p1?}` | Forwarded to Laravel | ManagerController@get_form_field |
| POST | `/api/manager/get_form_option` | Forwarded to Laravel | ManagerController@get_form_option |
| GET | `/api/manager/get_leave_status` | Forwarded to Laravel | ManagerController@getLeaveStatusOptions |
| GET | `/api/manager/get_leave_type` | Forwarded to Laravel | ManagerController@getLeaveType |
| POST | `/api/manager/get_monthly_attendance_report` | Forwarded to Laravel | ManagerController@getMonthlyAttendanceReport |
| POST | `/api/manager/get_option/{p1?}` | Forwarded to Laravel | ManagerController@get_option |
| POST | `/api/manager/get_page_env` | Forwarded to Laravel | ManagerController@get_page_env |
| POST | `/api/manager/get_page_info` | Forwarded to Laravel | ManagerController@get_page_info |
| GET | `/api/manager/get_partial_day_leave_type` | Forwarded to Laravel | ManagerController@get_partial_day_leave_type |
| GET | `/api/manager/get_search_bar_data/{p1?}` | Forwarded to Laravel | ManagerController@get_search_bar_data |
| POST | `/api/manager/get_side_bar` | Forwarded to Laravel | ManagerController@get_sidebar |
| POST | `/api/manager/get_side_bar1` | Forwarded to Laravel | ManagerController@get_sidebar1 |
| POST | `/api/manager/get_table_data_a` | Forwarded to Laravel | ManagerController@get_table_data |
| POST | `/api/manager/get_task_messages_by_task_id` | Forwarded to Laravel | ManagerController@get_task_messages_by_task_id |
| POST | `/api/manager/get_task_reports` | Forwarded to Laravel | ManagerController@get_task_reports |
| POST | `/api/manager/get_task_statuses` | Forwarded to Laravel | ManagerController@getTaskStatuses |
| POST | `/api/manager/get_task_team` | Forwarded to Laravel | ManagerController@getTaskTeam |
| POST | `/api/manager/leave/update-status` | Forwarded to Laravel | ManagerController@bulkUpdateStatus |
| POST | `/api/manager/leave_apply` | Forwarded to Laravel | ManagerController@leave_apply |
| POST | `/api/manager/page_action_button` | Forwarded to Laravel | ManagerController@page_action_button |
| POST | `/api/manager/page_action_button_confirm` | Forwarded to Laravel | ManagerController@page_action_button_confirm |
| POST | `/api/manager/report_pdf` | Forwarded to Laravel | ManagerController@report_pdf |
| POST | `/api/manager/routine_task_btn_status` | Forwarded to Laravel | ManagerController@routineTaskBtnStatus |
| POST | `/api/manager/run` | Forwarded to Laravel | ManagerController@run |
| POST | `/api/manager/select_search_option` | Forwarded to Laravel | ManagerController@getGuests |
| POST | `/api/manager/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | ManagerController@store_data |
| POST | `/api/manager/sub_task_list` | Forwarded to Laravel | ManagerController@sub_task_list |
| POST | `/api/manager/task_list` | Forwarded to Laravel | ManagerController@task_list |
| POST | `/api/manager/task_select_search` | Forwarded to Laravel | ManagerController@taskSelectSearch |
| POST | `/api/manager/update_task_status` | Forwarded to Laravel | ManagerController@updateTaskStatus |
| GET | `/api/manager/user-profile` | Forwarded to Laravel | ManagerController@userProfile |
| POST | `/api/store_manager/attendance` | Forwarded to Laravel | StoreManagerController@getMonthlyAttendance |
| POST | `/api/store_manager/create_procurement_note` | Forwarded to Laravel | StoreManagerController@create_procurement_note |
| POST | `/api/store_manager/create_procurement_note_search` | Forwarded to Laravel | StoreManagerController@create_procurement_note_search |
| POST | `/api/store_manager/delete_data` | Forwarded to Laravel | StoreManagerController@delete_data |
| POST | `/api/store_manager/edit_procurement_note` | Forwarded to Laravel | StoreManagerController@edit_procurement_note |
| POST | `/api/store_manager/edit_procurement_note_search` | Forwarded to Laravel | StoreManagerController@edit_procurement_note_search |
| POST | `/api/store_manager/employee_list` | Forwarded to Laravel | StoreManagerController@getEmployeeList |
| POST | `/api/store_manager/get_admission_form` | Forwarded to Laravel | StoreManagerController@get_admission_form |
| POST | `/api/store_manager/get_button_info` | Forwarded to Laravel | StoreManagerController@get_button_info |
| POST | `/api/store_manager/get_dashboard_data` | Forwarded to Laravel | StoreManagerController@get_dashboard_data |
| POST | `/api/store_manager/get_form_field/{p1?}` | Forwarded to Laravel | StoreManagerController@get_form_field |
| POST | `/api/store_manager/get_form_option` | Forwarded to Laravel | StoreManagerController@get_form_option |
| POST | `/api/store_manager/get_option/{p1?}` | Forwarded to Laravel | StoreManagerController@get_option |
| POST | `/api/store_manager/get_page_env` | Forwarded to Laravel | StoreManagerController@get_page_env |
| POST | `/api/store_manager/get_page_info` | Forwarded to Laravel | StoreManagerController@get_page_info |
| GET | `/api/store_manager/get_search_bar_data/{p1?}` | Forwarded to Laravel | StoreManagerController@get_search_bar_data |
| POST | `/api/store_manager/get_side_bar` | Forwarded to Laravel | StoreManagerController@get_sidebar |
| POST | `/api/store_manager/get_side_bar1` | Forwarded to Laravel | StoreManagerController@get_sidebar1 |
| POST | `/api/store_manager/get_table_data_a` | Forwarded to Laravel | StoreManagerController@get_table_data |
| POST | `/api/store_manager/ingredient_search` | Forwarded to Laravel | StoreManagerController@ingredient_search |
| POST | `/api/store_manager/page_action_button` | Forwarded to Laravel | StoreManagerController@page_action_button |
| POST | `/api/store_manager/page_action_button_confirm` | Forwarded to Laravel | StoreManagerController@page_action_button_confirm |
| POST | `/api/store_manager/report_pdf` | Forwarded to Laravel | StoreManagerController@report_pdf |
| POST | `/api/store_manager/store_data/{p1?}/{p2?}/{p3?}` | Forwarded to Laravel | StoreManagerController@store_data |
| POST | `/api/store_manager/update_procurement_note` | Forwarded to Laravel | StoreManagerController@update_procurement_note |
| GET | `/api/store_manager/user-profile` | Forwarded to Laravel | StoreManagerController@userProfile |
| POST | `/api/store_manager/vendor_status` | Forwarded to Laravel | StoreManagerController@vendor_status |
| POST | `/api/waiter/attendance` | Forwarded to Laravel | WaiterController@getMonthlyAttendance |
| POST | `/api/waiter/close_table_order` | Forwarded to Laravel | WaiterController@close_table_order |
| POST | `/api/waiter/create_table_order` | Forwarded to Laravel | WaiterController@create_table_order |
| POST | `/api/waiter/delete_data` | Forwarded to Laravel | WaiterController@delete_data |
| POST | `/api/waiter/employee_list` | Forwarded to Laravel | WaiterController@getEmployeeList |
| POST | `/api/waiter/get_button_info` | Forwarded to Laravel | WaiterController@get_button_info |
| POST | `/api/waiter/get_dashboard_data` | Forwarded to Laravel | WaiterController@get_dashboard_data |
| POST | `/api/waiter/get_form_field/{p1?}` | Forwarded to Laravel | WaiterController@get_form_field |
| POST | `/api/waiter/get_form_option` | Forwarded to Laravel | WaiterController@get_form_option |
| POST | `/api/waiter/get_option/{p1?}` | Forwarded to Laravel | WaiterController@get_option |
| POST | `/api/waiter/get_page_env` | Forwarded to Laravel | WaiterController@get_page_env |
| POST | `/api/waiter/get_page_info` | Forwarded to Laravel | WaiterController@get_page_info |
| GET | `/api/waiter/get_search_bar_data/{p1?}` | Forwarded to Laravel | WaiterController@get_search_bar_data |
| POST | `/api/waiter/get_serving_table` | Forwarded to Laravel | WaiterController@get_serving_table |
| POST | `/api/waiter/get_side_bar` | Forwarded to Laravel | WaiterController@get_sidebar |
| POST | `/api/waiter/get_table_data_a` | Forwarded to Laravel | WaiterController@get_table_data |
| POST | `/api/waiter/store_data/{p1?}/{p2?}` | Forwarded to Laravel | WaiterController@store_data |
| POST | `/api/waiter/update_table_order` | Forwarded to Laravel | WaiterController@update_table_order |
