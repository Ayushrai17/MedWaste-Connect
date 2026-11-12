import csv
import json
import logging
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import AdminActionAudit, PlatformSettings
from accounts.forms_admin import (
    UserSuspensionForm,
    FacilityActionForm,
    PlatformSettingsForm,
    ComplianceThresholdForm,
    BroadcastAlertForm,
)
from accounts.services.admin_service import (
    toggle_user_active_status,
    suspend_user,
    reactivate_user,
    approve_user_onboarding,
    reject_user_onboarding,
    broadcast_system_notification,
    get_platform_kpis,
    log_admin_action,
)
from accounts.views import role_required
from core.utils import quantize_weight
from facilities.models import TreatmentFacility
from facilities.services import (
    approve_treatment_facility,
    reject_treatment_facility,
    suspend_treatment_facility,
    reinstate_treatment_facility,
    get_facility_capacity_metrics,
)
from collection.models import CollectionRequest, CollectionStatusHistory
from treatment.models import FacilityReceptionManifest, FacilityBagReceipt, TreatmentBatch, DisposalRecord, FacilityAuditHistory
from tracking.models import CustodyEvent, CustodyHandover, CustodyDiscrepancy
from waste.models import WasteBag, WasteCategory
from reports.models import ComplianceThresholdConfig, GeneratedReportLog
from notifications.models import Notification
from notifications.services import send_notification

logger = logging.getLogger(__name__)
User = get_user_model()


# ==============================================================================
# 1. CENTRAL ADMIN CONTROL PANEL
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_control_panel(request):
    """
    Central Executive Directorate & Platform Control Panel.
    Aggregates platform KPIs, operational health indicators, pending approvals,
    and system alerts into a single unified command view.
    """
    kpis = get_platform_kpis()
    config = PlatformSettings.get_settings()

    # Recent pending onboarding items
    pending_facilities = TreatmentFacility.objects.filter(
        approval_status=TreatmentFacility.ApprovalStatus.PENDING_APPROVAL
    ).select_related("operator_user")[:5]

    pending_hospitals = User.objects.filter(
        role=User.Role.HOSPITAL,
        account_status=User.AccountStatus.PENDING_APPROVAL
    )[:5]

    # Critical active exceptions
    critical_discrepancies = CustodyDiscrepancy.objects.filter(
        severity=CustodyDiscrepancy.Severity.CRITICAL,
        status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]
    ).select_related("bag", "reported_by").order_by("-reported_at")[:5]

    # Stale / delayed collection requests (confirmed date passed or unassigned > 24h)
    today = timezone.localdate()
    stale_requests = CollectionRequest.objects.filter(
        Q(status__in=[CollectionRequest.Status.REQUESTED, CollectionRequest.Status.UNDER_REVIEW]) |
        Q(status=CollectionRequest.Status.SCHEDULED, confirmed_pickup_date__lt=today)
    ).select_related("hospital").order_by("created_at")[:5]

    # Recent administrative interventions
    recent_admin_actions = AdminActionAudit.objects.select_related("actor")[:8]

    # Newly / recently registered hospitals (visible even when auto-activated)
    recent_hospitals = User.objects.filter(role=User.Role.HOSPITAL).order_by("-date_joined")[:6]

    context = {
        "kpis": kpis,
        "platform_settings": config,
        "pending_facilities": pending_facilities,
        "pending_hospitals": pending_hospitals,
        "recent_hospitals": recent_hospitals,
        "critical_discrepancies": critical_discrepancies,
        "stale_requests": stale_requests,
        "recent_admin_actions": recent_admin_actions,
    }
    return render(request, "accounts/admin/dashboard_overview.html", context)


# ==============================================================================
# 2. USER ACCOUNT MANAGEMENT
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_user_list(request):
    """
    Role-aware platform user account management directory.
    Supports multi-field search and filtering by role and account lifecycle status.
    """
    config = PlatformSettings.get_settings()
    users_qs = User.objects.all().order_by("-date_joined")

    q = request.GET.get("q", "").strip()
    if q:
        users_qs = users_qs.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q) |
            Q(organization_name__icontains=q) |
            Q(phone__icontains=q)
        )

    role_filter = request.GET.get("role", "").strip()
    if role_filter:
        users_qs = users_qs.filter(role=role_filter)

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        if status_filter == "ACTIVE":
            users_qs = users_qs.filter(is_active=True, account_status=User.AccountStatus.ACTIVE)
        elif status_filter == "SUSPENDED":
            users_qs = users_qs.filter(account_status=User.AccountStatus.SUSPENDED)
        elif status_filter == "INACTIVE":
            users_qs = users_qs.filter(is_active=False).exclude(account_status=User.AccountStatus.SUSPENDED)
        elif status_filter == "PENDING":
            users_qs = users_qs.filter(account_status=User.AccountStatus.PENDING_APPROVAL)

    paginator = Paginator(users_qs, config.default_page_size)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "page_obj": page_obj,
        "search_q": q,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "role_choices": User.Role.choices,
        "total_count": paginator.count,
    }
    return render(request, "accounts/admin/user_list.html", context)


@role_required(User.Role.ADMIN)
def admin_user_detail(request, user_id):
    """
    Detailed administrative inspection of a specific user account,
    operational footprint, and historical audit ledger.
    """
    target_user = get_object_or_404(User, id=user_id)
    suspension_form = UserSuspensionForm()

    # Role-specific operational statistics
    hospital_stats = None
    collector_stats = None
    facility_stats = None

    if target_user.role == User.Role.HOSPITAL:
        bags_qs = WasteBag.objects.filter(hospital=target_user)
        hospital_stats = {
            "total_bags": bags_qs.count(),
            "total_weight": quantize_weight(bags_qs.aggregate(w=Sum("weight"))["w"]),
            "requests_count": target_user.collection_requests.count(),
            "discrepancies_count": CustodyDiscrepancy.objects.filter(bag__hospital=target_user).count(),
        }
    elif target_user.role == User.Role.COLLECTOR:
        reqs_qs = CollectionRequest.objects.filter(assigned_collector=target_user)
        collector_stats = {
            "total_assignments": reqs_qs.count(),
            "completed_pickups": reqs_qs.filter(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED]).count(),
            "rejected_assignments": reqs_qs.filter(status=CollectionRequest.Status.COLLECTOR_REJECTED).count(),
            "handovers_count": CustodyHandover.objects.filter(Q(releasing_user=target_user) | Q(receiving_user=target_user)).count(),
        }
    elif target_user.role == User.Role.FACILITY:
        facility = getattr(target_user, "managed_facility", None)
        if facility:
            facility_stats = {
                "facility": facility,
                "receptions_count": facility.reception_manifests.count(),
                "batches_count": facility.treatment_batches.count(),
                "capacity_metrics": get_facility_capacity_metrics(facility),
            }

    # Audit history affecting this user
    user_audits = AdminActionAudit.objects.filter(
        affected_model="User",
        affected_record_id=str(target_user.id)
    ).select_related("actor")[:15]

    context = {
        "target_user": target_user,
        "suspension_form": suspension_form,
        "hospital_stats": hospital_stats,
        "collector_stats": collector_stats,
        "facility_stats": facility_stats,
        "user_audits": user_audits,
    }
    return render(request, "accounts/admin/user_detail.html", context)


@require_POST
@role_required(User.Role.ADMIN)
def admin_user_toggle_active(request, user_id):
    """
    Activates or deactivates a user account with self-deactivation protection.
    """
    target_user = get_object_or_404(User, id=user_id)
    new_state = request.POST.get("is_active") == "1"
    reason = request.POST.get("reason", "Status toggled by administrator.")

    try:
        toggle_user_active_status(
            admin_user=request.user,
            target_user=target_user,
            is_active=new_state,
            reason=reason,
        )
        status_label = "activated" if new_state else "deactivated"
        messages.success(request, f"User account '{target_user.username}' has been successfully {status_label}.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("admin_user_detail", user_id=target_user.id)


@require_POST
@role_required(User.Role.ADMIN)
def admin_user_suspend(request, user_id):
    """
    Suspends a user account with mandatory administrative justification.
    """
    target_user = get_object_or_404(User, id=user_id)
    form = UserSuspensionForm(request.POST)

    if form.is_valid():
        try:
            suspend_user(
                admin_user=request.user,
                target_user=target_user,
                reason=form.cleaned_data["reason"],
            )
            messages.warning(request, f"User account '{target_user.username}' ({target_user.get_role_display()}) has been suspended.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        err = form.errors.get("reason", ["Invalid justification provided."])[0]
        messages.error(request, err)

    return redirect("admin_user_detail", user_id=target_user.id)


@require_POST
@role_required(User.Role.ADMIN)
def admin_user_reactivate(request, user_id):
    """
    Reactivates a suspended user account.
    """
    target_user = get_object_or_404(User, id=user_id)
    reason = request.POST.get("reason", "Account restored by administrator.")

    try:
        reactivate_user(
            admin_user=request.user,
            target_user=target_user,
            reason=reason,
        )
        messages.success(request, f"User account '{target_user.username}' has been restored to Active status.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("admin_user_detail", user_id=target_user.id)


# ==============================================================================
# 3. HOSPITAL ORGANIZATION MANAGEMENT
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_hospital_list(request):
    """
    Centralized administrative registry of all registered Hospital healthcare organizations.
    """
    config = PlatformSettings.get_settings()
    hospitals_qs = User.objects.filter(role=User.Role.HOSPITAL).order_by("-date_joined")

    q = request.GET.get("q", "").strip()
    if q:
        hospitals_qs = hospitals_qs.filter(
            Q(organization_name__icontains=q) |
            Q(username__icontains=q) |
            Q(email__icontains=q) |
            Q(phone__icontains=q)
        )

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        hospitals_qs = hospitals_qs.filter(account_status=status_filter)

    # Attach summary generation counts
    hospitals_data = []
    for h in hospitals_qs:
        bags_qs = WasteBag.objects.filter(hospital=h)
        hospitals_data.append({
            "hospital": h,
            "total_bags": bags_qs.count(),
            "total_weight": quantize_weight(bags_qs.aggregate(w=Sum("weight"))["w"]),
            "requests_count": h.collection_requests.count(),
            "open_discrepancies": CustodyDiscrepancy.objects.filter(bag__hospital=h, status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]).count(),
        })

    paginator = Paginator(hospitals_data, config.default_page_size)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "page_obj": page_obj,
        "search_q": q,
        "status_filter": status_filter,
        "status_choices": User.AccountStatus.choices,
        "total_count": paginator.count,
    }
    return render(request, "accounts/admin/hospital_list.html", context)


@role_required(User.Role.ADMIN)
def admin_hospital_detail(request, hospital_id):
    """
    Detailed inspection of hospital organization, waste generation breakdown,
    dispatch history, reception outcomes, and compliance indicators.
    """
    hospital_user = get_object_or_404(User, id=hospital_id, role=User.Role.HOSPITAL)

    # Waste generation stats by category
    bags_qs = WasteBag.objects.filter(hospital=hospital_user)
    cat_distribution = list(
        bags_qs.values("category__code", "category__name", "category__display_color")
        .annotate(count=Count("id"), weight=Sum("weight"))
        .order_by("category__code")
    )
    for row in cat_distribution:
        row["weight"] = quantize_weight(row.get("weight"))
    total_bags = bags_qs.count()
    total_weight = quantize_weight(bags_qs.aggregate(w=Sum("weight"))["w"])

    # Collection requests
    collection_requests = hospital_user.collection_requests.all().select_related("assigned_collector", "destination_facility").order_by("-created_at")[:10]
    total_requests = hospital_user.collection_requests.count()

    # Facility reception outcomes
    receipts_qs = FacilityBagReceipt.objects.filter(bag__hospital=hospital_user)
    reception_metrics = receipts_qs.aggregate(
        total=Count("id"),
        accepted=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED)),
        rejected=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.REJECTED)),
        missing=Count("id", filter=Q(acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING)),
    )
    acceptance_rate = (
        round((reception_metrics["accepted"] / reception_metrics["total"]) * 100, 1)
        if reception_metrics["total"] > 0 else 100.0
    )

    # Discrepancies
    discrepancies = CustodyDiscrepancy.objects.filter(bag__hospital=hospital_user).select_related("bag", "reported_by").order_by("-reported_at")[:8]

    # Administrative audit logs
    admin_audits = AdminActionAudit.objects.filter(
        affected_model="User",
        affected_record_id=str(hospital_user.id)
    )[:10]

    suspension_form = UserSuspensionForm()

    context = {
        "hospital": hospital_user,
        "total_bags": total_bags,
        "total_weight": total_weight,
        "cat_distribution": cat_distribution,
        "collection_requests": collection_requests,
        "total_requests": total_requests,
        "reception_metrics": reception_metrics,
        "acceptance_rate": acceptance_rate,
        "discrepancies": discrepancies,
        "admin_audits": admin_audits,
        "suspension_form": suspension_form,
    }
    return render(request, "accounts/admin/hospital_detail.html", context)


# ==============================================================================
# 4. COLLECTOR & LOGISTICS MANAGEMENT
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_collector_list(request):
    """
    Centralized directory of waste collection personnel, vehicle routes, and performance.
    """
    config = PlatformSettings.get_settings()
    collectors_qs = User.objects.filter(role=User.Role.COLLECTOR).order_by("-date_joined")

    q = request.GET.get("q", "").strip()
    if q:
        collectors_qs = collectors_qs.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q) |
            Q(phone__icontains=q) |
            Q(organization_name__icontains=q)
        )

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        collectors_qs = collectors_qs.filter(account_status=status_filter)

    collectors_data = []
    for c in collectors_qs:
        reqs = CollectionRequest.objects.filter(assigned_collector=c)
        completed = reqs.filter(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED]).count()
        rejected = reqs.filter(status=CollectionRequest.Status.COLLECTOR_REJECTED).count()
        collectors_data.append({
            "collector": c,
            "total_assigned": reqs.count(),
            "completed_pickups": completed,
            "rejected_assignments": rejected,
            "disputed_handovers": CustodyHandover.objects.filter(Q(releasing_user=c) | Q(receiving_user=c), status=CustodyHandover.Status.DISPUTED).count(),
        })

    paginator = Paginator(collectors_data, config.default_page_size)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "page_obj": page_obj,
        "search_q": q,
        "status_filter": status_filter,
        "status_choices": User.AccountStatus.choices,
        "total_count": paginator.count,
    }
    return render(request, "accounts/admin/collector_list.html", context)


@role_required(User.Role.ADMIN)
def admin_collector_detail(request, collector_id):
    """
    Detailed performance dossier for an individual collector driver.
    """
    collector = get_object_or_404(User, id=collector_id, role=User.Role.COLLECTOR)
    reqs_qs = CollectionRequest.objects.filter(assigned_collector=collector).select_related("hospital", "destination_facility").order_by("-created_at")

    total_assignments = reqs_qs.count()
    completed_pickups = reqs_qs.filter(status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED]).count()
    rejected_assignments = reqs_qs.filter(status=CollectionRequest.Status.COLLECTOR_REJECTED).count()

    handovers_qs = CustodyHandover.objects.filter(
        Q(releasing_user=collector) | Q(receiving_user=collector)
    )
    disputed_count = handovers_qs.filter(status=CustodyHandover.Status.DISPUTED).count()
    handovers = handovers_qs.select_related("releasing_user", "receiving_user").order_by("-handover_timestamp")[:10]

    discrepancies = CustodyDiscrepancy.objects.filter(
        Q(collection_request__assigned_collector=collector) | Q(reported_by=collector)
    ).select_related("bag", "reported_by").order_by("-reported_at")[:8]

    suspension_form = UserSuspensionForm()

    context = {
        "collector": collector,
        "total_assignments": total_assignments,
        "completed_pickups": completed_pickups,
        "rejected_assignments": rejected_assignments,
        "recent_requests": reqs_qs[:10],
        "handovers": handovers,
        "disputed_count": disputed_count,
        "discrepancies": discrepancies,
        "suspension_form": suspension_form,
    }
    return render(request, "accounts/admin/collector_detail.html", context)


# ==============================================================================
# 5. APPROVALS QUEUE (FACILITIES & ORGANIZATIONS)
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_approvals_queue(request):
    """
    Central onboarding queue managing treatment facility approvals,
    hospital organization verification, and rejected applications.
    """
    # 1. Pending Treatment Facilities
    pending_facilities = TreatmentFacility.objects.filter(
        approval_status=TreatmentFacility.ApprovalStatus.PENDING_APPROVAL
    ).select_related("operator_user")

    # 2. Pending Hospital / User Applications
    pending_users = User.objects.filter(
        account_status=User.AccountStatus.PENDING_APPROVAL
    ).order_by("-date_joined")

    # 3. Recently Approved Applications (last 30 days)
    approved_facilities = TreatmentFacility.objects.filter(
        approval_status=TreatmentFacility.ApprovalStatus.APPROVED
    ).select_related("operator_user").order_by("-updated_at")[:10]

    approved_users = User.objects.filter(
        account_status=User.AccountStatus.ACTIVE,
        approved_by__isnull=False
    ).select_related("approved_by").order_by("-approved_at")[:10]

    # Newly self-registered active hospitals (auto-activated when approval gate is disabled)
    self_registered_hospitals = User.objects.filter(
        role=User.Role.HOSPITAL,
        account_status=User.AccountStatus.ACTIVE,
        approved_by__isnull=True,
    ).order_by("-date_joined")[:10]

    # 4. Rejected Applications
    rejected_facilities = TreatmentFacility.objects.filter(
        approval_status=TreatmentFacility.ApprovalStatus.REJECTED
    ).select_related("operator_user").order_by("-updated_at")

    rejected_users = User.objects.filter(
        account_status=User.AccountStatus.REJECTED
    ).order_by("-date_joined")

    action_form = FacilityActionForm()
    default_tab = "pending-users" if (not pending_facilities.exists() and pending_users.exists()) else "pending-facilities"

    context = {
        "pending_facilities": pending_facilities,
        "pending_users": pending_users,
        "approved_facilities": approved_facilities,
        "approved_users": approved_users,
        "self_registered_hospitals": self_registered_hospitals,
        "rejected_facilities": rejected_facilities,
        "rejected_users": rejected_users,
        "action_form": action_form,
        "default_tab": default_tab,
    }
    return render(request, "accounts/admin/approvals_queue.html", context)


@require_POST
@role_required(User.Role.ADMIN)
def admin_approve_facility(request, facility_code):
    facility = get_object_or_404(TreatmentFacility, facility_code=facility_code)
    remarks = request.POST.get("reason", "Approved by administration.")
    try:
        approve_treatment_facility(request.user, facility, remarks=remarks)
        messages.success(request, f"Treatment Facility '{facility.name}' ({facility.facility_code}) has been APPROVED.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
    return redirect("admin_approvals_queue")


@require_POST
@role_required(User.Role.ADMIN)
def admin_reject_facility(request, facility_code):
    facility = get_object_or_404(TreatmentFacility, facility_code=facility_code)
    reason = request.POST.get("reason", "").strip()
    try:
        reject_treatment_facility(request.user, facility, reason=reason)
        messages.warning(request, f"Treatment Facility '{facility.name}' has been REJECTED.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
    return redirect("admin_approvals_queue")


@require_POST
@role_required(User.Role.ADMIN)
def admin_suspend_facility(request, facility_code):
    facility = get_object_or_404(TreatmentFacility, facility_code=facility_code)
    reason = request.POST.get("reason", "").strip()
    try:
        suspend_treatment_facility(request.user, facility, reason=reason)
        messages.warning(request, f"Treatment Facility '{facility.name}' has been SUSPENDED.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
    return redirect("facilities:admin_facility_detail", facility_code=facility.facility_code)


@require_POST
@role_required(User.Role.ADMIN)
def admin_reinstate_facility(request, facility_code):
    facility = get_object_or_404(TreatmentFacility, facility_code=facility_code)
    remarks = request.POST.get("reason", "Reinstated by administration after review.")
    try:
        reinstate_treatment_facility(request.user, facility, remarks=remarks)
        messages.success(request, f"Treatment Facility '{facility.name}' has been REINSTATED to active status.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
    return redirect("facilities:admin_facility_detail", facility_code=facility.facility_code)


@require_POST
@role_required(User.Role.ADMIN)
def admin_approve_user(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    remarks = request.POST.get("reason", "Approved by administrator.")
    try:
        approve_user_onboarding(request.user, target_user, remarks=remarks)
        messages.success(request, f"Account '{target_user.username}' has been APPROVED.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
    return redirect("admin_approvals_queue")


@require_POST
@role_required(User.Role.ADMIN)
def admin_reject_user(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    reason = request.POST.get("reason", "").strip()
    try:
        reject_user_onboarding(request.user, target_user, reason=reason)
        messages.warning(request, f"Account '{target_user.username}' has been REJECTED.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
    return redirect("admin_approvals_queue")


# ==============================================================================
# 6. OPERATIONAL OVERSIGHT CONSOLE
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_operational_oversight(request):
    """
    Centralized administrative operational oversight console covering:
    - Collection logistics (unassigned, delayed, in-progress, completed)
    - Treatment plant intake & reception inspection
    - Batch processing runs & residue disposal filings
    - Chain-of-custody handovers, disputes, and open discrepancies
    """
    tab = request.GET.get("tab", "collection").strip()

    # 1. Collection Oversight Data
    collection_requests = CollectionRequest.objects.all().select_related(
        "hospital", "assigned_collector", "destination_facility"
    ).order_by("-created_at")[:25]

    today = timezone.localdate()
    unassigned_requests = CollectionRequest.objects.filter(
        assigned_collector__isnull=True,
        status__in=[CollectionRequest.Status.REQUESTED, CollectionRequest.Status.UNDER_REVIEW, CollectionRequest.Status.APPROVED]
    ).count()

    delayed_requests = CollectionRequest.objects.filter(
        confirmed_pickup_date__lt=today,
        status__in=[CollectionRequest.Status.SCHEDULED, CollectionRequest.Status.ASSIGNED]
    ).count()

    rejected_assignments = CollectionRequest.objects.filter(
        status=CollectionRequest.Status.COLLECTOR_REJECTED
    ).count()

    # 2. Facility Oversight Data
    manifests = FacilityReceptionManifest.objects.all().select_related(
        "facility", "collection_request", "received_by"
    ).order_by("-received_at")[:25]

    damaged_receipts = FacilityBagReceipt.objects.filter(
        packaging_condition__in=[FacilityBagReceipt.PackagingCondition.DAMAGED, FacilityBagReceipt.PackagingCondition.LEAKING]
    ).count()

    missing_receipts = FacilityBagReceipt.objects.filter(
        acceptance_decision=FacilityBagReceipt.AcceptanceDecision.MISSING
    ).count()

    # 3. Treatment & Disposal Oversight Data
    batches = TreatmentBatch.objects.all().select_related("facility", "waste_category", "operator").order_by("-created_at")[:25]
    pending_batches = TreatmentBatch.objects.filter(status=TreatmentBatch.Status.PENDING).count()
    in_progress_batches = TreatmentBatch.objects.filter(status=TreatmentBatch.Status.IN_PROGRESS).count()
    failed_batches = TreatmentBatch.objects.filter(status__in=[TreatmentBatch.Status.FAILED, TreatmentBatch.Status.ON_HOLD]).count()

    disposals = DisposalRecord.objects.all().select_related("facility", "treatment_batch", "operator", "verified_by").order_by("-disposal_date")[:25]
    unverified_disposals = DisposalRecord.objects.filter(status=DisposalRecord.Status.DOCUMENTED).count()

    # 4. Chain of Custody Oversight Data
    handovers = CustodyHandover.objects.all().select_related("releasing_user", "receiving_user", "collection_request").order_by("-handover_timestamp")[:25]
    disputed_handovers = CustodyHandover.objects.filter(status=CustodyHandover.Status.DISPUTED).count()

    discrepancies = CustodyDiscrepancy.objects.all().select_related("bag", "reported_by", "resolved_by").order_by("-reported_at")[:25]
    open_critical_disc = CustodyDiscrepancy.objects.filter(severity=CustodyDiscrepancy.Severity.CRITICAL, status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]).count()
    open_high_disc = CustodyDiscrepancy.objects.filter(severity=CustodyDiscrepancy.Severity.HIGH, status__in=[CustodyDiscrepancy.Status.OPEN, CustodyDiscrepancy.Status.UNDER_REVIEW]).count()

    context = {
        "active_tab": tab,
        # Collection
        "collection_requests": collection_requests,
        "unassigned_requests": unassigned_requests,
        "delayed_requests": delayed_requests,
        "rejected_assignments": rejected_assignments,
        # Facility
        "manifests": manifests,
        "damaged_receipts": damaged_receipts,
        "missing_receipts": missing_receipts,
        # Treatment
        "batches": batches,
        "pending_batches": pending_batches,
        "in_progress_batches": in_progress_batches,
        "failed_batches": failed_batches,
        "disposals": disposals,
        "unverified_disposals": unverified_disposals,
        # Custody
        "handovers": handovers,
        "disputed_handovers": disputed_handovers,
        "discrepancies": discrepancies,
        "open_critical_disc": open_critical_disc,
        "open_high_disc": open_high_disc,
    }
    return render(request, "accounts/admin/operational_oversight.html", context)


# ==============================================================================
# 7. PLATFORM CONFIGURATION CENTER
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_platform_config(request):
    """
    Secure Platform Configuration Center.
    Manages operational runtime settings and compliance threshold configurations
    with mandatory audit reason logging.
    """
    settings_obj = PlatformSettings.get_settings()
    compliance_config = ComplianceThresholdConfig.get_config()

    if request.method == "POST":
        config_type = request.POST.get("config_type")

        if config_type == "platform_settings":
            settings_form = PlatformSettingsForm(request.POST, instance=settings_obj)
            compliance_form = ComplianceThresholdForm(instance=compliance_config)

            if settings_form.is_valid():
                reason = settings_form.cleaned_data["change_reason"]
                prev_state = f"page_size={settings_obj.default_page_size}, req_approval={settings_obj.hospital_requires_approval}, maintenance={settings_obj.maintenance_mode}"
                updated_settings = settings_form.save(commit=False)
                updated_settings.updated_by = request.user
                updated_settings.save()

                new_state = f"page_size={updated_settings.default_page_size}, req_approval={updated_settings.hospital_requires_approval}, maintenance={updated_settings.maintenance_mode}"
                log_admin_action(
                    actor=request.user,
                    action_type=AdminActionAudit.ActionType.CONFIG_UPDATE,
                    affected_model="PlatformSettings",
                    affected_record_id="SINGLETON",
                    affected_record_display="Platform Runtime Configuration",
                    previous_state=prev_state,
                    new_state=new_state,
                    reason=reason,
                )
                messages.success(request, "Platform operational runtime configuration updated successfully.")
                return redirect("admin_platform_config")
            else:
                messages.error(request, "Please correct errors in the platform settings form.")

        elif config_type == "compliance_thresholds":
            compliance_form = ComplianceThresholdForm(request.POST, instance=compliance_config)
            settings_form = PlatformSettingsForm(instance=settings_obj)

            if compliance_form.is_valid():
                reason = compliance_form.cleaned_data["change_reason"]
                prev_state = f"treatment={compliance_config.treatment_turnaround_hours}h, disposal={compliance_config.disposal_turnaround_days}d, disc_escalation={compliance_config.discrepancy_escalation_hours}h"
                updated_compliance = compliance_form.save(commit=False)
                updated_compliance.updated_by = request.user
                updated_compliance.save()

                new_state = f"treatment={updated_compliance.treatment_turnaround_hours}h, disposal={updated_compliance.disposal_turnaround_days}d, disc_escalation={updated_compliance.discrepancy_escalation_hours}h"
                log_admin_action(
                    actor=request.user,
                    action_type=AdminActionAudit.ActionType.COMPLIANCE_CONFIG_UPDATE,
                    affected_model="ComplianceThresholdConfig",
                    affected_record_id="SINGLETON",
                    affected_record_display="Compliance Threshold Operational Reminders",
                    previous_state=prev_state,
                    new_state=new_state,
                    reason=reason,
                )
                messages.success(request, "Compliance operational threshold reminders updated successfully.")
                return redirect("admin_platform_config")
            else:
                messages.error(request, "Please correct errors in the compliance thresholds form.")
    else:
        settings_form = PlatformSettingsForm(instance=settings_obj)
        compliance_form = ComplianceThresholdForm(instance=compliance_config)

    # History of configuration modifications
    config_audits = AdminActionAudit.objects.filter(
        action_type__in=[
            AdminActionAudit.ActionType.CONFIG_UPDATE,
            AdminActionAudit.ActionType.COMPLIANCE_CONFIG_UPDATE,
        ]
    ).select_related("actor")[:10]

    context = {
        "settings_form": settings_form,
        "compliance_form": compliance_form,
        "config_audits": config_audits,
    }
    return render(request, "accounts/admin/platform_config.html", context)


# ==============================================================================
# 8. ADMIN AUDIT LOG AND SECURITY HISTORY
# ==============================================================================

def sanitize_csv_cell(value):
    """
    Prevents CSV formula injection by prepending a single quote if string
    starts with =, +, -, @, tab, or carriage return.
    """
    if value is None:
        return ""
    val_str = str(value).strip()
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str


@role_required(User.Role.ADMIN)
def admin_audit_logs(request):
    """
    Unified multi-source administrative and operational audit log browser.
    Integrates AdminActionAudit, CustodyEvent, FacilityAuditHistory,
    CollectionStatusHistory, and GeneratedReportLog.
    """
    config = PlatformSettings.get_settings()
    log_source = request.GET.get("source", "ADMIN_ACTIONS").strip()
    q = request.GET.get("q", "").strip()

    entries = []
    total_count = 0

    if log_source == "ADMIN_ACTIONS":
        qs = AdminActionAudit.objects.all().select_related("actor")
        if q:
            qs = qs.filter(
                Q(action_reference__icontains=q) |
                Q(actor__username__icontains=q) |
                Q(affected_record_display__icontains=q) |
                Q(reason__icontains=q)
            )
        action_type = request.GET.get("action_type")
        if action_type:
            qs = qs.filter(action_type=action_type)

        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="admin_actions_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Timestamp", "Actor", "Role", "Action Type", "Target Entity", "Reason / Remarks"])
            for item in qs[:2000]:
                writer.writerow([
                    sanitize_csv_cell(item.action_reference),
                    sanitize_csv_cell(item.timestamp.isoformat() if item.timestamp else ""),
                    sanitize_csv_cell(item.actor.get_full_name() or item.actor.username if item.actor else "System"),
                    sanitize_csv_cell(item.actor_role or (item.actor.get_role_display() if item.actor else "")),
                    sanitize_csv_cell(item.get_action_type_display()),
                    sanitize_csv_cell(f"{item.affected_model}: {item.affected_record_display or item.affected_record_id}"),
                    sanitize_csv_cell(item.reason),
                ])
            return response

        paginator = Paginator(qs, config.default_page_size)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        for item in page_obj:
            entries.append({
                "reference": item.action_reference,
                "timestamp": item.timestamp,
                "actor": item.actor.get_full_name() or item.actor.username,
                "actor_role": item.actor_role or item.actor.get_role_display(),
                "action": item.get_action_type_display(),
                "target": f"{item.affected_model}: {item.affected_record_display or item.affected_record_id}",
                "reason": item.reason,
                "badge_class": "bg-primary",
            })
        total_count = paginator.count

    elif log_source == "CUSTODY_EVENTS":
        qs = CustodyEvent.objects.all().select_related("bag", "actor")
        if q:
            qs = qs.filter(Q(event_reference__icontains=q) | Q(bag__bag_id__icontains=q) | Q(remarks__icontains=q))

        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="custody_events_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Timestamp", "Actor", "Role", "Event Type", "Bag ID", "Remarks"])
            for item in qs[:2000]:
                writer.writerow([
                    sanitize_csv_cell(item.event_reference),
                    sanitize_csv_cell(item.timestamp.isoformat() if item.timestamp else ""),
                    sanitize_csv_cell(item.actor.username if item.actor else "System"),
                    sanitize_csv_cell(item.actor_role),
                    sanitize_csv_cell(item.get_event_type_display()),
                    sanitize_csv_cell(item.bag.bag_id if item.bag else ""),
                    sanitize_csv_cell(item.remarks or "Custody milestone logged"),
                ])
            return response

        paginator = Paginator(qs, config.default_page_size)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        for item in page_obj:
            entries.append({
                "reference": item.event_reference,
                "timestamp": item.timestamp,
                "actor": item.actor.username if item.actor else "System",
                "actor_role": item.actor_role,
                "action": item.get_event_type_display(),
                "target": f"Waste Bag: {item.bag.bag_id}",
                "reason": item.remarks or "Custody milestone logged",
                "badge_class": "bg-success",
            })
        total_count = paginator.count

    elif log_source == "FACILITY_AUDITS":
        qs = FacilityAuditHistory.objects.all().select_related("facility", "changed_by")
        if q:
            qs = qs.filter(Q(entity_reference__icontains=q) | Q(description__icontains=q) | Q(facility__name__icontains=q))

        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="facility_audits_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Timestamp", "Actor", "Role", "Action Type", "Facility", "Description"])
            for item in qs[:2000]:
                writer.writerow([
                    sanitize_csv_cell(item.entity_reference),
                    sanitize_csv_cell(item.timestamp.isoformat() if item.timestamp else ""),
                    sanitize_csv_cell(item.changed_by.username if item.changed_by else "System"),
                    sanitize_csv_cell(item.changed_by.get_role_display() if item.changed_by else ""),
                    sanitize_csv_cell(item.action_type),
                    sanitize_csv_cell(item.facility.name if item.facility else ""),
                    sanitize_csv_cell(item.description),
                ])
            return response

        paginator = Paginator(qs, config.default_page_size)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        for item in page_obj:
            entries.append({
                "reference": item.entity_reference,
                "timestamp": item.timestamp,
                "actor": item.changed_by.username,
                "actor_role": item.changed_by.get_role_display(),
                "action": item.action_type,
                "target": f"Facility: {item.facility.name}",
                "reason": item.description,
                "badge_class": "bg-info",
            })
        total_count = paginator.count

    elif log_source == "COLLECTION_HISTORY":
        qs = CollectionStatusHistory.objects.all().select_related("request", "changed_by")
        if q:
            qs = qs.filter(Q(request__reference_number__icontains=q) | Q(remarks__icontains=q))

        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="collection_history_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Timestamp", "Actor", "Role", "Status Transition", "Request Reference", "Remarks"])
            for item in qs[:2000]:
                writer.writerow([
                    sanitize_csv_cell(item.request.reference_number if item.request else ""),
                    sanitize_csv_cell(item.timestamp.isoformat() if item.timestamp else ""),
                    sanitize_csv_cell(item.changed_by.username if item.changed_by else "System"),
                    sanitize_csv_cell(item.changed_by.get_role_display() if item.changed_by else "Automated"),
                    sanitize_csv_cell(f"{item.previous_status} -> {item.new_status}"),
                    sanitize_csv_cell(item.request.reference_number if item.request else ""),
                    sanitize_csv_cell(item.remarks or "Status transition logged"),
                ])
            return response

        paginator = Paginator(qs, config.default_page_size)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        for item in page_obj:
            entries.append({
                "reference": item.request.reference_number,
                "timestamp": item.timestamp,
                "actor": item.changed_by.username if item.changed_by else "System",
                "actor_role": item.changed_by.get_role_display() if item.changed_by else "Automated",
                "action": f"{item.previous_status} → {item.new_status}",
                "target": f"Request: {item.request.reference_number}",
                "reason": item.remarks or "Status transition logged",
                "badge_class": "bg-warning text-dark",
            })
        total_count = paginator.count

    elif log_source == "REPORT_LOGS":
        qs = GeneratedReportLog.objects.all().select_related("generated_by")
        if q:
            qs = qs.filter(Q(report_reference__icontains=q) | Q(generated_by__username__icontains=q))

        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="report_logs_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Timestamp", "Generated By", "Role", "Export Format", "Report Type", "Details"])
            for item in qs[:2000]:
                writer.writerow([
                    sanitize_csv_cell(item.report_reference),
                    sanitize_csv_cell(item.generated_at.isoformat() if item.generated_at else ""),
                    sanitize_csv_cell(item.generated_by.username if item.generated_by else "System"),
                    sanitize_csv_cell(item.generated_by.get_role_display() if item.generated_by else ""),
                    sanitize_csv_cell(item.get_export_format_display()),
                    sanitize_csv_cell(item.get_report_type_display()),
                    sanitize_csv_cell(f"Records: {item.record_count} ({item.period_start} to {item.period_end})"),
                ])
            return response

        paginator = Paginator(qs, config.default_page_size)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        for item in page_obj:
            entries.append({
                "reference": item.report_reference,
                "timestamp": item.generated_at,
                "actor": item.generated_by.username,
                "actor_role": item.generated_by.get_role_display(),
                "action": f"Export {item.get_export_format_display()}",
                "target": item.get_report_type_display(),
                "reason": f"Records: {item.record_count} ({item.period_start} to {item.period_end})",
                "badge_class": "bg-secondary",
            })
        total_count = paginator.count

    context = {
        "log_source": log_source,
        "search_q": q,
        "entries": entries,
        "page_obj": page_obj,
        "total_count": total_count,
        "action_choices": AdminActionAudit.ActionType.choices,
    }
    return render(request, "accounts/admin/audit_logs.html", context)


# ==============================================================================
# 9. NOTIFICATION & SYSTEM ALERT MANAGEMENT
# ==============================================================================

@role_required(User.Role.ADMIN)
def admin_notifications_center(request):
    """
    Centralized administrative notification management console,
    system alert reviewer, and broadcast dispatcher.
    """
    config = PlatformSettings.get_settings()
    notifs_qs = Notification.objects.all().select_related("recipient").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    if q:
        notifs_qs = notifs_qs.filter(
            Q(title__icontains=q) |
            Q(message__icontains=q) |
            Q(recipient__username__icontains=q) |
            Q(reference_id__icontains=q)
        )

    type_filter = request.GET.get("type", "").strip()
    if type_filter:
        notifs_qs = notifs_qs.filter(notification_type=type_filter)

    status_filter = request.GET.get("read_status", "").strip()
    if status_filter == "unread":
        notifs_qs = notifs_qs.filter(is_read=False)
    elif status_filter == "read":
        notifs_qs = notifs_qs.filter(is_read=True)

    broadcast_form = BroadcastAlertForm()

    paginator = Paginator(notifs_qs, config.default_page_size)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Metric counts
    total_alerts = Notification.objects.count()
    unread_alerts = Notification.objects.filter(is_read=False).count()
    system_alerts = Notification.objects.filter(notification_type=Notification.NotificationType.SYSTEM_ALERT).count()

    context = {
        "page_obj": page_obj,
        "search_q": q,
        "type_filter": type_filter,
        "status_filter": status_filter,
        "type_choices": Notification.NotificationType.choices,
        "total_alerts": total_alerts,
        "unread_alerts": unread_alerts,
        "system_alerts": system_alerts,
        "broadcast_form": broadcast_form,
    }
    return render(request, "accounts/admin/notifications_center.html", context)


@require_POST
@role_required(User.Role.ADMIN)
def admin_mark_notification_reviewed(request, notification_id):
    """
    Marks a system alert or notification as reviewed / read.
    """
    notif = get_object_or_404(Notification, id=notification_id)
    notif.is_read = True
    notif.save(update_fields=["is_read"])
    messages.success(request, f"Alert '{notif.title}' marked as reviewed.")
    return redirect("admin_notifications_center")


@require_POST
@role_required(User.Role.ADMIN)
def admin_broadcast_alert(request):
    """
    Dispatches a broadcast announcement to stakeholders with an audit log entry.
    """
    form = BroadcastAlertForm(request.POST)
    if form.is_valid():
        try:
            count = broadcast_system_notification(
                admin_user=request.user,
                target_role=form.cleaned_data["target_role"],
                title=form.cleaned_data["title"],
                message=form.cleaned_data["message"],
            )
            messages.success(request, f"Announcement broadcasted successfully to {count} active recipient(s).")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "Invalid broadcast announcement parameters.")

    return redirect("admin_notifications_center")
