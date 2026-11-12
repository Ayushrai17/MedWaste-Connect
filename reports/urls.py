from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    # Role-Specific Analytics Dashboards
    path("admin/", views.admin_analytics, name="admin_analytics"),
    path("hospital/", views.hospital_analytics, name="hospital_analytics"),
    path("collector/", views.collector_analytics, name="collector_analytics"),
    path("facility/", views.facility_analytics, name="facility_analytics"),

    # Compliance Monitoring Console (12 Checkpoints)
    path("compliance/", views.compliance_monitor, name="compliance_monitor"),
    path("compliance/alerts/trigger/", views.trigger_compliance_alerts, name="trigger_compliance_alerts"),

    # Statutory Regulatory Reports & Audits
    path("regulatory/", views.regulatory_reports, name="regulatory_reports"),
    path("export/csv/", views.export_report_csv, name="export_report_csv"),
]

