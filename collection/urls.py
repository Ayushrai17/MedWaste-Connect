from django.urls import path
from . import views

app_name = "collection"

urlpatterns = [
    # Hospital Endpoints
    path("requests/", views.hospital_request_list, name="hospital_request_list"),
    path("requests/new/", views.hospital_request_create, name="hospital_request_create"),
    path("requests/<str:reference_number>/", views.collection_detail_view, name="hospital_request_detail"),
    path("requests/<str:reference_number>/cancel/", views.hospital_request_cancel, name="hospital_request_cancel"),

    # Admin Management Endpoints
    path("admin/", views.admin_collection_dashboard, name="admin_dashboard"),
    path("admin/requests/<str:reference_number>/", views.collection_detail_view, name="admin_request_detail"),
    path("admin/requests/<str:reference_number>/schedule/", views.admin_schedule_assign_view, name="admin_schedule_assign"),
    path("admin/requests/<str:reference_number>/approve/", views.admin_approve_view, name="admin_approve"),
    path("admin/requests/<str:reference_number>/reject/", views.admin_reject_view, name="admin_reject"),

    # Collector Logistics Endpoints
    path("collector/", views.collector_dashboard_view, name="collector_dashboard"),
    path("collector/requests/<str:reference_number>/", views.collection_detail_view, name="collector_request_detail"),
    path("collector/requests/<str:reference_number>/accept/", views.collector_accept_view, name="collector_accept"),
    path("collector/requests/<str:reference_number>/reject/", views.collector_reject_view, name="collector_reject"),
    path("collector/requests/<str:reference_number>/start/", views.collector_start_pickup_view, name="collector_start"),
    path("collector/requests/<str:reference_number>/collect/", views.collector_complete_collection_view, name="collector_collect"),
    path("collector/requests/<str:reference_number>/verify-bag/", views.collection_verify_bag_view, name="collector_verify_bag"),
]

