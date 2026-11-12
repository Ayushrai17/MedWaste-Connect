from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from waste.models import WasteBag
from .models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
)


class FacilityReceptionForm(forms.ModelForm):
    """
    Gate check-in form when a collection consignment enters the facility.
    """
    class Meta:
        model = FacilityReceptionManifest
        fields = [
            "gross_received_weight",
            "capacity_override_authorized",
            "capacity_override_reason",
            "remarks",
        ]
        widgets = {
            "gross_received_weight": forms.NumberInput(attrs={
                "class": "form-control form-control-lg fw-bold",
                "placeholder": "0.00",
                "step": "0.01",
            }),
            "capacity_override_authorized": forms.CheckboxInput(attrs={
                "class": "form-check-input",
                "id": "id_capacity_override_authorized",
            }),
            "capacity_override_reason": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Mandatory explanation if capacity override is authorized by administration...",
            }),
            "remarks": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Vehicle registration, driver remarks, gate seal condition...",
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        override = cleaned_data.get("capacity_override_authorized")
        reason = cleaned_data.get("capacity_override_reason")
        if override and (not reason or not reason.strip()):
            raise ValidationError({
                "capacity_override_reason": "A mandatory explanation is required when overriding plant capacity."
            })
        return cleaned_data


class BagInspectionDecisionForm(forms.Form):
    """
    Form to verify and record an acceptance/rejection decision on an individual waste bag.
    """
    actual_weight = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"})
    )
    packaging_condition = forms.ChoiceField(
        choices=FacilityBagReceipt.PackagingCondition.choices,
        widget=forms.Select(attrs={"class": "form-select"})
    )
    acceptance_decision = forms.ChoiceField(
        choices=FacilityBagReceipt.AcceptanceDecision.choices,
        widget=forms.Select(attrs={"class": "form-select"})
    )
    rejection_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "placeholder": "Mandatory if rejected: e.g. Improperly segregated sharps, leaking container...",
        })
    )
    reception_remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "placeholder": "Dock observation remarks...",
        })
    )

    def clean(self):
        cleaned_data = super().clean()
        decision = cleaned_data.get("acceptance_decision")
        reason = cleaned_data.get("rejection_reason")
        if decision == FacilityBagReceipt.AcceptanceDecision.REJECTED and (not reason or not reason.strip()):
            raise ValidationError({
                "rejection_reason": "A mandatory rejection explanation must be provided when rejecting a bag."
            })
        return cleaned_data


class TreatmentBatchCreateForm(forms.ModelForm):
    """
    Form for facility operators to configure and schedule a treatment processing batch.
    """
    bags = forms.ModelMultipleChoiceField(
        queryset=WasteBag.objects.none(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text="Select verified, accepted bags awaiting treatment."
    )

    class Meta:
        model = TreatmentBatch
        fields = [
            "treatment_method",
            "equipment_reference",
            "chamber_temperature_celsius",
            "chamber_pressure_psi",
            "cycle_duration_minutes",
            "treatment_remarks",
        ]
        widgets = {
            "treatment_method": forms.Select(attrs={"class": "form-select"}),
            "equipment_reference": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. Primary Incinerator Chamber #1 / Autoclave Unit B",
            }),
            "chamber_temperature_celsius": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. 1050.0",
                "step": "0.1",
            }),
            "chamber_pressure_psi": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. 15.0",
                "step": "0.1",
            }),
            "cycle_duration_minutes": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. 45",
            }),
            "treatment_remarks": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Operational remarks, cycle pre-heat notes...",
            }),
        }

    def __init__(self, facility, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        # Limit method choices to what the facility supports
        available_methods = []
        if facility.supports_incineration:
            available_methods.append((TreatmentBatch.TreatmentMethod.INCINERATION, "High-Temperature Incineration (>1050°C)"))
        if facility.supports_autoclaving:
            available_methods.append((TreatmentBatch.TreatmentMethod.AUTOCLAVING, "High-Pressure Steam Autoclaving"))
        if facility.supports_shredding:
            available_methods.append((TreatmentBatch.TreatmentMethod.SHREDDING, "Mechanical Sterilized Shredding"))
        if facility.supports_chemical_disinfection:
            available_methods.append((TreatmentBatch.TreatmentMethod.CHEMICAL_DISINFECTION, "Chemical Disinfection & Neutralization"))
        if facility.supports_encapsulation:
            available_methods.append((TreatmentBatch.TreatmentMethod.ENCAPSULATION, "Deep Burial / Encapsulation"))

        self.fields["treatment_method"].choices = available_methods or TreatmentBatch.TreatmentMethod.choices

        # Only bags that are ACCEPTED at this facility and not yet in any active/completed treatment batch
        active_treated_bag_ids = TreatmentBatch.objects.filter(
            facility=facility,
            status__in=[
                TreatmentBatch.Status.PENDING,
                TreatmentBatch.Status.IN_PROGRESS,
                TreatmentBatch.Status.COMPLETED,
            ]
        ).values_list("bags__id", flat=True)

        accepted_bag_ids = FacilityBagReceipt.objects.filter(
            reception_manifest__facility=facility,
            acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED
        ).exclude(bag_id__in=active_treated_bag_ids).values_list("bag_id", flat=True)

        self.fields["bags"].queryset = WasteBag.objects.filter(
            id__in=accepted_bag_ids
        ).select_related("category", "hospital").order_by("category__display_order", "-registered_at")


class TreatmentBatchCompleteForm(forms.Form):
    """
    Form to record final parameters upon concluding a treatment batch cycle.
    """
    chamber_temperature_celsius = forms.DecimalField(
        required=False,
        max_digits=6,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Recorded Temperature °C", "step": "0.1"})
    )
    chamber_pressure_psi = forms.DecimalField(
        required=False,
        max_digits=6,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Recorded Pressure PSI", "step": "0.1"})
    )
    cycle_duration_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Duration in minutes"})
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Scrubber check, post-treatment residue appearance..."})
    )


class TreatmentBatchHoldFailForm(forms.Form):
    """
    Form to record emergency hold or cycle failure.
    """
    new_status = forms.ChoiceField(
        choices=[
            (TreatmentBatch.Status.ON_HOLD, "Place On Hold / Paused"),
            (TreatmentBatch.Status.FAILED, "Mark Cycle as Failed"),
        ],
        widget=forms.Select(attrs={"class": "form-select"})
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Mandatory explanation (e.g. burner malfunction, power outage, steam pressure drop)...",
        })
    )

    def clean_reason(self):
        r = self.cleaned_data.get("reason", "").strip()
        if not r:
            raise ValidationError("A clear explanation is required.")
        return r


class DisposalRecordCreateForm(forms.ModelForm):
    """
    Form to record final safe disposition of treated residue.
    """
    class Meta:
        model = DisposalRecord
        fields = [
            "final_disposal_method",
            "disposal_location",
            "disposal_date",
            "disposal_time",
            "supporting_document",
            "remarks",
        ]
        widgets = {
            "final_disposal_method": forms.Select(attrs={"class": "form-select"}),
            "disposal_location": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. Hazardous Landfill Sector 9, Plot 4B / GreenCycle Recyclers",
            }),
            "disposal_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "disposal_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "supporting_document": forms.FileInput(attrs={"class": "form-control"}),
            "remarks": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Vehicle weighment slip number, landfill pit depth, manifest sign-off...",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("disposal_date"):
            self.initial["disposal_date"] = timezone.localdate()
        if not self.initial.get("disposal_time"):
            self.initial["disposal_time"] = timezone.localtime().strftime("%H:%M")


class DisposalVerifyForm(forms.Form):
    """
    Administrator compliance verification form.
    """
    verification_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Statutory audit remarks, weighment slip verified against CPCB Schedule I...",
        })
    )

