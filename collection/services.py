import logging
from datetime import datetime, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from notifications.services import send_notification, notify_administrators
from notifications.models import Notification
from waste.models import WasteBag
from .models import CollectionRequest, CollectionStatusHistory, BagVerificationRecord

logger = logging.getLogger(__name__)
User = get_user_model()


def get_active_bag_ids_for_hospital(hospital_user):
    """
    Returns set of bag IDs that are already committed to an active collection request.
    """
    return set(
        WasteBag.objects.filter(
            hospital=hospital_user,
            collection_requests__status__in=CollectionRequest.ACTIVE_STATUSES
        ).values_list("id", flat=True)
    )


def get_eligible_bags_for_hospital(hospital_user):
    """
    Returns QuerySet of waste bags eligible to be included in a new collection request:
    - Owned by this hospital
    - Custody status is REGISTERED
    - Not part of any active collection request
    """
    active_bag_ids = get_active_bag_ids_for_hospital(hospital_user)
    return (
        WasteBag.objects.filter(hospital=hospital_user, status=WasteBag.Status.REGISTERED)
        .exclude(id__in=active_bag_ids)
        .select_related("category", "batch")
        .order_by("-registered_at")
    )


def check_collector_overlap(collector_user, pickup_date, pickup_time, exclude_request_id=None):
    """
    Checks if a collector has an overlapping assignment on the same date and close time.
    Flags overlap if another active assignment exists on the exact same date within +/- 45 minutes.
    """
    if not collector_user or not pickup_date or not pickup_time:
        return False, None

    active_collector_requests = CollectionRequest.objects.filter(
        assigned_collector=collector_user,
        confirmed_pickup_date=pickup_date,
        status__in=[
            CollectionRequest.Status.ASSIGNED,
            CollectionRequest.Status.ACCEPTED,
            CollectionRequest.Status.PICKUP_IN_PROGRESS,
        ]
    )
    if exclude_request_id:
        active_collector_requests = active_collector_requests.exclude(id=exclude_request_id)

    target_dt = datetime.combine(pickup_date, pickup_time)
    for req in active_collector_requests:
        if req.confirmed_pickup_time:
            req_dt = datetime.combine(req.confirmed_pickup_date, req.confirmed_pickup_time)
            diff = abs((target_dt - req_dt).total_seconds())
            if diff < 2700:  # 45 minutes window overlap
                return True, req
    return False, None


@transaction.atomic
def create_collection_request(hospital_user, form_data, bag_queryset):
    """
    Creates a new CollectionRequest with selected bags under atomic transaction protection.
    Enforces strict hospital isolation and duplicate assignment prevention.
    """
    if hospital_user.role != User.Role.HOSPITAL:
        raise PermissionDenied("Only authorized Hospital accounts can submit collection requests.")

    selected_bags = list(bag_queryset)
    if not selected_bags:
        raise ValidationError("At least one eligible biomedical waste bag must be selected for collection.")

    selected_bag_ids = [b.id for b in selected_bags]

    # Lock and verify all selected bags belong to this hospital
    locked_bags = list(
        WasteBag.objects.select_for_update()
        .filter(id__in=selected_bag_ids, hospital=hospital_user)
    )
    if len(locked_bags) != len(selected_bags):
        raise ValidationError("One or more selected bags do not belong to your hospital or do not exist.")

    # Check for bags that are already part of an active request
    conflicting = CollectionRequest.objects.filter(
        status__in=CollectionRequest.ACTIVE_STATUSES,
        bags__id__in=selected_bag_ids
    ).distinct()
    if conflicting.exists():
        conflicting_ref = conflicting.first().reference_number
        raise ValidationError(f"One or more selected bags are already part of an active collection request ({conflicting_ref}).")

    # Verify all bags are in REGISTERED state
    for b in locked_bags:
        if b.status != WasteBag.Status.REGISTERED:
            raise ValidationError(f"Bag {b.bag_id} has custody status '{b.get_status_display()}' and cannot be requested.")

    # Create collection request
    req = CollectionRequest.objects.create(
        hospital=hospital_user,
        preferred_pickup_date=form_data["preferred_pickup_date"],
        preferred_pickup_time=form_data["preferred_pickup_time"],
        alternative_pickup_date=form_data.get("alternative_pickup_date"),
        alternative_pickup_time=form_data.get("alternative_pickup_time"),
        pickup_address=form_data["pickup_address"],
        contact_person=form_data["contact_person"],
        contact_phone=form_data["contact_phone"],
        special_instructions=form_data.get("special_instructions", ""),
        operational_notes=form_data.get("operational_notes", ""),
        status=CollectionRequest.Status.REQUESTED,
    )

    # Attach bags
    req.bags.set(locked_bags)
    req.update_aggregates()

    # Create verification records for collector check-in
    for bag in locked_bags:
        BagVerificationRecord.objects.create(
            collection_request=req,
            bag=bag,
            is_verified=False
        )
        try:
            from tracking.models import CustodyEvent
            from tracking.services import record_custody_event
            record_custody_event(
                bag=bag,
                collection_request=req,
                event_type=CustodyEvent.EventType.COLLECTION_REQUESTED,
                new_custody_status=WasteBag.Status.REGISTERED,
                actor=hospital_user,
                origin_location=req.pickup_address,
                verification_method="COLLECTION_MANIFEST_REQUEST",
                weight_at_event=bag.weight,
                remarks=f"Pickup dispatch requested ({req.reference_number}).",
            )
        except Exception as err:
            logger.warning(f"Could not log COLLECTION_REQUESTED event for bag {bag.bag_id}: {err}")

    # Record initial audit entry
    CollectionStatusHistory.objects.create(
        request=req,
        previous_status="",
        new_status=CollectionRequest.Status.REQUESTED,
        changed_by=hospital_user,
        remarks="Pickup request submitted by hospital.",
    )

    # Dispatch notification to administrators
    detail_url = reverse("collection:admin_request_detail", kwargs={"reference_number": req.reference_number})
    notify_administrators(
        notification_type=Notification.NotificationType.REQUEST_SUBMITTED,
        title=f"New Pickup Request: {req.reference_number}",
        message=f"{hospital_user.organization_name or hospital_user.username} submitted a pickup request for {req.total_bags} bags ({req.total_weight} KG).",
        reference_id=req.reference_number,
        link=detail_url,
    )

    # Confirmation notification to the requesting hospital
    hospital_url = reverse("collection:hospital_request_detail", kwargs={"reference_number": req.reference_number})
    send_notification(
        recipient=hospital_user,
        notification_type=Notification.NotificationType.REQUEST_SUBMITTED,
        title="Pickup Request Submitted Successfully",
        message=f"Your collection request {req.reference_number} has been logged for {req.total_bags} bags ({req.total_weight} KG).",
        reference_id=req.reference_number,
        link=hospital_url,
    )

    return req


@transaction.atomic
def cancel_collection_request(collection_request, user, cancellation_reason=""):
    """
    Cancels a collection request if it is still in a permitted pre-collection state.
    """
    if not collection_request.is_cancellable:
        raise ValidationError("This collection request has progressed beyond the pre-collection phase and cannot be cancelled.")

    # Validate permission
    is_admin = user.role == User.Role.ADMIN
    is_owner = collection_request.hospital_id == user.id
    if not (is_admin or is_owner):
        raise PermissionDenied("You do not have authorization to cancel this collection request.")

    old_status = collection_request.status
    collection_request.status = CollectionRequest.Status.CANCELLED
    collection_request.rejection_reason = cancellation_reason
    collection_request.save(update_fields=["status", "rejection_reason", "updated_at"])

    remarks = f"Cancelled by {user.get_role_display()}: {cancellation_reason}" if cancellation_reason else f"Cancelled by {user.get_role_display()}."
    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.CANCELLED,
        changed_by=user,
        remarks=remarks,
    )

    # Notify parties
    if is_owner:
        notify_administrators(
            notification_type=Notification.NotificationType.REQUEST_CANCELLED,
            title=f"Request Cancelled: {collection_request.reference_number}",
            message=f"{collection_request.hospital.organization_name or collection_request.hospital.username} cancelled pickup request {collection_request.reference_number}.",
            reference_id=collection_request.reference_number,
        )
    else:
        send_notification(
            recipient=collection_request.hospital,
            notification_type=Notification.NotificationType.REQUEST_CANCELLED,
            title=f"Request Cancelled: {collection_request.reference_number}",
            message=f"Administrator cancelled pickup request {collection_request.reference_number}. Reason: {cancellation_reason}",
            reference_id=collection_request.reference_number,
        )

    if collection_request.assigned_collector:
        send_notification(
            recipient=collection_request.assigned_collector,
            notification_type=Notification.NotificationType.REQUEST_CANCELLED,
            title=f"Assignment Cancelled: {collection_request.reference_number}",
            message=f"Pickup assignment {collection_request.reference_number} has been cancelled.",
            reference_id=collection_request.reference_number,
        )

    return collection_request


@transaction.atomic
def admin_review_request(collection_request, admin_user):
    """
    Transitions a request from REQUESTED to UNDER_REVIEW when opened by admin.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if collection_request.status == CollectionRequest.Status.REQUESTED:
        collection_request.status = CollectionRequest.Status.UNDER_REVIEW
        collection_request.save(update_fields=["status", "updated_at"])
        CollectionStatusHistory.objects.create(
            request=collection_request,
            previous_status=CollectionRequest.Status.REQUESTED,
            new_status=CollectionRequest.Status.UNDER_REVIEW,
            changed_by=admin_user,
            remarks="Review opened by platform administrator.",
        )
    return collection_request


@transaction.atomic
def admin_approve_request(collection_request, admin_user, remarks=""):
    """
    Approves a collection request in pre-scheduling review.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if collection_request.status not in [CollectionRequest.Status.REQUESTED, CollectionRequest.Status.UNDER_REVIEW]:
        raise ValidationError(f"Cannot approve request currently in status '{collection_request.get_status_display()}'.")

    old_status = collection_request.status
    collection_request.status = CollectionRequest.Status.APPROVED
    if remarks:
        collection_request.admin_notes = remarks
    collection_request.save(update_fields=["status", "admin_notes", "updated_at"])

    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.APPROVED,
        changed_by=admin_user,
        remarks=remarks or "Approved for scheduling by administration.",
    )

    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.REQUEST_APPROVED,
        title=f"Request Approved: {collection_request.reference_number}",
        message=f"Your pickup request {collection_request.reference_number} has been approved. Dispatch scheduling is in progress.",
        reference_id=collection_request.reference_number,
        link=reverse("collection:hospital_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    return collection_request


@transaction.atomic
def admin_reject_request(collection_request, admin_user, rejection_reason):
    """
    Rejects a collection request with a mandatory reason.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not rejection_reason or not rejection_reason.strip():
        raise ValidationError("A rejection explanation is required.")

    if collection_request.status not in [CollectionRequest.Status.REQUESTED, CollectionRequest.Status.UNDER_REVIEW, CollectionRequest.Status.APPROVED]:
        raise ValidationError(f"Cannot reject request currently in status '{collection_request.get_status_display()}'.")

    old_status = collection_request.status
    collection_request.status = CollectionRequest.Status.CANCELLED
    collection_request.rejection_reason = rejection_reason.strip()
    collection_request.save(update_fields=["status", "rejection_reason", "updated_at"])

    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.CANCELLED,
        changed_by=admin_user,
        remarks=f"Rejected by administrator: {rejection_reason.strip()}",
    )

    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.REQUEST_REJECTED,
        title=f"Pickup Request Rejected: {collection_request.reference_number}",
        message=f"Pickup request {collection_request.reference_number} was rejected by administration. Reason: {rejection_reason.strip()}",
        reference_id=collection_request.reference_number,
        link=reverse("collection:hospital_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    return collection_request


@transaction.atomic
def admin_schedule_and_assign(collection_request, admin_user, confirmed_date, confirmed_time, collector_user, remarks="", destination_facility=None):
    """
    Confirms pickup schedule, assigns an authorized collector, and prevents overlapping assignments.
    Preserves hospital's preferred schedule in preferred_* fields.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Administrative credentials required.")

    if not collection_request.can_be_assigned:
        raise ValidationError(f"Cannot schedule and assign request in status '{collection_request.get_status_display()}'.")

    if not confirmed_date or not confirmed_time:
        raise ValidationError("Confirmed pickup date and pickup time are mandatory.")

    today = timezone.localdate()
    if confirmed_date < today:
        raise ValidationError("Confirmed pickup date cannot be in the past.")

    if not collector_user or collector_user.role != User.Role.COLLECTOR:
        raise ValidationError("Selected assignee must be an authorized user with the Waste Collector role.")

    # Step 7 enforcement: Collector must be active and not suspended
    if not collector_user.is_active or getattr(collector_user, "account_status", None) == User.AccountStatus.SUSPENDED:
        raise ValidationError(
            f"Collector '{collector_user.get_full_name() or collector_user.username}' is currently inactive or suspended and cannot receive new assignments."
        )

    # Step 7 enforcement: Destination facility must be approved and active
    if destination_facility:
        from facilities.models import TreatmentFacility
        if destination_facility.approval_status != TreatmentFacility.ApprovalStatus.APPROVED or not destination_facility.is_active:
            raise ValidationError(
                f"Treatment facility '{destination_facility.name}' is currently {destination_facility.get_approval_status_display()} "
                f"(active={destination_facility.is_active}) and cannot receive new collection consignments."
            )

    # Overlap protection: check if collector is already assigned to a nearby appointment
    is_overlapping, overlapping_req = check_collector_overlap(
        collector_user=collector_user,
        pickup_date=confirmed_date,
        pickup_time=confirmed_time,
        exclude_request_id=collection_request.id,
    )
    if is_overlapping:
        raise ValidationError(
            f"Collector '{collector_user.get_full_name() or collector_user.username}' is already assigned to active request "
            f"{overlapping_req.reference_number} at {overlapping_req.confirmed_pickup_time.strftime('%H:%M')} on {confirmed_date}."
        )

    old_status = collection_request.status
    collection_request.confirmed_pickup_date = confirmed_date
    collection_request.confirmed_pickup_time = confirmed_time
    collection_request.assigned_collector = collector_user
    collection_request.assigned_at = timezone.now()
    collection_request.status = CollectionRequest.Status.ASSIGNED
    if destination_facility:
        collection_request.destination_facility = destination_facility
    if remarks:
        collection_request.admin_notes = remarks
    collection_request.save(
        update_fields=[
            "confirmed_pickup_date",
            "confirmed_pickup_time",
            "assigned_collector",
            "assigned_at",
            "destination_facility",
            "status",
            "admin_notes",
            "updated_at",
        ]
    )

    history_note = (
        f"Scheduled for {confirmed_date} at {confirmed_time.strftime('%H:%M')}. "
        f"Assigned to {collector_user.get_full_name() or collector_user.username}."
    )
    if remarks:
        history_note += f" Notes: {remarks}"

    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.ASSIGNED,
        changed_by=admin_user,
        remarks=history_note,
    )

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in collection_request.bags.all():
            record_custody_event(
                bag=b,
                collection_request=collection_request,
                event_type=CustodyEvent.EventType.COLLECTION_SCHEDULED,
                new_custody_status=WasteBag.Status.REGISTERED,
                actor=admin_user,
                origin_location=collection_request.pickup_address,
                remarks=f"Pickup scheduled for {confirmed_date} at {confirmed_time.strftime('%H:%M')}.",
            )
            record_custody_event(
                bag=b,
                collection_request=collection_request,
                event_type=CustodyEvent.EventType.COLLECTOR_ASSIGNED,
                new_custody_status=WasteBag.Status.REGISTERED,
                actor=admin_user,
                origin_location=collection_request.pickup_address,
                remarks=f"Collector assigned: {collector_user.get_full_name() or collector_user.username}.",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log assignment custody events: {cust_err}")

    # Notify Assigned Collector
    collector_url = reverse("collection:collector_request_detail", kwargs={"reference_number": collection_request.reference_number})
    send_notification(
        recipient=collector_user,
        notification_type=Notification.NotificationType.COLLECTOR_ASSIGNED,
        title=f"New Pickup Assignment: {collection_request.reference_number}",
        message=(
            f"You have been assigned to collect {collection_request.total_bags} bags ({collection_request.total_weight} KG) "
            f"from {collection_request.hospital.organization_name or collection_request.hospital.username} on "
            f"{confirmed_date} at {confirmed_time.strftime('%H:%M')}."
        ),
        reference_id=collection_request.reference_number,
        link=collector_url,
    )

    # Notify Hospital of confirmed schedule
    hospital_url = reverse("collection:hospital_request_detail", kwargs={"reference_number": collection_request.reference_number})
    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.SCHEDULE_CONFIRMED,
        title=f"Pickup Scheduled: {collection_request.reference_number}",
        message=(
            f"Your pickup has been confirmed for {confirmed_date} at {confirmed_time.strftime('%H:%M')}. "
            f"Collector assigned: {collector_user.get_full_name() or collector_user.username}."
        ),
        reference_id=collection_request.reference_number,
        link=hospital_url,
    )

    return collection_request


@transaction.atomic
def collector_accept_assignment(collection_request, collector_user, remarks=""):
    """
    Collector accepts the assigned collection request.
    """
    if collector_user.role != User.Role.COLLECTOR or collection_request.assigned_collector_id != collector_user.id:
        raise PermissionDenied("You can accept only collection requests specifically assigned to you.")

    if collection_request.status != CollectionRequest.Status.ASSIGNED:
        raise ValidationError(f"Cannot accept assignment in status '{collection_request.get_status_display()}'.")

    old_status = collection_request.status
    collection_request.status = CollectionRequest.Status.ACCEPTED
    if remarks:
        collection_request.collector_remarks = remarks
    collection_request.save(update_fields=["status", "collector_remarks", "updated_at"])

    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.ACCEPTED,
        changed_by=collector_user,
        remarks=remarks or "Assignment accepted by driver.",
    )

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in collection_request.bags.all():
            record_custody_event(
                bag=b,
                collection_request=collection_request,
                event_type=CustodyEvent.EventType.COLLECTOR_ACCEPTED,
                new_custody_status=WasteBag.Status.REGISTERED,
                actor=collector_user,
                remarks="Collector confirmed dispatch assignment.",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log collector acceptance custody event: {cust_err}")

    # Notify hospital
    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.COLLECTOR_ACCEPTED,
        title=f"Collector Confirmed: {collection_request.reference_number}",
        message=f"Collector {collector_user.get_full_name() or collector_user.username} has accepted your pickup appointment.",
        reference_id=collection_request.reference_number,
        link=reverse("collection:hospital_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    # Notify admin
    notify_administrators(
        notification_type=Notification.NotificationType.COLLECTOR_ACCEPTED,
        title=f"Assignment Accepted: {collection_request.reference_number}",
        message=f"Collector {collector_user.username} accepted pickup {collection_request.reference_number}.",
        reference_id=collection_request.reference_number,
    )

    return collection_request


@transaction.atomic
def collector_reject_assignment(collection_request, collector_user, rejection_reason):
    """
    Collector rejects an assignment with a mandatory reason.
    Request returns to admin unassigned queue.
    """
    if collector_user.role != User.Role.COLLECTOR or collection_request.assigned_collector_id != collector_user.id:
        raise PermissionDenied("You can reject only collection requests specifically assigned to you.")

    if not rejection_reason or not rejection_reason.strip():
        raise ValidationError("A mandatory rejection explanation must be provided.")

    if collection_request.status not in [CollectionRequest.Status.ASSIGNED, CollectionRequest.Status.ACCEPTED]:
        raise ValidationError(f"Cannot reject assignment in status '{collection_request.get_status_display()}'.")

    old_status = collection_request.status
    previous_collector = collection_request.assigned_collector
    collection_request.status = CollectionRequest.Status.COLLECTOR_REJECTED
    collection_request.rejection_reason = rejection_reason.strip()
    collection_request.assigned_collector = None
    collection_request.save(update_fields=["status", "rejection_reason", "assigned_collector", "updated_at"])

    remarks_text = f"Assignment rejected by {previous_collector.username}: {rejection_reason.strip()}"
    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.COLLECTOR_REJECTED,
        changed_by=collector_user,
        remarks=remarks_text,
    )

    # Notify admin for reassignment
    notify_administrators(
        notification_type=Notification.NotificationType.COLLECTOR_REJECTED,
        title=f"Assignment Rejected: {collection_request.reference_number}",
        message=f"Collector {collector_user.username} rejected pickup {collection_request.reference_number}. Reason: {rejection_reason.strip()}",
        reference_id=collection_request.reference_number,
        link=reverse("collection:admin_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    return collection_request


@transaction.atomic
def collector_start_pickup(collection_request, collector_user, remarks=""):
    """
    Collector marks journey / pickup as started.
    """
    if collector_user.role != User.Role.COLLECTOR or collection_request.assigned_collector_id != collector_user.id:
        raise PermissionDenied("You can start pickups only for requests assigned to you.")

    if collection_request.status != CollectionRequest.Status.ACCEPTED:
        raise ValidationError(f"Cannot start pickup from status '{collection_request.get_status_display()}'.")

    old_status = collection_request.status
    collection_request.status = CollectionRequest.Status.PICKUP_IN_PROGRESS
    if remarks:
        collection_request.collector_remarks = f"{collection_request.collector_remarks}\n[Started]: {remarks}".strip()
    collection_request.save(update_fields=["status", "collector_remarks", "updated_at"])

    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.PICKUP_IN_PROGRESS,
        changed_by=collector_user,
        remarks=remarks or "Collector has started transit to the hospital pickup dock.",
    )

    # Notify hospital
    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.PICKUP_STARTED,
        title=f"Collector En Route: {collection_request.reference_number}",
        message=f"Collector {collector_user.get_full_name() or collector_user.username} has started transit for your pickup.",
        reference_id=collection_request.reference_number,
        link=reverse("collection:hospital_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    return collection_request


@transaction.atomic
def collector_complete_collection(collection_request, collector_user, remarks=""):
    """
    Collector completes collection:
    - Sets actual_collection_time = timezone.now()
    - Transitions request status to COLLECTED (or COMPLETED)
    - Updates associated WasteBag statuses to IN_TRANSIT
    """
    if collector_user.role != User.Role.COLLECTOR or collection_request.assigned_collector_id != collector_user.id:
        raise PermissionDenied("You can complete collections only for requests assigned to you.")

    if collection_request.status != CollectionRequest.Status.PICKUP_IN_PROGRESS:
        raise ValidationError(f"Cannot complete collection from status '{collection_request.get_status_display()}'.")

    now = timezone.now()
    old_status = collection_request.status
    collection_request.status = CollectionRequest.Status.COLLECTED
    collection_request.actual_collection_time = now
    if remarks:
        collection_request.collector_remarks = f"{collection_request.collector_remarks}\n[Collected]: {remarks}".strip()
    collection_request.save(update_fields=["status", "actual_collection_time", "collector_remarks", "updated_at"])

    # Update custody status of all associated physical bags
    collection_request.bags.update(
        status=WasteBag.Status.IN_TRANSIT,
        updated_at=now,
    )

    try:
        from tracking.models import CustodyHandover, CustodyEvent
        from tracking.services import create_custody_handover, record_custody_event
        create_custody_handover(
            handover_type=CustodyHandover.HandoverType.HOSPITAL_TO_COLLECTOR,
            collection_request=collection_request,
            releasing_user=collection_request.hospital,
            receiving_user=collector_user,
            bags=collection_request.bags.all(),
            releasing_organization=collection_request.hospital.organization_name or collection_request.hospital.username,
            receiving_organization="Logistics Fleet",
            total_bags_expected=collection_request.total_bags,
            total_bags_verified=collection_request.bag_verifications.filter(is_verified=True).count(),
            expected_weight=collection_request.total_weight,
            actual_weight=collection_request.total_weight,
            status=CustodyHandover.Status.ACKNOWLEDGED,
            remarks=remarks or "Biomedical waste bags inspected, verified, and loaded into collection transport vehicle.",
        )
        for b in collection_request.bags.all():
            record_custody_event(
                bag=b,
                collection_request=collection_request,
                event_type=CustodyEvent.EventType.TRANSIT_STARTED,
                new_custody_status=WasteBag.Status.IN_TRANSIT,
                actor=collector_user,
                origin_location=collection_request.pickup_address,
                destination_location=getattr(collection_request.destination_facility, "name", "Authorized Treatment Facility"),
                verification_method="DISPATCH_SIGN_OFF",
                remarks="Consignment in transit to treatment facility.",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log collection completion handover/custody: {cust_err}")

    CollectionStatusHistory.objects.create(
        request=collection_request,
        previous_status=old_status,
        new_status=CollectionRequest.Status.COLLECTED,
        changed_by=collector_user,
        remarks=remarks or "Biomedical waste bags inspected, verified, and loaded into collection transport vehicle.",
    )

    # Notify hospital
    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.WASTE_COLLECTED,
        title=f"Waste Collected: {collection_request.reference_number}",
        message=f"All {collection_request.total_bags} bags ({collection_request.total_weight} KG) were collected at {now.strftime('%d %b %Y, %H:%M')}.",
        reference_id=collection_request.reference_number,
        link=reverse("collection:hospital_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    # Notify administration
    notify_administrators(
        notification_type=Notification.NotificationType.WASTE_COLLECTED,
        title=f"Pickup Completed: {collection_request.reference_number}",
        message=f"Collector {collector_user.username} collected {collection_request.total_bags} bags ({collection_request.total_weight} KG) from {collection_request.hospital.organization_name or collection_request.hospital.username}.",
        reference_id=collection_request.reference_number,
        link=reverse("collection:admin_request_detail", kwargs={"reference_number": collection_request.reference_number}),
    )

    return collection_request


@transaction.atomic
def verify_bag_for_collection(collection_request, bag_identifier_or_token, collector_user, notes=""):
    """
    Verifies that a scanned or entered bag ID or UUID token belongs to the given collection request.
    Marks BagVerificationRecord as verified.
    """
    if collector_user.role != User.Role.COLLECTOR or collection_request.assigned_collector_id != collector_user.id:
        raise PermissionDenied("You can verify bags only for requests assigned to you.")

    identifier = str(bag_identifier_or_token).strip()

    # Match by bag_id, or UUID qr_token, or ID
    matching_bag = collection_request.bags.filter(
        models.Q(bag_id__iexact=identifier) | models.Q(qr_token__iexact=identifier)
    ).first()

    if not matching_bag:
        raise ValidationError(f"Waste bag '{identifier}' is not part of collection request {collection_request.reference_number}.")

    verification_rec, _ = BagVerificationRecord.objects.get_or_create(
        collection_request=collection_request,
        bag=matching_bag,
    )
    verification_rec.is_verified = True
    verification_rec.verified_at = timezone.now()
    verification_rec.verified_by = collector_user
    if notes:
        verification_rec.notes = notes
    verification_rec.save()

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        record_custody_event(
            bag=matching_bag,
            collection_request=collection_request,
            event_type=CustodyEvent.EventType.COLLECTOR_VERIFIED,
            new_custody_status=WasteBag.Status.REGISTERED,
            actor=collector_user,
            origin_location=collection_request.pickup_address,
            destination_location="Transport Vehicle",
            verification_method="BARCODE_VERIFICATION",
            weight_at_event=matching_bag.weight,
            remarks=notes or f"Verified at hospital dock by collector {collector_user.username}.",
        )
    except Exception as cust_err:
        logger.warning(f"Could not log COLLECTOR_VERIFIED custody event: {cust_err}")

    return verification_rec

