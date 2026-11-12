from django import forms
from .models import CustodyDiscrepancy, CustodyEvent


class DiscrepancyReportForm(forms.ModelForm):
    """
    Form to report operational exceptions and custody discrepancies.
    """
    class Meta:
        model = CustodyDiscrepancy
        fields = ["discrepancy_type", "severity", "description"]
        widgets = {
            "discrepancy_type": forms.Select(attrs={
                "class": "form-select",
                "aria-label": "Discrepancy Type",
            }),
            "severity": forms.Select(attrs={
                "class": "form-select",
                "aria-label": "Severity Level",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Provide exhaustive operational details of the observed issue, damaged packaging, or missing inventory...",
                "required": True,
            }),
        }

    def clean_description(self):
        desc = self.cleaned_data.get("description", "").strip()
        if len(desc) < 10:
            raise forms.ValidationError("Please provide a more detailed description (at least 10 characters).")
        return desc


class DiscrepancyResolveForm(forms.ModelForm):
    """
    Form to resolve an existing discrepancy with required investigation notes.
    """
    class Meta:
        model = CustodyDiscrepancy
        fields = ["status", "resolution_notes"]
        widgets = {
            "status": forms.Select(
                choices=[
                    (CustodyDiscrepancy.Status.RESOLVED, "Resolved — Action Completed"),
                    (CustodyDiscrepancy.Status.CLOSED, "Closed — Formal Investigation Concluded"),
                    (CustodyDiscrepancy.Status.UNDER_REVIEW, "Under Review — Investigation Ongoing"),
                ],
                attrs={"class": "form-select"},
            ),
            "resolution_notes": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Detail corrective action taken, reconciliation verification, or containment measures...",
                "required": True,
            }),
        }

    def clean_resolution_notes(self):
        notes = self.cleaned_data.get("resolution_notes", "").strip()
        if len(notes) < 10:
            raise forms.ValidationError("Mandatory resolution notes must be at least 10 characters.")
        return notes


class CustodyCorrectionForm(forms.Form):
    """
    Compensating correction form for administrators to log an immutable audit adjustment.
    """
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Reason for audit adjustment or weighment calibration correction...",
            "required": True,
        }),
        help_text="Required rationale explaining why this compensating event is being appended to the custody ledger."
    )
    weight_at_event = forms.DecimalField(
        required=False,
        max_digits=8,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. 5.25",
            "step": "0.01",
        }),
        help_text="Recalibrated net weight (KG), if this is a weighment reconciliation."
    )
    evidence_document = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
        help_text="Upload signed weighment slip, incident report, or lab report."
    )

