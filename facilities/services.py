import logging
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import TreatmentFacility
from notifications.services import send_notification
from notifications.models import Notification

logger = logging.getLogger(__name__)
User = get_user_model()


def get_facility_for_user(user):
    """
    Returns the TreatmentFacility associated with the given user, or None.
    """
    if not user.is_authenticated:
        return None
    if user.role != User.Role.FACILITY:
        return None
    try:
        return user.managed_facility
    except TreatmentFacility.DoesNotExist:
        return None


@transaction.atomic
def register_treatment_facility(admin_user, form_data, accepted_categories=None):
    """
    Registers a new treatment facility profile. Authorized only for ADMIN users.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Only platform administrators can register treatment facilities.")

    operator_user = form_data.get("operator_user")
    if not operator_user or operator_user.role != User.Role.FACILITY:
        raise ValidationError("Assigned operator must be an existing user with the FACILITY role.")

    if hasattr(operator_user, "managed_facility"):
        raise ValidationError(f"User '{operator_user.username}' is already the designated operator of facility '{operator_user.managed_facility.name}'.")

    facility = TreatmentFacility.objects.create(
        name=form_data["name"],
        operator_user=operator_user,
        facility_type=form_data.get("facility_type", TreatmentFacility.FacilityType.CBWTF),
        license_number=form_data["license_number"],
        address=form_data["address"],
        district=form_data["district"],
        state=form_data.get("state", "National Capital Region"),
        postal_code=form_data["postal_code"],
        contact_person=form_data["contact_person"],
        contact_phone=form_data["contact_phone"],
        contact_email=form_data["contact_email"],
        daily_capacity_kg=form_data["daily_capacity_kg"],
        operating_days=form_data.get("operating_days", "Monday - Saturday"),
        operating_hours=form_data.get("operating_hours", "08:00 - 20:00"),
        supports_incineration=form_data.get("supports_incineration", True),
        supports_autoclaving=form_data.get("supports_autoclaving", True),
        supports_shredding=form_data.get("supports_shredding", True),
        supports_chemical_disinfection=form_data.get("supports_chemical_disinfection", True),
        supports_encapsulation=form_data.get("supports_encapsulation", False),
        approval_status=form_data.get("approval_status", TreatmentFacility.ApprovalStatus.APPROVED),
        operational_availability=form_data.get("operational_availability", TreatmentFacility.OperationalAvailability.AVAILABLE),
        is_active=form_data.get("is_active", True),
        admin_notes=form_data.get("admin_notes", ""),
    )

    if accepted_categories:
        facility.accepted_categories.set(accepted_categories)

    # Log audit
    try:
        from treatment.models import FacilityAuditHistory
        FacilityAuditHistory.objects.create(
            facility=facility,
            action_type="FACILITY_REGISTERED",
            entity_reference=facility.facility_code,
            changed_by=admin_user,
            description=f"Treatment Facility '{facility.name}' registered by admin {admin_user.username}.",
        )
    except Exception as e:
        logger.warning(f"Could not log FacilityAuditHistory: {e}")

    try:
        from accounts.models import AdminActionAudit
        AdminActionAudit.objects.create(
            actor=admin_user,
            action_type=AdminActionAudit.ActionType.FACILITY_APPROVAL if facility.approval_status == TreatmentFacility.ApprovalStatus.APPROVED else "FACILITY_REGISTERED",
            affected_model="TreatmentFacility",
            affected_record_id=facility.facility_code,
            affected_record_display=facility.name,
            affected_organization=facility.name,
            new_state=facility.approval_status,
            reason=facility.admin_notes or "Facility onboarded by administrator.",
        )
    except Exception as e:
        logger.warning(f"Could not log AdminActionAudit: {e}")

    return facility


@transaction.atomic
def update_facility_status(admin_user, facility, approval_status, operational_availability=None, is_active=None, admin_notes=""):
    """
    Updates administrative status and availability of a facility.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    old_status = facility.approval_status
    facility.approval_status = approval_status
    if operational_availability:
        facility.operational_availability = operational_availability
    if is_active is not None:
        facility.is_active = is_active
    if admin_notes:
        facility.admin_notes = admin_notes
    facility.save()

    # Log to FacilityAuditHistory
    try:
        from treatment.models import FacilityAuditHistory
        FacilityAuditHistory.objects.create(
            facility=facility,
            action_type=f"STATUS_UPDATED_{approval_status}",
            entity_reference=facility.facility_code,
            changed_by=admin_user,
            description=f"Approval status changed from {old_status} to {approval_status}. Notes: {admin_notes}",
        )
    except Exception as e:
        logger.warning(f"Could not log FacilityAuditHistory: {e}")

    # Log to AdminActionAudit
    try:
        from accounts.models import AdminActionAudit
        AdminActionAudit.objects.create(
            actor=admin_user,
            action_type=AdminActionAudit.ActionType.FACILITY_APPROVAL if approval_status == TreatmentFacility.ApprovalStatus.APPROVED else AdminActionAudit.ActionType.FACILITY_SUSPENSION if approval_status == TreatmentFacility.ApprovalStatus.SUSPENDED else AdminActionAudit.ActionType.FACILITY_REJECTION,
            affected_model="TreatmentFacility",
            affected_record_id=facility.facility_code,
            affected_record_display=facility.name,
            affected_organization=facility.name,
            previous_state=old_status,
            new_state=approval_status,
            reason=admin_notes or f"Facility status transitioned to {approval_status}",
        )
    except Exception as e:
        logger.warning(f"Could not log AdminActionAudit: {e}")

    return facility


@transaction.atomic
def approve_treatment_facility(admin_user, facility, remarks=""):
    """
    Formally approves a pending or reviewed treatment facility.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    old_status = facility.approval_status
    facility.approval_status = TreatmentFacility.ApprovalStatus.APPROVED
    facility.operational_availability = TreatmentFacility.OperationalAvailability.AVAILABLE
    facility.is_active = True
    if remarks:
        facility.admin_notes = remarks
    facility.save(update_fields=["approval_status", "operational_availability", "is_active", "admin_notes", "updated_at"])

    # Log audit
    try:
        from treatment.models import FacilityAuditHistory
        FacilityAuditHistory.objects.create(
            facility=facility,
            action_type="FACILITY_APPROVED",
            entity_reference=facility.facility_code,
            changed_by=admin_user,
            description=f"Treatment Facility '{facility.name}' approved by {admin_user.username}. {remarks}",
        )
    except Exception as e:
        logger.warning(f"Could not log FacilityAuditHistory: {e}")

    try:
        from accounts.models import AdminActionAudit
        AdminActionAudit.objects.create(
            actor=admin_user,
            action_type=AdminActionAudit.ActionType.FACILITY_APPROVAL,
            affected_model="TreatmentFacility",
            affected_record_id=facility.facility_code,
            affected_record_display=facility.name,
            affected_organization=facility.name,
            previous_state=old_status,
            new_state=TreatmentFacility.ApprovalStatus.APPROVED,
            reason=remarks or "Facility application formally approved by platform administrator.",
        )
    except Exception as e:
        logger.warning(f"Could not log AdminActionAudit: {e}")

    # Notify operator
    send_notification(
        recipient=facility.operator_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=f"Facility Approved: {facility.name}",
        message=f"Your treatment facility '{facility.name}' ({facility.facility_code}) has been verified and approved by administration. You may now receive waste consignments.",
        reference_id=facility.facility_code,
        link="/treatment/facility/",
    )

    return facility


@transaction.atomic
def reject_treatment_facility(admin_user, facility, reason):
    """
    Rejects a treatment facility application with a mandatory reason.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not reason or not reason.strip():
        raise ValidationError("A mandatory rejection justification is required.")

    old_status = facility.approval_status
    facility.approval_status = TreatmentFacility.ApprovalStatus.REJECTED
    facility.operational_availability = TreatmentFacility.OperationalAvailability.TEMPORARILY_UNAVAILABLE
    facility.is_active = False
    facility.admin_notes = reason.strip()
    facility.save(update_fields=["approval_status", "operational_availability", "is_active", "admin_notes", "updated_at"])

    # Log audit
    try:
        from treatment.models import FacilityAuditHistory
        FacilityAuditHistory.objects.create(
            facility=facility,
            action_type="FACILITY_REJECTED",
            entity_reference=facility.facility_code,
            changed_by=admin_user,
            description=f"Facility application rejected. Reason: {reason.strip()}",
        )
    except Exception as e:
        logger.warning(f"Could not log FacilityAuditHistory: {e}")

    try:
        from accounts.models import AdminActionAudit
        AdminActionAudit.objects.create(
            actor=admin_user,
            action_type=AdminActionAudit.ActionType.FACILITY_REJECTION,
            affected_model="TreatmentFacility",
            affected_record_id=facility.facility_code,
            affected_record_display=facility.name,
            affected_organization=facility.name,
            previous_state=old_status,
            new_state=TreatmentFacility.ApprovalStatus.REJECTED,
            reason=reason.strip(),
        )
    except Exception as e:
        logger.warning(f"Could not log AdminActionAudit: {e}")

    # Notify operator
    send_notification(
        recipient=facility.operator_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=f"Facility Application Rejected: {facility.name}",
        message=f"Your treatment facility application has been rejected by administration. Reason: {reason.strip()}",
        reference_id=facility.facility_code,
    )

    return facility


@transaction.atomic
def suspend_treatment_facility(admin_user, facility, reason):
    """
    Suspends an approved treatment facility with a mandatory reason.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not reason or not reason.strip():
        raise ValidationError("A mandatory suspension justification is required.")

    old_status = facility.approval_status
    facility.approval_status = TreatmentFacility.ApprovalStatus.SUSPENDED
    facility.operational_availability = TreatmentFacility.OperationalAvailability.TEMPORARILY_UNAVAILABLE
    facility.admin_notes = reason.strip()
    facility.save(update_fields=["approval_status", "operational_availability", "admin_notes", "updated_at"])

    # Log audit
    try:
        from treatment.models import FacilityAuditHistory
        FacilityAuditHistory.objects.create(
            facility=facility,
            action_type="FACILITY_SUSPENDED",
            entity_reference=facility.facility_code,
            changed_by=admin_user,
            description=f"Facility suspended by admin. Reason: {reason.strip()}",
        )
    except Exception as e:
        logger.warning(f"Could not log FacilityAuditHistory: {e}")

    try:
        from accounts.models import AdminActionAudit
        AdminActionAudit.objects.create(
            actor=admin_user,
            action_type=AdminActionAudit.ActionType.FACILITY_SUSPENSION,
            affected_model="TreatmentFacility",
            affected_record_id=facility.facility_code,
            affected_record_display=facility.name,
            affected_organization=facility.name,
            previous_state=old_status,
            new_state=TreatmentFacility.ApprovalStatus.SUSPENDED,
            reason=reason.strip(),
        )
    except Exception as e:
        logger.warning(f"Could not log AdminActionAudit: {e}")

    # Notify operator
    send_notification(
        recipient=facility.operator_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=f"Facility Suspended: {facility.name}",
        message=f"Your treatment facility '{facility.name}' has been suspended from receiving waste. Reason: {reason.strip()}",
        reference_id=facility.facility_code,
    )

    return facility


@transaction.atomic
def reinstate_treatment_facility(admin_user, facility, remarks=""):
    """
    Reinstates a suspended or rejected treatment facility after administrative review.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    old_status = facility.approval_status
    facility.approval_status = TreatmentFacility.ApprovalStatus.APPROVED
    facility.operational_availability = TreatmentFacility.OperationalAvailability.AVAILABLE
    facility.is_active = True
    if remarks:
        facility.admin_notes = remarks
    facility.save(update_fields=["approval_status", "operational_availability", "is_active", "admin_notes", "updated_at"])

    # Log audit
    try:
        from treatment.models import FacilityAuditHistory
        FacilityAuditHistory.objects.create(
            facility=facility,
            action_type="FACILITY_REINSTATED",
            entity_reference=facility.facility_code,
            changed_by=admin_user,
            description=f"Facility reinstated by admin. Remarks: {remarks}",
        )
    except Exception as e:
        logger.warning(f"Could not log FacilityAuditHistory: {e}")

    try:
        from accounts.models import AdminActionAudit
        AdminActionAudit.objects.create(
            actor=admin_user,
            action_type=AdminActionAudit.ActionType.FACILITY_REINSTATEMENT,
            affected_model="TreatmentFacility",
            affected_record_id=facility.facility_code,
            affected_record_display=facility.name,
            affected_organization=facility.name,
            previous_state=old_status,
            new_state=TreatmentFacility.ApprovalStatus.APPROVED,
            reason=remarks or "Facility reinstated by administrator after review.",
        )
    except Exception as e:
        logger.warning(f"Could not log AdminActionAudit: {e}")

    # Notify operator
    send_notification(
        recipient=facility.operator_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=f"Facility Reinstated: {facility.name}",
        message=f"Your treatment facility '{facility.name}' has been reinstated and is active to receive waste consignments.",
        reference_id=facility.facility_code,
        link="/treatment/facility/",
    )

    return facility


def get_facility_capacity_metrics(facility):
    """
    Aggregates comprehensive capacity and processing metrics for a facility.
    """
    accepted_today = facility.get_today_accepted_weight()
    awaiting = facility.get_waste_awaiting_processing_weight()
    processing = facility.get_waste_currently_processing_weight()
    remaining = facility.get_remaining_capacity()
    utilization = facility.get_capacity_utilization_percentage()
    availability = facility.get_dynamic_availability()

    return {
        "daily_capacity_kg": facility.daily_capacity_kg,
        "accepted_today_kg": accepted_today,
        "awaiting_processing_kg": awaiting,
        "currently_processing_kg": processing,
        "remaining_capacity_kg": remaining,
        "utilization_percentage": utilization,
        "dynamic_availability": availability,
    }
