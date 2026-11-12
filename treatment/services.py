import logging
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification
from notifications.services import send_notification, notify_administrators
from waste.models import WasteBag
from collection.models import CollectionRequest
from facilities.models import TreatmentFacility
from .models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
    FacilityAuditHistory,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def log_facility_audit(facility, action_type, entity_reference, user, description):
    """
    Appends an immutable audit log for facility events.
    """
    FacilityAuditHistory.objects.create(
        facility=facility,
        action_type=action_type,
        entity_reference=entity_reference,
        changed_by=user,
        description=description,
    )


# ==============================================================================
# INCOMING WASTE RECEPTION WORKFLOW
# ==============================================================================

@transaction.atomic
def receive_incoming_consignment(
    facility_user,
    collection_request,
    gross_received_weight=None,
    remarks="",
    capacity_override_authorized=False,
    capacity_override_reason="",
):
    """
    Creates a FacilityReceptionManifest for an incoming collection request.
    Verifies that the collection is in an in-transit / collected state and validates
    facility daily capacity limits before admission.
    """
    if facility_user.role != User.Role.FACILITY:
        raise PermissionDenied("Only authorized treatment facility operators can receive waste consignments.")

    facility = getattr(facility_user, "managed_facility", None)
    if not facility:
        raise PermissionDenied("User does not have an associated treatment facility profile.")

    if not facility.is_active or facility.approval_status != TreatmentFacility.ApprovalStatus.APPROVED:
        raise ValidationError("Your facility is not currently in an approved, active operational state.")

    # Check destination facility match if destination was specifically assigned
    if collection_request.destination_facility and collection_request.destination_facility_id != facility.id:
        raise PermissionDenied(f"This collection is assigned to destination facility '{collection_request.destination_facility.name}'.")

    # Verify collection request has arrived / is collected
    valid_reception_statuses = [
        CollectionRequest.Status.COLLECTED,
        CollectionRequest.Status.COMPLETED,
        CollectionRequest.Status.PICKUP_IN_PROGRESS,
    ]
    if collection_request.status not in valid_reception_statuses:
        raise ValidationError(
            f"Cannot receive collection request currently in status '{collection_request.get_status_display()}'. "
            f"Waste must be collected and in-transit."
        )

    # Check if reception already exists
    existing = FacilityReceptionManifest.objects.filter(collection_request=collection_request).first()
    if existing:
        return existing

    # Lock facility for safe capacity evaluation
    locked_facility = TreatmentFacility.objects.select_for_update().get(id=facility.id)
    expected_weight = collection_request.total_weight
    incoming_weight = gross_received_weight if gross_received_weight is not None else expected_weight

    # Real-time capacity check
    rem_capacity = locked_facility.get_remaining_capacity()
    if incoming_weight > rem_capacity:
        if not capacity_override_authorized:
            raise ValidationError(
                f"Incoming consignment ({incoming_weight} KG) exceeds remaining daily capacity "
                f"({rem_capacity} KG) of facility '{facility.name}'. Administrative override required."
            )
        if not capacity_override_reason or not capacity_override_reason.strip():
            raise ValidationError("A mandatory explanation must be provided when authorizing capacity override.")

    # Create reception manifest
    reception = FacilityReceptionManifest.objects.create(
        facility=locked_facility,
        collection_request=collection_request,
        received_by=facility_user,
        received_at=timezone.now(),
        status=FacilityReceptionManifest.Status.RECEIVED,
        gross_received_weight=incoming_weight,
        expected_weight=expected_weight,
        weight_discrepancy=incoming_weight - expected_weight,
        expected_bag_count=collection_request.total_bags,
        capacity_override_authorized=capacity_override_authorized,
        capacity_override_reason=capacity_override_reason if capacity_override_authorized else "",
        capacity_override_by=facility_user if capacity_override_authorized else None,
        remarks=remarks,
    )

    # Create itemized bag receipt records for each expected bag
    for bag in collection_request.bags.all():
        FacilityBagReceipt.objects.create(
            reception_manifest=reception,
            bag=bag,
            is_verified=False,
            actual_weight=bag.weight,
            packaging_condition=FacilityBagReceipt.PackagingCondition.INTACT,
            acceptance_decision=FacilityBagReceipt.AcceptanceDecision.PENDING,
        )

    # Update collection request destination if not already set
    if not collection_request.destination_facility:
        collection_request.destination_facility = locked_facility
        collection_request.save(update_fields=["destination_facility", "updated_at"])

    try:
        from tracking.models import CustodyHandover
        from tracking.services import create_custody_handover
        collector = collection_request.assigned_collector or facility_user
        create_custody_handover(
            handover_type=CustodyHandover.HandoverType.COLLECTOR_TO_FACILITY,
            collection_request=collection_request,
            reception_manifest=reception,
            releasing_user=collector,
            receiving_user=facility_user,
            bags=collection_request.bags.all(),
            releasing_organization="Logistics Fleet",
            receiving_organization=locked_facility.name,
            total_bags_expected=collection_request.total_bags,
            total_bags_verified=0,
            expected_weight=expected_weight,
            actual_weight=incoming_weight,
            status=CustodyHandover.Status.ACKNOWLEDGED,
            remarks=remarks or f"Gate reception manifest logged at {locked_facility.name}.",
        )
    except Exception as cust_err:
        logger.warning(f"Could not log facility reception handover: {cust_err}")

    # Audit log
    log_facility_audit(
        facility=locked_facility,
        action_type="RECEPTION_LOGGED",
        entity_reference=reception.reference_number,
        user=facility_user,
        description=f"Received collection {collection_request.reference_number} ({collection_request.total_bags} bags, {incoming_weight} KG).",
    )

    # In-App Notifications
    notify_administrators(
        notification_type=Notification.NotificationType.WASTE_ARRIVED,
        title=f"Consignment Arrived: {reception.reference_number}",
        message=f"Collection {collection_request.reference_number} arrived at {locked_facility.name} ({collection_request.total_bags} bags).",
        reference_id=reception.reference_number,
    )

    send_notification(
        recipient=collection_request.hospital,
        notification_type=Notification.NotificationType.WASTE_ARRIVED,
        title=f"Consignment Arrived at Treatment Plant: {collection_request.reference_number}",
        message=f"Your biomedical waste arrived at {locked_facility.name} for gate verification.",
        reference_id=reception.reference_number,
    )

    return reception


@transaction.atomic
def verify_reception_bag(
    reception_manifest,
    bag_identifier_or_token,
    facility_user,
    actual_weight=None,
    packaging_condition="INTACT",
    remarks="",
):
    """
    Scans / verifies an individual bag at the treatment facility reception dock.
    Matches by bag_id, qr_token UUID, or id.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or reception_manifest.facility_id != facility.id:
        raise PermissionDenied("You can verify bags only for consignments at your own facility.")

    identifier = str(bag_identifier_or_token).strip()

    # Match receipt
    receipt = reception_manifest.bag_receipts.filter(
        models.Q(bag__bag_id__iexact=identifier) |
        models.Q(bag__qr_token__iexact=identifier)
    ).select_related("bag", "bag__category").first()

    if not receipt:
        raise ValidationError(f"Waste bag '{identifier}' is not part of reception manifest {reception_manifest.reference_number}.")

    receipt.is_verified = True
    receipt.verified_at = timezone.now()
    receipt.verified_by = facility_user
    if actual_weight is not None:
        receipt.actual_weight = actual_weight
    if packaging_condition:
        receipt.packaging_condition = packaging_condition
    if remarks:
        receipt.reception_remarks = remarks
    receipt.save()

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        record_custody_event(
            bag=receipt.bag,
            reception_manifest=reception_manifest,
            event_type=CustodyEvent.EventType.FACILITY_VERIFIED,
            new_custody_status=receipt.bag.status,
            actor=facility_user,
            origin_location="Transport Vehicle",
            destination_location=facility.name,
            verification_method="GATE_INSPECTION_SCAN",
            weight_at_event=receipt.actual_weight,
            remarks=f"Packaging: {receipt.get_packaging_condition_display()}. Scale weight: {receipt.actual_weight} KG.",
        )
    except Exception as cust_err:
        logger.warning(f"Could not log FACILITY_VERIFIED event: {cust_err}")

    return receipt


@transaction.atomic
def record_bag_acceptance_decision(
    bag_receipt,
    facility_user,
    decision,
    rejection_reason="",
    remarks="",
):
    """
    Records acceptance, rejection, or missing status for an individual received bag.
    Rejection requires a mandatory reason.
    Accepted bags update WasteBag status to RECEIVED.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or bag_receipt.reception_manifest.facility_id != facility.id:
        raise PermissionDenied("Unauthorized facility access.")

    if decision not in [
        FacilityBagReceipt.AcceptanceDecision.ACCEPTED,
        FacilityBagReceipt.AcceptanceDecision.REJECTED,
        FacilityBagReceipt.AcceptanceDecision.MISSING,
    ]:
        raise ValidationError("Invalid acceptance decision.")

    if decision == FacilityBagReceipt.AcceptanceDecision.REJECTED:
        if not rejection_reason or not rejection_reason.strip():
            raise ValidationError("A mandatory rejection explanation must be provided when rejecting a waste bag.")
        bag_receipt.rejection_reason = rejection_reason.strip()

    if decision == FacilityBagReceipt.AcceptanceDecision.ACCEPTED:
        if not bag_receipt.is_verified:
            raise ValidationError(f"Bag {bag_receipt.bag.bag_id} must be verified before it can be accepted for treatment.")
        # Update bag lifecycle custody state to RECEIVED
        bag = bag_receipt.bag
        bag.status = WasteBag.Status.RECEIVED
        bag.save(update_fields=["status", "updated_at"])

    bag_receipt.acceptance_decision = decision
    if remarks:
        bag_receipt.reception_remarks = remarks
    bag_receipt.save()

    # Custody Event & Discrepancy Hooks
    try:
        from tracking.models import CustodyEvent, CustodyDiscrepancy
        from tracking.services import record_custody_event, report_discrepancy

        if decision == FacilityBagReceipt.AcceptanceDecision.ACCEPTED:
            record_custody_event(
                bag=bag_receipt.bag,
                reception_manifest=bag_receipt.reception_manifest,
                event_type=CustodyEvent.EventType.FACILITY_ACCEPTED,
                new_custody_status=WasteBag.Status.RECEIVED,
                actor=facility_user,
                origin_location=facility.name,
                destination_location="Facility Processing Buffer",
                verification_method="MANIFEST_ACCEPTANCE",
                weight_at_event=bag_receipt.actual_weight,
                remarks=f"Accepted for prescribed treatment at {facility.name}.",
            )
        elif decision == FacilityBagReceipt.AcceptanceDecision.REJECTED:
            record_custody_event(
                bag=bag_receipt.bag,
                reception_manifest=bag_receipt.reception_manifest,
                event_type=CustodyEvent.EventType.FACILITY_REJECTED,
                new_custody_status=bag_receipt.bag.status,
                actor=facility_user,
                origin_location=facility.name,
                verification_method="INSPECTION_REJECTION",
                weight_at_event=bag_receipt.actual_weight,
                remarks=f"Rejected: {rejection_reason.strip()}",
            )
            report_discrepancy(
                discrepancy_type=CustodyDiscrepancy.DiscrepancyType.REJECTED_BY_FACILITY,
                severity=CustodyDiscrepancy.Severity.HIGH,
                description=f"Bag {bag_receipt.bag.bag_id} rejected at facility {facility.name}. Reason: {rejection_reason.strip()}",
                reported_by=facility_user,
                bag=bag_receipt.bag,
                collection_request=bag_receipt.reception_manifest.collection_request,
                reception_manifest=bag_receipt.reception_manifest,
            )
        elif decision == FacilityBagReceipt.AcceptanceDecision.MISSING:
            record_custody_event(
                bag=bag_receipt.bag,
                reception_manifest=bag_receipt.reception_manifest,
                event_type=CustodyEvent.EventType.FACILITY_MISSING,
                new_custody_status=bag_receipt.bag.status,
                actor=facility_user,
                remarks="Flagged missing from expected incoming consignment.",
            )
            report_discrepancy(
                discrepancy_type=CustodyDiscrepancy.DiscrepancyType.MISSING_AT_RECEPTION,
                severity=CustodyDiscrepancy.Severity.CRITICAL,
                description=f"Bag {bag_receipt.bag.bag_id} missing upon arrival at {facility.name} (Manifest {bag_receipt.reception_manifest.reference_number}).",
                reported_by=facility_user,
                bag=bag_receipt.bag,
                collection_request=bag_receipt.reception_manifest.collection_request,
                reception_manifest=bag_receipt.reception_manifest,
            )
    except Exception as cust_err:
        logger.warning(f"Could not log decision custody event/discrepancy: {cust_err}")

    # Recalculate parent manifest totals
    bag_receipt.reception_manifest.update_metrics()

    return bag_receipt


@transaction.atomic
def finalize_reception_manifest(reception_manifest, facility_user):
    """
    Concludes reception inspection of a manifest, computes discrepancies, and notifies parties.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or reception_manifest.facility_id != facility.id:
        raise PermissionDenied("Unauthorized facility access.")

    # Ensure all receipts have been resolved
    pending_count = reception_manifest.bag_receipts.filter(
        acceptance_decision=FacilityBagReceipt.AcceptanceDecision.PENDING
    ).count()
    if pending_count > 0:
        raise ValidationError(f"{pending_count} bag(s) still have a pending decision. Verify or reject all items first.")

    reception_manifest.update_metrics()

    # Audit log
    log_facility_audit(
        facility=facility,
        action_type="RECEPTION_CONCLUDED",
        entity_reference=reception_manifest.reference_number,
        user=facility_user,
        description=(
            f"Concluded reception: {reception_manifest.accepted_bag_count} accepted, "
            f"{reception_manifest.rejected_bag_count} rejected, {reception_manifest.missing_bag_count} missing. "
            f"Status: {reception_manifest.get_status_display()}."
        ),
    )

    # Determine notifications based on outcome
    hospital = reception_manifest.collection_request.hospital
    notif_type = Notification.NotificationType.RECEPTION_VERIFIED
    if reception_manifest.status == FacilityReceptionManifest.Status.REJECTED:
        notif_type = Notification.NotificationType.RECEPTION_REJECTED
    elif reception_manifest.status in [FacilityReceptionManifest.Status.DISCREPANCY_REPORTED, FacilityReceptionManifest.Status.PARTIALLY_ACCEPTED]:
        notif_type = Notification.NotificationType.RECEPTION_DISCREPANCY

    send_notification(
        recipient=hospital,
        notification_type=notif_type,
        title=f"Waste Reception: {reception_manifest.reference_number} ({reception_manifest.get_status_display()})",
        message=(
            f"Reception completed at {facility.name}. Accepted: {reception_manifest.accepted_bag_count}, "
            f"Rejected: {reception_manifest.rejected_bag_count}, Missing: {reception_manifest.missing_bag_count}. "
            f"Net accepted weight: {reception_manifest.gross_received_weight} KG."
        ),
        reference_id=reception_manifest.reference_number,
    )

    notify_administrators(
        notification_type=notif_type,
        title=f"Reception Outcome: {reception_manifest.reference_number}",
        message=(
            f"{facility.name} completed reception for {hospital.organization_name or hospital.username}: "
            f"{reception_manifest.accepted_bag_count} accepted, {reception_manifest.rejected_bag_count} rejected. "
            f"Status: {reception_manifest.get_status_display()}."
        ),
        reference_id=reception_manifest.reference_number,
    )

    return reception_manifest


# ==============================================================================
# TREATMENT PROCESS WORKFLOW
# ==============================================================================

@transaction.atomic
def create_treatment_batch(
    facility_user,
    treatment_method,
    bag_queryset,
    equipment_reference="",
    chamber_temperature=None,
    chamber_pressure=None,
    cycle_duration=None,
    remarks="",
):
    """
    Assembles a new TreatmentBatch from verified, accepted bags.
    Validates facility technology support, prevents duplicate active treatment,
    and blocks rejected bags.
    """
    if facility_user.role != User.Role.FACILITY:
        raise PermissionDenied("Only authorized treatment facility operators can execute treatment batches.")

    facility = getattr(facility_user, "managed_facility", None)
    if not facility:
        raise PermissionDenied("User does not have an associated treatment facility profile.")

    # Validate facility capability for this treatment method
    method_support_map = {
        TreatmentBatch.TreatmentMethod.INCINERATION: facility.supports_incineration,
        TreatmentBatch.TreatmentMethod.AUTOCLAVING: facility.supports_autoclaving,
        TreatmentBatch.TreatmentMethod.SHREDDING: facility.supports_shredding,
        TreatmentBatch.TreatmentMethod.CHEMICAL_DISINFECTION: facility.supports_chemical_disinfection,
        TreatmentBatch.TreatmentMethod.ENCAPSULATION: facility.supports_encapsulation,
    }
    if not method_support_map.get(treatment_method, False):
        raise ValidationError(f"Facility '{facility.name}' is not licensed or configured for {treatment_method}.")

    selected_bags = list(bag_queryset)
    if not selected_bags:
        raise ValidationError("At least one accepted waste bag must be selected for treatment.")

    selected_bag_ids = [b.id for b in selected_bags]

    # Lock selected bags
    locked_bags = list(
        WasteBag.objects.select_for_update().filter(id__in=selected_bag_ids)
    )

    # Verify all bags belong to this facility's reception manifests and are ACCEPTED
    for bag in locked_bags:
        receipt = FacilityBagReceipt.objects.filter(
            reception_manifest__facility=facility,
            bag=bag
        ).first()
        if not receipt:
            raise ValidationError(f"Bag {bag.bag_id} was not received at this facility.")
        if receipt.acceptance_decision != FacilityBagReceipt.AcceptanceDecision.ACCEPTED:
            raise ValidationError(
                f"Bag {bag.bag_id} has acceptance status '{receipt.get_acceptance_decision_display()}' "
                f"and cannot enter treatment processing."
            )

    # Check that no bag is already part of an active/completed treatment batch
    conflicting_batches = TreatmentBatch.objects.filter(
        bags__id__in=selected_bag_ids,
        status__in=[
            TreatmentBatch.Status.PENDING,
            TreatmentBatch.Status.IN_PROGRESS,
            TreatmentBatch.Status.COMPLETED,
        ]
    ).distinct()
    if conflicting_batches.exists():
        conf_ref = conflicting_batches.first().batch_number
        raise ValidationError(f"One or more selected bags are already part of treatment batch {conf_ref}.")

    # Primary waste category
    first_cat = locked_bags[0].category if locked_bags else None

    batch = TreatmentBatch.objects.create(
        facility=facility,
        treatment_method=treatment_method,
        waste_category=first_cat,
        status=TreatmentBatch.Status.PENDING,
        operator=facility_user,
        equipment_reference=equipment_reference,
        chamber_temperature_celsius=chamber_temperature,
        chamber_pressure_psi=chamber_pressure,
        cycle_duration_minutes=cycle_duration,
        treatment_remarks=remarks,
    )
    batch.bags.set(locked_bags)
    batch.recalculate_processing_weight()

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in locked_bags:
            record_custody_event(
                bag=b,
                treatment_batch=batch,
                event_type=CustodyEvent.EventType.TREATMENT_BATCHED,
                new_custody_status=WasteBag.Status.RECEIVED,
                actor=facility_user,
                origin_location="Facility Processing Buffer",
                destination_location=f"Batch {batch.batch_number}",
                verification_method="BATCH_ALLOCATION",
                weight_at_event=b.weight,
                remarks=f"Allocated to treatment batch {batch.batch_number} ({batch.get_treatment_method_display()}).",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log TREATMENT_BATCHED events: {cust_err}")

    log_facility_audit(
        facility=facility,
        action_type="TREATMENT_BATCH_CREATED",
        entity_reference=batch.batch_number,
        user=facility_user,
        description=f"Created treatment batch {batch.batch_number} ({batch.get_treatment_method_display()}, {batch.processing_weight} KG).",
    )

    return batch


@transaction.atomic
def start_treatment_batch(treatment_batch, facility_user, remarks=""):
    """
    Initiates execution of a treatment batch cycle.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or treatment_batch.facility_id != facility.id:
        raise PermissionDenied("Unauthorized facility access.")

    if treatment_batch.status != TreatmentBatch.Status.PENDING:
        raise ValidationError(f"Cannot start treatment batch in status '{treatment_batch.get_status_display()}'.")

    treatment_batch.status = TreatmentBatch.Status.IN_PROGRESS
    treatment_batch.start_time = timezone.now()
    if remarks:
        treatment_batch.treatment_remarks = f"{treatment_batch.treatment_remarks}\n[Started]: {remarks}".strip()
    treatment_batch.save(update_fields=["status", "start_time", "treatment_remarks", "updated_at"])

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in treatment_batch.bags.all():
            record_custody_event(
                bag=b,
                treatment_batch=treatment_batch,
                event_type=CustodyEvent.EventType.TREATMENT_STARTED,
                new_custody_status=WasteBag.Status.RECEIVED,
                actor=facility_user,
                origin_location="Facility Processing Buffer",
                destination_location=treatment_batch.equipment_reference or "Primary Processing Unit",
                remarks=f"Processing cycle started ({treatment_batch.get_treatment_method_display()}).",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log TREATMENT_STARTED events: {cust_err}")

    log_facility_audit(
        facility=facility,
        action_type="TREATMENT_STARTED",
        entity_reference=treatment_batch.batch_number,
        user=facility_user,
        description=f"Treatment run initiated on {treatment_batch.equipment_reference or 'Primary Unit'}.",
    )

    return treatment_batch


@transaction.atomic
def complete_treatment_batch(
    treatment_batch,
    facility_user,
    end_time=None,
    chamber_temperature=None,
    chamber_pressure=None,
    cycle_duration=None,
    remarks="",
):
    """
    Concludes execution of a treatment batch, updating bags to TREATED status.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or treatment_batch.facility_id != facility.id:
        raise PermissionDenied("Unauthorized facility access.")

    if treatment_batch.status != TreatmentBatch.Status.IN_PROGRESS:
        raise ValidationError(f"Cannot complete treatment batch in status '{treatment_batch.get_status_display()}'.")

    now = end_time or timezone.now()
    treatment_batch.status = TreatmentBatch.Status.COMPLETED
    treatment_batch.end_time = now
    if chamber_temperature is not None:
        treatment_batch.chamber_temperature_celsius = chamber_temperature
    if chamber_pressure is not None:
        treatment_batch.chamber_pressure_psi = chamber_pressure
    if cycle_duration is not None:
        treatment_batch.cycle_duration_minutes = cycle_duration
    if remarks:
        treatment_batch.treatment_remarks = f"{treatment_batch.treatment_remarks}\n[Completed]: {remarks}".strip()

    treatment_batch.save(update_fields=[
        "status",
        "end_time",
        "chamber_temperature_celsius",
        "chamber_pressure_psi",
        "cycle_duration_minutes",
        "treatment_remarks",
        "updated_at",
    ])

    # Transition associated physical bags to TREATED
    treatment_batch.bags.update(
        status=WasteBag.Status.TREATED,
        updated_at=now,
    )

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in treatment_batch.bags.all():
            record_custody_event(
                bag=b,
                treatment_batch=treatment_batch,
                event_type=CustodyEvent.EventType.TREATMENT_COMPLETED,
                new_custody_status=WasteBag.Status.TREATED,
                actor=facility_user,
                origin_location=treatment_batch.equipment_reference or "Primary Processing Unit",
                destination_location="Decontaminated Residue Staging",
                verification_method="CYCLE_TELEMETRY",
                weight_at_event=b.weight,
                remarks=f"Decontamination cycle concluded safely ({treatment_batch.get_treatment_method_display()}).",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log TREATMENT_COMPLETED events: {cust_err}")

    log_facility_audit(
        facility=facility,
        action_type="TREATMENT_COMPLETED",
        entity_reference=treatment_batch.batch_number,
        user=facility_user,
        description=f"Treatment run {treatment_batch.batch_number} concluded safely ({treatment_batch.processing_weight} KG).",
    )

    # Notify originating hospitals
    hospitals = set(b.hospital for b in treatment_batch.bags.all())
    for hosp in hospitals:
        send_notification(
            recipient=hosp,
            notification_type=Notification.NotificationType.TREATMENT_COMPLETED,
            title=f"Waste Treatment Completed: {treatment_batch.batch_number}",
            message=f"Biomedical waste processed successfully at {facility.name} via {treatment_batch.get_treatment_method_display()}.",
            reference_id=treatment_batch.batch_number,
        )

    notify_administrators(
        notification_type=Notification.NotificationType.TREATMENT_COMPLETED,
        title=f"Treatment Concluded: {treatment_batch.batch_number}",
        message=f"{facility.name} concluded treatment batch {treatment_batch.batch_number} ({treatment_batch.processing_weight} KG).",
        reference_id=treatment_batch.batch_number,
    )

    return treatment_batch


@transaction.atomic
def fail_or_hold_treatment_batch(treatment_batch, facility_user, new_status, reason):
    """
    Marks a treatment cycle as FAILED or ON_HOLD with mandatory explanation.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or treatment_batch.facility_id != facility.id:
        raise PermissionDenied("Unauthorized facility access.")

    if new_status not in [TreatmentBatch.Status.FAILED, TreatmentBatch.Status.ON_HOLD]:
        raise ValidationError("Invalid operational hold status.")

    if not reason or not reason.strip():
        raise ValidationError("A mandatory explanation must be recorded for failed or paused treatment.")

    treatment_batch.status = new_status
    treatment_batch.failure_or_hold_reason = reason.strip()
    treatment_batch.save(update_fields=["status", "failure_or_hold_reason", "updated_at"])

    log_facility_audit(
        facility=facility,
        action_type=f"TREATMENT_{new_status}",
        entity_reference=treatment_batch.batch_number,
        user=facility_user,
        description=f"Treatment batch set to {new_status}: {reason.strip()}",
    )

    notify_administrators(
        notification_type=Notification.NotificationType.TREATMENT_FAILED,
        title=f"Treatment {new_status.title()}: {treatment_batch.batch_number}",
        message=f"Facility {facility.name} placed batch {treatment_batch.batch_number} on {new_status}: {reason.strip()}",
        reference_id=treatment_batch.batch_number,
    )

    return treatment_batch


# ==============================================================================
# DISPOSAL & FINAL DOCUMENTATION WORKFLOW
# ==============================================================================

@transaction.atomic
def create_disposal_record(
    facility_user,
    treatment_batch,
    final_disposal_method,
    disposal_location,
    disposal_date=None,
    disposal_time=None,
    supporting_document=None,
    remarks="",
):
    """
    Documents final disposal of treated residue (e.g. incinerator ash to landfill,
    shredded plastic to recycler).
    Requires treatment_batch to be in COMPLETED status.
    """
    facility = getattr(facility_user, "managed_facility", None)
    if not facility or treatment_batch.facility_id != facility.id:
        raise PermissionDenied("Unauthorized facility access.")

    if treatment_batch.status != TreatmentBatch.Status.COMPLETED:
        raise ValidationError(
            f"Cannot document final disposal for treatment batch in status '{treatment_batch.get_status_display()}'. "
            f"Treatment must be COMPLETED first."
        )

    disposal = DisposalRecord.objects.create(
        treatment_batch=treatment_batch,
        facility=facility,
        disposal_date=disposal_date or timezone.localdate(),
        disposal_time=disposal_time or timezone.localtime().time(),
        final_disposal_method=final_disposal_method,
        disposal_location=disposal_location,
        operator=facility_user,
        status=DisposalRecord.Status.DOCUMENTED,
        supporting_document=supporting_document,
        remarks=remarks,
    )

    # Attach bags from treatment batch and update status to DISPOSED
    bags = treatment_batch.bags.all()
    disposal.bags.set(bags)
    bags.update(
        status=WasteBag.Status.DISPOSED,
        updated_at=timezone.now(),
    )

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in bags:
            record_custody_event(
                bag=b,
                disposal_record=disposal,
                event_type=CustodyEvent.EventType.DISPOSAL_DOCUMENTED,
                new_custody_status=WasteBag.Status.DISPOSED,
                actor=facility_user,
                origin_location=facility.name,
                destination_location=disposal_location,
                verification_method="COMPLIANCE_CERTIFICATE",
                remarks=f"Disposal documented: {disposal.get_final_disposal_method_display()} at {disposal_location}.",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log DISPOSAL_DOCUMENTED events: {cust_err}")

    log_facility_audit(
        facility=facility,
        action_type="DISPOSAL_DOCUMENTED",
        entity_reference=disposal.reference_number,
        user=facility_user,
        description=f"Final disposal documented: {disposal.get_final_disposal_method_display()} at {disposal_location}.",
    )

    # Notify administrator
    notify_administrators(
        notification_type=Notification.NotificationType.DISPOSAL_DOCUMENTED,
        title=f"Disposal Documented: {disposal.reference_number}",
        message=f"{facility.name} filed disposal documentation for batch {treatment_batch.batch_number}.",
        reference_id=disposal.reference_number,
    )

    # Notify originating hospitals
    hospitals = set(b.hospital for b in bags)
    for hosp in hospitals:
        send_notification(
            recipient=hosp,
            notification_type=Notification.NotificationType.DISPOSAL_DOCUMENTED,
            title=f"Final Waste Disposal Documented: {disposal.reference_number}",
            message=f"Treated biomedical waste residue was safely disposed of via {disposal.get_final_disposal_method_display()} ({disposal_location}).",
            reference_id=disposal.reference_number,
        )

    return disposal


@transaction.atomic
def verify_disposal_record(admin_user, disposal_record, verification_notes=""):
    """
    Administrator compliance audit and verification of final disposal documentation.
    """
    if admin_user.role != User.Role.ADMIN:
        raise PermissionDenied("Only platform administrators can verify disposal compliance records.")

    disposal_record.status = DisposalRecord.Status.VERIFIED
    disposal_record.verified_by = admin_user
    disposal_record.verified_at = timezone.now()
    if verification_notes:
        disposal_record.verification_notes = verification_notes
    disposal_record.save(update_fields=["status", "verified_by", "verified_at", "verification_notes", "updated_at"])

    try:
        from tracking.models import CustodyEvent
        from tracking.services import record_custody_event
        for b in disposal_record.bags.all():
            record_custody_event(
                bag=b,
                disposal_record=disposal_record,
                event_type=CustodyEvent.EventType.DISPOSAL_VERIFIED,
                new_custody_status=WasteBag.Status.DISPOSED,
                actor=admin_user,
                origin_location=disposal_record.disposal_location,
                destination_location="Final Certified Disposal",
                verification_method="ADMIN_COMPLIANCE_AUDIT",
                remarks=f"Disposal compliance approved by administrator {admin_user.username}.",
            )
    except Exception as cust_err:
        logger.warning(f"Could not log DISPOSAL_VERIFIED events: {cust_err}")

    log_facility_audit(
        facility=disposal_record.facility,
        action_type="DISPOSAL_VERIFIED",
        entity_reference=disposal_record.reference_number,
        user=admin_user,
        description=f"Disposal compliance verified by administrator {admin_user.username}.",
    )

    # Notify facility operator
    send_notification(
        recipient=disposal_record.facility.operator_user,
        notification_type=Notification.NotificationType.DISPOSAL_VERIFIED,
        title=f"Disposal Verified: {disposal_record.reference_number}",
        message=f"Administrator {admin_user.username} approved disposal record {disposal_record.reference_number}.",
        reference_id=disposal_record.reference_number,
    )

    return disposal_record

