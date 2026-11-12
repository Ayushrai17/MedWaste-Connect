import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from collection.models import CollectionRequest, BagVerificationRecord
from facilities.models import TreatmentFacility
from reports.models import ComplianceThresholdConfig
from tracking.models import CustodyEvent, CustodyHandover, CustodyDiscrepancy
from treatment.models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
)
from waste.models import WasteBag, WasteCategory

User = get_user_model()


class ComplianceStatus:
    RECORDED = "Recorded"
    PENDING = "Pending"
    INCOMPLETE = "Incomplete"
    NEEDS_REVIEW = "Needs Review"
    VERIFIED = "Verified in System"


class ComplianceSeverity:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def evaluate_compliance_checkpoints(user, start_dt=None, end_dt=None):
    """
    Evaluates biomedical waste handling integrity across all 12 operational checkpoints.
    Role-scoped for ADMIN, HOSPITAL, COLLECTOR, and FACILITY users.
    Returns:
        {
            "overall_score": float (0-100),
            "status_summary": {status: count},
            "severity_summary": {severity: count},
            "checkpoints": [
                {
                    "number": int (1-12),
                    "code": str,
                    "title": str,
                    "stage": str,
                    "status": str (ComplianceStatus),
                    "severity": str (ComplianceSeverity),
                    "summary": str,
                    "metrics": dict,
                    "affected_count": int,
                    "recommendation": str,
                    "data_gap_notice": str or None,
                }, ...
            ],
            "statutory_data_gap": str,
            "threshold_config": ComplianceThresholdConfig,
        }
    """
    threshold_config = ComplianceThresholdConfig.get_config()
    now = timezone.now()

    # Base role filter lookups
    role = getattr(user, "role", User.Role.ADMIN)
    hospital = user if role == User.Role.HOSPITAL else None
    managed_facility = getattr(user, "managed_facility", None) if role == User.Role.FACILITY else None

    # Base QuerySets scoped by role and optional time range
    bags_qs = WasteBag.objects.all()
    reqs_qs = CollectionRequest.objects.all()
    verifications_qs = BagVerificationRecord.objects.all()
    handovers_qs = CustodyHandover.objects.all()
    manifests_qs = FacilityReceptionManifest.objects.all()
    receipts_qs = FacilityBagReceipt.objects.all()
    batches_qs = TreatmentBatch.objects.all()
    disposals_qs = DisposalRecord.objects.all()
    discrepancies_qs = CustodyDiscrepancy.objects.all()
    events_qs = CustodyEvent.objects.all()

    # Apply date filters if provided
    if start_dt and end_dt:
        bags_qs = bags_qs.filter(registered_at__range=(start_dt, end_dt))
        reqs_qs = reqs_qs.filter(created_at__range=(start_dt, end_dt))
        manifests_qs = manifests_qs.filter(received_at__range=(start_dt, end_dt))
        batches_qs = batches_qs.filter(created_at__range=(start_dt, end_dt))
        handovers_qs = handovers_qs.filter(handover_timestamp__range=(start_dt, end_dt))
        discrepancies_qs = discrepancies_qs.filter(reported_at__range=(start_dt, end_dt))

    # Apply Role-Based Tenant Isolation
    if role == User.Role.HOSPITAL and hospital:
        bags_qs = bags_qs.filter(hospital=hospital)
        reqs_qs = reqs_qs.filter(hospital=hospital)
        verifications_qs = verifications_qs.filter(bag__hospital=hospital)
        handovers_qs = handovers_qs.filter(collection_request__hospital=hospital)
        manifests_qs = manifests_qs.filter(collection_request__hospital=hospital)
        receipts_qs = receipts_qs.filter(bag__hospital=hospital)
        batches_qs = batches_qs.filter(bags__hospital=hospital).distinct()
        disposals_qs = disposals_qs.filter(facility__reception_manifests__collection_request__hospital=hospital).distinct()
        discrepancies_qs = discrepancies_qs.filter(bag__hospital=hospital)
        events_qs = events_qs.filter(bag__hospital=hospital)

    elif role == User.Role.COLLECTOR:
        reqs_qs = reqs_qs.filter(assigned_collector=user)
        bags_qs = bags_qs.filter(collection_verifications__collection_request__assigned_collector=user).distinct()
        verifications_qs = verifications_qs.filter(collection_request__assigned_collector=user)
        handovers_qs = handovers_qs.filter(Q(releasing_user=user) | Q(receiving_user=user))
        manifests_qs = manifests_qs.filter(collection_request__assigned_collector=user)
        receipts_qs = receipts_qs.filter(reception_manifest__collection_request__assigned_collector=user)
        batches_qs = batches_qs.none()
        disposals_qs = disposals_qs.none()
        discrepancies_qs = discrepancies_qs.filter(
            Q(reported_by=user) | Q(bag__collection_verifications__collection_request__assigned_collector=user)
        ).distinct()

    elif role == User.Role.FACILITY and managed_facility:
        manifests_qs = manifests_qs.filter(facility=managed_facility)
        receipts_qs = receipts_qs.filter(reception_manifest__facility=managed_facility)
        batches_qs = batches_qs.filter(facility=managed_facility)
        disposals_qs = disposals_qs.filter(facility=managed_facility)
        handovers_qs = handovers_qs.filter(Q(receiving_user=user) | Q(releasing_user=user) | Q(collection_request__destination_facility=managed_facility))
        bags_qs = bags_qs.filter(facility_receipts__reception_manifest__facility=managed_facility).distinct()
        reqs_qs = reqs_qs.filter(destination_facility=managed_facility)
        discrepancies_qs = discrepancies_qs.filter(
            Q(reported_by=user) | Q(bag__facility_receipts__reception_manifest__facility=managed_facility)
        ).distinct()

    checkpoints = []

    # ==========================================
    # Checkpoint 1: Waste Registration & Categorization
    # ==========================================
    total_registered_bags = bags_qs.count()
    bags_missing_cat = bags_qs.filter(category__isnull=True).count()
    bags_invalid_weight = bags_qs.filter(weight__lte=Decimal("0.00")).count()
    cp1_affected = bags_missing_cat + bags_invalid_weight

    if cp1_affected > 0:
        cp1_status = ComplianceStatus.INCOMPLETE
        cp1_sev = ComplianceSeverity.HIGH
        cp1_rec = f"Correct segregation and weight entry for {cp1_affected} bag(s) with invalid data."
    elif total_registered_bags == 0:
        cp1_status = ComplianceStatus.RECORDED
        cp1_sev = ComplianceSeverity.LOW
        cp1_rec = "No registered waste bags in selected time window."
    else:
        cp1_status = ComplianceStatus.VERIFIED
        cp1_sev = ComplianceSeverity.LOW
        cp1_rec = "All registered bags have valid categories and recorded positive weights."

    checkpoints.append({
        "number": 1,
        "code": "CP-01",
        "title": "Waste Registration & Categorization",
        "stage": "Generation (Point of Origin)",
        "status": cp1_status,
        "severity": cp1_sev,
        "summary": f"{total_registered_bags} bag(s) registered; {cp1_affected} invalid or missing categorization.",
        "metrics": {"total_registered": total_registered_bags, "invalid_weight_or_cat": cp1_affected},
        "affected_count": cp1_affected,
        "recommendation": cp1_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 2: Bag Identification & QR Ready
    # ==========================================
    qr_ready_count = bags_qs.filter(qr_status=WasteBag.QRStatus.READY).count()
    qr_pending_count = bags_qs.filter(qr_status=WasteBag.QRStatus.PENDING).count()
    qr_failed_count = bags_qs.filter(qr_status=WasteBag.QRStatus.FAILED).count()

    if qr_failed_count > 0:
        cp2_status = ComplianceStatus.NEEDS_REVIEW
        cp2_sev = ComplianceSeverity.HIGH
        cp2_rec = f"Regenerate QR barcodes for {qr_failed_count} bag(s) with generation failures."
    elif qr_pending_count > 0:
        cp2_status = ComplianceStatus.PENDING
        cp2_sev = ComplianceSeverity.MEDIUM
        cp2_rec = f"Awaiting QR barcode rendering for {qr_pending_count} bag(s)."
    elif total_registered_bags == 0:
        cp2_status = ComplianceStatus.RECORDED
        cp2_sev = ComplianceSeverity.LOW
        cp2_rec = "No QR barcodes required for current selection."
    else:
        cp2_status = ComplianceStatus.VERIFIED
        cp2_sev = ComplianceSeverity.LOW
        cp2_rec = "All registered bags possess ready, unique tracking QR barcodes."

    checkpoints.append({
        "number": 2,
        "code": "CP-02",
        "title": "Bag Identification & QR Ready",
        "stage": "Barcode Labelling",
        "status": cp2_status,
        "severity": cp2_sev,
        "summary": f"{qr_ready_count} QR ready, {qr_pending_count} pending, {qr_failed_count} failed.",
        "metrics": {"ready": qr_ready_count, "pending": qr_pending_count, "failed": qr_failed_count},
        "affected_count": qr_failed_count + qr_pending_count,
        "recommendation": cp2_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 3: Collection Request & Scheduling
    # ==========================================
    total_reqs = reqs_qs.count()
    unassigned_reqs = reqs_qs.filter(status=CollectionRequest.Status.REQUESTED, assigned_collector__isnull=True)
    unassigned_count = unassigned_reqs.count()
    stale_unassigned_cutoff = now - datetime.timedelta(hours=24)
    stale_unassigned_count = unassigned_reqs.filter(created_at__lt=stale_unassigned_cutoff).count()

    if stale_unassigned_count > 0:
        cp3_status = ComplianceStatus.NEEDS_REVIEW
        cp3_sev = ComplianceSeverity.HIGH
        cp3_rec = f"Assign collectors to {stale_unassigned_count} pickup request(s) pending over 24 hours."
    elif unassigned_count > 0:
        cp3_status = ComplianceStatus.PENDING
        cp3_sev = ComplianceSeverity.MEDIUM
        cp3_rec = f"{unassigned_count} new request(s) awaiting collector assignment."
    else:
        cp3_status = ComplianceStatus.VERIFIED if total_reqs > 0 else ComplianceStatus.RECORDED
        cp3_sev = ComplianceSeverity.LOW
        cp3_rec = "All collection requests are actively assigned or completed."

    checkpoints.append({
        "number": 3,
        "code": "CP-03",
        "title": "Collection Request & Scheduling",
        "stage": "Dispatch & Logistics",
        "status": cp3_status,
        "severity": cp3_sev,
        "summary": f"{total_reqs} request(s) logged; {unassigned_count} unassigned ({stale_unassigned_count} > 24h).",
        "metrics": {"total": total_reqs, "unassigned": unassigned_count, "stale_unassigned": stale_unassigned_count},
        "affected_count": stale_unassigned_count if stale_unassigned_count > 0 else unassigned_count,
        "recommendation": cp3_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 4: Collector Dock Verification
    # ==========================================
    total_verifications = verifications_qs.count()
    unverified_records = verifications_qs.filter(is_verified=False).count()
    
    # Bags that are in transit but never verified
    in_transit_unverified = bags_qs.filter(
        status=WasteBag.Status.IN_TRANSIT,
        collection_verifications__is_verified=False
    ).distinct().count()

    if in_transit_unverified > 0:
        cp4_status = ComplianceStatus.NEEDS_REVIEW
        cp4_sev = ComplianceSeverity.HIGH
        cp4_rec = f"{in_transit_unverified} bag(s) in transit without confirmed collector scan verification."
    elif unverified_records > 0:
        cp4_status = ComplianceStatus.INCOMPLETE
        cp4_sev = ComplianceSeverity.MEDIUM
        cp4_rec = f"Reconcile {unverified_records} unverified bag record(s) logged during pickup dock scans."
    else:
        cp4_status = ComplianceStatus.VERIFIED if total_verifications > 0 else ComplianceStatus.RECORDED
        cp4_sev = ComplianceSeverity.LOW
        cp4_rec = "Collector physical scans match bag inventory at loading dock."

    checkpoints.append({
        "number": 4,
        "code": "CP-04",
        "title": "Collector Dock Verification",
        "stage": "Dock Inspection",
        "status": cp4_status,
        "severity": cp4_sev,
        "summary": f"{total_verifications} scan verification(s); {in_transit_unverified} unverified in transit.",
        "metrics": {"total_scans": total_verifications, "in_transit_unverified": in_transit_unverified},
        "affected_count": in_transit_unverified + unverified_records,
        "recommendation": cp4_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 5: Hospital-to-Collector Custody Handover
    # ==========================================
    hosp_handovers = handovers_qs.filter(handover_type=CustodyHandover.HandoverType.HOSPITAL_TO_COLLECTOR)
    total_hosp_ho = hosp_handovers.count()
    disputed_hosp_ho = hosp_handovers.filter(status=CustodyHandover.Status.DISPUTED).count()

    if disputed_hosp_ho > 0:
        cp5_status = ComplianceStatus.NEEDS_REVIEW
        cp5_sev = ComplianceSeverity.CRITICAL
        cp5_rec = f"Investigate {disputed_hosp_ho} disputed handover manifest(s) between hospital and collector."
    elif total_hosp_ho == 0:
        cp5_status = ComplianceStatus.RECORDED
        cp5_sev = ComplianceSeverity.LOW
        cp5_rec = "No hospital-to-collector handovers recorded in current period."
    else:
        cp5_status = ComplianceStatus.VERIFIED
        cp5_sev = ComplianceSeverity.LOW
        cp5_rec = "All hospital-to-transporter custody handovers validated without dispute."

    checkpoints.append({
        "number": 5,
        "code": "CP-05",
        "title": "Hospital-to-Collector Custody Handover",
        "stage": "Custody Transfer",
        "status": cp5_status,
        "severity": cp5_sev,
        "summary": f"{total_hosp_ho} handover(s) recorded; {disputed_hosp_ho} disputed or flagged.",
        "metrics": {"total_handovers": total_hosp_ho, "disputed": disputed_hosp_ho},
        "affected_count": disputed_hosp_ho,
        "recommendation": cp5_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 6: Facility Reception & Acknowledgment
    # ==========================================
    facility_handovers = handovers_qs.filter(handover_type=CustodyHandover.HandoverType.COLLECTOR_TO_FACILITY)
    total_fac_ho = facility_handovers.count()
    disputed_fac_ho = facility_handovers.filter(status=CustodyHandover.Status.DISPUTED).count()
    unacknowledged_manifests = manifests_qs.filter(status=FacilityReceptionManifest.Status.RECEIVED).count()

    if disputed_fac_ho > 0:
        cp6_status = ComplianceStatus.NEEDS_REVIEW
        cp6_sev = ComplianceSeverity.CRITICAL
        cp6_rec = f"Urgent review required: {disputed_fac_ho} collector-to-facility handover(s) disputed."
    elif unacknowledged_manifests > 0:
        cp6_status = ComplianceStatus.PENDING
        cp6_sev = ComplianceSeverity.MEDIUM
        cp6_rec = f"{unacknowledged_manifests} reception manifest(s) under inspection awaiting facility sign-off."
    else:
        cp6_status = ComplianceStatus.VERIFIED if total_fac_ho > 0 else ComplianceStatus.RECORDED
        cp6_sev = ComplianceSeverity.LOW
        cp6_rec = "Consignments successfully received and acknowledged at authorized facility gates."

    checkpoints.append({
        "number": 6,
        "code": "CP-06",
        "title": "Facility Reception & Acknowledgment",
        "stage": "Facility Inbound Gate",
        "status": cp6_status,
        "severity": cp6_sev,
        "summary": f"{total_fac_ho} facility handover(s); {disputed_fac_ho} disputed; {unacknowledged_manifests} under inspection.",
        "metrics": {"total": total_fac_ho, "disputed": disputed_fac_ho, "unacknowledged": unacknowledged_manifests},
        "affected_count": disputed_fac_ho + unacknowledged_manifests,
        "recommendation": cp6_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 7: Accepted/Rejected Bag Reconciliation
    # ==========================================
    total_receipts = receipts_qs.count()
    rejected_receipts = receipts_qs.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED).count()
    missing_receipts = receipts_qs.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING).count()
    flagged_reception = rejected_receipts + missing_receipts

    if flagged_reception > 0:
        cp7_status = ComplianceStatus.NEEDS_REVIEW
        cp7_sev = ComplianceSeverity.HIGH
        cp7_rec = f"Review {rejected_receipts} rejected and {missing_receipts} missing bag(s) in quarantine records."
    elif total_receipts == 0:
        cp7_status = ComplianceStatus.RECORDED
        cp7_sev = ComplianceSeverity.LOW
        cp7_rec = "No facility bag receipts logged in selected period."
    else:
        cp7_status = ComplianceStatus.VERIFIED
        cp7_sev = ComplianceSeverity.LOW
        cp7_rec = "100% of received bags accepted and reconciled with collection manifests."

    checkpoints.append({
        "number": 7,
        "code": "CP-07",
        "title": "Accepted/Rejected Bag Reconciliation",
        "stage": "Weighing & Physical Inspection",
        "status": cp7_status,
        "severity": cp7_sev,
        "summary": f"{total_receipts} bag receipt(s); {rejected_receipts} rejected; {missing_receipts} missing.",
        "metrics": {"total": total_receipts, "rejected": rejected_receipts, "missing": missing_receipts},
        "affected_count": flagged_reception,
        "recommendation": cp7_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 8: Treatment Batch Assignment
    # ==========================================
    received_unbatched_bags = bags_qs.filter(
        status=WasteBag.Status.RECEIVED,
        treatment_batches__isnull=True
    ).distinct()
    unbatched_count = received_unbatched_bags.count()
    
    # Operational alert threshold from config
    stale_treatment_cutoff = now - datetime.timedelta(hours=threshold_config.treatment_turnaround_hours)
    overdue_unbatched = received_unbatched_bags.filter(updated_at__lt=stale_treatment_cutoff).count()

    if overdue_unbatched > 0:
        cp8_status = ComplianceStatus.NEEDS_REVIEW
        cp8_sev = ComplianceSeverity.HIGH
        cp8_rec = f"Schedule {overdue_unbatched} received bag(s) waiting over {threshold_config.treatment_turnaround_hours}h for treatment batching."
    elif unbatched_count > 0:
        cp8_status = ComplianceStatus.PENDING
        cp8_sev = ComplianceSeverity.MEDIUM
        cp8_rec = f"{unbatched_count} received bag(s) in facility staging queue awaiting batch assignment."
    else:
        cp8_status = ComplianceStatus.VERIFIED if bags_qs.filter(status=WasteBag.Status.TREATED).exists() else ComplianceStatus.RECORDED
        cp8_sev = ComplianceSeverity.LOW
        cp8_rec = "All received biomedical waste successfully assigned to treatment runs."

    checkpoints.append({
        "number": 8,
        "code": "CP-08",
        "title": "Treatment Batch Assignment",
        "stage": "Treatment Staging",
        "status": cp8_status,
        "severity": cp8_sev,
        "summary": f"{unbatched_count} bag(s) in staging queue ({overdue_unbatched} exceeding {threshold_config.treatment_turnaround_hours}h threshold).",
        "metrics": {"unbatched": unbatched_count, "overdue_unbatched": overdue_unbatched},
        "affected_count": overdue_unbatched if overdue_unbatched > 0 else unbatched_count,
        "recommendation": cp8_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 9: Treatment Cycle Completion
    # ==========================================
    total_batches = batches_qs.count()
    failed_batches = batches_qs.filter(status=TreatmentBatch.Status.FAILED).count()
    hold_batches = batches_qs.filter(status=TreatmentBatch.Status.ON_HOLD).count()
    completed_batches = batches_qs.filter(status=TreatmentBatch.Status.COMPLETED).count()

    if failed_batches > 0:
        cp9_status = ComplianceStatus.NEEDS_REVIEW
        cp9_sev = ComplianceSeverity.CRITICAL
        cp9_rec = f"Investigate {failed_batches} failed treatment batch(es) for autoclave/incineration parameter failure."
    elif hold_batches > 0:
        cp9_status = ComplianceStatus.PENDING
        cp9_sev = ComplianceSeverity.MEDIUM
        cp9_rec = f"{hold_batches} batch(es) on operational hold awaiting parameter calibration."
    elif total_batches == 0:
        cp9_status = ComplianceStatus.RECORDED
        cp9_sev = ComplianceSeverity.LOW
        cp9_rec = "No treatment cycles logged in current period."
    else:
        cp9_status = ComplianceStatus.VERIFIED
        cp9_sev = ComplianceSeverity.LOW
        cp9_rec = f"{completed_batches} treatment cycles completed successfully according to CPCB guidelines."

    checkpoints.append({
        "number": 9,
        "code": "CP-09",
        "title": "Treatment Cycle Completion",
        "stage": "Autoclave / Incineration Processing",
        "status": cp9_status,
        "severity": cp9_sev,
        "summary": f"{total_batches} batch(es); {completed_batches} completed; {failed_batches} failed; {hold_batches} on hold.",
        "metrics": {"total": total_batches, "completed": completed_batches, "failed": failed_batches, "on_hold": hold_batches},
        "affected_count": failed_batches + hold_batches,
        "recommendation": cp9_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 10: Disposal Documentation
    # ==========================================
    # Completed treatment batches requiring disposal
    completed_batches_qs = batches_qs.filter(status=TreatmentBatch.Status.COMPLETED)
    total_completed_batches = completed_batches_qs.count()
    batches_without_disposal = completed_batches_qs.filter(disposal_records__isnull=True).count()
    
    # Overdue disposal cutoff
    disposal_cutoff = now - datetime.timedelta(days=threshold_config.disposal_turnaround_days)
    overdue_disposal_batches = completed_batches_qs.filter(
        disposal_records__isnull=True
    ).filter(
        Q(end_time__lt=disposal_cutoff) | Q(created_at__lt=disposal_cutoff)
    ).count()

    if overdue_disposal_batches > 0:
        cp10_status = ComplianceStatus.NEEDS_REVIEW
        cp10_sev = ComplianceSeverity.HIGH
        cp10_rec = f"Log secure residue disposal certificates for {overdue_disposal_batches} batch(es) treated > {threshold_config.disposal_turnaround_days} days ago."
    elif batches_without_disposal > 0:
        cp10_status = ComplianceStatus.PENDING
        cp10_sev = ComplianceSeverity.MEDIUM
        cp10_rec = f"{batches_without_disposal} completed batch(es) pending final residue disposal documentation."
    else:
        cp10_status = ComplianceStatus.VERIFIED if total_completed_batches > 0 else ComplianceStatus.RECORDED
        cp10_sev = ComplianceSeverity.LOW
        cp10_rec = "All treated biomedical residues possess documented disposal manifests."

    checkpoints.append({
        "number": 10,
        "code": "CP-10",
        "title": "Disposal Documentation",
        "stage": "Final Residue Handling",
        "status": cp10_status,
        "severity": cp10_sev,
        "summary": f"{batches_without_disposal} batch(es) awaiting disposal documentation ({overdue_disposal_batches} > {threshold_config.disposal_turnaround_days}d).",
        "metrics": {"without_disposal": batches_without_disposal, "overdue_disposal": overdue_disposal_batches},
        "affected_count": overdue_disposal_batches if overdue_disposal_batches > 0 else batches_without_disposal,
        "recommendation": cp10_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 11: Disposal Compliance Verification
    # ==========================================
    total_disposals = disposals_qs.count()
    documented_disposals = disposals_qs.filter(status=DisposalRecord.Status.DOCUMENTED).count()
    verified_disposals = disposals_qs.filter(status=DisposalRecord.Status.VERIFIED).count()
    
    # Overdue verification cutoff
    verif_cutoff = now - datetime.timedelta(days=threshold_config.verification_turnaround_days)
    overdue_verif_disposals = disposals_qs.filter(
        status=DisposalRecord.Status.DOCUMENTED,
        created_at__lt=verif_cutoff
    ).count()

    if overdue_verif_disposals > 0:
        cp11_status = ComplianceStatus.NEEDS_REVIEW
        cp11_sev = ComplianceSeverity.MEDIUM
        cp11_rec = f"Platform administrator review required for {overdue_verif_disposals} disposal manifest(s) logged > {threshold_config.verification_turnaround_days} days ago."
    elif documented_disposals > 0:
        cp11_status = ComplianceStatus.PENDING
        cp11_sev = ComplianceSeverity.LOW
        cp11_rec = f"{documented_disposals} documented disposal manifest(s) awaiting administrative verification."
    else:
        cp11_status = ComplianceStatus.VERIFIED if total_disposals > 0 else ComplianceStatus.RECORDED
        cp11_sev = ComplianceSeverity.LOW
        cp11_rec = "All recorded residue disposal manifests have completed statutory verification."

    checkpoints.append({
        "number": 11,
        "code": "CP-11",
        "title": "Disposal Compliance Verification",
        "stage": "Regulatory Audit & Sign-off",
        "status": cp11_status,
        "severity": cp11_sev,
        "summary": f"{total_disposals} disposal record(s); {verified_disposals} verified; {documented_disposals} awaiting sign-off.",
        "metrics": {"total": total_disposals, "verified": verified_disposals, "pending_verification": documented_disposals},
        "affected_count": overdue_verif_disposals if overdue_verif_disposals > 0 else documented_disposals,
        "recommendation": cp11_rec,
        "data_gap_notice": None,
    })

    # ==========================================
    # Checkpoint 12: Custody Event Continuity & Exceptions
    # ==========================================
    open_discrepancies = discrepancies_qs.filter(
        status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]
    )
    open_disc_count = open_discrepancies.count()
    critical_disc_count = open_discrepancies.filter(severity=CustodyDiscrepancy.Severity.CRITICAL).count()
    
    # Stale critical discrepancies
    disc_cutoff = now - datetime.timedelta(hours=threshold_config.discrepancy_escalation_hours)
    stale_critical_disc = open_discrepancies.filter(
        severity__in=[CustodyDiscrepancy.Severity.CRITICAL, CustodyDiscrepancy.Severity.HIGH],
        reported_at__lt=disc_cutoff
    ).count()

    if critical_disc_count > 0 or stale_critical_disc > 0:
        cp12_status = ComplianceStatus.NEEDS_REVIEW
        cp12_sev = ComplianceSeverity.CRITICAL
        if stale_critical_disc > 0:
            cp12_rec = f"Urgent executive escalation: {stale_critical_disc} high/critical discrepancy(ies) unresolved after {threshold_config.discrepancy_escalation_hours}h."
        else:
            cp12_rec = f"Critical risk exception: {critical_disc_count} critical chain of custody exception(s) actively open."
    elif open_disc_count > 0:
        cp12_status = ComplianceStatus.NEEDS_REVIEW
        cp12_sev = ComplianceSeverity.HIGH
        cp12_rec = f"Reconcile {open_disc_count} open chain of custody exception(s) with involved handlers."
    else:
        cp12_status = ComplianceStatus.VERIFIED
        cp12_sev = ComplianceSeverity.LOW
        cp12_rec = "Complete immutable chain of custody preserved with zero unresolved discrepancies."

    checkpoints.append({
        "number": 12,
        "code": "CP-12",
        "title": "Custody Event Continuity & Exceptions",
        "stage": "Chain of Custody Audit Trail",
        "status": cp12_status,
        "severity": cp12_sev,
        "summary": f"{open_disc_count} open exception(s); {critical_disc_count} critical; {stale_critical_disc} exceeding {threshold_config.discrepancy_escalation_hours}h escalation limit.",
        "metrics": {"open_discrepancies": open_disc_count, "critical": critical_disc_count, "stale_critical": stale_critical_disc},
        "affected_count": open_disc_count,
        "recommendation": cp12_rec,
        "data_gap_notice": None,
    })

    # Summary calculations
    rec_count = sum(1 for c in checkpoints if c["status"] == ComplianceStatus.RECORDED)
    pend_count = sum(1 for c in checkpoints if c["status"] == ComplianceStatus.PENDING)
    inc_count = sum(1 for c in checkpoints if c["status"] == ComplianceStatus.INCOMPLETE)
    nr_count = sum(1 for c in checkpoints if c["status"] == ComplianceStatus.NEEDS_REVIEW)
    ver_count = sum(1 for c in checkpoints if c["status"] == ComplianceStatus.VERIFIED)

    status_summary = {
        "recorded": rec_count,
        "pending": pend_count,
        "incomplete": inc_count,
        "needs_review": nr_count,
        "verified": ver_count,
        "Recorded": rec_count,
        "Pending": pend_count,
        "Incomplete": inc_count,
        "Needs_Review": nr_count,
        "Verified": ver_count,
        "Verified_in_System": ver_count,
        ComplianceStatus.RECORDED: rec_count,
        ComplianceStatus.PENDING: pend_count,
        ComplianceStatus.INCOMPLETE: inc_count,
        ComplianceStatus.NEEDS_REVIEW: nr_count,
        ComplianceStatus.VERIFIED: ver_count,
    }

    low_sev = sum(1 for c in checkpoints if c["severity"] == ComplianceSeverity.LOW)
    med_sev = sum(1 for c in checkpoints if c["severity"] == ComplianceSeverity.MEDIUM)
    high_sev = sum(1 for c in checkpoints if c["severity"] == ComplianceSeverity.HIGH)
    crit_sev = sum(1 for c in checkpoints if c["severity"] == ComplianceSeverity.CRITICAL)

    severity_summary = {
        "low": low_sev,
        "medium": med_sev,
        "high": high_sev,
        "critical": crit_sev,
        "LOW": low_sev,
        "MEDIUM": med_sev,
        "HIGH": high_sev,
        "CRITICAL": crit_sev,
        ComplianceSeverity.LOW: low_sev,
        ComplianceSeverity.MEDIUM: med_sev,
        ComplianceSeverity.HIGH: high_sev,
        ComplianceSeverity.CRITICAL: crit_sev,
    }

    # Deterministic Compliance Health Index (0 - 100)
    # Deductions: CRITICAL (-20), HIGH (-10), MEDIUM (-5), LOW (-0)
    deduction = (
        severity_summary[ComplianceSeverity.CRITICAL] * 20
        + severity_summary[ComplianceSeverity.HIGH] * 10
        + severity_summary[ComplianceSeverity.MEDIUM] * 5
    )
    overall_score = max(0, 100 - deduction)

    # Statutory Data Gap Notice
    statutory_data_gap = (
        "Statutory Data Gap Notice: TreatmentFacility model stores SPCB license numbers "
        "(Consent to Operate / Authorization), but statutory expiration dates are not tracked in the schema. "
        "Regulatory license renewal statuses cannot be automatically audited until expiration dates are recorded."
    )

    return {
        "overall_score": overall_score,
        "status_summary": status_summary,
        "severity_summary": severity_summary,
        "checkpoints": checkpoints,
        "statutory_data_gap": statutory_data_gap,
        "threshold_config": threshold_config,
    }
