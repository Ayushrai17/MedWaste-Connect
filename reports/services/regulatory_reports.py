import csv
import io
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from collection.models import CollectionRequest
from core.utils import quantize_weight
from facilities.models import TreatmentFacility
from reports.models import GeneratedReportLog
from tracking.models import CustodyDiscrepancy, CustodyHandover
from treatment.models import FacilityBagReceipt, FacilityReceptionManifest, TreatmentBatch, DisposalRecord
from waste.models import WasteBag, WasteCategory

User = get_user_model()


def sanitize_csv_cell(value):
    """
    Prevents CSV formula injection by prepending a single quote if string starts with =, +, -, @, tab, or carriage return.
    """
    if value is None:
        return ""
    val_str = str(value).strip()
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str


def get_form_iv_data(user, start_date, end_date, hospital_id=None, facility_id=None):
    """
    Generates CPCB Form IV Bio-Medical Waste Annual / Periodic Return Summary.
    """
    start_dt = timezone.make_aware(timezone.datetime.combine(start_date, timezone.datetime.min.time()))
    end_dt = timezone.make_aware(timezone.datetime.combine(end_date, timezone.datetime.max.time()))

    role = getattr(user, "role", User.Role.ADMIN)
    hospital = user if role == User.Role.HOSPITAL else None
    managed_facility = getattr(user, "managed_facility", None) if role == User.Role.FACILITY else None

    bags_qs = WasteBag.objects.filter(registered_at__range=(start_dt, end_dt))
    batches_qs = TreatmentBatch.objects.filter(created_at__range=(start_dt, end_dt))
    disposals_qs = DisposalRecord.objects.filter(disposal_date__range=(start_date, end_date))
    discrepancies_qs = CustodyDiscrepancy.objects.filter(reported_at__range=(start_dt, end_dt))

    # Tenant scoping
    if role == User.Role.HOSPITAL and hospital:
        bags_qs = bags_qs.filter(hospital=hospital)
        batches_qs = batches_qs.filter(bags__hospital=hospital).distinct()
        disposals_qs = disposals_qs.filter(facility__reception_manifests__collection_request__hospital=hospital).distinct()
        discrepancies_qs = discrepancies_qs.filter(bag__hospital=hospital)
    elif role == User.Role.FACILITY and managed_facility:
        bags_qs = bags_qs.filter(facility_receipts__manifest__facility=managed_facility).distinct()
        batches_qs = batches_qs.filter(facility=managed_facility)
        disposals_qs = disposals_qs.filter(facility=managed_facility)
        discrepancies_qs = discrepancies_qs.filter(
            Q(reported_by=user) | Q(bag__facility_receipts__manifest__facility=managed_facility)
        ).distinct()
    else:
        # Admin scope filters
        if hospital_id:
            bags_qs = bags_qs.filter(hospital_id=hospital_id)
            discrepancies_qs = discrepancies_qs.filter(bag__hospital_id=hospital_id)
        if facility_id:
            batches_qs = batches_qs.filter(facility_id=facility_id)
            disposals_qs = disposals_qs.filter(facility_id=facility_id)

    # Aggregations
    total_generated_bags = bags_qs.count()
    total_generated_weight = quantize_weight(bags_qs.aggregate(w=Sum("weight"))["w"])

    # Category breakdown (Yellow, Red, White, Blue)
    categories = WasteCategory.objects.filter(is_active=True).order_by("display_order")
    cat_summary = []
    for cat in categories:
        cbags = bags_qs.filter(category=cat)
        cnt = cbags.count()
        w = quantize_weight(cbags.aggregate(w=Sum("weight"))["w"])
        pct = (float(w) / float(total_generated_weight) * 100) if total_generated_weight > 0 else 0.0
        cat_summary.append({
            "code": cat.code,
            "name": cat.name,
            "color_code": cat.display_color,
            "treatment_methods": cat.treatment_description,
            "bags_count": cnt,
            "weight_kg": w,
            "percentage": round(pct, 1),
        })

    # Treatment summary
    completed_batches = batches_qs.filter(status=TreatmentBatch.Status.COMPLETED)
    treated_weight = quantize_weight(completed_batches.aggregate(w=Sum("processing_weight"))["w"])
    
    treatment_by_method = []
    method_choices_map = dict(TreatmentBatch.TreatmentMethod.choices)
    for m in completed_batches.values("treatment_method").annotate(cnt=Count("id"), w=Sum("processing_weight")):
        treatment_by_method.append({
            "method": method_choices_map.get(m["treatment_method"], m["treatment_method"]),
            "batches_count": m["cnt"],
            "weight_kg": quantize_weight(m["w"]),
        })

    # Disposal residue summary
    total_disposed_weight = quantize_weight(disposals_qs.aggregate(w=Sum("treatment_batch__processing_weight"))["w"])
    total_residue_weight = total_disposed_weight

    # Discrepancies & incidents
    discrepancy_count = discrepancies_qs.count()
    critical_incidents = discrepancies_qs.filter(severity=CustodyDiscrepancy.Severity.CRITICAL).count()

    # Facility & Hospital details
    hospitals_represented = bags_qs.values("hospital__organization_name", "hospital__username").distinct()
    facilities_represented = batches_qs.values("facility__name", "facility__license_number", "facility__state").distinct()

    return {
        "title": "Form IV — Annual Return of Bio-Medical Waste Management",
        "regulatory_standard": "Bio-Medical Waste Management Rules, 2016 (CPCB/SPCB)",
        "period_start": start_date,
        "period_end": end_date,
        "total_generated_bags": total_generated_bags,
        "total_generated_weight": total_generated_weight,
        "category_summary": cat_summary,
        "treated_weight": treated_weight,
        "treatment_by_method": treatment_by_method,
        "total_disposed_weight": total_disposed_weight,
        "total_residue_weight": total_residue_weight,
        "discrepancy_count": discrepancy_count,
        "critical_incidents": critical_incidents,
        "hospitals_represented": list(hospitals_represented),
        "facilities_represented": list(facilities_represented),
        "license_data_gap_notice": (
            "Treatment facility statutory operating authorization numbers are verified from State PCB registries. "
            "Specific license expiration dates are not presently recorded in the database schema."
        ),
    }


def get_category_summary_data(user, start_date, end_date, hospital_id=None):
    """
    Detailed Category-wise Waste Generation & Segregation Audit.
    """
    start_dt = timezone.make_aware(timezone.datetime.combine(start_date, timezone.datetime.min.time()))
    end_dt = timezone.make_aware(timezone.datetime.combine(end_date, timezone.datetime.max.time()))

    role = getattr(user, "role", User.Role.ADMIN)
    hospital = user if role == User.Role.HOSPITAL else None

    bags_qs = WasteBag.objects.filter(registered_at__range=(start_dt, end_dt)).select_related("category", "hospital")

    if role == User.Role.HOSPITAL and hospital:
        bags_qs = bags_qs.filter(hospital=hospital)
    elif hospital_id:
        bags_qs = bags_qs.filter(hospital_id=hospital_id)

    total_bags = bags_qs.count()
    total_weight = quantize_weight(bags_qs.aggregate(w=Sum("weight"))["w"])

    categories = WasteCategory.objects.filter(is_active=True).order_by("display_order")
    rows = []

    for cat in categories:
        cat_bags = bags_qs.filter(category=cat)
        cnt = cat_bags.count()
        w = quantize_weight(cat_bags.aggregate(w=Sum("weight"))["w"])
        pct = (float(w) / float(total_weight) * 100) if total_weight > 0 else 0.0

        status_counts = {
            "registered": cat_bags.filter(status=WasteBag.Status.REGISTERED).count(),
            "in_transit": cat_bags.filter(status=WasteBag.Status.IN_TRANSIT).count(),
            "received": cat_bags.filter(status=WasteBag.Status.RECEIVED).count(),
            "treated": cat_bags.filter(status=WasteBag.Status.TREATED).count(),
            "disposed": cat_bags.filter(status=WasteBag.Status.DISPOSED).count(),
        }

        rows.append({
            "category_code": cat.code,
            "category_name": cat.name,
            "color_code": cat.display_color,
            "treatment_methods": cat.treatment_description,
            "bags_count": cnt,
            "weight_kg": w,
            "percentage": round(pct, 1),
            "status_counts": status_counts,
        })

    return {
        "title": "Category-Wise Waste Generation & Segregation Audit",
        "period_start": start_date,
        "period_end": end_date,
        "total_bags": total_bags,
        "total_weight": total_weight,
        "rows": rows,
    }


def get_facility_performance_data(user, start_date, end_date, facility_id=None):
    """
    Treatment Plant Reception & Treatment Audit.
    """
    start_dt = timezone.make_aware(timezone.datetime.combine(start_date, timezone.datetime.min.time()))
    end_dt = timezone.make_aware(timezone.datetime.combine(end_date, timezone.datetime.max.time()))

    role = getattr(user, "role", User.Role.ADMIN)
    managed_facility = getattr(user, "managed_facility", None) if role == User.Role.FACILITY else None

    facilities_qs = TreatmentFacility.objects.filter(is_active=True)
    if role == User.Role.FACILITY and managed_facility:
        facilities_qs = facilities_qs.filter(id=managed_facility.id)
    elif facility_id:
        facilities_qs = facilities_qs.filter(id=facility_id)

    facility_reports = []
    days_count = max((end_date - start_date).days + 1, 1)

    for fac in facilities_qs:
        manifests = FacilityReceptionManifest.objects.filter(facility=fac, received_at__range=(start_dt, end_dt))
        receipts = FacilityBagReceipt.objects.filter(reception_manifest__facility=fac, verified_at__range=(start_dt, end_dt))
        batches = TreatmentBatch.objects.filter(facility=fac, created_at__range=(start_dt, end_dt))
        disposals = DisposalRecord.objects.filter(facility=fac, disposal_date__range=(start_date, end_date))

        total_received_bags = receipts.count()
        accepted_bags = receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED).count()
        rejected_bags = receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED).count()
        missing_bags = receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING).count()

        received_weight = quantize_weight(receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED).aggregate(w=Sum("actual_weight"))["w"])
        
        completed_batches_cnt = batches.filter(status=TreatmentBatch.Status.COMPLETED).count()
        treated_weight = quantize_weight(batches.filter(status=TreatmentBatch.Status.COMPLETED).aggregate(w=Sum("processing_weight"))["w"])
        failed_batches_cnt = batches.filter(status=TreatmentBatch.Status.FAILED).count()

        disposed_weight = quantize_weight(disposals.aggregate(w=Sum("treatment_batch__processing_weight"))["w"])

        total_period_capacity = fac.daily_capacity_kg * days_count
        utilization_pct = (
            round((float(received_weight) / float(total_period_capacity)) * 100, 1)
            if total_period_capacity > 0 else 0.0
        )

        facility_reports.append({
            "facility": fac,
            "manifests_count": manifests.count(),
            "received_bags": total_received_bags,
            "accepted_bags": accepted_bags,
            "rejected_bags": rejected_bags,
            "missing_bags": missing_bags,
            "received_weight": received_weight,
            "completed_batches": completed_batches_cnt,
            "failed_batches": failed_batches_cnt,
            "treated_weight": treated_weight,
            "disposed_weight": disposed_weight,
            "daily_capacity": fac.daily_capacity_kg,
            "total_period_capacity": total_period_capacity,
            "utilization_pct": utilization_pct,
        })

    return {
        "title": "Treatment Facility Reception & Processing Audit",
        "period_start": start_date,
        "period_end": end_date,
        "days_count": days_count,
        "facilities": facility_reports,
    }


def get_dispatch_manifest_data(user, start_date, end_date, hospital_id=None, collector_id=None):
    """
    Collection Dispatch & Consignment Transit Log.
    """
    start_dt = timezone.make_aware(timezone.datetime.combine(start_date, timezone.datetime.min.time()))
    end_dt = timezone.make_aware(timezone.datetime.combine(end_date, timezone.datetime.max.time()))

    role = getattr(user, "role", User.Role.ADMIN)
    reqs_qs = CollectionRequest.objects.filter(created_at__range=(start_dt, end_dt)).select_related(
        "hospital", "assigned_collector", "destination_facility"
    ).order_by("-created_at")

    if role == User.Role.HOSPITAL:
        reqs_qs = reqs_qs.filter(hospital=user)
    elif role == User.Role.COLLECTOR:
        reqs_qs = reqs_qs.filter(assigned_collector=user)
    elif role == User.Role.FACILITY and getattr(user, "managed_facility", None):
        reqs_qs = reqs_qs.filter(destination_facility=user.managed_facility)
    else:
        if hospital_id:
            reqs_qs = reqs_qs.filter(hospital_id=hospital_id)
        if collector_id:
            reqs_qs = reqs_qs.filter(assigned_collector_id=collector_id)

    items = []
    for req in reqs_qs[:200]:  # Cap at 200 for printable / preview efficiency
        verified_count = req.bag_verifications.filter(is_verified=True).count()
        total_req_bags = req.bag_verifications.count()
        handovers = CustodyHandover.objects.filter(collection_request=req)
        has_dispute = handovers.filter(status=CustodyHandover.Status.DISPUTED).exists()

        sched_str = f"{req.confirmed_pickup_date} {req.confirmed_pickup_time}" if req.confirmed_pickup_date and req.confirmed_pickup_time else f"{req.preferred_pickup_date} {req.preferred_pickup_time}"

        items.append({
            "tracking_number": req.reference_number,
            "hospital_name": req.hospital.organization_name or req.hospital.username if req.hospital else "—",
            "collector_name": req.assigned_collector.get_full_name() or req.assigned_collector.username if req.assigned_collector else "Unassigned",
            "vehicle_number": "—",
            "status": req.get_status_display(),
            "scheduled_time": sched_str,
            "actual_time": req.actual_collection_time,
            "completed_time": req.actual_collection_time,
            "facility_name": req.destination_facility.name if req.destination_facility else "—",
            "verified_bags": verified_count,
            "total_bags": total_req_bags or req.total_bags,
            "total_weight": req.total_weight or Decimal("0.00"),
            "has_dispute": has_dispute,
        })

    return {
        "title": "Collection Dispatch & Consignment Transit Log",
        "period_start": start_date,
        "period_end": end_date,
        "total_requests": reqs_qs.count(),
        "items": items,
    }


def get_discrepancy_audit_data(user, start_date, end_date):
    """
    Exceptions & Discrepancy Reconciliation Audit.
    """
    start_dt = timezone.make_aware(timezone.datetime.combine(start_date, timezone.datetime.min.time()))
    end_dt = timezone.make_aware(timezone.datetime.combine(end_date, timezone.datetime.max.time()))

    role = getattr(user, "role", User.Role.ADMIN)
    discrepancies_qs = CustodyDiscrepancy.objects.filter(
        reported_at__range=(start_dt, end_dt)
    ).select_related("bag", "reported_by", "resolved_by").order_by("-reported_at")

    if role == User.Role.HOSPITAL:
        discrepancies_qs = discrepancies_qs.filter(bag__hospital=user)
    elif role == User.Role.COLLECTOR:
        discrepancies_qs = discrepancies_qs.filter(
            Q(reported_by=user) | Q(bag__collection_verifications__collection_request__assigned_collector=user)
        ).distinct()
    elif role == User.Role.FACILITY and getattr(user, "managed_facility", None):
        discrepancies_qs = discrepancies_qs.filter(
            Q(reported_by=user) | Q(bag__facility_receipts__manifest__facility=user.managed_facility)
        ).distinct()

    items = []
    for disc in discrepancies_qs[:200]:
        items.append({
            "discrepancy_id": disc.id,
            "bag_barcode": disc.bag.bag_id if disc.bag else "—",
            "discrepancy_type": disc.get_discrepancy_type_display(),
            "severity": disc.severity,
            "status": disc.get_status_display(),
            "expected_value": disc.expected_value or "—",
            "found_value": disc.found_value or "—",
            "weight_variance": disc.weight_variance_kg,
            "reported_by": disc.reported_by.username if disc.reported_by else "System",
            "reported_at": disc.reported_at,
            "resolved_at": disc.resolved_at,
            "resolution_notes": disc.resolution_notes or "—",
        })

    return {
        "title": "Exceptions & Discrepancy Reconciliation Audit",
        "period_start": start_date,
        "period_end": end_date,
        "total_discrepancies": discrepancies_qs.count(),
        "open_count": discrepancies_qs.filter(status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]).count(),
        "resolved_count": discrepancies_qs.filter(status=CustodyDiscrepancy.Status.RESOLVED).count(),
        "critical_count": discrepancies_qs.filter(severity=CustodyDiscrepancy.Severity.CRITICAL).count(),
        "items": items,
    }


def generate_report_csv(report_type, data, user):
    """
    Generates a secure, formula-injection-safe CSV string buffer and logs the generation event.
    """
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Standard Regulatory Header Block
    writer.writerow([sanitize_csv_cell("MEDWASTE CONNECT — STATUTORY & OPERATIONAL COMPLIANCE REPORT")])
    writer.writerow([sanitize_csv_cell(f"Report: {data.get('title', report_type)}")])
    writer.writerow([sanitize_csv_cell(f"Reporting Period: {data.get('period_start')} to {data.get('period_end')}")])
    writer.writerow([sanitize_csv_cell(f"Generated By: {user.username} ({user.get_role_display()})")])
    writer.writerow([sanitize_csv_cell(f"Timestamp: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")])
    writer.writerow([])  # Blank spacer

    records_count = 0

    if report_type == GeneratedReportLog.ReportType.ANNUAL_FORM_IV:
        writer.writerow([
            sanitize_csv_cell("Category Code"),
            sanitize_csv_cell("Category Name"),
            sanitize_csv_cell("Mandated Treatment Method"),
            sanitize_csv_cell("Total Bags"),
            sanitize_csv_cell("Total Weight (kg)"),
            sanitize_csv_cell("Share (%)"),
        ])
        for row in data.get("category_summary", []):
            writer.writerow([
                sanitize_csv_cell(row["code"]),
                sanitize_csv_cell(row["name"]),
                sanitize_csv_cell(row["treatment_methods"]),
                sanitize_csv_cell(row["bags_count"]),
                sanitize_csv_cell(str(row["weight_kg"])),
                sanitize_csv_cell(str(row["percentage"])),
            ])
            records_count += 1

        writer.writerow([])
        writer.writerow([sanitize_csv_cell("Treatment Method"), sanitize_csv_cell("Batches Run"), sanitize_csv_cell("Treated Weight (kg)")])
        for tm in data.get("treatment_by_method", []):
            writer.writerow([
                sanitize_csv_cell(tm["method"]),
                sanitize_csv_cell(tm["batches_count"]),
                sanitize_csv_cell(str(tm["weight_kg"])),
            ])

        writer.writerow([])
        writer.writerow([sanitize_csv_cell("Regulatory Metric"), sanitize_csv_cell("Value")])
        writer.writerow([sanitize_csv_cell("Total Generated Bags"), sanitize_csv_cell(data.get("total_generated_bags"))])
        writer.writerow([sanitize_csv_cell("Total Generated Weight (kg)"), sanitize_csv_cell(str(data.get("total_generated_weight")))])
        writer.writerow([sanitize_csv_cell("Total Treated Weight (kg)"), sanitize_csv_cell(str(data.get("treated_weight")))])
        writer.writerow([sanitize_csv_cell("Total Disposed Residue (kg)"), sanitize_csv_cell(str(data.get("total_residue_weight")))])
        writer.writerow([sanitize_csv_cell("Reported Discrepancies"), sanitize_csv_cell(data.get("discrepancy_count"))])
        writer.writerow([sanitize_csv_cell("Critical Incident Count"), sanitize_csv_cell(data.get("critical_incidents"))])

    elif report_type == GeneratedReportLog.ReportType.CATEGORY_SUMMARY:
        writer.writerow([
            sanitize_csv_cell("Category Code"),
            sanitize_csv_cell("Category Name"),
            sanitize_csv_cell("Color"),
            sanitize_csv_cell("Total Bags"),
            sanitize_csv_cell("Total Weight (kg)"),
            sanitize_csv_cell("Share (%)"),
            sanitize_csv_cell("Registered"),
            sanitize_csv_cell("In Transit"),
            sanitize_csv_cell("Received"),
            sanitize_csv_cell("Treated"),
            sanitize_csv_cell("Disposed"),
        ])
        for r in data.get("rows", []):
            sc = r["status_counts"]
            writer.writerow([
                sanitize_csv_cell(r["category_code"]),
                sanitize_csv_cell(r["category_name"]),
                sanitize_csv_cell(r["color_code"]),
                sanitize_csv_cell(r["bags_count"]),
                sanitize_csv_cell(str(r["weight_kg"])),
                sanitize_csv_cell(str(r["percentage"])),
                sanitize_csv_cell(sc["registered"]),
                sanitize_csv_cell(sc["in_transit"]),
                sanitize_csv_cell(sc["received"]),
                sanitize_csv_cell(sc["treated"]),
                sanitize_csv_cell(sc["disposed"]),
            ])
            records_count += 1

    elif report_type == GeneratedReportLog.ReportType.FACILITY_PERFORMANCE:
        writer.writerow([
            sanitize_csv_cell("Facility Name"),
            sanitize_csv_cell("License Number"),
            sanitize_csv_cell("Daily Cap (kg)"),
            sanitize_csv_cell("Period Intake (kg)"),
            sanitize_csv_cell("Accepted Bags"),
            sanitize_csv_cell("Rejected Bags"),
            sanitize_csv_cell("Missing Bags"),
            sanitize_csv_cell("Completed Batches"),
            sanitize_csv_cell("Treated Weight (kg)"),
            sanitize_csv_cell("Residue Disposed (kg)"),
            sanitize_csv_cell("Capacity Utilization (%)"),
        ])
        for fac in data.get("facilities", []):
            writer.writerow([
                sanitize_csv_cell(fac["facility"].name),
                sanitize_csv_cell(fac["facility"].license_number),
                sanitize_csv_cell(str(fac["daily_capacity"])),
                sanitize_csv_cell(str(fac["received_weight"])),
                sanitize_csv_cell(fac["accepted_bags"]),
                sanitize_csv_cell(fac["rejected_bags"]),
                sanitize_csv_cell(fac["missing_bags"]),
                sanitize_csv_cell(fac["completed_batches"]),
                sanitize_csv_cell(str(fac["treated_weight"])),
                sanitize_csv_cell(str(fac["disposed_weight"])),
                sanitize_csv_cell(str(fac["utilization_pct"])),
            ])
            records_count += 1

    elif report_type == GeneratedReportLog.ReportType.DISPATCH_MANIFEST:
        writer.writerow([
            sanitize_csv_cell("Request / Manifest No"),
            sanitize_csv_cell("Hospital"),
            sanitize_csv_cell("Collector"),
            sanitize_csv_cell("Vehicle"),
            sanitize_csv_cell("Status"),
            sanitize_csv_cell("Bags Verified"),
            sanitize_csv_cell("Total Bags"),
            sanitize_csv_cell("Total Weight (kg)"),
            sanitize_csv_cell("Facility"),
            sanitize_csv_cell("Disputed Handover"),
        ])
        for item in data.get("items", []):
            writer.writerow([
                sanitize_csv_cell(item["tracking_number"]),
                sanitize_csv_cell(item["hospital_name"]),
                sanitize_csv_cell(item["collector_name"]),
                sanitize_csv_cell(item["vehicle_number"]),
                sanitize_csv_cell(item["status"]),
                sanitize_csv_cell(item["verified_bags"]),
                sanitize_csv_cell(item["total_bags"]),
                sanitize_csv_cell(str(item["total_weight"])),
                sanitize_csv_cell(item["facility_name"]),
                sanitize_csv_cell("YES" if item["has_dispute"] else "NO"),
            ])
            records_count += 1

    elif report_type == GeneratedReportLog.ReportType.DISCREPANCY_AUDIT:
        writer.writerow([
            sanitize_csv_cell("Discrepancy ID"),
            sanitize_csv_cell("Bag Barcode"),
            sanitize_csv_cell("Type"),
            sanitize_csv_cell("Severity"),
            sanitize_csv_cell("Status"),
            sanitize_csv_cell("Expected Value"),
            sanitize_csv_cell("Found Value"),
            sanitize_csv_cell("Weight Variance (kg)"),
            sanitize_csv_cell("Reported By"),
            sanitize_csv_cell("Reported At"),
            sanitize_csv_cell("Resolved At"),
        ])
        for item in data.get("items", []):
            writer.writerow([
                sanitize_csv_cell(item["discrepancy_id"]),
                sanitize_csv_cell(item["bag_barcode"]),
                sanitize_csv_cell(item["discrepancy_type"]),
                sanitize_csv_cell(item["severity"]),
                sanitize_csv_cell(item["status"]),
                sanitize_csv_cell(item["expected_value"]),
                sanitize_csv_cell(item["found_value"]),
                sanitize_csv_cell(str(item["weight_variance"]) if item["weight_variance"] is not None else "0.00"),
                sanitize_csv_cell(item["reported_by"]),
                sanitize_csv_cell(item["reported_at"].strftime("%Y-%m-%d %H:%M") if item["reported_at"] else "—"),
                sanitize_csv_cell(item["resolved_at"].strftime("%Y-%m-%d %H:%M") if item["resolved_at"] else "—"),
            ])
            records_count += 1

    # Log Report Generation Audit Entry
    GeneratedReportLog.objects.create(
        report_type=report_type,
        reporting_period_start=data.get("period_start"),
        reporting_period_end=data.get("period_end"),
        generated_by=user,
        export_format=GeneratedReportLog.ExportFormat.CSV,
        parameters_summary=f"Role: {user.get_role_display()}, Exported: {records_count} records",
        records_count=records_count,
    )

    return output.getvalue()
