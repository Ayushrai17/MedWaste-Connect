from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.views import role_required
from waste.models import WasteBag
from collection.models import CollectionRequest
from facilities.models import TreatmentFacility
from facilities.services import get_facility_capacity_metrics
from .models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
    FacilityAuditHistory,
)
from .forms import (
    FacilityReceptionForm,
    BagInspectionDecisionForm,
    TreatmentBatchCreateForm,
    TreatmentBatchCompleteForm,
    TreatmentBatchHoldFailForm,
    DisposalRecordCreateForm,
    DisposalVerifyForm,
)
from .services import (
    receive_incoming_consignment,
    verify_reception_bag,
    record_bag_acceptance_decision,
    finalize_reception_manifest,
    create_treatment_batch,
    start_treatment_batch,
    complete_treatment_batch,
    fail_or_hold_treatment_batch,
    create_disposal_record,
    verify_disposal_record,
)

User = get_user_model()


def _get_user_facility_or_403(user):
    """
    Helper to enforce facility ownership and active status.
    """
    if user.role != User.Role.FACILITY:
        raise PermissionDenied("Treatment facility credentials required.")
    facility = getattr(user, "managed_facility", None)
    if not facility:
        raise PermissionDenied("No treatment facility is registered for your account.")
    return facility


# ==============================================================================
# FACILITY OPERATOR DASHBOARD
# ==============================================================================

@role_required(User.Role.FACILITY)
def facility_dashboard(request):
    """
    Primary operational dashboard for the authenticated Treatment Facility.
    """
    facility = getattr(request.user, "managed_facility", None)
    if not facility:
        context = {
            "has_facility": False,
            "role_display": "Facility Operator",
        }
        return render(request, "treatment/facility/no_facility.html", context)

    metrics = get_facility_capacity_metrics(facility)

    # Incoming collections awaiting reception (in-transit or collected)
    incoming_collections = CollectionRequest.objects.filter(
        models.Q(destination_facility=facility) |
        models.Q(destination_facility__isnull=True),
        status__in=[
            CollectionRequest.Status.COLLECTED,
            CollectionRequest.Status.PICKUP_IN_PROGRESS,
            CollectionRequest.Status.COMPLETED,
        ]
    ).exclude(
        facility_receptions__facility=facility
    ).select_related("hospital", "assigned_collector")[:5]

    # Active reception manifests
    active_receptions = facility.reception_manifests.filter(
        status__in=[
            FacilityReceptionManifest.Status.RECEIVED,
            FacilityReceptionManifest.Status.PARTIALLY_ACCEPTED,
            FacilityReceptionManifest.Status.DISCREPANCY_REPORTED,
        ]
    ).select_related("collection_request", "collection_request__hospital")[:5]

    # Active treatment batches (in progress or pending)
    active_batches = facility.treatment_batches.filter(
        status__in=[TreatmentBatch.Status.PENDING, TreatmentBatch.Status.IN_PROGRESS]
    ).select_related("operator")[:5]

    # Batches completed awaiting disposal documentation
    completed_batches_awaiting_disposal = facility.treatment_batches.filter(
        status=TreatmentBatch.Status.COMPLETED,
        disposal_records__isnull=True
    ).count()

    # High-level counters
    stats = {
        "pending_receptions": active_receptions.count(),
        "active_batches": active_batches.count(),
        "awaiting_disposal": completed_batches_awaiting_disposal,
        "total_disposals": facility.disposal_records.count(),
    }

    context = {
        "has_facility": True,
        "facility": facility,
        "metrics": metrics,
        "stats": stats,
        "incoming_collections": incoming_collections,
        "active_receptions": active_receptions,
        "active_batches": active_batches,
    }
    return render(request, "treatment/facility/dashboard.html", context)


# ==============================================================================
# INCOMING WASTE RECEPTION VIEWS
# ==============================================================================

@role_required(User.Role.FACILITY)
def facility_incoming_manifests(request):
    """
    Queue of collections assigned to this facility or arriving from hospitals.
    """
    facility = _get_user_facility_or_403(request.user)

    tab = request.GET.get("tab", "incoming").strip().lower()

    if tab == "receptions":
        # Manifests already checked in
        receptions_qs = facility.reception_manifests.all().select_related(
            "collection_request", "collection_request__hospital"
        )
        paginator = Paginator(receptions_qs, 15)
        page_obj = paginator.get_page(request.GET.get("page"))
        context = {
            "facility": facility,
            "page_obj": page_obj,
            "current_tab": "receptions",
        }
        return render(request, "treatment/facility/reception_list.html", context)

    # Incoming collections awaiting reception
    incoming_qs = CollectionRequest.objects.filter(
        models.Q(destination_facility=facility) |
        models.Q(destination_facility__isnull=True),
        status__in=[
            CollectionRequest.Status.COLLECTED,
            CollectionRequest.Status.PICKUP_IN_PROGRESS,
            CollectionRequest.Status.COMPLETED,
        ]
    ).exclude(
        facility_receptions__facility=facility
    ).select_related("hospital", "assigned_collector")

    q = request.GET.get("q", "").strip()
    if q:
        incoming_qs = incoming_qs.filter(
            models.Q(reference_number__icontains=q) |
            models.Q(hospital__organization_name__icontains=q) |
            models.Q(hospital__username__icontains=q)
        )

    paginator = Paginator(incoming_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "facility": facility,
        "page_obj": page_obj,
        "current_tab": "incoming",
        "search_q": q,
    }
    return render(request, "treatment/facility/incoming_collections.html", context)


@role_required(User.Role.FACILITY)
def facility_receive_consignment(request, collection_reference):
    """
    Gate check-in form to receive an arriving collection dispatch.
    """
    facility = _get_user_facility_or_403(request.user)
    collection_req = get_object_or_404(
        CollectionRequest.objects.select_related("hospital", "assigned_collector").prefetch_related("bags"),
        reference_number=collection_reference
    )

    # Check if already received
    existing = FacilityReceptionManifest.objects.filter(
        facility=facility,
        collection_request=collection_req
    ).first()
    if existing:
        return redirect("treatment:facility_reception_detail", reception_reference=existing.reference_number)

    if request.method == "POST":
        form = FacilityReceptionForm(request.POST)
        if form.is_valid():
            try:
                manifest = receive_incoming_consignment(
                    facility_user=request.user,
                    collection_request=collection_req,
                    gross_received_weight=form.cleaned_data.get("gross_received_weight"),
                    remarks=form.cleaned_data.get("remarks", ""),
                    capacity_override_authorized=form.cleaned_data.get("capacity_override_authorized", False),
                    capacity_override_reason=form.cleaned_data.get("capacity_override_reason", ""),
                )
                messages.success(
                    request,
                    f"Consignment received successfully! Manifest {manifest.reference_number} created."
                )
                return redirect("treatment:facility_reception_detail", reception_reference=manifest.reference_number)
            except (ValidationError, PermissionDenied) as e:
                if hasattr(e, "messages"):
                    for msg in e.messages:
                        messages.error(request, msg)
                else:
                    messages.error(request, str(e))
    else:
        initial_data = {
            "gross_received_weight": collection_req.total_weight,
        }
        form = FacilityReceptionForm(initial=initial_data)

    metrics = get_facility_capacity_metrics(facility)

    context = {
        "facility": facility,
        "collection_req": collection_req,
        "form": form,
        "metrics": metrics,
    }
    return render(request, "treatment/facility/receive_consignment.html", context)


@role_required(User.Role.FACILITY)
def facility_reception_detail(request, reception_reference):
    """
    Itemized bag verification and acceptance inspection console.
    """
    facility = _get_user_facility_or_403(request.user)
    manifest = get_object_or_404(
        FacilityReceptionManifest.objects.select_related(
            "facility",
            "collection_request",
            "collection_request__hospital",
            "received_by",
        ).prefetch_related(
            "bag_receipts",
            "bag_receipts__bag",
            "bag_receipts__bag__category",
        ),
        reference_number=reception_reference,
        facility=facility,
    )

    receipts = manifest.bag_receipts.all().order_by("bag__bag_id")
    verified_count = receipts.filter(is_verified=True).count()
    accepted_count = receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED).count()
    rejected_count = receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED).count()
    pending_count = receipts.filter(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.PENDING).count()

    context = {
        "facility": facility,
        "manifest": manifest,
        "receipts": receipts,
        "verified_count": verified_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "pending_count": pending_count,
        "is_finalized": manifest.status in [
            FacilityReceptionManifest.Status.ACCEPTED,
            FacilityReceptionManifest.Status.PARTIALLY_ACCEPTED,
            FacilityReceptionManifest.Status.REJECTED,
        ],
    }
    return render(request, "treatment/facility/reception_detail.html", context)


@role_required(User.Role.FACILITY)
@require_POST
def facility_verify_bag_scan(request, reception_reference):
    """
    Scans/enters an individual Bag ID or UUID QR token to verify bag receipt at the facility dock.
    """
    facility = _get_user_facility_or_403(request.user)
    manifest = get_object_or_404(
        FacilityReceptionManifest,
        reference_number=reception_reference,
        facility=facility
    )

    identifier = request.POST.get("bag_identifier", "").strip()
    actual_weight_str = request.POST.get("actual_weight", "").strip()
    packaging_condition = request.POST.get("packaging_condition", "INTACT")
    remarks = request.POST.get("remarks", "").strip()

    actual_weight = Decimal(actual_weight_str) if actual_weight_str else None

    if not identifier:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Bag tracking identifier is required."}, status=400)
        messages.error(request, "Bag tracking identifier is required.")
        return redirect("treatment:facility_reception_detail", reception_reference=reception_reference)

    try:
        receipt = verify_reception_bag(
            reception_manifest=manifest,
            bag_identifier_or_token=identifier,
            facility_user=request.user,
            actual_weight=actual_weight,
            packaging_condition=packaging_condition,
            remarks=remarks,
        )
        msg = f"Bag {receipt.bag.bag_id} verified ({receipt.bag.category.code}, {receipt.actual_weight} KG)."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": msg,
                "bag_id": receipt.bag.bag_id,
                "category": receipt.bag.category.code,
                "weight": str(receipt.actual_weight),
            })
        messages.success(request, msg)
    except (ValidationError, PermissionDenied) as e:
        err = str(e)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": err}, status=400)
        messages.error(request, err)

    return redirect("treatment:facility_reception_detail", reception_reference=reception_reference)


@role_required(User.Role.FACILITY)
@require_POST
def facility_bag_decision(request, reception_reference, receipt_id):
    """
    Records an acceptance, rejection, or missing decision on an individual verified bag.
    """
    facility = _get_user_facility_or_403(request.user)
    manifest = get_object_or_404(
        FacilityReceptionManifest,
        reference_number=reception_reference,
        facility=facility
    )
    receipt = get_object_or_404(FacilityBagReceipt, id=receipt_id, reception_manifest=manifest)

    decision = request.POST.get("acceptance_decision", "").strip()
    rejection_reason = request.POST.get("rejection_reason", "").strip()
    remarks = request.POST.get("reception_remarks", "").strip()

    try:
        record_bag_acceptance_decision(
            bag_receipt=receipt,
            facility_user=request.user,
            decision=decision,
            rejection_reason=rejection_reason,
            remarks=remarks,
        )
        messages.success(request, f"Decision updated for bag {receipt.bag.bag_id}: {receipt.get_acceptance_decision_display()}.")
    except (ValidationError, PermissionDenied) as e:
        if hasattr(e, "messages"):
            for m in e.messages:
                messages.error(request, m)
        else:
            messages.error(request, str(e))

    return redirect("treatment:facility_reception_detail", reception_reference=reception_reference)


@role_required(User.Role.FACILITY)
@require_POST
def facility_finalize_reception(request, reception_reference):
    """
    Finalizes the reception manifest once all bags have inspection decisions.
    """
    facility = _get_user_facility_or_403(request.user)
    manifest = get_object_or_404(
        FacilityReceptionManifest,
        reference_number=reception_reference,
        facility=facility
    )

    try:
        finalize_reception_manifest(manifest, request.user)
        messages.success(request, f"Reception manifest {reception_reference} finalized successfully! Status: {manifest.get_status_display()}.")
    except (ValidationError, PermissionDenied) as e:
        if hasattr(e, "messages"):
            for m in e.messages:
                messages.error(request, m)
        else:
            messages.error(request, str(e))

    return redirect("treatment:facility_reception_detail", reception_reference=reception_reference)


# ==============================================================================
# TREATMENT PROCESSING WORKFLOW
# ==============================================================================

@role_required(User.Role.FACILITY)
def treatment_batch_list(request):
    """
    Treatment operations log and batch management list.
    """
    facility = _get_user_facility_or_403(request.user)
    base_qs = facility.treatment_batches.all().select_related("operator", "waste_category").prefetch_related("bags")

    tab = request.GET.get("tab", "active").strip().lower()
    if tab == "pending":
        batches_qs = base_qs.filter(status=TreatmentBatch.Status.PENDING)
    elif tab == "in_progress":
        batches_qs = base_qs.filter(status=TreatmentBatch.Status.IN_PROGRESS)
    elif tab == "completed":
        batches_qs = base_qs.filter(status=TreatmentBatch.Status.COMPLETED)
    elif tab == "failed":
        batches_qs = base_qs.filter(status__in=[TreatmentBatch.Status.FAILED, TreatmentBatch.Status.ON_HOLD])
    else:
        tab = "active"
        batches_qs = base_qs.filter(status__in=[TreatmentBatch.Status.PENDING, TreatmentBatch.Status.IN_PROGRESS])

    paginator = Paginator(batches_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "facility": facility,
        "page_obj": page_obj,
        "current_tab": tab,
    }
    return render(request, "treatment/facility/treatment_list.html", context)


@role_required(User.Role.FACILITY)
def treatment_batch_create(request):
    """
    Form to configure and create a new treatment batch from accepted bags.
    """
    facility = _get_user_facility_or_403(request.user)

    if request.method == "POST":
        form = TreatmentBatchCreateForm(facility, request.POST)
        if form.is_valid():
            try:
                batch = create_treatment_batch(
                    facility_user=request.user,
                    treatment_method=form.cleaned_data["treatment_method"],
                    bag_queryset=form.cleaned_data["bags"],
                    equipment_reference=form.cleaned_data.get("equipment_reference", ""),
                    chamber_temperature=form.cleaned_data.get("chamber_temperature_celsius"),
                    chamber_pressure=form.cleaned_data.get("chamber_pressure_psi"),
                    cycle_duration=form.cleaned_data.get("cycle_duration_minutes"),
                    remarks=form.cleaned_data.get("treatment_remarks", ""),
                )
                messages.success(request, f"Treatment batch {batch.batch_number} created successfully ({batch.processing_weight} KG).")
                return redirect("treatment:treatment_batch_detail", batch_number=batch.batch_number)
            except (ValidationError, PermissionDenied) as e:
                if hasattr(e, "messages"):
                    for m in e.messages:
                        messages.error(request, m)
                else:
                    messages.error(request, str(e))
    else:
        form = TreatmentBatchCreateForm(facility)

    context = {
        "facility": facility,
        "form": form,
        "eligible_bags_count": form.fields["bags"].queryset.count(),
    }
    return render(request, "treatment/facility/treatment_create.html", context)


@role_required(User.Role.FACILITY)
def treatment_batch_detail(request, batch_number):
    """
    Operational control view for a single treatment batch.
    """
    facility = _get_user_facility_or_403(request.user)
    batch = get_object_or_404(
        TreatmentBatch.objects.select_related("facility", "operator", "waste_category").prefetch_related("bags", "bags__category", "bags__hospital"),
        batch_number=batch_number,
        facility=facility
    )

    complete_form = TreatmentBatchCompleteForm(initial={
        "chamber_temperature_celsius": batch.chamber_temperature_celsius,
        "chamber_pressure_psi": batch.chamber_pressure_psi,
        "cycle_duration_minutes": batch.cycle_duration_minutes,
    })
    hold_form = TreatmentBatchHoldFailForm()

    # Check if a disposal record already exists
    disposal_record = batch.disposal_records.first()

    context = {
        "facility": facility,
        "batch": batch,
        "complete_form": complete_form,
        "hold_form": hold_form,
        "disposal_record": disposal_record,
    }
    return render(request, "treatment/facility/treatment_detail.html", context)


@role_required(User.Role.FACILITY)
@require_POST
def treatment_batch_start(request, batch_number):
    """
    Starts treatment processing cycle.
    """
    facility = _get_user_facility_or_403(request.user)
    batch = get_object_or_404(TreatmentBatch, batch_number=batch_number, facility=facility)
    remarks = request.POST.get("remarks", "").strip()

    try:
        start_treatment_batch(batch, request.user, remarks=remarks)
        messages.success(request, f"Treatment batch {batch_number} started! Processing in progress.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("treatment:treatment_batch_detail", batch_number=batch_number)


@role_required(User.Role.FACILITY)
@require_POST
def treatment_batch_complete(request, batch_number):
    """
    Records cycle completion, parameters, and marks bags as TREATED.
    """
    facility = _get_user_facility_or_403(request.user)
    batch = get_object_or_404(TreatmentBatch, batch_number=batch_number, facility=facility)
    form = TreatmentBatchCompleteForm(request.POST)

    if form.is_valid():
        try:
            complete_treatment_batch(
                treatment_batch=batch,
                facility_user=request.user,
                chamber_temperature=form.cleaned_data.get("chamber_temperature_celsius"),
                chamber_pressure=form.cleaned_data.get("chamber_pressure_psi"),
                cycle_duration=form.cleaned_data.get("cycle_duration_minutes"),
                remarks=form.cleaned_data.get("remarks", ""),
            )
            messages.success(request, f"Treatment batch {batch_number} completed safely! Bags updated to TREATED.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "Please check the entered completion parameters.")

    return redirect("treatment:treatment_batch_detail", batch_number=batch_number)


@role_required(User.Role.FACILITY)
@require_POST
def treatment_batch_hold_fail(request, batch_number):
    """
    Places a treatment run on hold or marks it as failed with mandatory reason.
    """
    facility = _get_user_facility_or_403(request.user)
    batch = get_object_or_404(TreatmentBatch, batch_number=batch_number, facility=facility)
    form = TreatmentBatchHoldFailForm(request.POST)

    if form.is_valid():
        try:
            fail_or_hold_treatment_batch(
                treatment_batch=batch,
                facility_user=request.user,
                new_status=form.cleaned_data["new_status"],
                reason=form.cleaned_data["reason"],
            )
            messages.warning(request, f"Batch {batch_number} updated to {form.cleaned_data['new_status']}.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "A mandatory explanation is required.")

    return redirect("treatment:treatment_batch_detail", batch_number=batch_number)


# ==============================================================================
# DISPOSAL DOCUMENTATION VIEWS
# ==============================================================================

@role_required(User.Role.FACILITY)
def disposal_record_list(request):
    """
    List of final post-treatment residue disposal records.
    """
    facility = _get_user_facility_or_403(request.user)
    disposals_qs = facility.disposal_records.all().select_related(
        "treatment_batch", "operator", "verified_by"
    ).prefetch_related("bags")

    paginator = Paginator(disposals_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "facility": facility,
        "page_obj": page_obj,
    }
    return render(request, "treatment/facility/disposal_list.html", context)


@role_required(User.Role.FACILITY)
def disposal_record_create(request, batch_number):
    """
    Form to document final disposal of residue from a COMPLETED treatment batch.
    """
    facility = _get_user_facility_or_403(request.user)
    batch = get_object_or_404(
        TreatmentBatch.objects.prefetch_related("bags"),
        batch_number=batch_number,
        facility=facility
    )

    if batch.status != TreatmentBatch.Status.COMPLETED:
        messages.error(request, f"Cannot document disposal for batch in status '{batch.get_status_display()}'. Batch must be COMPLETED.")
        return redirect("treatment:treatment_batch_detail", batch_number=batch_number)

    if batch.disposal_records.exists():
        existing = batch.disposal_records.first()
        messages.info(request, f"Disposal already documented for batch {batch_number}.")
        return redirect("treatment:disposal_record_detail", disposal_reference=existing.reference_number)

    if request.method == "POST":
        form = DisposalRecordCreateForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                disposal = create_disposal_record(
                    facility_user=request.user,
                    treatment_batch=batch,
                    final_disposal_method=form.cleaned_data["final_disposal_method"],
                    disposal_location=form.cleaned_data["disposal_location"],
                    disposal_date=form.cleaned_data["disposal_date"],
                    disposal_time=form.cleaned_data["disposal_time"],
                    supporting_document=form.cleaned_data.get("supporting_document"),
                    remarks=form.cleaned_data.get("remarks", ""),
                )
                messages.success(request, f"Disposal record {disposal.reference_number} documented successfully! Bags marked as DISPOSED.")
                return redirect("treatment:disposal_record_detail", disposal_reference=disposal.reference_number)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
    else:
        form = DisposalRecordCreateForm()

    context = {
        "facility": facility,
        "batch": batch,
        "form": form,
    }
    return render(request, "treatment/facility/disposal_create.html", context)


def disposal_record_detail(request, disposal_reference):
    """
    Operational & statutory inspection view of a final disposal record.
    Accessible to Facility, Admin, and Originating Hospitals.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    disposal = get_object_or_404(
        DisposalRecord.objects.select_related(
            "facility",
            "treatment_batch",
            "treatment_batch__operator",
            "operator",
            "verified_by"
        ).prefetch_related("bags", "bags__category", "bags__hospital"),
        reference_number=disposal_reference
    )

    # Authorization check
    user = request.user
    if user.role == User.Role.FACILITY:
        if disposal.facility.operator_user_id != user.id:
            raise PermissionDenied("You do not have access to other facilities' disposal records.")
    elif user.role == User.Role.HOSPITAL:
        # Verify hospital owns at least one bag in this disposal
        if not disposal.bags.filter(hospital=user).exists():
            raise PermissionDenied("You can view only disposal records associated with your hospital's waste.")
    elif user.role == User.Role.COLLECTOR:
        raise PermissionDenied("Collectors do not have direct access to treatment disposal certificates.")

    verify_form = DisposalVerifyForm() if user.role == User.Role.ADMIN else None

    context = {
        "disposal": disposal,
        "verify_form": verify_form,
        "is_admin": user.role == User.Role.ADMIN,
        "is_facility": user.role == User.Role.FACILITY,
        "is_hospital": user.role == User.Role.HOSPITAL,
    }
    return render(request, "treatment/disposal_detail.html", context)


# ==============================================================================
# ADMINISTRATOR TREATMENT & COMPLIANCE VIEWS
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_treatment_overview(request):
    """
    Central administrative oversight dashboard for treatment facilities,
    receptions, treatment batches, and disposal records across the platform.
    """
    # Filter parameters
    facility_filter = request.GET.get("facility", "").strip()
    status_filter = request.GET.get("status", "").strip()
    tab = request.GET.get("tab", "facilities").strip().lower()

    facilities_list = TreatmentFacility.objects.all().order_by("name")

    if tab == "receptions":
        receptions_qs = FacilityReceptionManifest.objects.all().select_related(
            "facility", "collection_request", "collection_request__hospital"
        )
        if facility_filter:
            receptions_qs = receptions_qs.filter(facility_id=facility_filter)
        if status_filter:
            receptions_qs = receptions_qs.filter(status=status_filter)
        paginator = Paginator(receptions_qs, 15)
        page_obj = paginator.get_page(request.GET.get("page"))
        context = {
            "current_tab": "receptions",
            "page_obj": page_obj,
            "facilities_list": facilities_list,
            "selected_facility": facility_filter,
            "selected_status": status_filter,
            "status_choices": FacilityReceptionManifest.Status.choices,
        }
        return render(request, "treatment/admin/overview.html", context)

    elif tab == "batches":
        batches_qs = TreatmentBatch.objects.all().select_related("facility", "operator", "waste_category")
        if facility_filter:
            batches_qs = batches_qs.filter(facility_id=facility_filter)
        if status_filter:
            batches_qs = batches_qs.filter(status=status_filter)
        paginator = Paginator(batches_qs, 15)
        page_obj = paginator.get_page(request.GET.get("page"))
        context = {
            "current_tab": "batches",
            "page_obj": page_obj,
            "facilities_list": facilities_list,
            "selected_facility": facility_filter,
            "selected_status": status_filter,
            "status_choices": TreatmentBatch.Status.choices,
        }
        return render(request, "treatment/admin/overview.html", context)

    elif tab == "disposals":
        disposals_qs = DisposalRecord.objects.all().select_related(
            "facility", "treatment_batch", "operator", "verified_by"
        )
        if facility_filter:
            disposals_qs = disposals_qs.filter(facility_id=facility_filter)
        if status_filter:
            disposals_qs = disposals_qs.filter(status=status_filter)
        paginator = Paginator(disposals_qs, 15)
        page_obj = paginator.get_page(request.GET.get("page"))
        context = {
            "current_tab": "disposals",
            "page_obj": page_obj,
            "facilities_list": facilities_list,
            "selected_facility": facility_filter,
            "selected_status": status_filter,
            "status_choices": DisposalRecord.Status.choices,
        }
        return render(request, "treatment/admin/overview.html", context)

    # Default tab: facilities summary
    facility_data = []
    for fac in facilities_list:
        metrics = get_facility_capacity_metrics(fac)
        facility_data.append({
            "facility": fac,
            "metrics": metrics,
            "total_receptions": fac.reception_manifests.count(),
            "total_batches": fac.treatment_batches.count(),
            "total_disposals": fac.disposal_records.count(),
        })

    context = {
        "current_tab": "facilities",
        "facility_data": facility_data,
        "facilities_list": facilities_list,
    }
    return render(request, "treatment/admin/overview.html", context)


@role_required(User.Role.ADMIN)
@require_POST
def admin_verify_disposal(request, disposal_reference):
    """
    Administrator compliance verification for a disposal documentation record.
    """
    disposal = get_object_or_404(DisposalRecord, reference_number=disposal_reference)
    form = DisposalVerifyForm(request.POST)

    if form.is_valid():
        try:
            verify_disposal_record(
                admin_user=request.user,
                disposal_record=disposal,
                verification_notes=form.cleaned_data.get("verification_notes", "")
            )
            messages.success(request, f"Disposal record {disposal_reference} verified successfully!")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "Verification form validation error.")

    return redirect("treatment:disposal_record_detail", disposal_reference=disposal_reference)
