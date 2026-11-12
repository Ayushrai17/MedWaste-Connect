import csv
import logging
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from accounts.views import role_required
from core.utils import quantize_weight
from collection.models import CollectionRequest
from treatment.models import FacilityReceptionManifest
from waste.models import WasteBag, WasteCategory
from waste.services.qr_service import build_tracking_url, get_or_create_qr_image

from .forms import DiscrepancyReportForm, DiscrepancyResolveForm, CustodyCorrectionForm
from .models import CustodyDiscrepancy, CustodyEvent, CustodyHandover
from .services import (
    can_user_access_bag_tracking,
    get_bag_timeline,
    record_custody_event,
    report_discrepancy,
    resolve_discrepancy,
)

logger = logging.getLogger(__name__)
User = get_user_model()


@login_required
def bag_tracking_detail(request, bag_id):
    """
    Dedicated Bag Custody History & Visual Timeline Page.
    Enforces strict role-based data isolation.
    """
    bag = get_object_or_404(
        WasteBag.objects.select_related("category", "batch", "hospital", "registered_by"),
        bag_id=bag_id,
    )

    if not can_user_access_bag_tracking(request.user, bag):
        messages.error(request, "Access restricted. You do not have permission to view internal custody records for this bag.")
        return redirect("dashboard_redirect")

    # Get rich timeline data model
    timeline = get_bag_timeline(bag, requesting_user=request.user)

    # Forms for modals
    discrepancy_form = DiscrepancyReportForm()
    correction_form = CustodyCorrectionForm() if request.user.role == User.Role.ADMIN else None

    # Public tracking URL for reference
    public_track_url = build_tracking_url(bag, request=request)

    context = {
        "bag": bag,
        "timeline": timeline,
        "discrepancy_form": discrepancy_form,
        "correction_form": correction_form,
        "public_track_url": public_track_url,
        "can_correct": request.user.role == User.Role.ADMIN,
    }
    return render(request, "tracking/bag_tracking_detail.html", context)


@login_required
def bag_custody_report(request, bag_id):
    """
    Formal Statutory Printable Chain of Custody Audit Certificate for an individual waste bag.
    Formatted for print / PDF output under Indian Bio-Medical Waste Management Rules.
    """
    bag = get_object_or_404(
        WasteBag.objects.select_related("category", "batch", "hospital", "registered_by"),
        bag_id=bag_id,
    )

    if not can_user_access_bag_tracking(request.user, bag):
        raise PermissionDenied("Access to statutory custody report denied.")

    timeline = get_bag_timeline(bag, requesting_user=request.user)
    public_track_url = build_tracking_url(bag, request=request)

    # Find collection request if any
    col_req = bag.collection_requests.select_related("assigned_collector", "hospital").order_by("-created_at").first()

    # Find facility reception receipt if any
    receipt = bag.facility_receipts.select_related("reception_manifest__facility", "reception_manifest__received_by").order_by("-id").first()

    # Find treatment batch if any
    t_batch = bag.treatment_batches.select_related("facility", "operator").order_by("-created_at").first()

    # Find disposal record if any
    d_record = bag.disposal_records.select_related("facility", "operator", "verified_by").order_by("-created_at").first()

    context = {
        "bag": bag,
        "timeline": timeline,
        "col_req": col_req,
        "receipt": receipt,
        "t_batch": t_batch,
        "d_record": d_record,
        "public_track_url": public_track_url,
        "generated_at": timezone.now(),
        "generated_by": request.user,
    }
    return render(request, "tracking/bag_custody_report.html", context)


@login_required
def consignment_custody_report(request, reference_number):
    """
    Printable Consignment Chain of Custody Manifest for an entire Collection Request.
    """
    col_req = get_object_or_404(
        CollectionRequest.objects.select_related("hospital", "assigned_collector", "destination_facility"),
        reference_number=reference_number,
    )

    # Isolation check
    u = request.user
    if u.role == User.Role.ADMIN:
        pass
    elif u.role == User.Role.HOSPITAL and col_req.hospital_id == u.id:
        pass
    elif u.role == User.Role.COLLECTOR and col_req.assigned_collector_id == u.id:
        pass
    elif u.role == User.Role.FACILITY:
        facility = getattr(u, "managed_facility", None)
        if not facility or col_req.destination_facility_id != facility.id:
            raise PermissionDenied("Consignment access denied.")
    else:
        raise PermissionDenied("Access restricted.")

    bags = list(col_req.bags.select_related("category", "batch").order_by("bag_id"))
    handovers = list(col_req.custody_handovers.select_related("releasing_user", "receiving_user").order_by("handover_timestamp"))
    reception_manifest = FacilityReceptionManifest.objects.filter(collection_request=col_req).first()

    context = {
        "col_req": col_req,
        "bags": bags,
        "handovers": handovers,
        "reception_manifest": reception_manifest,
        "generated_at": timezone.now(),
        "generated_by": request.user,
    }
    return render(request, "tracking/consignment_custody_report.html", context)


@login_required
def tracking_dashboard(request):
    """
    Chain of Custody Tracking & Audit Search Hub.
    Role-scoped search for bags, handovers, and discrepancy events.
    """
    u = request.user

    # Base QuerySet scoped strictly to user role
    if u.role == User.Role.ADMIN:
        bags_qs = WasteBag.objects.all()
        handovers_qs = CustodyHandover.objects.all()
    elif u.role == User.Role.HOSPITAL:
        bags_qs = WasteBag.objects.filter(hospital=u)
        handovers_qs = CustodyHandover.objects.filter(collection_request__hospital=u)
    elif u.role == User.Role.COLLECTOR:
        bags_qs = WasteBag.objects.filter(collection_requests__assigned_collector=u).distinct()
        handovers_qs = CustodyHandover.objects.filter(collection_request__assigned_collector=u)
    elif u.role == User.Role.FACILITY:
        facility = getattr(u, "managed_facility", None)
        if facility:
            bags_qs = WasteBag.objects.filter(
                Q(facility_receipts__reception_manifest__facility=facility) |
                Q(treatment_batches__facility=facility)
            ).distinct()
            handovers_qs = CustodyHandover.objects.filter(reception_manifest__facility=facility)
        else:
            bags_qs = WasteBag.objects.none()
            handovers_qs = CustodyHandover.objects.none()
    else:
        bags_qs = WasteBag.objects.none()
        handovers_qs = CustodyHandover.objects.none()

    # Search query
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    category_filter = request.GET.get("category", "").strip()

    if q:
        bags_qs = bags_qs.filter(
            Q(bag_id__icontains=q) |
            Q(ward__icontains=q) |
            Q(department__icontains=q) |
            Q(batch__batch_number__icontains=q)
        )

    if status_filter:
        bags_qs = bags_qs.filter(status=status_filter)

    if category_filter:
        bags_qs = bags_qs.filter(category__code=category_filter)

    bags_qs = bags_qs.select_related("category", "hospital", "batch").order_by("-registered_at")

    # Metrics
    metrics = bags_qs.aggregate(
        total_count=Count("id"),
        total_weight=Sum("weight"),
        registered_count=Count("id", filter=Q(status=WasteBag.Status.REGISTERED)),
        transit_count=Count("id", filter=Q(status=WasteBag.Status.IN_TRANSIT)),
        received_count=Count("id", filter=Q(status=WasteBag.Status.RECEIVED)),
        treated_count=Count("id", filter=Q(status=WasteBag.Status.TREATED)),
        disposed_count=Count("id", filter=Q(status=WasteBag.Status.DISPOSED)),
    )
    metrics["total_weight"] = quantize_weight(metrics.get("total_weight"))

    paginator = Paginator(bags_qs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    categories = WasteCategory.objects.filter(is_active=True).order_by("display_order")
    recent_handovers = handovers_qs.select_related("releasing_user", "receiving_user")[:5]

    context = {
        "page_obj": page_obj,
        "metrics": metrics,
        "categories": categories,
        "status_choices": WasteBag.Status.choices,
        "q": q,
        "status_filter": status_filter,
        "category_filter": category_filter,
        "recent_handovers": recent_handovers,
    }
    return render(request, "tracking/tracking_dashboard.html", context)


@login_required
def discrepancy_list(request):
    """
    Exceptions & Custody Discrepancies Management Console.
    """
    u = request.user

    if u.role == User.Role.ADMIN:
        qs = CustodyDiscrepancy.objects.all()
    elif u.role == User.Role.HOSPITAL:
        qs = CustodyDiscrepancy.objects.filter(
            Q(bag__hospital=u) | Q(collection_request__hospital=u) | Q(reported_by=u)
        ).distinct()
    elif u.role == User.Role.COLLECTOR:
        qs = CustodyDiscrepancy.objects.filter(
            Q(collection_request__assigned_collector=u) | Q(reported_by=u)
        ).distinct()
    elif u.role == User.Role.FACILITY:
        facility = getattr(u, "managed_facility", None)
        if facility:
            qs = CustodyDiscrepancy.objects.filter(
                Q(reception_manifest__facility=facility) | Q(reported_by=u)
            ).distinct()
        else:
            qs = CustodyDiscrepancy.objects.filter(reported_by=u)
    else:
        qs = CustodyDiscrepancy.objects.none()

    status_filter = request.GET.get("status", "").strip()
    severity_filter = request.GET.get("severity", "").strip()
    type_filter = request.GET.get("type", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if severity_filter:
        qs = qs.filter(severity=severity_filter)
    if type_filter:
        qs = qs.filter(discrepancy_type=type_filter)

    qs = qs.select_related("bag", "collection_request", "reported_by", "resolved_by").order_by("-reported_at")

    aggregates = qs.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status=CustodyDiscrepancy.Status.OPEN)),
        under_review=Count("id", filter=Q(status=CustodyDiscrepancy.Status.UNDER_REVIEW)),
        resolved=Count("id", filter=Q(status=CustodyDiscrepancy.Status.RESOLVED)),
        critical=Count("id", filter=Q(severity=CustodyDiscrepancy.Severity.CRITICAL)),
    )

    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "aggregates": aggregates,
        "status_choices": CustodyDiscrepancy.Status.choices,
        "severity_choices": CustodyDiscrepancy.Severity.choices,
        "type_choices": CustodyDiscrepancy.DiscrepancyType.choices,
        "status_filter": status_filter,
        "severity_filter": severity_filter,
        "type_filter": type_filter,
    }
    return render(request, "tracking/discrepancy_list.html", context)


@login_required
def discrepancy_detail(request, discrepancy_reference):
    """
    Detailed view and resolution workflow for a single discrepancy record.
    """
    discrepancy = get_object_or_404(
        CustodyDiscrepancy.objects.select_related("bag", "collection_request", "reception_manifest", "reported_by", "resolved_by"),
        discrepancy_reference=discrepancy_reference,
    )

    can_resolve = request.user.role == User.Role.ADMIN

    if request.method == "POST":
        if not can_resolve:
            raise PermissionDenied("Only administrators can resolve discrepancies.")
        form = DiscrepancyResolveForm(request.POST, instance=discrepancy)
        if form.is_valid():
            resolve_discrepancy(
                discrepancy=discrepancy,
                resolved_by=request.user,
                resolution_notes=form.cleaned_data["resolution_notes"],
                new_status=form.cleaned_data["status"],
            )
            messages.success(request, f"Discrepancy {discrepancy.discrepancy_reference} has been updated to {discrepancy.get_status_display()}.")
            return redirect("tracking:discrepancy_detail", discrepancy_reference=discrepancy.discrepancy_reference)
    else:
        form = DiscrepancyResolveForm(instance=discrepancy) if can_resolve else None

    context = {
        "discrepancy": discrepancy,
        "form": form,
        "can_resolve": can_resolve,
    }
    return render(request, "tracking/discrepancy_detail.html", context)


@require_POST
@login_required
def report_bag_discrepancy(request, bag_id):
    """
    Quick discrepancy reporting endpoint from the bag custody tracking view.
    """
    bag = get_object_or_404(WasteBag, bag_id=bag_id)
    if not can_user_access_bag_tracking(request.user, bag):
        raise PermissionDenied("Access restricted.")

    form = DiscrepancyReportForm(request.POST)
    if form.is_valid():
        disc = report_discrepancy(
            discrepancy_type=form.cleaned_data["discrepancy_type"],
            severity=form.cleaned_data["severity"],
            description=form.cleaned_data["description"],
            reported_by=request.user,
            bag=bag,
            collection_request=bag.collection_requests.order_by("-created_at").first(),
            reception_manifest=bag.facility_receipts.select_related("reception_manifest").order_by("-id").first().reception_manifest if bag.facility_receipts.exists() else None,
        )
        messages.success(request, f"Exception logged successfully: {disc.discrepancy_reference}. Administrators have been notified.")
    else:
        messages.error(request, "Could not submit discrepancy. Please check the entered fields.")

    return redirect("tracking:bag_tracking_detail", bag_id=bag.bag_id)


@require_POST
@role_required(User.Role.ADMIN)
def add_compensating_correction(request, bag_id):
    """
    Appends an immutable CUSTODY_CORRECTION event to a bag for audit corrections.
    """
    bag = get_object_or_404(WasteBag, bag_id=bag_id)
    form = CustodyCorrectionForm(request.POST, request.FILES)

    if form.is_valid():
        remarks = form.cleaned_data["remarks"].strip()
        weight = form.cleaned_data.get("weight_at_event")
        evidence = form.cleaned_data.get("evidence_document")

        record_custody_event(
            bag=bag,
            event_type=CustodyEvent.EventType.CUSTODY_CORRECTION,
            new_custody_status=bag.status,
            actor=request.user,
            origin_location="",
            destination_location="",
            verification_method="ADMIN_COMPENSATING_ENTRY",
            weight_at_event=weight or bag.weight,
            remarks=remarks,
            evidence_document=evidence,
            is_compensating_event=True,
        )
        messages.success(request, f"Compensating custody audit entry appended for bag {bag.bag_id}.")
    else:
        messages.error(request, "Failed to record compensating correction. Check inputs.")

    return redirect("tracking:bag_tracking_detail", bag_id=bag.bag_id)
