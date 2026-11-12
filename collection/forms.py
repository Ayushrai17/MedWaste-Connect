from datetime import datetime
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from waste.models import WasteBag
from .models import CollectionRequest
from .services import get_eligible_bags_for_hospital, check_collector_overlap

User = get_user_model()


class CollectionRequestCreateForm(forms.ModelForm):
    """
    Form for hospitals to create a collection request.
    Restricts selectable bags strictly to the requesting hospital's eligible, uncollected inventory.
    """
    bags = forms.ModelMultipleChoiceField(
        queryset=WasteBag.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        help_text="Select one or more eligible biomedical waste bags for pickup."
    )

    class Meta:
        model = CollectionRequest
        fields = [
            "preferred_pickup_date",
            "preferred_pickup_time",
            "alternative_pickup_date",
            "alternative_pickup_time",
            "pickup_address",
            "contact_person",
            "contact_phone",
            "special_instructions",
            "operational_notes",
        ]
        widgets = {
            "preferred_pickup_date": forms.DateInput(attrs={
                "class": "form-control",
                "type": "date",
            }),
            "preferred_pickup_time": forms.TimeInput(attrs={
                "class": "form-control",
                "type": "time",
            }),
            "alternative_pickup_date": forms.DateInput(attrs={
                "class": "form-control",
                "type": "date",
            }),
            "alternative_pickup_time": forms.TimeInput(attrs={
                "class": "form-control",
                "type": "time",
            }),
            "pickup_address": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. Building C, West Loading Dock, Basement Bay 2",
            }),
            "contact_person": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Authorized hospital waste manager or nurse in charge",
            }),
            "contact_phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "+91 98765 43210",
            }),
            "special_instructions": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Security check instructions, dock access protocol, or decontamination notes...",
            }),
            "operational_notes": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Optional internal operational reference or ward handover memo...",
            }),
        }

    def __init__(self, hospital_user, *args, **kwargs):
        self.hospital_user = hospital_user
        super().__init__(*args, **kwargs)

        today_str = timezone.localdate().isoformat()
        self.fields["preferred_pickup_date"].widget.attrs["min"] = today_str
        self.fields["alternative_pickup_date"].widget.attrs["min"] = today_str

        # Restrict selectable bags strictly to eligible bags of this hospital
        eligible_bags = get_eligible_bags_for_hospital(hospital_user)
        self.fields["bags"].queryset = eligible_bags

        # Populate defaults safely from user profile or most recent pickup request if blank
        if not self.is_bound:
            if not self.initial.get("pickup_address"):
                profile_address = (getattr(hospital_user, "address", "") or "").strip()
                if not profile_address:
                    profile_address = (
                        CollectionRequest.objects.filter(hospital=hospital_user)
                        .exclude(pickup_address="")
                        .order_by("-created_at")
                        .values_list("pickup_address", flat=True)
                        .first()
                        or ""
                    )
                if profile_address:
                    self.initial["pickup_address"] = profile_address
            phone = (getattr(hospital_user, "phone", "") or "").strip()
            if phone and not self.initial.get("contact_phone"):
                self.initial["contact_phone"] = phone
            contact_default = (hospital_user.get_full_name() or getattr(hospital_user, "organization_name", "") or "").strip()
            if contact_default and not self.initial.get("contact_person"):
                self.initial["contact_person"] = contact_default

    def clean_preferred_pickup_date(self):
        pref_date = self.cleaned_data.get("preferred_pickup_date")
        if pref_date and pref_date < timezone.localdate():
            raise ValidationError("Preferred pickup date cannot be in the past.")
        return pref_date

    def clean_alternative_pickup_date(self):
        alt_date = self.cleaned_data.get("alternative_pickup_date")
        if alt_date and alt_date < timezone.localdate():
            raise ValidationError("Alternative pickup date cannot be in the past.")
        return alt_date

    def clean_bags(self):
        selected = self.cleaned_data.get("bags")
        if not selected or not selected.exists():
            raise ValidationError("Please select at least one registered biomedical waste bag for collection.")
        # Ensure all selected bags belong to this hospital
        for b in selected:
            if b.hospital_id != self.hospital_user.id:
                raise ValidationError("Selected bags must belong to your hospital.")
            if b.status != WasteBag.Status.REGISTERED:
                raise ValidationError(f"Bag {b.bag_id} has custody status '{b.get_status_display()}' and cannot be scheduled.")
        return selected


class AdminScheduleAssignForm(forms.Form):
    """
    Form for platform administrators to confirm the collection schedule and assign a collector.
    """
    confirmed_pickup_date = forms.DateField(
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        required=True,
        help_text="Confirmed collection date."
    )
    confirmed_pickup_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
        required=True,
        help_text="Confirmed collection time window."
    )
    assigned_collector = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.COLLECTOR, is_active=True).order_by("organization_name", "username"),
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
        help_text="Select authorized collector / transport driver."
    )
    destination_facility = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "form-select"}),
        required=False,
        help_text="Designate destination Treatment Facility (CBWTF)."
    )
    admin_notes = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "placeholder": "Dispatch notes, vehicle instructions, or schedule adjustments...",
        }),
        required=False,
    )

    def __init__(self, collection_request, *args, **kwargs):
        from facilities.models import TreatmentFacility
        self.collection_request = collection_request
        super().__init__(*args, **kwargs)

        today_str = timezone.localdate().isoformat()
        self.fields["confirmed_pickup_date"].widget.attrs["min"] = today_str
        self.fields["destination_facility"].queryset = TreatmentFacility.objects.filter(
            is_active=True,
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED
        ).order_by("name")

        # Pre-populate with confirmed date/time or hospital's preferred schedule
        if not self.is_bound:
            self.initial["confirmed_pickup_date"] = (
                collection_request.confirmed_pickup_date or collection_request.preferred_pickup_date
            )
            self.initial["confirmed_pickup_time"] = (
                collection_request.confirmed_pickup_time or collection_request.preferred_pickup_time
            )
            if collection_request.assigned_collector:
                self.initial["assigned_collector"] = collection_request.assigned_collector
            if collection_request.destination_facility:
                self.initial["destination_facility"] = collection_request.destination_facility
            if collection_request.admin_notes:
                self.initial["admin_notes"] = collection_request.admin_notes

    def clean_confirmed_pickup_date(self):
        conf_date = self.cleaned_data.get("confirmed_pickup_date")
        if conf_date and conf_date < timezone.localdate():
            raise ValidationError("Confirmed pickup date cannot be in the past.")
        return conf_date

    def clean(self):
        cleaned_data = super().clean()
        conf_date = cleaned_data.get("confirmed_pickup_date")
        conf_time = cleaned_data.get("confirmed_pickup_time")
        collector = cleaned_data.get("assigned_collector")

        if conf_date and conf_time and collector:
            is_overlapping, overlapping_req = check_collector_overlap(
                collector_user=collector,
                pickup_date=conf_date,
                pickup_time=conf_time,
                exclude_request_id=self.collection_request.id,
            )
            if is_overlapping:
                raise ValidationError(
                    f"Collector '{collector.get_full_name() or collector.username}' is already assigned to active request "
                    f"{overlapping_req.reference_number} at {overlapping_req.confirmed_pickup_time.strftime('%H:%M')} on {conf_date}."
                )
        return cleaned_data


class AdminRejectForm(forms.Form):
    """
    Form for administrator to reject a collection request with explanation.
    """
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Provide clear regulatory, logistical, or documentation reasons for rejection...",
        }),
        required=True,
    )


class CollectorRejectForm(forms.Form):
    """
    Form for collector to reject an assigned collection request.
    """
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "State mandatory reason for declining assignment (e.g. vehicle mechanical fault, capacity full, route unavailable)...",
        }),
        required=True,
    )


class CollectorActionForm(forms.Form):
    """
    Form for collector to add remarks when starting pickup or completing collection.
    """
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "placeholder": "Enter field observations, gross vehicle weight, dock handover remarks...",
        }),
        required=False,
    )

