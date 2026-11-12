import logging
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, models
from django.utils import timezone

from accounts.models import AdminActionAudit, PlatformSettings
from notifications.models import Notification
from notifications.services import send_notification
from facilities.models import TreatmentFacility
from collection.models import CollectionRequest
from waste.models import WasteBag
from tracking.models import CustodyDiscrepancy

logger = logging.getLogger(__name__)
User = get_user_model()


def log_admin_action(actor, action_type, affected_model, affected_record_id, reason, affected_record_display="", affected_organization="", previous_state="", new_state="", ip_address=None):
    """
    Appends an immutable administrative action audit log.
    """
    try:
        return AdminActionAudit.objects.create(
            actor=actor,
            action_type=action_type,
            affected_model=affected_model,
            affected_record_id=str(affected_record_id),
            affected_record_display=affected_record_display[:255],
            affected_organization=affected_organization[:150],
            previous_state=str(previous_state),
            new_state=str(new_state),
            reason=reason,
            ip_address=ip_address,
        )
    except Exception as e:
        logger.error(f"Failed to record AdminActionAudit: {e}", exc_info=True)
        return None


@transaction.atomic
def toggle_user_active_status(admin_user, target_user, is_active, reason=""):
    """
    Activates or deactivates a user account with safeguards protecting
    against self-deactivation and deactivation of the last active administrator.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if target_user.id == admin_user.id and not is_active:
        raise ValidationError("Safety violation: You cannot deactivate your own active administrator account.")

    if target_user.role == User.Role.ADMIN and not is_active:
        active_admins_count = User.objects.filter(role=User.Role.ADMIN, is_active=True).exclude(id=target_user.id).count()
        if active_admins_count == 0:
            raise ValidationError("Safety violation: Cannot deactivate the sole remaining active administrator on the platform.")

    prev_state = f"is_active={target_user.is_active}, status={target_user.account_status}"
    target_user.is_active = is_active
    target_user.account_status = User.AccountStatus.ACTIVE if is_active else User.AccountStatus.INACTIVE
    if reason:
        target_user.admin_remarks = reason.strip()
    target_user.save(update_fields=["is_active", "account_status", "admin_remarks"])

    action_type = AdminActionAudit.ActionType.USER_ACTIVATION if is_active else AdminActionAudit.ActionType.USER_DEACTIVATION
    log_admin_action(
        actor=admin_user,
        action_type=action_type,
        affected_model="User",
        affected_record_id=target_user.id,
        affected_record_display=target_user.get_full_name() or target_user.username,
        affected_organization=target_user.organization_name,
        previous_state=prev_state,
        new_state=f"is_active={target_user.is_active}, status={target_user.account_status}",
        reason=reason or f"User active status changed to {is_active}",
    )

    return target_user


@transaction.atomic
def suspend_user(admin_user, target_user, reason):
    """
    Suspends a user account with mandatory administrative reason.
    Protects against self-suspension and suspending the last active administrator.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not reason or not reason.strip():
        raise ValidationError("A mandatory administrative justification is required to suspend an account.")

    if target_user.id == admin_user.id:
        raise ValidationError("Safety violation: You cannot suspend your own active administrator account.")

    if target_user.role == User.Role.ADMIN:
        active_admins_count = User.objects.filter(role=User.Role.ADMIN, is_active=True).exclude(id=target_user.id).count()
        if active_admins_count == 0:
            raise ValidationError("Safety violation: Cannot suspend the sole remaining active administrator on the platform.")

    prev_status = target_user.account_status
    target_user.account_status = User.AccountStatus.SUSPENDED
    target_user.is_active = False
    target_user.suspension_reason = reason.strip()
    target_user.save(update_fields=["account_status", "is_active", "suspension_reason"])

    # Determine action type
    if target_user.role == User.Role.HOSPITAL:
        action_type = AdminActionAudit.ActionType.HOSPITAL_SUSPENSION
    elif target_user.role == User.Role.COLLECTOR:
        action_type = AdminActionAudit.ActionType.COLLECTOR_SUSPENSION
    else:
        action_type = AdminActionAudit.ActionType.USER_SUSPENSION

    log_admin_action(
        actor=admin_user,
        action_type=action_type,
        affected_model="User",
        affected_record_id=target_user.id,
        affected_record_display=target_user.get_full_name() or target_user.username,
        affected_organization=target_user.organization_name,
        previous_state=prev_status,
        new_state=User.AccountStatus.SUSPENDED,
        reason=reason.strip(),
    )

    # Dispatch notification to user
    send_notification(
        recipient=target_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title="Account Suspended by Administration",
        message=f"Your MedWaste Connect account has been suspended. Reason: {reason.strip()}",
    )

    return target_user


@transaction.atomic
def reactivate_user(admin_user, target_user, reason=""):
    """
    Reactivates a previously suspended or inactive user account.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    prev_status = target_user.account_status
    target_user.account_status = User.AccountStatus.ACTIVE
    target_user.is_active = True
    if reason:
        target_user.admin_remarks = f"Reactivated: {reason.strip()}"
    target_user.save(update_fields=["account_status", "is_active", "admin_remarks"])

    # Determine action type
    if target_user.role == User.Role.HOSPITAL:
        action_type = AdminActionAudit.ActionType.HOSPITAL_REACTIVATION
    elif target_user.role == User.Role.COLLECTOR:
        action_type = AdminActionAudit.ActionType.COLLECTOR_REACTIVATION
    else:
        action_type = AdminActionAudit.ActionType.USER_REACTIVATION

    log_admin_action(
        actor=admin_user,
        action_type=action_type,
        affected_model="User",
        affected_record_id=target_user.id,
        affected_record_display=target_user.get_full_name() or target_user.username,
        affected_organization=target_user.organization_name,
        previous_state=prev_status,
        new_state=User.AccountStatus.ACTIVE,
        reason=reason or "Account reactivated by administrator.",
    )

    # Dispatch notification
    send_notification(
        recipient=target_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title="Account Reactivated",
        message="Your MedWaste Connect account has been restored to active status. You may now resume operations.",
        link="/dashboard/",
    )

    return target_user


@transaction.atomic
def approve_user_onboarding(admin_user, target_user, remarks=""):
    """
    Approves an onboarding user application (e.g. pending hospital or staff).
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    prev_status = target_user.account_status
    target_user.account_status = User.AccountStatus.ACTIVE
    target_user.is_active = True
    target_user.approved_by = admin_user
    target_user.approved_at = timezone.now()
    if remarks:
        target_user.admin_remarks = remarks.strip()
    target_user.save(update_fields=["account_status", "is_active", "approved_by", "approved_at", "admin_remarks"])

    log_admin_action(
        actor=admin_user,
        action_type=AdminActionAudit.ActionType.USER_APPROVAL,
        affected_model="User",
        affected_record_id=target_user.id,
        affected_record_display=target_user.get_full_name() or target_user.username,
        affected_organization=target_user.organization_name,
        previous_state=prev_status,
        new_state=User.AccountStatus.ACTIVE,
        reason=remarks or "Account onboarding application approved by administrator.",
    )

    send_notification(
        recipient=target_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title="Account Approved",
        message="Your MedWaste Connect onboarding application has been verified and approved.",
        link="/dashboard/",
    )

    return target_user


@transaction.atomic
def reject_user_onboarding(admin_user, target_user, reason):
    """
    Rejects an onboarding user application with mandatory reason.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not reason or not reason.strip():
        raise ValidationError("A mandatory rejection justification is required.")

    prev_status = target_user.account_status
    target_user.account_status = User.AccountStatus.REJECTED
    target_user.is_active = False
    target_user.admin_remarks = reason.strip()
    target_user.save(update_fields=["account_status", "is_active", "admin_remarks"])

    log_admin_action(
        actor=admin_user,
        action_type=AdminActionAudit.ActionType.USER_REJECTION,
        affected_model="User",
        affected_record_id=target_user.id,
        affected_record_display=target_user.get_full_name() or target_user.username,
        affected_organization=target_user.organization_name,
        previous_state=prev_status,
        new_state=User.AccountStatus.REJECTED,
        reason=reason.strip(),
    )

    send_notification(
        recipient=target_user,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title="Onboarding Application Rejected",
        message=f"Your MedWaste Connect application was not approved. Reason: {reason.strip()}",
    )

    return target_user


@transaction.atomic
def broadcast_system_notification(admin_user, target_role, title, message):
    """
    Dispatches a broadcast notification to all active users matching target_role,
    or across all roles if target_role is 'ALL'.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not title or not title.strip() or not message or not message.strip():
        raise ValidationError("Title and message are mandatory.")

    recipients_qs = User.objects.filter(is_active=True)
    if target_role and target_role != "ALL":
        recipients_qs = recipients_qs.filter(role=target_role)

    count = 0
    for u in recipients_qs:
        send_notification(
            recipient=u,
            notification_type=Notification.NotificationType.SYSTEM_ALERT,
            title=f"[Announcement] {title.strip()}",
            message=message.strip(),
            link="/dashboard/",
        )
        count += 1

    log_admin_action(
        actor=admin_user,
        action_type=AdminActionAudit.ActionType.NOTIFICATION_BROADCAST,
        affected_model="Notification",
        affected_record_id=f"BROADCAST-{timezone.now().strftime('%Y%m%d%H%M%S')}",
        affected_record_display=f"Broadcast to {target_role}: {title.strip()}",
        reason=f"Administrative broadcast to {count} recipient(s).",
    )

    return count


def get_platform_kpis():
    """
    Aggregates central platform metrics for Section 2 of Admin Control Panel.
    """
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    inactive_users = User.objects.filter(is_active=False).count()

    total_hospitals = User.objects.filter(role=User.Role.HOSPITAL).count()
    total_collectors = User.objects.filter(role=User.Role.COLLECTOR).count()
    total_facilities = TreatmentFacility.objects.count()

    pending_hospital_approvals = User.objects.filter(
        role=User.Role.HOSPITAL,
        account_status=User.AccountStatus.PENDING_APPROVAL
    ).count()

    pending_facility_approvals = TreatmentFacility.objects.filter(
        approval_status=TreatmentFacility.ApprovalStatus.PENDING_APPROVAL
    ).count()

    suspended_hospitals = User.objects.filter(
        role=User.Role.HOSPITAL,
        account_status=User.AccountStatus.SUSPENDED
    ).count()

    suspended_facilities = TreatmentFacility.objects.filter(
        approval_status=TreatmentFacility.ApprovalStatus.SUSPENDED
    ).count()

    total_suspended_orgs = suspended_hospitals + suspended_facilities

    active_requests = CollectionRequest.objects.filter(
        status__in=CollectionRequest.ACTIVE_STATUSES
    ).count()

    in_transit_bags = WasteBag.objects.filter(
        status=WasteBag.Status.IN_TRANSIT
    ).count()

    open_critical_discrepancies = CustodyDiscrepancy.objects.filter(
        severity=CustodyDiscrepancy.Severity.CRITICAL,
        status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]
    ).count()

    # System alerts in the last 7 days
    recent_cutoff = timezone.now() - timezone.timedelta(days=7)
    system_alerts_count = Notification.objects.filter(
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        created_at__gte=recent_cutoff,
        is_read=False
    ).count()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": inactive_users,
        "total_hospitals": total_hospitals,
        "total_collectors": total_collectors,
        "total_facilities": total_facilities,
        "pending_hospital_approvals": pending_hospital_approvals,
        "pending_facility_approvals": pending_facility_approvals,
        "suspended_hospitals": suspended_hospitals,
        "suspended_facilities": suspended_facilities,
        "suspended_organizations": total_suspended_orgs,
        "active_collection_requests": active_requests,
        "in_transit_bags": in_transit_bags,
        "open_critical_discrepancies": open_critical_discrepancies,
        "system_alerts_count": system_alerts_count,
    }

