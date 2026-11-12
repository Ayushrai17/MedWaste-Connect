from django.urls import path
from . import views

app_name = "treatment"

urlpatterns = [
    # Facility Dashboard & Gate Reception
    path("facility/dashboard/", views.facility_dashboard, name="facility_dashboard"),
    path("facility/incoming/", views.facility_incoming_manifests, name="facility_incoming_manifests"),
    path("facility/receive/<str:collection_reference>/", views.facility_receive_consignment, name="facility_receive_consignment"),
    path("facility/receptions/<str:reception_reference>/", views.facility_reception_detail, name="facility_reception_detail"),
    path("facility/receptions/<str:reception_reference>/verify-bag/", views.facility_verify_bag_scan, name="facility_verify_bag_scan"),
    path("facility/receptions/<str:reception_reference>/decision/<int:receipt_id>/", views.facility_bag_decision, name="facility_bag_decision"),
    path("facility/receptions/<str:reception_reference>/finalize/", views.facility_finalize_reception, name="facility_finalize_reception"),

    # Treatment Operations
    path("facility/treatment/", views.treatment_batch_list, name="treatment_batch_list"),
    path("facility/treatment/new/", views.treatment_batch_create, name="treatment_batch_create"),
    path("facility/treatment/<str:batch_number>/", views.treatment_batch_detail, name="treatment_batch_detail"),
    path("facility/treatment/<str:batch_number>/start/", views.treatment_batch_start, name="treatment_batch_start"),
    path("facility/treatment/<str:batch_number>/complete/", views.treatment_batch_complete, name="treatment_batch_complete"),
    path("facility/treatment/<str:batch_number>/hold-fail/", views.treatment_batch_hold_fail, name="treatment_batch_hold_fail"),

    # Disposal Records
    path("facility/disposals/", views.disposal_record_list, name="disposal_record_list"),
    path("facility/disposals/new/<str:batch_number>/", views.disposal_record_create, name="disposal_record_create"),
    path("disposals/<str:disposal_reference>/", views.disposal_record_detail, name="disposal_record_detail"),

    # Administrator Oversight & Verification
    path("admin/overview/", views.admin_treatment_overview, name="admin_treatment_overview"),
    path("admin/disposals/<str:disposal_reference>/verify/", views.admin_verify_disposal, name="admin_verify_disposal"),
]

