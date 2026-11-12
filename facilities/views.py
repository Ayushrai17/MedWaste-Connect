from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.views import role_required
from .models import TreatmentFacility
from .forms import FacilityRegistrationForm, FacilityStatusUpdateForm
from .services import (
    register_treatment_facility,
    update_facility_status,
    get_facility_capacity_metrics,
)

User = get_user_model()


@role_required(User.Role.ADMIN)
def admin_facility_list(request):
    """
    Administrative overview of all registered Treatment Facilities.
    """
    facilities_qs = TreatmentFacility.objects.all().select_related("operator_user").prefetch_related("accepted_categories")

    # Filter by approval status
    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        facilities_qs = facilities_qs.filter(approval_status=status_filter)

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        facilities_qs = facilities_qs.filter(
            models.Q(name__icontains=q) |
            models.Q(facility_code__icontains=q) |
            models.Q(district__icontains=q) |
            models.Q(license_number__icontains=q)
        )

    # Attach live metrics for each facility
    facility_data = []
    for fac in facilities_qs:
        metrics = get_facility_capacity_metrics(fac)
        facility_data.append({
            "facility": fac,
            "metrics": metrics,
        })

    context = {
        "facilities": facility_data,
        "selected_status": status_filter,
        "search_q": q,
        "status_choices": TreatmentFacility.ApprovalStatus.choices,
    }
    return render(request, "facilities/admin/facility_list.html", context)


@role_required(User.Role.ADMIN)
def admin_facility_create(request):
    """
    Administrative form to onboard a new treatment facility.
    """
    if request.method == "POST":
        form = FacilityRegistrationForm(request.POST)
        if form.is_valid():
            try:
                facility = register_treatment_facility(
                    admin_user=request.user,
                    form_data=form.cleaned_data,
                    accepted_categories=form.cleaned_data.get("accepted_categories"),
                )
                messages.success(request, f"Treatment Facility '{facility.name}' registered successfully ({facility.facility_code}).")
                return redirect("facilities:admin_facility_detail", facility_code=facility.facility_code)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
    else:
        form = FacilityRegistrationForm()

    context = {
        "form": form,
    }
    return render(request, "facilities/admin/facility_create.html", context)


@role_required(User.Role.ADMIN)
def admin_facility_detail(request, facility_code):
    """
    Administrative facility profile, capacity inspection, and status management.
    """
    facility = get_object_or_404(
        TreatmentFacility.objects.select_related("operator_user").prefetch_related("accepted_categories"),
        facility_code=facility_code
    )

    if request.method == "POST":
        status_form = FacilityStatusUpdateForm(request.POST, instance=facility)
        if status_form.is_valid():
            try:
                update_facility_status(
                    admin_user=request.user,
                    facility=facility,
                    approval_status=status_form.cleaned_data["approval_status"],
                    operational_availability=status_form.cleaned_data["operational_availability"],
                    is_active=status_form.cleaned_data["is_active"],
                    admin_notes=status_form.cleaned_data.get("admin_notes", ""),
                )
                messages.success(request, f"Facility status updated successfully.")
                return redirect("facilities:admin_facility_detail", facility_code=facility_code)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
    else:
        status_form = FacilityStatusUpdateForm(instance=facility)

    metrics = get_facility_capacity_metrics(facility)

    # Recent activity
    recent_receptions = facility.reception_manifests.all().select_related("collection_request")[:5]
    recent_batches = facility.treatment_batches.all()[:5]
    recent_disposals = facility.disposal_records.all()[:5]
    recent_audits = facility.audit_logs.all().select_related("changed_by")[:10]

    context = {
        "facility": facility,
        "metrics": metrics,
        "status_form": status_form,
        "recent_receptions": recent_receptions,
        "recent_batches": recent_batches,
        "recent_disposals": recent_disposals,
        "recent_audits": recent_audits,
    }
    return render(request, "facilities/admin/facility_detail.html", context)
