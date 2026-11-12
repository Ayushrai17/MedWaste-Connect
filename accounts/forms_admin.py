from django import forms
from django.core.exceptions import ValidationError
from accounts.models import User, PlatformSettings
from reports.models import ComplianceThresholdConfig


class UserSuspensionForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Provide statutory, compliance, or operational justification for this account suspension...",
            "required": True,
        }),
        label="Suspension Justification",
        help_text="Mandatory audit trail reason. This explanation will be recorded in the audit log and notified to the user."
    )

    def clean_reason(self):
        reason = self.cleaned_data.get("reason", "").strip()
        if len(reason) < 5:
            raise ValidationError("Please provide a comprehensive explanation (at least 5 characters).")
        return reason


class FacilityActionForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Administrative notes or justification for this decision...",
        }),
        required=False,
        label="Administrative Remarks / Justification"
    )


class PlatformSettingsForm(forms.ModelForm):
    change_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "placeholder": "Mandatory administrative reason for updating platform operational settings...",
            "required": True,
        }),
        label="Justification for Configuration Change",
        help_text="Recorded permanently in the administrative action audit ledger."
    )

    class Meta:
        model = PlatformSettings
        fields = [
            "platform_name",
            "hospital_requires_approval",
            "default_page_size",
            "enable_in_app_notifications",
            "enable_system_alerts",
            "stale_collection_hours",
            "auto_escalate_critical_discrepancies",
            "maintenance_mode",
            "maintenance_message",
        ]
        widgets = {
            "platform_name": forms.TextInput(attrs={"class": "form-control"}),
            "hospital_requires_approval": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "default_page_size": forms.NumberInput(attrs={"class": "form-control", "min": 10, "max": 100}),
            "enable_in_app_notifications": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "enable_system_alerts": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "stale_collection_hours": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 168}),
            "auto_escalate_critical_discrepancies": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "maintenance_mode": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "maintenance_message": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def clean_default_page_size(self):
        size = self.cleaned_data.get("default_page_size")
        if size is not None and (size < 10 or size > 100):
            raise ValidationError("Page size must be between 10 and 100.")
        return size

    def clean_stale_collection_hours(self):
        hours = self.cleaned_data.get("stale_collection_hours")
        if hours is not None and (hours < 1 or hours > 168):
            raise ValidationError("Stale collection hours must be between 1 and 168 (7 days).")
        return hours

    def clean_change_reason(self):
        reason = self.cleaned_data.get("change_reason", "").strip()
        if len(reason) < 5:
            raise ValidationError("Please provide a valid reason (at least 5 characters).")
        return reason


class ComplianceThresholdForm(forms.ModelForm):
    change_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "placeholder": "Mandatory administrative reason for updating compliance threshold reminders...",
            "required": True,
        }),
        label="Justification for Threshold Change",
        help_text="Recorded permanently in the administrative action audit ledger."
    )

    class Meta:
        model = ComplianceThresholdConfig
        fields = [
            "treatment_turnaround_hours",
            "disposal_turnaround_days",
            "verification_turnaround_days",
            "discrepancy_escalation_hours",
            "is_active",
        ]
        widgets = {
            "treatment_turnaround_hours": forms.NumberInput(attrs={"class": "form-control", "min": 12, "max": 168}),
            "disposal_turnaround_days": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 30}),
            "verification_turnaround_days": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 14}),
            "discrepancy_escalation_hours": forms.NumberInput(attrs={"class": "form-control", "min": 6, "max": 72}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "treatment_turnaround_hours": "Treatment Staging Reminder (Hours)",
            "disposal_turnaround_days": "Disposal Documentation Limit (Days)",
            "verification_turnaround_days": "Disposal Verification Window (Days)",
            "discrepancy_escalation_hours": "Critical Discrepancy Escalation Window (Hours)",
            "is_active": "Compliance Automation Active Switch",
        }

    def clean_treatment_turnaround_hours(self):
        val = self.cleaned_data.get("treatment_turnaround_hours")
        if val is not None and (val < 12 or val > 168):
            raise ValidationError("Treatment staging reminder must be between 12 and 168 hours.")
        return val

    def clean_disposal_turnaround_days(self):
        val = self.cleaned_data.get("disposal_turnaround_days")
        if val is not None and (val < 1 or val > 30):
            raise ValidationError("Disposal documentation turnaround must be between 1 and 30 days.")
        return val

    def clean_verification_turnaround_days(self):
        val = self.cleaned_data.get("verification_turnaround_days")
        if val is not None and (val < 1 or val > 14):
            raise ValidationError("Disposal verification window must be between 1 and 14 days.")
        return val

    def clean_discrepancy_escalation_hours(self):
        val = self.cleaned_data.get("discrepancy_escalation_hours")
        if val is not None and (val < 6 or val > 72):
            raise ValidationError("Discrepancy escalation limit must be between 6 and 72 hours.")
        return val

    def clean_change_reason(self):
        reason = self.cleaned_data.get("change_reason", "").strip()
        if len(reason) < 5:
            raise ValidationError("Please provide a valid reason (at least 5 characters).")
        return reason


class BroadcastAlertForm(forms.Form):
    ROLE_TARGETS = [
        ("ALL", "All Registered Stakeholders"),
        ("HOSPITAL", "Hospitals & Healthcare Clinics Only"),
        ("COLLECTOR", "Waste Collectors & Logistics Only"),
        ("FACILITY", "Treatment Facilities Only"),
        ("ADMIN", "System Administrators Only"),
    ]

    target_role = forms.ChoiceField(
        choices=ROLE_TARGETS,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Audience Scope"
    )
    title = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Scheduled System Upgrade / Urgent Regulatory Notice",
        }),
        label="Alert Headline"
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Broadcast communication message content...",
        }),
        label="Announcement Message"
    )

