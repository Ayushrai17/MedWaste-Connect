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
from core.utils import quantize_weight
from waste.models import WasteBag
from notifications.models import Notification
from .models import CollectionRequest, CollectionStatusHistory, BagVerificationRecord
from .forms import (
    CollectionRequestCreateForm,
    AdminScheduleAssignForm,
    AdminRejectForm,
    CollectorRejectForm,
    CollectorActionForm,
)
from .services import (
    get_eligible_bags_for_hospital,
    create_collection_request,
    cancel_collection_request,
    admin_review_request,
    admin_approve_request,
    admin_reject_request,
    admin_schedule_and_assign,
    collector_accept_assignment,
    collector_reject_assignment,
    collector_start_pickup,
    collector_complete_collection,
    verify_bag_for_collection,
)

User = get_user_model()


# ==============================================================================
# HOSPITAL WORKFLOW VIEWS
# ==============================================================================

@role_required(User.Role.HOSPITAL)
def hospital_request_list(request):
    """
    Hospital dashboard view displaying their collection requests, status tabs,
    and pickup history.
    """
    hospital_user = request.user
    base_qs = CollectionRequest.objects.filter(hospital=hospital_user).select_related(
        "assigned_collector"
    ).prefetch_related("bags")

    # Status tab filtering
    tab = request.GET.get("tab", "all").strip().lower()
    if tab == "pending":
        requests_qs = base_qs.filter(status__in=[
            CollectionRequest.Status.REQUESTED,
            CollectionRequest.Status.UNDER_REVIEW,
            CollectionRequest.Status.APPROVED,
        ])
    elif tab == "scheduled":
        requests_qs = base_qs.filter(status__in=[
            CollectionRequest.Status.SCHEDULED,
            CollectionRequest.Status.ASSIGNED,
            CollectionRequest.Status.ACCEPTED,
            CollectionRequest.Status.PICKUP_IN_PROGRESS,
        ])
    elif tab == "completed":
        requests_qs = base_qs.filter(status__in=[
            CollectionRequest.Status.COLLECTED,
            CollectionRequest.Status.COMPLETED,
        ])
    elif tab == "cancelled":
        requests_qs = base_qs.filter(status=CollectionRequest.Status.CANCELLED)
    else:
        tab = "all"
        requests_qs = base_qs

    # Search by reference number or notes
    q = request.GET.get("q", "").strip()
    if q:
        requests_qs = requests_qs.filter(
            models.Q(reference_number__icontains=q) |
            models.Q(pickup_address__icontains=q) |
            models.Q(contact_person__icontains=q)
        )

    # Calculate summary metrics for hospital
    stats = base_qs.aggregate(
        total_requests=models.Count("id"),
        active_pickups=models.Count("id", filter=models.Q(status__in=CollectionRequest.ACTIVE_STATUSES)),
        completed_pickups=models.Count("id", filter=models.Q(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED])),
        total_weight_collected=models.Sum("total_weight", filter=models.Q(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED])),
    )

    paginator = Paginator(requests_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "current_tab": tab,
        "search_q": q,
        "stats_total_requests": stats.get("total_requests") or 0,
        "stats_active_pickups": stats.get("active_pickups") or 0,
        "stats_completed_pickups": stats.get("completed_pickups") or 0,
        "stats_total_weight_collected": quantize_weight(stats.get("total_weight_collected")),
    }
    return render(request, "collection/hospital/request_list.html", context)


@role_required(User.Role.HOSPITAL)
def hospital_request_create(request):
    """
    Hospital request form enabling staff to select registered, uncollected waste bags
    and submit a pickup dispatch request.
    """
    hospital_user = request.user
    eligible_bags = get_eligible_bags_for_hospital(hospital_user)

    if request.method == "POST":
        form = CollectionRequestCreateForm(hospital_user, request.POST)
        if form.is_valid():
            try:
                collection_req = create_collection_request(
                    hospital_user=hospital_user,
                    form_data=form.cleaned_data,
                    bag_queryset=form.cleaned_data["bags"],
                )
                messages.success(
                    request,
                    f"Collection request {collection_req.reference_number} submitted successfully! "
                    f"({collection_req.total_bags} bags, {collection_req.total_weight} KG)."
                )
                return redirect("collection:hospital_request_detail", reference_number=collection_req.reference_number)
            except ValidationError as ve:
                for error in ve.messages:
                    messages.error(request, error)
            except Exception as e:
                messages.error(request, f"Failed to submit collection request: {str(e)}")
    else:
        form = CollectionRequestCreateForm(hospital_user)

    # Compute batch groupings for easier selection in UI
    batches_map = {}
    individual_bags = []
    for bag in eligible_bags:
        if bag.batch:
            b_num = bag.batch.batch_number
            if b_num not in batches_map:
                batches_map[b_num] = {
                    "batch": bag.batch,
                    "bags": [],
                    "total_weight": Decimal("0.00"),
                }
            batches_map[b_num]["bags"].append(bag)
            batches_map[b_num]["total_weight"] += bag.weight
        else:
            individual_bags.append(bag)

    context = {
        "form": form,
        "eligible_bags": eligible_bags,
        "batches_group": list(batches_map.values()),
        "individual_bags": individual_bags,
        "eligible_bags_count": eligible_bags.count(),
    }
    return render(request, "collection/hospital/request_create.html", context)


@role_required(User.Role.HOSPITAL)
@require_POST
def hospital_request_cancel(request, reference_number):
    """
    Hospital cancellation of a pre-collection request.
    """
    collection_req = get_object_or_404(CollectionRequest, reference_number=reference_number, hospital=request.user)
    reason = request.POST.get("cancellation_reason", "").strip()

    try:
        cancel_collection_request(collection_req, request.user, cancellation_reason=reason)
        messages.success(request, f"Collection request {reference_number} was successfully cancelled.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("collection:hospital_request_detail", reference_number=reference_number)


# ==============================================================================
# ADMINISTRATOR WORKFLOW VIEWS
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_collection_dashboard(request):
    """
    Administrator command center for reviewing incoming requests, scheduling pickups,
    and managing collector fleet assignments.
    """
    base_qs = CollectionRequest.objects.all().select_related("hospital", "assigned_collector").prefetch_related("bags")

    # Multi-parameter filtering
    status_filter = request.GET.get("status", "").strip()
    hospital_filter = request.GET.get("hospital", "").strip()
    date_filter = request.GET.get("date", "").strip()
    q = request.GET.get("q", "").strip()

    filtered_qs = base_qs
    if status_filter:
        filtered_qs = filtered_qs.filter(status=status_filter)
    if hospital_filter:
        filtered_qs = filtered_qs.filter(hospital_id=hospital_filter)
    if date_filter:
        try:
            d = timezone.datetime.strptime(date_filter, "%Y-%m-%d").date()
            filtered_qs = filtered_qs.filter(
                models.Q(confirmed_pickup_date=d) |
                models.Q(confirmed_pickup_date__isnull=True, preferred_pickup_date=d)
            )
        except ValueError:
            pass
    if q:
        filtered_qs = filtered_qs.filter(
            models.Q(reference_number__icontains=q) |
            models.Q(hospital__organization_name__icontains=q) |
            models.Q(hospital__username__icontains=q) |
            models.Q(contact_person__icontains=q)
        )

    # Compute high-level operational statistics
    stats = base_qs.aggregate(
        total_requests=models.Count("id"),
        incoming=models.Count("id", filter=models.Q(status=CollectionRequest.Status.REQUESTED)),
        under_review=models.Count("id", filter=models.Q(status=CollectionRequest.Status.UNDER_REVIEW)),
        awaiting_assignment=models.Count("id", filter=models.Q(status__in=[
            CollectionRequest.Status.APPROVED,
            CollectionRequest.Status.COLLECTOR_REJECTED
        ])),
        scheduled=models.Count("id", filter=models.Q(status__in=[
            CollectionRequest.Status.SCHEDULED,
            CollectionRequest.Status.ASSIGNED,
            CollectionRequest.Status.ACCEPTED,
        ])),
        in_transit=models.Count("id", filter=models.Q(status=CollectionRequest.Status.PICKUP_IN_PROGRESS)),
        completed=models.Count("id", filter=models.Q(status__in=[
            CollectionRequest.Status.COLLECTED,
            CollectionRequest.Status.COMPLETED
        ])),
    )

    paginator = Paginator(filtered_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Active hospitals and collectors for filter dropdowns
    hospitals = User.objects.filter(role=User.Role.HOSPITAL, is_active=True).order_by("organization_name", "username")
    collectors = User.objects.filter(role=User.Role.COLLECTOR, is_active=True).order_by("organization_name", "username")

    context = {
        "page_obj": page_obj,
        "stats": stats,
        "status_choices": CollectionRequest.Status.choices,
        "selected_status": status_filter,
        "selected_hospital": hospital_filter,
        "selected_date": date_filter,
        "search_q": q,
        "hospitals": hospitals,
        "collectors": collectors,
    }
    return render(request, "collection/admin/management_dashboard.html", context)


@role_required(User.Role.ADMIN)
def admin_schedule_assign_view(request, reference_number):
    """
    Dedicated view for platform administrator to confirm the pickup schedule
    and assign an available collector without overlap conflicts.
    """
    collection_req = get_object_or_404(
        CollectionRequest.objects.select_related("hospital", "assigned_collector"),
        reference_number=reference_number
    )

    if not collection_req.can_be_assigned:
        messages.error(request, f"Request {reference_number} is in status '{collection_req.get_status_display()}' and cannot be scheduled.")
        return redirect("collection:admin_request_detail", reference_number=reference_number)

    if request.method == "POST":
        form = AdminScheduleAssignForm(collection_req, request.POST)
        if form.is_valid():
            try:
                admin_schedule_and_assign(
                    collection_request=collection_req,
                    admin_user=request.user,
                    confirmed_date=form.cleaned_data["confirmed_pickup_date"],
                    confirmed_time=form.cleaned_data["confirmed_pickup_time"],
                    collector_user=form.cleaned_data["assigned_collector"],
                    remarks=form.cleaned_data.get("admin_notes", ""),
                    destination_facility=form.cleaned_data.get("destination_facility"),
                )
                messages.success(
                    request,
                    f"Request {reference_number} scheduled for {form.cleaned_data['confirmed_pickup_date']} "
                    f"and assigned to {form.cleaned_data['assigned_collector'].username}."
                )
                return redirect("collection:admin_request_detail", reference_number=reference_number)
            except ValidationError as ve:
                for error in ve.messages:
                    messages.error(request, error)
            except Exception as e:
                messages.error(request, f"Scheduling error: {str(e)}")
    else:
        form = AdminScheduleAssignForm(collection_req)

    context = {
        "form": form,
        "collection_req": collection_req,
    }
    return render(request, "collection/admin/schedule_assign.html", context)


@role_required(User.Role.ADMIN)
@require_POST
def admin_approve_view(request, reference_number):
    """
    Administrator approves a collection request.
    """
    collection_req = get_object_or_404(CollectionRequest, reference_number=reference_number)
    remarks = request.POST.get("remarks", "").strip()

    try:
        admin_approve_request(collection_req, request.user, remarks=remarks)
        messages.success(request, f"Collection request {reference_number} was approved.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("collection:admin_request_detail", reference_number=reference_number)


@role_required(User.Role.ADMIN)
def admin_reject_view(request, reference_number):
    """
    Administrator rejects a collection request with explanation.
    """
    collection_req = get_object_or_404(CollectionRequest, reference_number=reference_number)

    if request.method == "POST":
        form = AdminRejectForm(request.POST)
        if form.is_valid():
            try:
                admin_reject_request(
                    collection_request=collection_req,
                    admin_user=request.user,
                    rejection_reason=form.cleaned_data["rejection_reason"]
                )
                messages.warning(request, f"Collection request {reference_number} was rejected.")
                return redirect("collection:admin_request_detail", reference_number=reference_number)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
    else:
        form = AdminRejectForm()

    context = {
        "form": form,
        "collection_req": collection_req,
    }
    return render(request, "collection/admin/reject_request.html", context)


# ==============================================================================
# COLLECTOR WORKFLOW VIEWS
# ==============================================================================

@role_required(User.Role.COLLECTOR)
def collector_dashboard_view(request):
    """
    Mobile-optimized logistics dashboard for waste collection drivers:
    - View assigned pickups
    - Accept or reject assignments
    - Start journey and record collection handovers
    """
    collector_user = request.user
    base_qs = CollectionRequest.objects.filter(assigned_collector=collector_user).select_related(
        "hospital"
    ).prefetch_related("bags", "bags__category")

    today = timezone.localdate()

    tab = request.GET.get("tab", "active").strip().lower()
    if tab == "pending":
        requests_qs = base_qs.filter(status=CollectionRequest.Status.ASSIGNED)
    elif tab == "today":
        requests_qs = base_qs.filter(
            status__in=[
                CollectionRequest.Status.ASSIGNED,
                CollectionRequest.Status.ACCEPTED,
                CollectionRequest.Status.PICKUP_IN_PROGRESS,
            ],
            confirmed_pickup_date=today
        )
    elif tab == "completed":
        requests_qs = base_qs.filter(status__in=[
            CollectionRequest.Status.COLLECTED,
            CollectionRequest.Status.COMPLETED
        ])
    else:
        tab = "active"
        requests_qs = base_qs.filter(status__in=[
            CollectionRequest.Status.ASSIGNED,
            CollectionRequest.Status.ACCEPTED,
            CollectionRequest.Status.PICKUP_IN_PROGRESS,
        ])

    stats = base_qs.aggregate(
        pending_acceptance=models.Count("id", filter=models.Q(status=CollectionRequest.Status.ASSIGNED)),
        active_pickups=models.Count("id", filter=models.Q(status__in=[
            CollectionRequest.Status.ACCEPTED,
            CollectionRequest.Status.PICKUP_IN_PROGRESS,
        ])),
        today_pickups=models.Count("id", filter=models.Q(confirmed_pickup_date=today)),
        completed_pickups=models.Count("id", filter=models.Q(status__in=[
            CollectionRequest.Status.COLLECTED,
            CollectionRequest.Status.COMPLETED,
        ])),
    )

    paginator = Paginator(requests_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "current_tab": tab,
        "stats_pending_acceptance": stats.get("pending_acceptance") or 0,
        "stats_active_pickups": stats.get("active_pickups") or 0,
        "stats_today_pickups": stats.get("today_pickups") or 0,
        "stats_completed_pickups": stats.get("completed_pickups") or 0,
        "today_date": today,
    }
    return render(request, "collection/collector/collector_dashboard.html", context)


@role_required(User.Role.COLLECTOR)
@require_POST
def collector_accept_view(request, reference_number):
    """
    Collector accepts an assigned collection request.
    """
    collection_req = get_object_or_404(
        CollectionRequest,
        reference_number=reference_number,
        assigned_collector=request.user
    )
    remarks = request.POST.get("remarks", "").strip()

    try:
        collector_accept_assignment(collection_req, request.user, remarks=remarks)
        messages.success(request, f"You accepted assignment {reference_number}. Proceed according to scheduled route.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("collection:collector_request_detail", reference_number=reference_number)


@role_required(User.Role.COLLECTOR)
def collector_reject_view(request, reference_number):
    """
    Collector rejects an assigned collection request with mandatory explanation.
    """
    collection_req = get_object_or_404(
        CollectionRequest,
        reference_number=reference_number,
        assigned_collector=request.user
    )

    if request.method == "POST":
        form = CollectorRejectForm(request.POST)
        if form.is_valid():
            try:
                collector_reject_assignment(
                    collection_request=collection_req,
                    collector_user=request.user,
                    rejection_reason=form.cleaned_data["rejection_reason"]
                )
                messages.warning(request, f"Assignment {reference_number} was rejected and returned to dispatch queue.")
                return redirect("collection:collector_dashboard")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
    else:
        form = CollectorRejectForm()

    context = {
        "form": form,
        "collection_req": collection_req,
    }
    return render(request, "collection/collector/reject_assignment.html", context)


@role_required(User.Role.COLLECTOR)
@require_POST
def collector_start_pickup_view(request, reference_number):
    """
    Collector marks journey / pickup as started.
    """
    collection_req = get_object_or_404(
        CollectionRequest,
        reference_number=reference_number,
        assigned_collector=request.user
    )
    remarks = request.POST.get("remarks", "").strip()

    try:
        collector_start_pickup(collection_req, request.user, remarks=remarks)
        messages.info(request, f"Pickup {reference_number} is now in progress. Safe travels!")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("collection:collector_request_detail", reference_number=reference_number)


@role_required(User.Role.COLLECTOR)
@require_POST
def collector_complete_collection_view(request, reference_number):
    """
    Collector marks waste as collected and updates actual handover timestamp.
    """
    collection_req = get_object_or_404(
        CollectionRequest,
        reference_number=reference_number,
        assigned_collector=request.user
    )
    remarks = request.POST.get("remarks", "").strip()

    try:
        collector_complete_collection(collection_req, request.user, remarks=remarks)
        messages.success(request, f"Collection {reference_number} marked as complete! Bags are now In Transit.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("collection:collector_request_detail", reference_number=reference_number)


# ==============================================================================
# OPERATIONAL DETAIL & BAG VERIFICATION VIEWS
# ==============================================================================

def collection_detail_view(request, reference_number):
    """
    Unified operational detail view with strict role-based access control:
    - Hospitals: only their own requests
    - Collectors: only their own assigned requests
    - Admins: any request
    """
    if not request.user.is_authenticated:
        return redirect("login")

    collection_req = get_object_or_404(
        CollectionRequest.objects.select_related("hospital", "assigned_collector").prefetch_related(
            "bags",
            "bags__category",
            "bags__batch",
            "status_history",
            "status_history__changed_by",
            "bag_verifications",
        ),
        reference_number=reference_number
    )

    # Enforce multi-tenant and role authorization
    user = request.user
    if user.role == User.Role.HOSPITAL:
        if collection_req.hospital_id != user.id:
            raise PermissionDenied("You do not have permission to view collection requests for other hospitals.")
    elif user.role == User.Role.COLLECTOR:
        if collection_req.assigned_collector_id != user.id:
            raise PermissionDenied("You do not have permission to view collection requests assigned to other drivers.")
    elif user.role != User.Role.ADMIN:
        raise PermissionDenied("You do not have access to collection request manifests.")

    # If admin opens a REQUESTED request, transition to UNDER_REVIEW
    if user.role == User.Role.ADMIN and collection_req.status == CollectionRequest.Status.REQUESTED:
        admin_review_request(collection_req, user)

    # Verification status map for bags in this request
    verifications = {v.bag_id: v for v in collection_req.bag_verifications.all()}
    bags_with_verifications = []
    verified_count = 0
    for bag in collection_req.bags.all():
        v = verifications.get(bag.id)
        is_v = v.is_verified if v else False
        if is_v:
            verified_count += 1
        bags_with_verifications.append({
            "bag": bag,
            "verification": v,
            "is_verified": is_v,
        })

    verification_percentage = round((verified_count / collection_req.total_bags) * 100, 1) if collection_req.total_bags > 0 else 0.0

    context = {
        "collection_req": collection_req,
        "bags_with_verifications": bags_with_verifications,
        "verified_count": verified_count,
        "verification_percentage": verification_percentage,
        "status_history": collection_req.status_history.all(),
        "is_admin": user.role == User.Role.ADMIN,
        "is_hospital": user.role == User.Role.HOSPITAL,
        "is_collector": user.role == User.Role.COLLECTOR,
    }
    return render(request, "collection/request_detail.html", context)


@role_required(User.Role.COLLECTOR)
@require_POST
def collection_verify_bag_view(request, reference_number):
    """
    Collector bag check-in endpoint:
    Verifies that a scanned or manually entered Bag ID or UUID QR token
    belongs to this specific assigned collection request.
    """
    collection_req = get_object_or_404(
        CollectionRequest,
        reference_number=reference_number,
        assigned_collector=request.user
    )

    identifier = request.POST.get("bag_identifier", "").strip()
    notes = request.POST.get("notes", "").strip()

    if not identifier:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Bag tracking identifier is required."}, status=400)
        messages.error(request, "Bag tracking identifier is required.")
        return redirect("collection:collector_request_detail", reference_number=reference_number)

    try:
        rec = verify_bag_for_collection(
            collection_request=collection_req,
            bag_identifier_or_token=identifier,
            collector_user=request.user,
            notes=notes,
        )
        msg = f"Bag {rec.bag.bag_id} verified successfully ({rec.bag.category.code}, {rec.bag.weight} KG)."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": msg,
                "bag_id": rec.bag.bag_id,
                "category": rec.bag.category.code,
                "weight": str(rec.bag.weight),
            })
        messages.success(request, msg)
    except (ValidationError, PermissionDenied) as e:
        err_msg = str(e)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": err_msg}, status=400)
        messages.error(request, err_msg)

    return redirect("collection:collector_request_detail", reference_number=reference_number)


# ==============================================================================
# IN-APP NOTIFICATIONS VIEWS
# ==============================================================================

def notification_list_view(request):
    """
    Displays full notification history for the authenticated user.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    notifications_qs = Notification.objects.filter(recipient=request.user)
    paginator = Paginator(notifications_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "notifications/notification_list.html", {"page_obj": page_obj})


@require_POST
def notification_mark_read_view(request, notification_id):
    """
    Marks a single notification as read and redirects to its destination link.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.mark_as_read()

    next_url = notification.link if notification.link else "notifications:list"
    return redirect(next_url)


@require_POST
def notification_mark_all_read_view(request):
    """
    Marks all notifications for authenticated user as read.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    messages.success(request, "All notifications marked as read.")
    return redirect("notifications:list")
