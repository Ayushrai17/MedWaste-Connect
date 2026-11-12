import logging
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from waste.models import WasteBag
from notifications.models import Notification
from notifications.services import send_notification, notify_administrators
from .models import CustodyEvent, CustodyHandover, CustodyDiscrepancy

logger = logging.getLogger(__name__)
User = get_user_model()


def record_custody_event(
    bag,
    event_type,
    new_custody_status=None,
    actor=None,
    actor_role="",
    origin_location="",
    destination_location="",
    verification_method="",
    weight_at_event=None,
    remarks="",
    collection_request=None,
    reception_manifest=None,
    treatment_batch=None,
    disposal_record=None,
    related_event=None,
    is_compensating_event=False,
    evidence_document=None,
    timestamp=None,
):
    """
    Records an immutable append-only CustodyEvent for a specific WasteBag.
    Idempotent: prevents duplicate consecutive identical events for the same bag and reference.
    """
    if not bag:
        raise ValueError("A valid WasteBag instance is required to record a custody event.")

    event_time = timestamp or timezone.now()

    # Determine previous custody status
    last_event = bag.custody_events.order_by("-timestamp", "-id").first()
    previous_status = last_event.new_custody_status if last_event else bag.status

    # If new_custody_status not supplied, default to current bag status
    target_status = new_custody_status or bag.status

    # Idempotency check: check if the exact same event type was logged recently (within 5 seconds)
    # for this bag with the same entity to avoid duplicate clicks
    recent_dup = CustodyEvent.objects.filter(
        bag=bag,
        event_type=event_type,
        collection_request=collection_request,
        reception_manifest=reception_manifest,
        treatment_batch=treatment_batch,
        disposal_record=disposal_record,
    ).order_by("-timestamp").first()

    if recent_dup and not is_compensating_event:
        time_diff = (event_time - recent_dup.timestamp).total_seconds()
        if abs(time_diff) < 5:
            logger.debug(f"Skipping duplicate custody event {event_type} for bag {bag.bag_id}")
            return recent_dup

    # Determine actor role if not explicitly provided
    resolved_role = actor_role
    if not resolved_role and actor:
        resolved_role = getattr(actor, "role", "")
        if hasattr(actor, "get_role_display"):
            resolved_role = actor.get_role_display()

    # Create immutable event
    event = CustodyEvent.objects.create(
        bag=bag,
        collection_request=collection_request,
        reception_manifest=reception_manifest,
        treatment_batch=treatment_batch,
        disposal_record=disposal_record,
        event_type=event_type,
        previous_custody_status=previous_status,
        new_custody_status=target_status,
        actor=actor,
        actor_role=resolved_role,
        timestamp=event_time,
        origin_location=origin_location,
        destination_location=destination_location,
        verification_method=verification_method,
        weight_at_event=weight_at_event,
        remarks=remarks,
        evidence_document=evidence_document,
        related_event=related_event,
        is_compensating_event=is_compensating_event,
    )

    return event


@transaction.atomic
def create_custody_handover(
    handover_type,
    collection_request,
    releasing_user,
    receiving_user,
    bags,
    releasing_role="",
    receiving_role="",
    releasing_organization="",
    receiving_organization="",
    reception_manifest=None,
    total_bags_expected=0,
    total_bags_verified=0,
    expected_weight=Decimal("0.00"),
    actual_weight=Decimal("0.00"),
    status=CustodyHandover.Status.ACKNOWLEDGED,
    dispute_reason="",
    remarks="",
):
    """
    Executes a formal dual-party custody handover, records the handshake contract,
    and appends immutable custody events to each physical bag.
    """
    handover = CustodyHandover.objects.create(
        handover_type=handover_type,
        collection_request=collection_request,
        reception_manifest=reception_manifest,
        releasing_user=releasing_user,
        releasing_role=releasing_role or (releasing_user.get_role_display() if hasattr(releasing_user, "get_role_display") else ""),
        releasing_organization=releasing_organization or getattr(releasing_user, "organization_name", ""),
        receiving_user=receiving_user,
        receiving_role=receiving_role or (receiving_user.get_role_display() if hasattr(receiving_user, "get_role_display") else ""),
        receiving_organization=receiving_organization or getattr(receiving_user, "organization_name", ""),
        handover_timestamp=timezone.now(),
        total_bags_expected=total_bags_expected,
        total_bags_verified=total_bags_verified,
        expected_weight=expected_weight,
        actual_weight=actual_weight,
        status=status,
        dispute_reason=dispute_reason,
        remarks=remarks,
        releasing_signature_acknowledged=True,
        receiving_signature_acknowledged=(status != CustodyHandover.Status.DISPUTED),
    )

    bag_list = list(bags)
    if bag_list:
        handover.bags.set(bag_list)

    # Determine event type based on handover type
    if handover_type == CustodyHandover.HandoverType.HOSPITAL_TO_COLLECTOR:
        evt_type = CustodyEvent.EventType.CUSTODY_TRANSFERRED_TO_COLLECTOR
        new_status = WasteBag.Status.IN_TRANSIT
        origin = handover.releasing_organization or "Hospital Waste Ward"
        destination = "Transport Vehicle"
    else:
        evt_type = CustodyEvent.EventType.FACILITY_ARRIVED
        new_status = WasteBag.Status.IN_TRANSIT
        origin = "Transport Vehicle"
        destination = getattr(reception_manifest.facility, "name", "Treatment Facility") if reception_manifest else "Treatment Facility"

    for bag in bag_list:
        record_custody_event(
            bag=bag,
            event_type=evt_type,
            new_custody_status=new_status,
            actor=receiving_user,
            actor_role=receiving_role or (receiving_user.get_role_display() if hasattr(receiving_user, "get_role_display") else ""),
            origin_location=origin,
            destination_location=destination,
            verification_method="DIGITAL_HANDOVER_SIGNATURE",
            weight_at_event=bag.weight,
            remarks=f"Handover {handover.handover_reference}. {remarks}".strip(),
            collection_request=collection_request,
            reception_manifest=reception_manifest,
        )

    return handover


@transaction.atomic
def report_discrepancy(
    discrepancy_type,
    description,
    reported_by,
    bag=None,
    collection_request=None,
    reception_manifest=None,
    severity=CustodyDiscrepancy.Severity.MEDIUM,
):
    """
    Formal discrepancy logging mechanism. Alerts administrators and logs exception against bag.
    """
    if not description or not description.strip():
        raise ValidationError("A detailed description of the discrepancy is required.")

    discrepancy = CustodyDiscrepancy.objects.create(
        discrepancy_type=discrepancy_type,
        severity=severity,
        status=CustodyDiscrepancy.Status.OPEN,
        description=description.strip(),
        reported_by=reported_by,
        bag=bag,
        collection_request=collection_request,
        reception_manifest=reception_manifest,
        reported_at=timezone.now(),
    )

    # If associated with a bag, log a custody event or note
    if bag:
        record_custody_event(
            bag=bag,
            event_type=CustodyEvent.EventType.FACILITY_REJECTED if discrepancy_type == CustodyDiscrepancy.DiscrepancyType.REJECTED_BY_FACILITY else CustodyEvent.EventType.CUSTODY_CORRECTION,
            new_custody_status=bag.status,
            actor=reported_by,
            origin_location="",
            destination_location="",
            verification_method="DISCREPANCY_FLAG",
            remarks=f"Discrepancy [{discrepancy.discrepancy_reference}]: {discrepancy.get_discrepancy_type_display()} — {description[:100]}",
            collection_request=collection_request,
            reception_manifest=reception_manifest,
            is_compensating_event=True,
        )

    # Notify administrators
    notify_administrators(
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=f"Custody Exception: {discrepancy.discrepancy_reference} ({discrepancy.get_severity_display()})",
        message=f"{reported_by.get_full_name() or reported_by.username} reported: {discrepancy.get_discrepancy_type_display()}. {description[:150]}",
        reference_id=discrepancy.discrepancy_reference,
    )

    return discrepancy


@transaction.atomic
def resolve_discrepancy(
    discrepancy,
    resolved_by,
    resolution_notes,
    new_status=CustodyDiscrepancy.Status.RESOLVED,
):
    """
    Resolves an open discrepancy with mandatory action notes.
    """
    if not resolution_notes or not resolution_notes.strip():
        raise ValidationError("Resolution notes must be provided.")

    discrepancy.status = new_status
    discrepancy.resolved_by = resolved_by
    discrepancy.resolved_at = timezone.now()
    discrepancy.resolution_notes = resolution_notes.strip()
    discrepancy.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_notes", "updated_at"])

    # Notify reporting user
    send_notification(
        recipient=discrepancy.reported_by,
        notification_type=Notification.NotificationType.SYSTEM_ALERT,
        title=f"Discrepancy Resolved: {discrepancy.discrepancy_reference}",
        message=f"Your reported exception {discrepancy.discrepancy_reference} was marked as {discrepancy.get_status_display()} by {resolved_by.username}.",
        reference_id=discrepancy.discrepancy_reference,
    )

    return discrepancy


def can_user_access_bag_tracking(user, bag):
    """
    Enforces strict role-based access control for detailed custody tracking.
    - ADMIN: Full access to any bag.
    - HOSPITAL: Access only to bags originated by their hospital.
    - COLLECTOR: Access to bags part of collection requests assigned to them.
    - FACILITY: Access to bags received/manifested at their managed facility.
    - Anonymous or unauthorized: False (must use public sanitized tracker).
    """
    if not user or not user.is_authenticated:
        return False

    if user.role == User.Role.ADMIN:
        return True

    if user.role == User.Role.HOSPITAL:
        return bag.hospital_id == user.id

    if user.role == User.Role.COLLECTOR:
        return bag.collection_requests.filter(assigned_collector=user).exists()

    if user.role == User.Role.FACILITY:
        facility = getattr(user, "managed_facility", None)
        if not facility:
            return False
        # Bag is at facility if part of a reception manifest or treatment batch for this facility
        has_reception = bag.facility_receipts.filter(reception_manifest__facility=facility).exists()
        has_batch = bag.treatment_batches.filter(facility=facility).exists()
        return has_reception or has_batch

    return False


def backfill_bag_events_if_missing(bag):
    """
    Safe idempotency reconstructor: If a bag was created or processed before Step 5
    was implemented and lacks CustodyEvents, backfills them accurately from verified
    database relationships without altering timestamps or fabricating unknown data.
    """
    if bag.custody_events.exists():
        return

    events_to_create = []

    # 1. Registration
    events_to_create.append(
        CustodyEvent(
            bag=bag,
            event_type=CustodyEvent.EventType.BAG_REGISTERED,
            previous_custody_status="",
            new_custody_status=WasteBag.Status.REGISTERED,
            actor=bag.registered_by or bag.hospital,
            actor_role="Hospital Staff",
            timestamp=bag.registered_at,
            origin_location=f"{bag.hospital.organization_name or bag.hospital.username} ({bag.ward})",
            destination_location="Hospital Storage Dock",
            verification_method="WEIGHMENT_AND_TAGGING",
            weight_at_event=bag.weight,
            remarks=f"Initial registration in batch {bag.batch.batch_number if bag.batch else 'Independent'}",
        )
    )

    # 2. QR Generated
    if bag.qr_status == WasteBag.QRStatus.READY:
        events_to_create.append(
            CustodyEvent(
                bag=bag,
                event_type=CustodyEvent.EventType.QR_GENERATED,
                previous_custody_status=WasteBag.Status.REGISTERED,
                new_custody_status=WasteBag.Status.REGISTERED,
                actor=bag.registered_by or bag.hospital,
                actor_role="System / Hospital Staff",
                timestamp=bag.registered_at,
                origin_location=f"{bag.hospital.organization_name or bag.hospital.username}",
                verification_method="QR_CRYPTO_GENERATION",
                remarks="Dynamic cryptographic QR code label generated and affixed.",
            )
        )

    # 3. Collection Dispatch
    col_req = bag.collection_requests.order_by("-created_at").first()
    if col_req:
        events_to_create.append(
            CustodyEvent(
                bag=bag,
                collection_request=col_req,
                event_type=CustodyEvent.EventType.COLLECTION_REQUESTED,
                previous_custody_status=WasteBag.Status.REGISTERED,
                new_custody_status=WasteBag.Status.REGISTERED,
                actor=col_req.hospital,
                actor_role="Hospital Staff",
                timestamp=col_req.created_at,
                origin_location=col_req.pickup_address,
                remarks=f"Scheduled pickup requested ({col_req.reference_number}).",
            )
        )

        if col_req.status in [
            col_req.Status.COLLECTED,
            col_req.Status.COMPLETED,
        ] or bag.status in [WasteBag.Status.IN_TRANSIT, WasteBag.Status.RECEIVED, WasteBag.Status.TREATED, WasteBag.Status.DISPOSED]:
            col_time = col_req.actual_collection_time or col_req.updated_at
            events_to_create.append(
                CustodyEvent(
                    bag=bag,
                    collection_request=col_req,
                    event_type=CustodyEvent.EventType.COLLECTOR_VERIFIED,
                    previous_custody_status=WasteBag.Status.REGISTERED,
                    new_custody_status=WasteBag.Status.IN_TRANSIT,
                    actor=col_req.assigned_collector,
                    actor_role="Waste Collector",
                    timestamp=col_time,
                    origin_location=col_req.pickup_address,
                    destination_location="Transport Vehicle",
                    verification_method="BARCODE_VERIFICATION",
                    weight_at_event=bag.weight,
                    remarks=f"Verified and loaded by collector {col_req.assigned_collector.username if col_req.assigned_collector else 'Assigned Collector'}.",
                )
            )
            events_to_create.append(
                CustodyEvent(
                    bag=bag,
                    collection_request=col_req,
                    event_type=CustodyEvent.EventType.TRANSIT_STARTED,
                    previous_custody_status=WasteBag.Status.IN_TRANSIT,
                    new_custody_status=WasteBag.Status.IN_TRANSIT,
                    actor=col_req.assigned_collector,
                    actor_role="Waste Collector",
                    timestamp=col_time,
                    origin_location="Hospital Dock",
                    destination_location="Authorized Treatment Facility",
                    verification_method="DISPATCH_SIGN_OFF",
                    remarks="Consignment in transit.",
                )
            )

    # 4. Facility Reception
    receipt = bag.facility_receipts.select_related("reception_manifest__facility").order_by("-id").first()
    if receipt:
        manifest = receipt.reception_manifest
        events_to_create.append(
            CustodyEvent(
                bag=bag,
                reception_manifest=manifest,
                event_type=CustodyEvent.EventType.FACILITY_VERIFIED,
                previous_custody_status=WasteBag.Status.IN_TRANSIT,
                new_custody_status=WasteBag.Status.RECEIVED if receipt.acceptance_decision == receipt.AcceptanceDecision.ACCEPTED else bag.status,
                actor=manifest.received_by,
                actor_role="Treatment Facility Operator",
                timestamp=manifest.received_at,
                origin_location="Transport Vehicle",
                destination_location=manifest.facility.name,
                verification_method="GATE_INSPECTION_SCAN",
                weight_at_event=receipt.actual_weight,
                remarks=f"Manifest {manifest.reference_number}: {receipt.get_packaging_condition_display()}.",
            )
        )
        if receipt.acceptance_decision == receipt.AcceptanceDecision.ACCEPTED:
            events_to_create.append(
                CustodyEvent(
                    bag=bag,
                    reception_manifest=manifest,
                    event_type=CustodyEvent.EventType.FACILITY_ACCEPTED,
                    previous_custody_status=WasteBag.Status.IN_TRANSIT,
                    new_custody_status=WasteBag.Status.RECEIVED,
                    actor=manifest.received_by,
                    actor_role="Treatment Facility Operator",
                    timestamp=manifest.received_at,
                    origin_location=manifest.facility.name,
                    verification_method="MANIFEST_ACCEPTANCE",
                    weight_at_event=receipt.actual_weight,
                    remarks=f"Accepted for processing at {manifest.facility.name}.",
                )
            )

    # 5. Treatment Run
    t_batch = bag.treatment_batches.select_related("facility").order_by("-created_at").first()
    if t_batch and t_batch.status == t_batch.Status.COMPLETED:
        events_to_create.append(
            CustodyEvent(
                bag=bag,
                treatment_batch=t_batch,
                event_type=CustodyEvent.EventType.TREATMENT_COMPLETED,
                previous_custody_status=WasteBag.Status.RECEIVED,
                new_custody_status=WasteBag.Status.TREATED,
                actor=t_batch.operator,
                actor_role="Treatment Facility Operator",
                timestamp=t_batch.end_time or t_batch.updated_at,
                origin_location=t_batch.facility.name,
                destination_location="Decontaminated Residue Storage",
                verification_method="CYCLE_TELEMETRY",
                remarks=f"Processed via {t_batch.get_treatment_method_display()} (Batch {t_batch.batch_number}).",
            )
        )

    # 6. Final Disposal
    d_record = bag.disposal_records.select_related("facility").order_by("-created_at").first()
    if d_record:
        events_to_create.append(
            CustodyEvent(
                bag=bag,
                disposal_record=d_record,
                event_type=CustodyEvent.EventType.DISPOSAL_DOCUMENTED,
                previous_custody_status=WasteBag.Status.TREATED,
                new_custody_status=WasteBag.Status.DISPOSED,
                actor=d_record.operator,
                actor_role="Treatment Facility Operator",
                timestamp=timezone.make_aware(timezone.datetime.combine(d_record.disposal_date, d_record.disposal_time)) if not timezone.is_aware(timezone.datetime.combine(d_record.disposal_date, d_record.disposal_time)) else timezone.datetime.combine(d_record.disposal_date, d_record.disposal_time),
                origin_location=d_record.facility.name,
                destination_location=d_record.disposal_location,
                verification_method="COMPLIANCE_CERTIFICATE",
                remarks=f"Final disposal: {d_record.get_final_disposal_method_display()} at {d_record.disposal_location}.",
            )
        )

    # Bulk create safely
    CustodyEvent.objects.bulk_create(events_to_create, ignore_conflicts=True)


def get_bag_timeline(bag, requesting_user=None):
    """
    Compiles a comprehensive 6-stage lifecycle timeline model for an individual waste bag.
    Stages:
      1. WARD REGISTRATION
      2. COLLECTION DISPATCH
      3. CUSTODY HANDOVER & TRANSIT
      4. FACILITY RECEPTION & INSPECTION
      5. TREATMENT & PROCESSING
      6. FINAL RESIDUE DISPOSAL & AUDIT
    """
    # Ensure backfill if needed
    backfill_bag_events_if_missing(bag)

    events = list(bag.custody_events.select_related("actor", "collection_request", "reception_manifest", "treatment_batch", "disposal_record").order_by("timestamp", "id"))
    discrepancies = list(bag.discrepancies.select_related("reported_by", "resolved_by").order_by("-reported_at"))
    handovers = list(bag.custody_handovers.select_related("releasing_user", "receiving_user").order_by("handover_timestamp"))

    # Map events to 6 conceptual stages
    stage_1_types = [CustodyEvent.EventType.BAG_REGISTERED, CustodyEvent.EventType.QR_GENERATED]
    stage_2_types = [
        CustodyEvent.EventType.COLLECTION_REQUESTED,
        CustodyEvent.EventType.COLLECTION_SCHEDULED,
        CustodyEvent.EventType.COLLECTOR_ASSIGNED,
        CustodyEvent.EventType.COLLECTOR_ACCEPTED,
    ]
    stage_3_types = [
        CustodyEvent.EventType.COLLECTOR_VERIFIED,
        CustodyEvent.EventType.CUSTODY_TRANSFERRED_TO_COLLECTOR,
        CustodyEvent.EventType.TRANSIT_STARTED,
    ]
    stage_4_types = [
        CustodyEvent.EventType.FACILITY_ARRIVED,
        CustodyEvent.EventType.FACILITY_VERIFIED,
        CustodyEvent.EventType.FACILITY_ACCEPTED,
        CustodyEvent.EventType.FACILITY_REJECTED,
        CustodyEvent.EventType.FACILITY_MISSING,
    ]
    stage_5_types = [
        CustodyEvent.EventType.TREATMENT_BATCHED,
        CustodyEvent.EventType.TREATMENT_STARTED,
        CustodyEvent.EventType.TREATMENT_COMPLETED,
        CustodyEvent.EventType.TREATMENT_FAILED,
    ]
    stage_6_types = [
        CustodyEvent.EventType.DISPOSAL_DOCUMENTED,
        CustodyEvent.EventType.DISPOSAL_VERIFIED,
        CustodyEvent.EventType.CUSTODY_CORRECTION,
    ]

    all_event_types = set(e.event_type for e in events)

    # Determine status of each stage
    stages = [
        {
            "stage_number": 1,
            "title": "Ward Registration",
            "subtitle": "Generation & Barcoding",
            "icon": "bi-hospital",
            "is_completed": bool(all_event_types.intersection(stage_1_types)),
            "is_active": bag.status == WasteBag.Status.REGISTERED and not bool(all_event_types.intersection(stage_2_types)),
            "events": [e for e in events if e.event_type in stage_1_types],
        },
        {
            "stage_number": 2,
            "title": "Collection Dispatch",
            "subtitle": "Scheduling & Allocation",
            "icon": "bi-calendar-check",
            "is_completed": bool(all_event_types.intersection(stage_2_types)),
            "is_active": bool(all_event_types.intersection(stage_2_types)) and not bool(all_event_types.intersection(stage_3_types)),
            "events": [e for e in events if e.event_type in stage_2_types],
        },
        {
            "stage_number": 3,
            "title": "Dock Handover & Transit",
            "subtitle": "Dual Sign-off & Logistics",
            "icon": "bi-truck",
            "is_completed": bool(all_event_types.intersection(stage_3_types)),
            "is_active": bag.status == WasteBag.Status.IN_TRANSIT,
            "events": [e for e in events if e.event_type in stage_3_types],
        },
        {
            "stage_number": 4,
            "title": "Facility Inspection",
            "subtitle": "Gate Reception & QA",
            "icon": "bi-clipboard-check",
            "is_completed": bool(all_event_types.intersection(stage_4_types)),
            "is_active": bag.status == WasteBag.Status.RECEIVED and not bool(all_event_types.intersection(stage_5_types)),
            "events": [e for e in events if e.event_type in stage_4_types],
        },
        {
            "stage_number": 5,
            "title": "Treatment Processing",
            "subtitle": "Decontamination Cycle",
            "icon": "bi-fire",
            "is_completed": bool(all_event_types.intersection([CustodyEvent.EventType.TREATMENT_COMPLETED])),
            "is_active": bag.status == WasteBag.Status.TREATED and not bool(all_event_types.intersection(stage_6_types)),
            "events": [e for e in events if e.event_type in stage_5_types],
        },
        {
            "stage_number": 6,
            "title": "Final Disposal & Audit",
            "subtitle": "Statutory Closeout",
            "icon": "bi-shield-check",
            "is_completed": bool(all_event_types.intersection(stage_6_types)),
            "is_active": bag.status == WasteBag.Status.DISPOSED,
            "events": [e for e in events if e.event_type in stage_6_types],
        },
    ]

    # Calculate completed stage count and progress percentage
    completed_stages = sum(1 for s in stages if s["is_completed"])
    if bag.status == WasteBag.Status.DISPOSED:
        progress_pct = 100
        current_stage_num = 6
    elif bag.status == WasteBag.Status.TREATED:
        progress_pct = 83
        current_stage_num = 5
    elif bag.status == WasteBag.Status.RECEIVED:
        progress_pct = 66
        current_stage_num = 4
    elif bag.status == WasteBag.Status.IN_TRANSIT:
        progress_pct = 50
        current_stage_num = 3
    elif bool(all_event_types.intersection(stage_2_types)):
        progress_pct = 33
        current_stage_num = 2
    else:
        progress_pct = 16
        current_stage_num = 1

    # Duration calculation
    total_duration = None
    if events:
        first_time = events[0].timestamp
        last_time = events[-1].timestamp
        total_duration = last_time - first_time

    # Active discrepancy count
    open_discrepancies_count = sum(1 for d in discrepancies if d.status in [CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW])

    return {
        "bag": bag,
        "events": events,
        "stages": stages,
        "handovers": handovers,
        "discrepancies": discrepancies,
        "open_discrepancies_count": open_discrepancies_count,
        "current_stage_num": current_stage_num,
        "completed_stages": completed_stages,
        "progress_pct": progress_pct,
        "total_duration": total_duration,
        "first_event": events[0] if events else None,
        "latest_event": events[-1] if events else None,
    }
