from functools import wraps
from django.contrib import messages
from django.contrib.auth import login as auth_login, views as auth_views
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from core.utils import quantize_weight
from notifications.models import Notification
from notifications.services import notify_administrators
from .forms import HospitalRegistrationForm
from .models import PlatformSettings, User


class CustomLoginView(auth_views.LoginView):
    """
    Role-aware LoginView rendering a styled login form with remember-me support.
    Redirects authenticated users strictly to their designated role dashboard.
    """
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        # Handle "Remember me" option
        remember_me = self.request.POST.get("remember_me")
        if not remember_me:
            # Session expires on browser close
            self.request.session.set_expiry(0)
        else:
            # Session persists for default age (e.g. 2 weeks)
            self.request.session.set_expiry(1209600)
        return super().form_valid(form)

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to

        user = self.request.user
        if user.is_authenticated:
            if user.role == User.Role.ADMIN:
                return reverse_lazy("dashboard_admin")
            elif user.role == User.Role.HOSPITAL:
                return reverse_lazy("dashboard_hospital")
            elif user.role == User.Role.COLLECTOR:
                return reverse_lazy("dashboard_collector")
            elif user.role == User.Role.FACILITY:
                return reverse_lazy("dashboard_facility")
        return reverse_lazy("home")


def register(request):
    """
    Public registration flow restricted strictly to Hospital and Healthcare Clinic staff.
    Public self-registration for ADMIN, COLLECTOR, or FACILITY is not permitted.
    """
    if request.user.is_authenticated:
        return redirect("dashboard_redirect")

    if request.method == "POST":
        form = HospitalRegistrationForm(request.POST)
        if form.is_valid():
            platform_settings = PlatformSettings.get_settings()
            requires_approval = bool(platform_settings.hospital_requires_approval)

            user = form.save(commit=False)
            user.account_status = (
                User.AccountStatus.PENDING_APPROVAL
                if requires_approval
                else User.AccountStatus.ACTIVE
            )
            user.is_active = True
            user.save()

            if platform_settings.enable_in_app_notifications:
                org_label = user.organization_name or user.username
                if requires_approval:
                    notify_administrators(
                        notification_type=Notification.NotificationType.SYSTEM_ALERT,
                        title=f"Hospital Approval Required: {org_label}",
                        message=(
                            f"{org_label} (@{user.username}) registered and is awaiting "
                            f"administrative verification."
                        ),
                        reference_id=f"HOSP-{user.id}",
                        link=reverse("admin_approvals_queue"),
                    )
                else:
                    notify_administrators(
                        notification_type=Notification.NotificationType.SYSTEM_ALERT,
                        title=f"New Hospital Registered: {org_label}",
                        message=(
                            f"{org_label} (@{user.username}) completed self-registration "
                            f"and is now active on the platform."
                        ),
                        reference_id=f"HOSP-{user.id}",
                        link=reverse("admin_hospital_detail", args=[user.id]),
                    )

            auth_login(request, user)
            if requires_approval:
                messages.warning(
                    request,
                    f"Registration submitted for {user.organization_name}. "
                    f"Your hospital account is pending administrative approval before operational modules unlock."
                )
            else:
                messages.success(
                    request,
                    f"Registration successful! Welcome to MedWaste Connect, {user.organization_name}."
                )
            return redirect("dashboard_hospital")
    else:
        form = HospitalRegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def role_required(*allowed_roles, allow_pending=False):
    """
    Decorator to protect views:
    - If unauthenticated: redirect to login.
    - If user is inactive or suspended: raise PermissionDenied (HTTP 403).
    - If user role not in allowed_roles: raise PermissionDenied (HTTP 403).
    - If user is pending approval (unless allow_pending=True): raise PermissionDenied (HTTP 403).
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.is_active or getattr(request.user, "is_suspended", False):
                raise PermissionDenied("Your account is inactive or suspended. Please contact platform administration.")
            if request.user.role not in allowed_roles:
                raise PermissionDenied("You do not have permission to access this role dashboard.")
            if not allow_pending and getattr(request.user, "is_pending_approval", False):
                raise PermissionDenied(
                    "Your hospital account is pending administrative verification before accessing operational modules."
                )
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


def dashboard_redirect(request):
    """
    Dispatches logged-in users directly to their designated role dashboard.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    if not request.user.is_active or getattr(request.user, "is_suspended", False):
        raise PermissionDenied("Your account is inactive or suspended. Please contact platform administration.")

    role = request.user.role
    if role == User.Role.ADMIN:
        return redirect("dashboard_admin")
    elif role == User.Role.HOSPITAL:
        return redirect("dashboard_hospital")
    elif role == User.Role.COLLECTOR:
        return redirect("dashboard_collector")
    elif role == User.Role.FACILITY:
        return redirect("dashboard_facility")
    return redirect("home")


@role_required(User.Role.ADMIN)
def admin_dashboard(request):
    from collection.models import CollectionRequest
    incoming_count = CollectionRequest.objects.filter(
        status__in=[CollectionRequest.Status.REQUESTED, CollectionRequest.Status.UNDER_REVIEW]
    ).count()
    context = {
        "role_display": "Administrator",
        "incoming_requests_count": incoming_count,
    }
    return render(request, "accounts/dashboard.html", context)


@role_required(User.Role.HOSPITAL, allow_pending=True)
def hospital_dashboard(request):
    from collection.models import CollectionRequest
    from tracking.models import CustodyDiscrepancy
    from waste.models import WasteBag

    bags_qs = WasteBag.objects.filter(hospital=request.user)
    requests_qs = CollectionRequest.objects.filter(hospital=request.user)

    active_requests = requests_qs.filter(status__in=CollectionRequest.ACTIVE_STATUSES).count()
    total_bags_count = bags_qs.count()
    registered_bags_count = bags_qs.filter(status=WasteBag.Status.REGISTERED).count()
    in_transit_bags_count = bags_qs.filter(status=WasteBag.Status.IN_TRANSIT).count()
    treated_disposed_count = bags_qs.filter(
        status__in=[WasteBag.Status.TREATED, WasteBag.Status.DISPOSED]
    ).count()
    total_waste_weight = quantize_weight(bags_qs.aggregate(total=Sum("weight")).get("total"))
    completed_requests_count = requests_qs.filter(status=CollectionRequest.Status.COMPLETED).count()
    open_discrepancies_count = (
        CustodyDiscrepancy.objects.filter(
            Q(bag__hospital=request.user)
            | Q(collection_request__hospital=request.user)
            | Q(reception_manifest__collection_request__hospital=request.user),
            status__in=[
                CustodyDiscrepancy.Status.OPEN,
                CustodyDiscrepancy.Status.UNDER_REVIEW,
            ],
        )
        .distinct()
        .count()
    )

    recent_bags = bags_qs.select_related("category", "batch").order_by("-registered_at")[:5]
    recent_requests = requests_qs.select_related(
        "assigned_collector", "destination_facility"
    ).order_by("-created_at")[:5]

    context = {
        "role_display": "Hospital",
        "is_pending_approval": request.user.is_pending_approval,
        "active_requests_count": active_requests,
        "total_bags_count": total_bags_count,
        "registered_bags_count": registered_bags_count,
        "in_transit_bags_count": in_transit_bags_count,
        "treated_disposed_count": treated_disposed_count,
        "total_waste_weight": total_waste_weight,
        "completed_requests_count": completed_requests_count,
        "open_discrepancies_count": open_discrepancies_count,
        "recent_bags": recent_bags,
        "recent_requests": recent_requests,
    }
    return render(request, "accounts/dashboard.html", context)


@role_required(User.Role.COLLECTOR)
def collector_dashboard(request):
    from collection.models import CollectionRequest

    assigned_qs = CollectionRequest.objects.filter(assigned_collector=request.user)
    pending_acceptance = assigned_qs.filter(status=CollectionRequest.Status.ASSIGNED).count()
    active_pickups = assigned_qs.filter(
        status__in=[CollectionRequest.Status.ACCEPTED, CollectionRequest.Status.PICKUP_IN_PROGRESS]
    ).count()
    in_transit_count = assigned_qs.filter(status=CollectionRequest.Status.COLLECTED).count()
    completed_pickups = assigned_qs.filter(status=CollectionRequest.Status.COMPLETED).count()
    total_collected_weight = quantize_weight(
        assigned_qs.filter(
            status__in=[CollectionRequest.Status.COLLECTED, CollectionRequest.Status.COMPLETED]
        ).aggregate(total=Sum("total_weight")).get("total")
    )
    recent_assignments = assigned_qs.select_related(
        "hospital", "destination_facility"
    ).order_by("-updated_at")[:5]

    context = {
        "role_display": "Collector",
        "pending_acceptance_count": pending_acceptance,
        "active_pickups_count": active_pickups,
        "in_transit_count": in_transit_count,
        "completed_pickups_count": completed_pickups,
        "total_collected_weight": total_collected_weight,
        "recent_assignments": recent_assignments,
    }
    return render(request, "accounts/dashboard.html", context)


@role_required(User.Role.FACILITY)
def facility_dashboard(request):
    from collection.models import CollectionRequest
    from facilities.services import get_facility_capacity_metrics
    from treatment.models import FacilityReceptionManifest, TreatmentBatch

    facility = getattr(request.user, "managed_facility", None)
    facility_metrics = get_facility_capacity_metrics(facility) if facility else None

    pending_incoming_count = 0
    active_receptions_count = 0
    active_batches_count = 0
    completed_batches_count = 0
    total_disposals_count = 0
    total_received_weight = quantize_weight(0)
    recent_batches = []

    if facility:
        pending_incoming_count = CollectionRequest.objects.filter(
            Q(destination_facility=facility) | Q(destination_facility__isnull=True),
            status__in=[
                CollectionRequest.Status.COLLECTED,
                CollectionRequest.Status.PICKUP_IN_PROGRESS,
                CollectionRequest.Status.COMPLETED,
            ],
        ).exclude(facility_receptions__facility=facility).count()
        active_receptions_count = facility.reception_manifests.filter(
            status__in=[
                FacilityReceptionManifest.Status.RECEIVED,
                FacilityReceptionManifest.Status.PARTIALLY_ACCEPTED,
                FacilityReceptionManifest.Status.DISCREPANCY_REPORTED,
            ]
        ).count()
        active_batches_count = facility.treatment_batches.filter(
            status__in=[TreatmentBatch.Status.PENDING, TreatmentBatch.Status.IN_PROGRESS]
        ).count()
        completed_batches_count = facility.treatment_batches.filter(
            status=TreatmentBatch.Status.COMPLETED
        ).count()
        total_disposals_count = facility.disposal_records.count()
        total_received_weight = quantize_weight(
            facility.reception_manifests.aggregate(total=Sum("gross_received_weight")).get("total")
        )
        recent_batches = facility.treatment_batches.order_by("-created_at")[:5]

    context = {
        "role_display": "Facility",
        "facility": facility,
        "facility_metrics": facility_metrics,
        "pending_incoming_count": pending_incoming_count,
        "active_receptions_count": active_receptions_count,
        "active_batches_count": active_batches_count,
        "completed_batches_count": completed_batches_count,
        "total_disposals_count": total_disposals_count,
        "total_received_weight": total_received_weight,
        "recent_batches": recent_batches,
    }
    return render(request, "accounts/dashboard.html", context)
