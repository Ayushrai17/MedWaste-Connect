import datetime
import json
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.views import role_required
from facilities.models import TreatmentFacility
from waste.models import WasteCategory
from .models import ComplianceThresholdConfig, GeneratedReportLog
from .services.alert_service import run_compliance_exception_audit
from .services.analytics_engine import (
    get_admin_analytics,
    get_collector_analytics,
    get_facility_analytics,
    get_hospital_analytics,
    parse_date_range,
)
from .services.compliance_engine import evaluate_compliance_checkpoints
from .services.regulatory_reports import (
    generate_report_csv,
    get_category_summary_data,
    get_discrepancy_audit_data,
    get_dispatch_manifest_data,
    get_facility_performance_data,
    get_form_iv_data,
)

User = get_user_model()


@role_required(User.Role.ADMIN)
def admin_analytics(request):
    """
    Central Executive & Regulatory Analytics Console for Platform Administrators.
    """
    preset = request.GET.get("preset", "last_30_days")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    hospital_id = request.GET.get("hospital")
    facility_id = request.GET.get("facility")
    category_code = request.GET.get("category")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    data = get_admin_analytics(
        start_dt=start_dt,
        end_dt=end_dt,
        start_date=start_date,
        end_date=end_date,
        hospital_id=hospital_id if hospital_id else None,
        facility_id=facility_id if facility_id else None,
        category_code=category_code if category_code else None,
    )

    # Filter Options
    hospitals = User.objects.filter(role=User.Role.HOSPITAL).order_by("organization_name", "username")
    facilities = TreatmentFacility.objects.filter(is_active=True).order_by("name")
    categories = WasteCategory.objects.filter(is_active=True).order_by("display_order")

    context = {
        "title": "Platform Analytics & Operational Overview",
        "date_label": date_label,
        "preset": preset,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "selected_hospital": int(hospital_id) if hospital_id and hospital_id.isdigit() else None,
        "selected_facility": int(facility_id) if facility_id and facility_id.isdigit() else None,
        "selected_category": category_code,
        "hospitals": hospitals,
        "facilities": facilities,
        "categories": categories,
        "analytics": data,
        "cat_labels_json": json.dumps(data["cat_labels"]),
        "cat_weights_json": json.dumps(data["cat_weights"]),
        "cat_colors_json": json.dumps(data["cat_colors"]),
        "trend_labels_json": json.dumps(data["trend_labels"]),
        "trend_weights_json": json.dumps(data["trend_weights"]),
    }
    return render(request, "reports/admin_analytics.html", context)


@role_required(User.Role.HOSPITAL)
def hospital_analytics(request):
    """
    Hospital Waste Generation, Segregation & Collection Analytics Dashboard.
    """
    preset = request.GET.get("preset", "last_30_days")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    category_code = request.GET.get("category")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    data = get_hospital_analytics(
        hospital_user=request.user,
        start_dt=start_dt,
        end_dt=end_dt,
        start_date=start_date,
        end_date=end_date,
        category_code=category_code if category_code else None,
    )

    categories = WasteCategory.objects.filter(is_active=True).order_by("display_order")

    context = {
        "title": "Hospital Waste Analytics & Trends",
        "date_label": date_label,
        "preset": preset,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "selected_category": category_code,
        "categories": categories,
        "analytics": data,
        "cat_labels_json": json.dumps(data["cat_labels"]),
        "cat_weights_json": json.dumps(data["cat_weights"]),
        "cat_colors_json": json.dumps(data["cat_colors"]),
        "trend_labels_json": json.dumps(data["trend_labels"]),
        "trend_weights_json": json.dumps(data["trend_weights"]),
    }
    return render(request, "reports/hospital_analytics.html", context)


@role_required(User.Role.COLLECTOR)
def collector_analytics(request):
    """
    Collector Transport Performance, Pickup Turnaround & Verification Analytics.
    """
    preset = request.GET.get("preset", "last_30_days")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    data = get_collector_analytics(
        collector_user=request.user,
        start_dt=start_dt,
        end_dt=end_dt,
        start_date=start_date,
        end_date=end_date,
    )

    context = {
        "title": "Collector Operations & Route Performance",
        "date_label": date_label,
        "preset": preset,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "analytics": data,
    }
    return render(request, "reports/collector_analytics.html", context)


@role_required(User.Role.FACILITY)
def facility_analytics(request):
    """
    Treatment Plant Intake, Autoclave/Incineration Cycles & Residue Disposal Analytics.
    """
    managed_facility = getattr(request.user, "managed_facility", None)
    if not managed_facility:
        messages.error(request, "Your facility account is not currently assigned to a treatment plant.")
        return redirect("dashboard_facility")

    preset = request.GET.get("preset", "last_30_days")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    data = get_facility_analytics(
        facility=managed_facility,
        start_dt=start_dt,
        end_dt=end_dt,
        start_date=start_date,
        end_date=end_date,
    )

    context = {
        "title": f"Facility Analytics — {managed_facility.name}",
        "date_label": date_label,
        "preset": preset,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "analytics": data,
        "method_labels_json": json.dumps(data["method_labels"]),
        "method_weights_json": json.dumps(data["method_weights"]),
    }
    return render(request, "reports/facility_analytics.html", context)


@login_required
def compliance_monitor(request):
    """
    Centralized 12-Checkpoint Biomedical Waste Compliance Audit Console.
    Role-scoped for Admin, Hospital, Collector, and Facility roles.
    """
    preset = request.GET.get("preset", "last_30_days")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    audit_data = evaluate_compliance_checkpoints(
        user=request.user,
        start_dt=start_dt,
        end_dt=end_dt,
    )

    context = {
        "title": "Biomedical Waste Compliance & 12-Point Audit Monitor",
        "date_label": date_label,
        "preset": preset,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "audit": audit_data,
        "checkpoints": audit_data["checkpoints"],
        "threshold_config": audit_data["threshold_config"],
    }
    return render(request, "reports/compliance_monitor.html", context)


@login_required
def regulatory_reports(request):
    """
    Statutory CPCB/SPCB Compliance Reporting Suite with Interactive Preview & Print-Ready View.
    Supports Form IV Annual Return, Segregation Audits, Facility Performance, Dispatch Manifest, and Discrepancies.
    """
    role = getattr(request.user, "role", None)
    if role not in (User.Role.ADMIN, User.Role.HOSPITAL, User.Role.FACILITY):
        raise PermissionDenied("You do not have permission to view statutory regulatory returns.")

    report_type = request.GET.get("report_type", GeneratedReportLog.ReportType.ANNUAL_FORM_IV)
    preset = request.GET.get("preset", "current_year")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    hospital_id = request.GET.get("hospital")
    facility_id = request.GET.get("facility")
    export_format = request.GET.get("format", "preview")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    # Fetch corresponding report data
    if report_type == GeneratedReportLog.ReportType.ANNUAL_FORM_IV:
        data = get_form_iv_data(request.user, start_date, end_date, hospital_id, facility_id)
    elif report_type == GeneratedReportLog.ReportType.CATEGORY_SUMMARY:
        data = get_category_summary_data(request.user, start_date, end_date, hospital_id)
    elif report_type == GeneratedReportLog.ReportType.FACILITY_PERFORMANCE:
        data = get_facility_performance_data(request.user, start_date, end_date, facility_id)
    elif report_type == GeneratedReportLog.ReportType.DISPATCH_MANIFEST:
        data = get_dispatch_manifest_data(request.user, start_date, end_date, hospital_id)
    elif report_type == GeneratedReportLog.ReportType.DISCREPANCY_AUDIT:
        data = get_discrepancy_audit_data(request.user, start_date, end_date)
    else:
        messages.error(request, "Unrecognized regulatory report type.")
        return redirect("reports:regulatory_reports")

    # Log report preview or print
    log_format = GeneratedReportLog.ExportFormat.PRINT if export_format == "print" else GeneratedReportLog.ExportFormat.PREVIEW
    GeneratedReportLog.objects.create(
        report_type=report_type,
        reporting_period_start=start_date,
        reporting_period_end=end_date,
        generated_by=request.user,
        export_format=log_format,
        parameters_summary=f"Preset: {preset}, Range: {start_date} to {end_date}",
        records_count=data.get("total_generated_bags") or data.get("total_bags") or len(data.get("items", [])) or 1,
    )

    # Recent Audit Reports
    recent_logs = GeneratedReportLog.objects.select_related("generated_by").order_by("-generated_at")[:10]

    context = {
        "title": data.get("title", "Statutory Compliance Report"),
        "report_type": report_type,
        "ReportType": GeneratedReportLog.ReportType,
        "preset": preset,
        "date_label": date_label,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "report_data": data,
        "recent_logs": recent_logs,
        "generated_at": timezone.now(),
        "generated_by": request.user,
    }

    if export_format == "print":
        return render(request, "reports/regulatory_report_print.html", context)

    return render(request, "reports/regulatory_reports.html", context)


@login_required
def export_report_csv(request):
    """
    Downloads formula-injection-safe CSV export for any statutory compliance return.
    """
    report_type = request.GET.get("report_type", GeneratedReportLog.ReportType.ANNUAL_FORM_IV)
    preset = request.GET.get("preset", "current_year")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    hospital_id = request.GET.get("hospital")
    facility_id = request.GET.get("facility")

    start_dt, end_dt, start_date, end_date, date_label = parse_date_range(preset, date_from, date_to)

    if report_type == GeneratedReportLog.ReportType.ANNUAL_FORM_IV:
        data = get_form_iv_data(request.user, start_date, end_date, hospital_id, facility_id)
    elif report_type == GeneratedReportLog.ReportType.CATEGORY_SUMMARY:
        data = get_category_summary_data(request.user, start_date, end_date, hospital_id)
    elif report_type == GeneratedReportLog.ReportType.FACILITY_PERFORMANCE:
        data = get_facility_performance_data(request.user, start_date, end_date, facility_id)
    elif report_type == GeneratedReportLog.ReportType.DISPATCH_MANIFEST:
        data = get_dispatch_manifest_data(request.user, start_date, end_date, hospital_id)
    elif report_type == GeneratedReportLog.ReportType.DISCREPANCY_AUDIT:
        data = get_discrepancy_audit_data(request.user, start_date, end_date)
    else:
        messages.error(request, "Invalid report type requested for export.")
        return redirect("reports:regulatory_reports")

    csv_content = generate_report_csv(report_type, data, request.user)

    filename = f"medwaste_{report_type.lower()}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
    response = HttpResponse(csv_content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@role_required(User.Role.ADMIN)
@require_POST
def trigger_compliance_alerts(request):
    """
    Administrative trigger to scan operational records against configured thresholds
    and dispatch notification alerts for pending items.
    """
    result = run_compliance_exception_audit()
    dispatched = result.get("alerts_dispatched", 0)
    messages.success(
        request,
        f"Automated compliance audit completed: {dispatched} operational alert(s) dispatched to stakeholders."
    )
    return redirect("reports:compliance_monitor")
