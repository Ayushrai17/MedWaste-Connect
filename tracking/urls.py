from django.urls import path
from . import views

app_name = "tracking"

urlpatterns = [
    path("dashboard/", views.tracking_dashboard, name="tracking_dashboard"),
    path("bags/<str:bag_id>/", views.bag_tracking_detail, name="bag_tracking_detail"),
    path("bags/<str:bag_id>/report/", views.bag_custody_report, name="bag_custody_report"),
    path("bags/<str:bag_id>/discrepancy/", views.report_bag_discrepancy, name="report_bag_discrepancy"),
    path("bags/<str:bag_id>/correct/", views.add_compensating_correction, name="add_compensating_correction"),
    path("consignments/<str:reference_number>/report/", views.consignment_custody_report, name="consignment_custody_report"),
    path("discrepancies/", views.discrepancy_list, name="discrepancy_list"),
    path("discrepancies/<str:discrepancy_reference>/", views.discrepancy_detail, name="discrepancy_detail"),
]

