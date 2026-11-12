import datetime
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from notifications.models import Notification
from reports.models import ComplianceThresholdConfig
from tracking.models import CustodyDiscrepancy
from treatment.models import TreatmentBatch, DisposalRecord
from waste.models import WasteBag

User = get_user_model()


def dispatch_compliance_alert(recipient, title, message, reference_id, link="", cooldown_hours=24):
    """
    Dispatches a SYSTEM_ALERT notification with duplicate prevention within cooldown_hours.
    Returns True if notification created, False if suppressed due to cooldown.
    """
    cutoff = timezone.now() - datetime.timedelta(hours=cooldown_hours)
    existing = Notification.objects.filter(
        recipient=recipient,
        reference_id=reference_id,
        created_at__gte=cutoff
    ).exists()

    if existing:
        return False

    Notification.objects.create(
        recipient=recipient,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=title,
        message=message,
        reference_id=reference_id,
        link=link,
    )
    return True


def run_compliance_exception_audit():
    """
    Scans the database against active ComplianceThresholdConfig thresholds
    and dispatches targeted notifications to administrators and facility officers.
    Returns a summary dict of alerts dispatched.
    """
    config = ComplianceThresholdConfig.get_config()
    if not config.is_active:
        return {"status": "inactive", "alerts_dispatched": 0}

    now = timezone.now()
    admin_users = list(User.objects.filter(role=User.Role.ADMIN, is_active=True))
    dispatched_count = 0

    # 1. Received Waste Awaiting Treatment > treatment_turnaround_hours
    treatment_cutoff = now - datetime.timedelta(hours=config.treatment_turnaround_hours)
    stale_received_bags = WasteBag.objects.filter(
        status=WasteBag.Status.RECEIVED,
        treatment_batches__isnull=True,
        updated_at__lt=treatment_cutoff
    ).select_related("hospital", "category")[:50]

    for bag in stale_received_bags:
        ref_id = f"ALERT_TREATMENT_OVERDUE_BAG_{bag.id}"
        title = f"Operational Alert: Waste Staging Overdue ({bag.bag_id})"
        msg = (
            f"Waste bag {bag.bag_id} ({bag.category.name if bag.category else 'Unknown'}, {bag.weight} kg) "
            f"has been awaiting treatment batching for over {config.treatment_turnaround_hours} hours. "
            f"Origin: {bag.hospital.organization_name or bag.hospital.username if bag.hospital else 'Unknown'}."
        )
        link = "/treatment/batches/create/"

        # Send to admins
        for admin in admin_users:
            if dispatch_compliance_alert(admin, title, msg, ref_id, link):
                dispatched_count += 1

    # 2. Treated Waste Awaiting Disposal Documentation > disposal_turnaround_days
    disposal_cutoff = now - datetime.timedelta(days=config.disposal_turnaround_days)
    overdue_batches = TreatmentBatch.objects.filter(
        status=TreatmentBatch.Status.COMPLETED,
        disposal_records__isnull=True
    ).filter(
        Q(end_time__lt=disposal_cutoff) | Q(created_at__lt=disposal_cutoff)
    ).select_related("facility")[:50]

    for batch in overdue_batches:
        completion_dt = batch.end_time or batch.created_at
        ref_id = f"ALERT_DISPOSAL_OVERDUE_BATCH_{batch.id}"
        title = f"Operational Alert: Disposal Documentation Pending ({batch.batch_number})"
        msg = (
            f"Treatment batch {batch.batch_number} at {batch.facility.name} was completed on "
            f"{completion_dt.strftime('%Y-%m-%d') if completion_dt else 'recently'}, "
            f"but final residue disposal documentation has not been logged within the {config.disposal_turnaround_days}-day threshold."
        )
        link = f"/treatment/batches/{batch.id}/"

        for admin in admin_users:
            if dispatch_compliance_alert(admin, title, msg, ref_id, link):
                dispatched_count += 1

        # Also alert facility operator if assigned
        if batch.facility and batch.facility.operator_user:
            if dispatch_compliance_alert(batch.facility.operator_user, title, msg, ref_id, link):
                dispatched_count += 1

    # 3. Disposal Records Pending Verification > verification_turnaround_days
    verif_cutoff = now - datetime.timedelta(days=config.verification_turnaround_days)
    overdue_disposals = DisposalRecord.objects.filter(
        status=DisposalRecord.Status.DOCUMENTED,
        created_at__lt=verif_cutoff
    ).select_related("facility")[:50]

    for disp in overdue_disposals:
        ref_id = f"ALERT_DISPOSAL_VERIF_OVERDUE_{disp.id}"
        title = f"Compliance Reminder: Disposal Audit Sign-off Needed ({disp.reference_number})"
        weight_disp = disp.treatment_batch.processing_weight if disp.treatment_batch else 0
        msg = (
            f"Residue disposal manifest {disp.reference_number} from {disp.facility.name} "
            f"({weight_disp} kg via {disp.get_final_disposal_method_display()}) has been pending "
            f"administrative verification for over {config.verification_turnaround_days} days."
        )
        link = f"/treatment/disposals/{disp.id}/"

        for admin in admin_users:
            if dispatch_compliance_alert(admin, title, msg, ref_id, link):
                dispatched_count += 1

    # 4. Open Critical/High Discrepancies > discrepancy_escalation_hours
    disc_cutoff = now - datetime.timedelta(hours=config.discrepancy_escalation_hours)
    stale_discrepancies = CustodyDiscrepancy.objects.filter(
        status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW],
        severity__in=[CustodyDiscrepancy.Severity.CRITICAL, CustodyDiscrepancy.Severity.HIGH],
        reported_at__lt=disc_cutoff
    ).select_related("bag", "reported_by")[:50]

    for disc in stale_discrepancies:
        ref_id = f"ALERT_DISCREPANCY_ESCALATION_{disc.id}"
        title = f"Urgent Escalation: Unresolved {disc.severity} Discrepancy #{disc.id}"
        msg = (
            f"Discrepancy #{disc.id} ({disc.get_discrepancy_type_display()}) on bag "
            f"{disc.bag.bag_id if disc.bag else 'Unknown'} reported by {disc.reported_by.username if disc.reported_by else 'System'} "
            f"remains unresolved after {config.discrepancy_escalation_hours} hours."
        )
        link = f"/tracking/discrepancies/{disc.id}/"

        for admin in admin_users:
            if dispatch_compliance_alert(admin, title, msg, ref_id, link):
                dispatched_count += 1

    return {
        "status": "success",
        "alerts_dispatched": dispatched_count,
        "config": {
            "treatment_hours": config.treatment_turnaround_hours,
            "disposal_days": config.disposal_turnaround_days,
            "verification_days": config.verification_turnaround_days,
            "escalation_hours": config.discrepancy_escalation_hours,
        }
    }
