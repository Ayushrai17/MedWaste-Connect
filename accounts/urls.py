from django.contrib.auth import views as auth_views
from django.urls import path
from . import views, views_admin

urlpatterns = [
    # Auth & Root Dashboards
    path("register/", views.register, name="register"),
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="home"), name="logout"),
    path("dashboard/", views.dashboard_redirect, name="dashboard_redirect"),
    path("dashboard/admin/", views_admin.admin_control_panel, name="dashboard_admin"),
    path("dashboard/hospital/", views.hospital_dashboard, name="dashboard_hospital"),
    path("dashboard/collector/", views.collector_dashboard, name="dashboard_collector"),
    path("dashboard/facility/", views.facility_dashboard, name="dashboard_facility"),

    # Step 7: Central Admin Control Panel & User Management
    path("dashboard/admin/users/", views_admin.admin_user_list, name="admin_user_list"),
    path("dashboard/admin/users/<int:user_id>/", views_admin.admin_user_detail, name="admin_user_detail"),
    path("dashboard/admin/users/<int:user_id>/toggle-status/", views_admin.admin_user_toggle_active, name="admin_user_toggle_active"),
    path("dashboard/admin/users/<int:user_id>/suspend/", views_admin.admin_user_suspend, name="admin_user_suspend"),
    path("dashboard/admin/users/<int:user_id>/reactivate/", views_admin.admin_user_reactivate, name="admin_user_reactivate"),

    # Hospital Organization Management
    path("dashboard/admin/hospitals/", views_admin.admin_hospital_list, name="admin_hospital_list"),
    path("dashboard/admin/hospitals/<int:hospital_id>/", views_admin.admin_hospital_detail, name="admin_hospital_detail"),

    # Collector Logistics Management
    path("dashboard/admin/collectors/", views_admin.admin_collector_list, name="admin_collector_list"),
    path("dashboard/admin/collectors/<int:collector_id>/", views_admin.admin_collector_detail, name="admin_collector_detail"),

    # Onboarding & Facility Approval Queue
    path("dashboard/admin/approvals/", views_admin.admin_approvals_queue, name="admin_approvals_queue"),
    path("dashboard/admin/approvals/facility/<str:facility_code>/approve/", views_admin.admin_approve_facility, name="admin_approve_facility"),
    path("dashboard/admin/approvals/facility/<str:facility_code>/reject/", views_admin.admin_reject_facility, name="admin_reject_facility"),
    path("dashboard/admin/approvals/facility/<str:facility_code>/suspend/", views_admin.admin_suspend_facility, name="admin_suspend_facility"),
    path("dashboard/admin/approvals/facility/<str:facility_code>/reinstate/", views_admin.admin_reinstate_facility, name="admin_reinstate_facility"),
    path("dashboard/admin/approvals/user/<int:user_id>/approve/", views_admin.admin_approve_user, name="admin_approve_user"),
    path("dashboard/admin/approvals/user/<int:user_id>/reject/", views_admin.admin_reject_user, name="admin_reject_user"),

    # Operational Oversight
    path("dashboard/admin/operations/", views_admin.admin_operational_oversight, name="admin_operational_oversight"),

    # Platform Configuration
    path("dashboard/admin/config/", views_admin.admin_platform_config, name="admin_platform_config"),

    # Unified Audit Logs
    path("dashboard/admin/audit-logs/", views_admin.admin_audit_logs, name="admin_audit_logs"),

    # Notification & Alert Center
    path("dashboard/admin/notifications/", views_admin.admin_notifications_center, name="admin_notifications_center"),
    path("dashboard/admin/notifications/<int:notification_id>/review/", views_admin.admin_mark_notification_reviewed, name="admin_mark_notification_reviewed"),
    path("dashboard/admin/notifications/broadcast/", views_admin.admin_broadcast_alert, name="admin_broadcast_alert"),
]
