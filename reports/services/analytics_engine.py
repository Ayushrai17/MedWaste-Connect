import calendar
import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.models import (
    Avg,
    Count,
    DurationField,
    F,
    ExpressionWrapper,
    Q,
    Sum,
)
from django.utils import timezone

from collection.models import CollectionRequest, BagVerificationRecord
from core.utils import quantize_weight
from facilities.models import TreatmentFacility
from tracking.models import CustodyEvent, CustodyHandover, CustodyDiscrepancy
from treatment.models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
)
from waste.models import WasteBag, WasteCategory, WasteRegistrationBatch

User = get_user_model()


def parse_date_range(preset="last_30_days", date_from=None, date_to=None):
    """
    Parses date range presets or custom dates with consistent timezone handling.
    Returns (start_datetime, end_datetime, label).
    """
    now = timezone.now()
    today = timezone.localdate()

    if preset == "today":
        start_date = today
        end_date = today
        label = "Today"
    elif preset == "last_7_days":
        start_date = today - datetime.timedelta(days=7)
        end_date = today
        label = "Last 7 Days"
    elif preset == "last_30_days":
        start_date = today - datetime.timedelta(days=30)
        end_date = today
        label = "Last 30 Days"
    elif preset == "current_month":
        start_date = today.replace(day=1)
        end_date = today
        label = f"Current Month ({today.strftime('%B %Y')})"
    elif preset == "previous_month":
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month - datetime.timedelta(days=1)
        start_date = last_day_prev_month.replace(day=1)
        end_date = last_day_prev_month
        label = f"Previous Month ({start_date.strftime('%B %Y')})"
    elif preset == "current_year":
        start_date = today.replace(month=1, day=1)
        end_date = today
        label = f"Current Year ({today.year})"
    elif preset == "custom" and date_from and date_to:
        try:
            if isinstance(date_from, str):
                start_date = datetime.date.fromisoformat(date_from.strip())
            else:
                start_date = date_from
            if isinstance(date_to, str):
                end_date = datetime.date.fromisoformat(date_to.strip())
            else:
                end_date = date_to

            # Ensure start <= end
            if start_date > end_date:
                start_date, end_date = end_date, start_date
            label = f"Custom ({start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')})"
        except (ValueError, TypeError):
            # Fallback
            start_date = today - datetime.timedelta(days=30)
            end_date = today
            label = "Last 30 Days (Fallback)"
    elif preset == "all_time":
        start_date = datetime.date(2020, 1, 1)
        end_date = today
        label = "All Time"
    else:
        start_date = today - datetime.timedelta(days=30)
        end_date = today
        label = "Last 30 Days"

    # Convert dates to aware datetimes bounding the entire days
    start_dt = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min))
    end_dt = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))

    return start_dt, end_dt, start_date, end_date, label


def get_admin_analytics(start_dt, end_dt, start_date, end_date, hospital_id=None, facility_id=None, category_code=None):
    """
    Central administrative platform-wide analytics aggregation engine.
    Computes Waste Overview, Collection Performance, Treatment Performance,
    and Compliance Indicators.
    """
    # Base QuerySets filtered by date range
    bags_qs = WasteBag.objects.filter(registered_at__range=(start_dt, end_dt))
    reqs_qs = CollectionRequest.objects.filter(created_at__range=(start_dt, end_dt))
    manifests_qs = FacilityReceptionManifest.objects.filter(received_at__range=(start_dt, end_dt))
    batches_qs = TreatmentBatch.objects.filter(created_at__range=(start_dt, end_dt))
    disposals_qs = DisposalRecord.objects.filter(disposal_date__range=(start_date, end_date))
    discrepancies_qs = CustodyDiscrepancy.objects.filter(reported_at__range=(start_dt, end_dt))
    handovers_qs = CustodyHandover.objects.filter(handover_timestamp__range=(start_dt, end_dt))

    # Optional Admin Scope Filters
    if hospital_id:
        bags_qs = bags_qs.filter(hospital_id=hospital_id)
        reqs_qs = reqs_qs.filter(hospital_id=hospital_id)
        discrepancies_qs = discrepancies_qs.filter(bag__hospital_id=hospital_id)
    if facility_id:
        manifests_qs = manifests_qs.filter(facility_id=facility_id)
        batches_qs = batches_qs.filter(facility_id=facility_id)
        disposals_qs = disposals_qs.filter(facility_id=facility_id)
    if category_code:
        bags_qs = bags_qs.filter(category__code=category_code)

    # 1. Waste Overview Aggregations
    waste_aggs = bags_qs.aggregate(
        total_bags=Count("id"),
        total_weight=Sum("weight"),
        in_transit=Count("id", filter=Q(status=WasteBag.Status.IN_TRANSIT)),
        received=Count("id", filter=Q(status=WasteBag.Status.RECEIVED)),
        treated=Count("id", filter=Q(status=WasteBag.Status.TREATED)),
        disposed=Count("id", filter=Q(status=WasteBag.Status.DISPOSED)),
    )
    total_bags = waste_aggs["total_bags"] or 0
    total_weight = quantize_weight(waste_aggs["total_weight"])
    hospitals_count = bags_qs.values("hospital").distinct().count()
    active_requests_count = CollectionRequest.objects.filter(status__in=CollectionRequest.ACTIVE_STATUSES).count()

    # Category Breakdown
    active_cats = WasteCategory.objects.filter(is_active=True).order_by("display_order")
    cat_aggs = (
        bags_qs.values("category__code")
        .annotate(count=Count("id"), weight=Sum("weight"))
    )
    cat_map = {item["category__code"]: item for item in cat_aggs}
    category_distribution = []
    cat_labels = []
    cat_weights = []
    cat_counts = []
    cat_colors = []

    for c in active_cats:
        item = cat_map.get(c.code, {})
        c_count = item.get("count", 0)
        c_weight = quantize_weight(item.get("weight"))
        weight_pct = round((float(c_weight) / float(total_weight)) * 100, 1) if total_weight > 0 else 0.0

        category_distribution.append({
            "category": c,
            "count": c_count,
            "weight": c_weight,
            "percentage": weight_pct,
        })
        cat_labels.append(f"{c.code} ({c.name})")
        cat_weights.append(float(c_weight))
        cat_counts.append(c_count)
        cat_colors.append(c.display_color)

    # Lifecycle Stage Distribution
    stage_breakdown = [
        {"stage": "Ward Registered", "count": total_bags - (waste_aggs["in_transit"] + waste_aggs["received"] + waste_aggs["treated"] + waste_aggs["disposed"]), "color": "#64748B"},
        {"stage": "In Transit", "count": waste_aggs["in_transit"] or 0, "color": "#F36B21"},
        {"stage": "Received at Plant", "count": waste_aggs["received"] or 0, "color": "#007A68"},
        {"stage": "Treated & Decontaminated", "count": waste_aggs["treated"] or 0, "color": "#005B50"},
        {"stage": "Final Residue Disposed", "count": waste_aggs["disposed"] or 0, "color": "#071F1D"},
    ]

    # Daily Generation Trend (Last 14 days or days in range up to 30)
    num_days = min((end_date - start_date).days + 1, 31)
    daily_labels = []
    daily_weights = []
    daily_counts = []
    cur_date = end_date - datetime.timedelta(days=num_days - 1)
    for _ in range(num_days):
        d_start = timezone.make_aware(datetime.datetime.combine(cur_date, datetime.time.min))
        d_end = timezone.make_aware(datetime.datetime.combine(cur_date, datetime.time.max))
        d_agg = bags_qs.filter(registered_at__range=(d_start, d_end)).aggregate(
            w=Sum("weight"), c=Count("id")
        )
        daily_labels.append(cur_date.strftime("%d %b"))
        daily_weights.append(float(quantize_weight(d_agg["w"])))
        daily_counts.append(d_agg["c"] or 0)
        cur_date += datetime.timedelta(days=1)

    # 2. Collection Performance Aggregations
    req_aggs = reqs_qs.aggregate(
        total_reqs=Count("id"),
        pending=Count("id", filter=Q(status__in=[CollectionRequest.Status.REQUESTED, CollectionRequest.Status.UNDER_REVIEW])),
        scheduled=Count("id", filter=Q(status__in=[CollectionRequest.Status.APPROVED, CollectionRequest.Status.SCHEDULED])),
        assigned=Count("id", filter=Q(status__in=[CollectionRequest.Status.ASSIGNED, CollectionRequest.Status.ACCEPTED])),
        in_progress=Count("id", filter=Q(status=CollectionRequest.Status.PICKUP_IN_PROGRESS)),
        completed=Count("id", filter=Q(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED])),
        cancelled=Count("id", filter=Q(status=CollectionRequest.Status.CANCELLED)),
        rejected=Count("id", filter=Q(status=CollectionRequest.Status.COLLECTOR_REJECTED)),
    )

    # Average turnaround times calculation (using completed collections)
    completed_reqs = reqs_qs.filter(
        status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED],
        actual_collection_time__isnull=False,
    )
    pickup_turnaround_hours = []
    for r in completed_reqs.only("created_at", "actual_collection_time")[:500]:
        diff_hours = (r.actual_collection_time - r.created_at).total_seconds() / 3600.0
        if diff_hours >= 0:
            pickup_turnaround_hours.append(diff_hours)
    avg_pickup_turnaround = round(sum(pickup_turnaround_hours) / len(pickup_turnaround_hours), 1) if pickup_turnaround_hours else None

    # Turnaround from pickup to facility reception
    reception_turnaround_hours = []
    manifests_with_actual = manifests_qs.filter(
        collection_request__actual_collection_time__isnull=False
    ).select_related("collection_request")[:500]
    for m in manifests_with_actual:
        diff_h = (m.received_at - m.collection_request.actual_collection_time).total_seconds() / 3600.0
        if diff_h >= 0:
            reception_turnaround_hours.append(diff_h)
    avg_reception_turnaround = round(sum(reception_turnaround_hours) / len(reception_turnaround_hours), 1) if reception_turnaround_hours else None

    # 3. Treatment Performance Aggregations
    total_facilities = TreatmentFacility.objects.count()
    approved_facilities = TreatmentFacility.objects.filter(approval_status=TreatmentFacility.ApprovalStatus.APPROVED).count()
    suspended_facilities = TreatmentFacility.objects.filter(approval_status=TreatmentFacility.ApprovalStatus.SUSPENDED).count()

    batch_aggs = batches_qs.aggregate(
        total_batches=Count("id"),
        in_progress=Count("id", filter=Q(status=TreatmentBatch.Status.IN_PROGRESS)),
        completed=Count("id", filter=Q(status=TreatmentBatch.Status.COMPLETED)),
        failed_hold=Count("id", filter=Q(status__in=[TreatmentBatch.Status.FAILED, TreatmentBatch.Status.ON_HOLD])),
        total_treated_weight=Sum("processing_weight", filter=Q(status=TreatmentBatch.Status.COMPLETED)),
    )
    total_treated_weight = quantize_weight(batch_aggs["total_treated_weight"])

    # Treatment Method Distribution
    method_aggs = (
        batches_qs.filter(status=TreatmentBatch.Status.COMPLETED)
        .values("treatment_method")
        .annotate(count=Count("id"), weight=Sum("processing_weight"))
    )
    method_choices_map = dict(TreatmentBatch.TreatmentMethod.choices)
    method_distribution = []
    for m in method_aggs:
        method_distribution.append({
            "method_code": m["treatment_method"],
            "method_name": method_choices_map.get(m["treatment_method"], m["treatment_method"]),
            "count": m["count"],
            "weight": quantize_weight(m["weight"]),
        })

    # Capacity utilization calculation
    # Sum of daily capacity for approved facilities * number of days in range
    days_count = max((end_date - start_date).days + 1, 1)
    approved_daily_capacity = quantize_weight(
        TreatmentFacility.objects.filter(
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED
        ).aggregate(cap=Sum("daily_capacity_kg"))["cap"]
    )
    total_theoretical_capacity = approved_daily_capacity * days_count

    gross_manifest_weight = quantize_weight(manifests_qs.aggregate(w=Sum("gross_received_weight"))["w"])
    overall_capacity_utilization_pct = (
        round((float(gross_manifest_weight) / float(total_theoretical_capacity)) * 100, 1)
        if total_theoretical_capacity > 0 else 0.0
    )

    # 4. Compliance Overview Aggregations
    bags_awaiting_treatment = WasteBag.objects.filter(status=WasteBag.Status.RECEIVED).count()
    bags_awaiting_disposal = WasteBag.objects.filter(status=WasteBag.Status.TREATED).count()
    disposals_awaiting_verification = DisposalRecord.objects.filter(status=DisposalRecord.Status.DOCUMENTED).count()

    disc_aggs = discrepancies_qs.aggregate(
        open_count=Count("id", filter=Q(status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW])),
        critical_count=Count("id", filter=Q(severity=CustodyDiscrepancy.Severity.CRITICAL)),
        high_count=Count("id", filter=Q(severity=CustodyDiscrepancy.Severity.HIGH)),
        missing_count=Count("id", filter=Q(discrepancy_type__in=[CustodyDiscrepancy.DiscrepancyType.MISSING_AT_PICKUP, CustodyDiscrepancy.DiscrepancyType.MISSING_AT_RECEPTION])),
        rejected_count=Count("id", filter=Q(discrepancy_type=CustodyDiscrepancy.DiscrepancyType.REJECTED_BY_FACILITY)),
    )
    disputed_handovers_count = handovers_qs.filter(status=CustodyHandover.Status.DISPUTED).count()

    # Nested dictionaries for template & test namespacing
    waste_overview = {
        "total_bags": total_bags,
        "total_weight": total_weight,
        "category_distribution": category_distribution,
        "in_transit": waste_aggs["in_transit"] or 0,
        "received": waste_aggs["received"] or 0,
        "treated": waste_aggs["treated"] or 0,
        "disposed": waste_aggs["disposed"] or 0,
    }
    treatment_overview = {
        "completed_batches": batch_aggs.get("completed") or 0,
        "total_treated_weight": total_treated_weight,
        "methods": method_distribution,
    }
    collection_overview = {
        "completed_requests": req_aggs.get("completed") or 0,
        "avg_pickup_turnaround_hours": avg_pickup_turnaround or 0,
    }
    compliance_overview = {
        "open_discrepancies": disc_aggs.get("open_count") or 0,
        "critical_discrepancies": disc_aggs.get("critical_count") or 0,
        "license_data_gap_notice": (
            "Treatment facility statutory operating authorization numbers are verified from State PCB registries. "
            "Specific license expiration dates are not presently recorded in the database schema."
        ),
    }

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days_count": days_count,
        # Namespaced dicts
        "waste_overview": waste_overview,
        "treatment_overview": treatment_overview,
        "collection_overview": collection_overview,
        "compliance_overview": compliance_overview,
        # Flat Waste KPIs
        "total_bags": total_bags,
        "total_weight": total_weight,
        "hospitals_count": hospitals_count,
        "active_requests_count": active_requests_count,
        "in_transit_count": waste_aggs["in_transit"] or 0,
        "received_count": waste_aggs["received"] or 0,
        "treated_count": waste_aggs["treated"] or 0,
        "disposed_count": waste_aggs["disposed"] or 0,
        "category_distribution": category_distribution,
        "cat_labels": cat_labels,
        "cat_weights": cat_weights,
        "cat_counts": cat_counts,
        "cat_colors": cat_colors,
        "stage_breakdown": stage_breakdown,
        "daily_labels": daily_labels,
        "daily_weights": daily_weights,
        "daily_counts": daily_counts,
        "trend_labels": daily_labels,
        "trend_weights": daily_weights,
        # Collection KPIs
        "req_aggs": req_aggs,
        "avg_pickup_turnaround": avg_pickup_turnaround,
        "avg_reception_turnaround": avg_reception_turnaround,
        # Treatment KPIs
        "total_facilities": total_facilities,
        "approved_facilities": approved_facilities,
        "suspended_facilities": suspended_facilities,
        "batch_aggs": batch_aggs,
        "total_treated_weight": total_treated_weight,
        "method_distribution": method_distribution,
        "approved_daily_capacity": approved_daily_capacity,
        "total_theoretical_capacity": total_theoretical_capacity,
        "gross_manifest_weight": gross_manifest_weight,
        "overall_capacity_utilization_pct": overall_capacity_utilization_pct,
        # Compliance KPIs
        "bags_awaiting_treatment": bags_awaiting_treatment,
        "bags_awaiting_disposal": bags_awaiting_disposal,
        "disposals_awaiting_verification": disposals_awaiting_verification,
        "disc_aggs": disc_aggs,
        "disputed_handovers_count": disputed_handovers_count,
        "license_expiry_data_gap": True,  # Identifies statutory license expiration date data gap
    }


def get_hospital_analytics(hospital_user, start_dt, end_dt, start_date, end_date, category_code=None):
    """
    Hospital-specific analytics engine.
    Strictly isolated to request.user (Hospital).
    """
    bags_qs = WasteBag.objects.filter(hospital=hospital_user, registered_at__range=(start_dt, end_dt))
    if category_code:
        bags_qs = bags_qs.filter(category__code=category_code)
    reqs_qs = CollectionRequest.objects.filter(hospital=hospital_user, created_at__range=(start_dt, end_dt))
    discrepancies_qs = CustodyDiscrepancy.objects.filter(
        Q(bag__hospital=hospital_user) | Q(collection_request__hospital=hospital_user),
        reported_at__range=(start_dt, end_dt),
    )

    # 1. Generation Aggregates
    aggs = bags_qs.aggregate(
        total_bags=Count("id"),
        total_weight=Sum("weight"),
        registered=Count("id", filter=Q(status=WasteBag.Status.REGISTERED)),
        in_transit=Count("id", filter=Q(status=WasteBag.Status.IN_TRANSIT)),
        received=Count("id", filter=Q(status=WasteBag.Status.RECEIVED)),
        treated=Count("id", filter=Q(status=WasteBag.Status.TREATED)),
        disposed=Count("id", filter=Q(status=WasteBag.Status.DISPOSED)),
    )
    total_bags = aggs["total_bags"] or 0
    total_weight = quantize_weight(aggs["total_weight"])

    # Category Breakdown
    active_cats = WasteCategory.objects.filter(is_active=True).order_by("display_order")
    cat_aggs = (
        bags_qs.values("category__code")
        .annotate(count=Count("id"), weight=Sum("weight"))
    )
    cat_map = {item["category__code"]: item for item in cat_aggs}
    category_distribution = []
    cat_labels = []
    cat_weights = []
    cat_colors = []

    for c in active_cats:
        item = cat_map.get(c.code, {})
        c_count = item.get("count", 0)
        c_weight = quantize_weight(item.get("weight"))
        weight_pct = round((float(c_weight) / float(total_weight)) * 100, 1) if total_weight > 0 else 0.0

        category_distribution.append({
            "category": c,
            "count": c_count,
            "weight": c_weight,
            "percentage": weight_pct,
        })
        cat_labels.append(c.code)
        cat_weights.append(float(c_weight))
        cat_colors.append(c.display_color)

    # Daily Generation Trend
    num_days = min((end_date - start_date).days + 1, 31)
    daily_labels = []
    daily_weights = []
    cur_date = end_date - datetime.timedelta(days=num_days - 1)
    for _ in range(num_days):
        d_start = timezone.make_aware(datetime.datetime.combine(cur_date, datetime.time.min))
        d_end = timezone.make_aware(datetime.datetime.combine(cur_date, datetime.time.max))
        d_agg = bags_qs.filter(registered_at__range=(d_start, d_end)).aggregate(w=Sum("weight"))
        daily_labels.append(cur_date.strftime("%d %b"))
        daily_weights.append(float(quantize_weight(d_agg["w"])))
        cur_date += datetime.timedelta(days=1)

    # Collection History & Turnaround
    total_requests = reqs_qs.count()
    completed_requests = reqs_qs.filter(
        status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED],
        actual_collection_time__isnull=False,
    )
    turnaround_hours = []
    for r in completed_requests.only("created_at", "actual_collection_time")[:200]:
        diff = (r.actual_collection_time - r.created_at).total_seconds() / 3600.0
        if diff >= 0:
            turnaround_hours.append(diff)
    avg_turnaround = round(sum(turnaround_hours) / len(turnaround_hours), 1) if turnaround_hours else None

    # Facility Acceptance vs Rejection Rate for this hospital
    receipts_qs = FacilityBagReceipt.objects.filter(bag__hospital=hospital_user)
    receipt_aggs = receipts_qs.aggregate(
        total=Count("id"),
        accepted=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED)),
        rejected=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED)),
        missing=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING)),
    )
    total_receipts = receipt_aggs["total"] or 0
    acceptance_rate_pct = (
        round((receipt_aggs["accepted"] / total_receipts) * 100, 1)
        if total_receipts > 0 else 100.0
    )

    open_discrepancies = discrepancies_qs.filter(status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]).count()

    facility_acceptance = {
        "accepted": receipt_aggs["accepted"] or 0,
        "rejected": receipt_aggs["rejected"] or 0,
        "missing": receipt_aggs["missing"] or 0,
        "acceptance_rate": acceptance_rate_pct,
    }
    collection_turnaround = {
        "avg_fulfillment_hours": avg_turnaround or 0,
        "completed_requests": completed_requests.count(),
    }
    avg_bag_weight = quantize_weight(total_weight / total_bags) if total_bags > 0 else Decimal("0.00")

    return {
        "hospital": hospital_user,
        "total_bags": total_bags,
        "total_weight": total_weight,
        "avg_bag_weight": avg_bag_weight,
        "facility_acceptance": facility_acceptance,
        "collection_turnaround": collection_turnaround,
        "aggs": aggs,
        "category_distribution": category_distribution,
        "cat_labels": cat_labels,
        "cat_weights": cat_weights,
        "cat_colors": cat_colors,
        "daily_labels": daily_labels,
        "daily_weights": daily_weights,
        "trend_labels": daily_labels,
        "trend_weights": daily_weights,
        "total_requests": total_requests,
        "completed_requests_count": completed_requests.count(),
        "avg_turnaround": avg_turnaround,
        "receipt_aggs": receipt_aggs,
        "acceptance_rate_pct": acceptance_rate_pct,
        "open_discrepancies": open_discrepancies,
    }


def get_collector_analytics(collector_user, start_dt, end_dt, start_date, end_date):
    """
    Collector-specific performance analytics.
    Strictly isolated to request.user (Collector).
    """
    reqs_qs = CollectionRequest.objects.filter(assigned_collector=collector_user, created_at__range=(start_dt, end_dt))
    verifications_qs = BagVerificationRecord.objects.filter(
        collection_request__assigned_collector=collector_user,
        is_verified=True,
        verified_at__range=(start_dt, end_dt),
    )
    handovers_qs = CustodyHandover.objects.filter(
        Q(releasing_user=collector_user) | Q(receiving_user=collector_user),
        handover_timestamp__range=(start_dt, end_dt),
    )
    discrepancies_qs = CustodyDiscrepancy.objects.filter(
        Q(collection_request__assigned_collector=collector_user) | Q(reported_by=collector_user),
        reported_at__range=(start_dt, end_dt),
    )

    req_aggs = reqs_qs.aggregate(
        total_assigned=Count("id"),
        accepted=Count("id", filter=Q(status__in=[CollectionRequest.Status.ACCEPTED, CollectionRequest.Status.PICKUP_IN_PROGRESS, CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED])),
        completed=Count("id", filter=Q(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED])),
        rejected=Count("id", filter=Q(status=CollectionRequest.Status.COLLECTOR_REJECTED)),
    )

    # Average pickup completion duration
    completed_reqs = reqs_qs.filter(
        status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED],
        actual_collection_time__isnull=False,
    )
    pickup_hours = []
    for r in completed_reqs.only("assigned_at", "actual_collection_time")[:200]:
        if r.assigned_at and r.actual_collection_time:
            diff = (r.actual_collection_time - r.assigned_at).total_seconds() / 3600.0
            if diff >= 0:
                pickup_hours.append(diff)
    avg_pickup_completion_hours = round(sum(pickup_hours) / len(pickup_hours), 1) if pickup_hours else None

    # Total verified bags and weight collected
    verified_bags_count = verifications_qs.count()
    total_verified_weight = quantize_weight(verifications_qs.aggregate(w=Sum("bag__weight"))["w"])

    # Facility reception outcomes for collections done by this collector
    manifests = FacilityReceptionManifest.objects.filter(
        collection_request__assigned_collector=collector_user,
        received_at__range=(start_dt, end_dt),
    )
    manifest_aggs = manifests.aggregate(
        total=Count("id"),
        accepted_manifests=Count("id", filter=Q(status=FacilityReceptionManifest.Status.ACCEPTED)),
        discrepancy_manifests=Count("id", filter=Q(status__in=[FacilityReceptionManifest.Status.DISCREPANCY_REPORTED, FacilityReceptionManifest.Status.PARTIALLY_ACCEPTED])),
        rejected_manifests=Count("id", filter=Q(status=FacilityReceptionManifest.Status.REJECTED)),
    )

    disputed_handovers = handovers_qs.filter(status=CustodyHandover.Status.DISPUTED).count()
    reported_exceptions = discrepancies_qs.count()

    # Facility receipts for verified collections
    receipts_qs = FacilityBagReceipt.objects.filter(
        reception_manifest__collection_request__assigned_collector=collector_user,
        verified_at__range=(start_dt, end_dt),
    )
    facility_receipts = {
        "accepted": receipts_qs.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED).count(),
        "rejected": receipts_qs.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED).count(),
        "missing": receipts_qs.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING).count(),
    }

    return {
        "req_aggs": req_aggs,
        "avg_pickup_completion_hours": avg_pickup_completion_hours,
        "avg_pickup_duration_hours": avg_pickup_completion_hours or 0,
        "verified_bags_count": verified_bags_count,
        "total_verified_weight": total_verified_weight,
        "facility_manifests_count": manifest_aggs["total"] or 0,
        "manifest_aggs": manifest_aggs,
        "facility_receipts": facility_receipts,
        "handovers": {"disputed_handovers": disputed_handovers},
        "discrepancies": {"reported_by_me": reported_exceptions},
        "disputed_handovers": disputed_handovers,
        "reported_exceptions": reported_exceptions,
    }


def get_facility_analytics(facility, start_dt, end_dt, start_date, end_date):
    """
    Treatment plant-specific operational analytics.
    Strictly isolated to managed_facility.
    """
    manifests_qs = FacilityReceptionManifest.objects.filter(facility=facility, received_at__range=(start_dt, end_dt))
    receipts_qs = FacilityBagReceipt.objects.filter(reception_manifest__facility=facility, reception_manifest__received_at__range=(start_dt, end_dt))
    batches_qs = TreatmentBatch.objects.filter(facility=facility, created_at__range=(start_dt, end_dt))
    disposals_qs = DisposalRecord.objects.filter(facility=facility, disposal_date__range=(start_date, end_date))

    # Reception Metrics
    total_bags_received = receipts_qs.count()
    total_received_weight = quantize_weight(manifests_qs.aggregate(w=Sum("gross_received_weight"))["w"])

    receipt_aggs = receipts_qs.aggregate(
        accepted=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED)),
        rejected=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED)),
        missing=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING)),
    )
    accepted_bags = receipt_aggs["accepted"] or 0
    rejected_bags = receipt_aggs["rejected"] or 0
    reception_discrepancies = manifests_qs.filter(
        Q(status=FacilityReceptionManifest.Status.DISCREPANCY_REPORTED) | Q(weight_discrepancy__gt=0) | Q(weight_discrepancy__lt=0)
    ).count()

    # Treatment Processing Metrics
    batch_aggs = batches_qs.aggregate(
        total_batches=Count("id"),
        in_progress=Count("id", filter=Q(status=TreatmentBatch.Status.IN_PROGRESS)),
        completed=Count("id", filter=Q(status=TreatmentBatch.Status.COMPLETED)),
        failed_or_hold=Count("id", filter=Q(status__in=[TreatmentBatch.Status.FAILED, TreatmentBatch.Status.ON_HOLD])),
        total_treated_weight=Sum("processing_weight", filter=Q(status=TreatmentBatch.Status.COMPLETED)),
    )
    total_treated_weight = quantize_weight(batch_aggs["total_treated_weight"])

    # Treatment Method Distribution
    method_aggs = (
        batches_qs.filter(status=TreatmentBatch.Status.COMPLETED)
        .values("treatment_method")
        .annotate(count=Count("id"), weight=Sum("processing_weight"))
    )
    method_choices_map = dict(TreatmentBatch.TreatmentMethod.choices)
    methods = []
    method_labels = []
    method_weights = []
    for m in method_aggs:
        name = method_choices_map.get(m["treatment_method"], m["treatment_method"])
        m_weight = quantize_weight(m["weight"])
        methods.append({"name": name, "count": m["count"], "weight": m_weight})
        method_labels.append(name)
        method_weights.append(float(m_weight))

    # Accurate Capacity Utilization:
    # Daily licensed capacity compared to the actual average daily intake during this period
    days_count = max((end_date - start_date).days + 1, 1)
    daily_cap = quantize_weight(facility.daily_capacity_kg)
    total_period_capacity = daily_cap * days_count
    avg_daily_received_weight = quantize_weight(total_received_weight / Decimal(str(days_count)))
    period_capacity_utilization_pct = (
        round((float(total_received_weight) / float(total_period_capacity)) * 100, 2)
        if total_period_capacity > 0 else 0.0
    )

    # Disposal Records
    disposal_aggs = disposals_qs.aggregate(
        total=Count("id"),
        verified=Count("id", filter=Q(status=DisposalRecord.Status.VERIFIED)),
        awaiting_verification=Count("id", filter=Q(status=DisposalRecord.Status.DOCUMENTED)),
    )

    return {
        "facility": facility,
        "days_count": days_count,
        "total_bags_received": total_bags_received,
        "total_received_weight": total_received_weight,
        "accepted_bags": accepted_bags,
        "rejected_bags": rejected_bags,
        "reception_discrepancies": reception_discrepancies,
        "batch_aggs": batch_aggs,
        "total_treated_weight": total_treated_weight,
        "methods": methods,
        "method_labels": method_labels,
        "method_weights": method_weights,
        "daily_capacity": daily_cap,
        "total_period_capacity": total_period_capacity,
        "avg_daily_received_weight": avg_daily_received_weight,
        "period_capacity_utilization_pct": period_capacity_utilization_pct,
        "disposal_aggs": disposal_aggs,
    }
