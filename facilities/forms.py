from django import forms
from django.contrib.auth import get_user_model
from waste.models import WasteCategory
from .models import TreatmentFacility

User = get_user_model()


class FacilityRegistrationForm(forms.ModelForm):
    """
    Form for Administrators to onboard and register a new Treatment Facility.
    """
    class Meta:
        model = TreatmentFacility
        fields = [
            "name",
            "operator_user",
            "facility_type",
            "license_number",
            "address",
            "district",
            "state",
            "postal_code",
            "contact_person",
            "contact_phone",
            "contact_email",
            "daily_capacity_kg",
            "operating_days",
            "operating_hours",
            "supports_incineration",
            "supports_autoclaving",
            "supports_shredding",
            "supports_chemical_disinfection",
            "supports_encapsulation",
            "accepted_categories",
            "approval_status",
            "operational_availability",
            "is_active",
            "admin_notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Apex Bio-Clean Environmental Solutions"}),
            "operator_user": forms.Select(attrs={"class": "form-select"}),
            "facility_type": forms.Select(attrs={"class": "form-select"}),
            "license_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. SPCB/CTO/BMW/2026/8941"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Industrial Estate, Plot 12B"}),
            "district": forms.TextInput(attrs={"class": "form-control", "placeholder": "District / City"}),
            "state": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "PIN code"}),
            "contact_person": forms.TextInput(attrs={"class": "form-control", "placeholder": "Full Name"}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "+91 XXXXX XXXXX"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "contact@facility.example.com"}),
            "daily_capacity_kg": forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 5000.00", "step": "0.01"}),
            "operating_days": forms.TextInput(attrs={"class": "form-control"}),
            "operating_hours": forms.TextInput(attrs={"class": "form-control"}),
            "supports_incineration": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "supports_autoclaving": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "supports_shredding": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "supports_chemical_disinfection": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "supports_encapsulation": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "accepted_categories": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "approval_status": forms.Select(attrs={"class": "form-select"}),
            "operational_availability": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "admin_notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit operator_user choices to users with FACILITY role without a facility
        self.fields["operator_user"].queryset = User.objects.filter(
            role=User.Role.FACILITY
        ).order_by("username")
        self.fields["accepted_categories"].queryset = WasteCategory.objects.filter(is_active=True).order_by("display_order")


class FacilityStatusUpdateForm(forms.ModelForm):
    """
    Administrative form to toggle approval, operational availability, and inspection notes.
    """
    class Meta:
        model = TreatmentFacility
        fields = [
            "approval_status",
            "operational_availability",
            "is_active",
            "admin_notes",
        ]
        widgets = {
            "approval_status": forms.Select(attrs={"class": "form-select"}),
            "operational_availability": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "admin_notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

