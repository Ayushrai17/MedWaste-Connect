from django.conf import settings
from django.db import models
from django.utils import timezone


class ComplianceThresholdConfig(models.Model):
    """
    Configurable operational thresholds for automated compliance reminders and exception escalations.
    Important: These represent operational reminders, not arbitrary legal statutory deadlines.
    """
    treatment_turnaround_hours = models.PositiveIntegerField(
        default=48,
        help_text="Operational reminder threshold: Hours before flagging received waste awaiting treatment batching."
    )
    disposal_turnaround_days = models.PositiveIntegerField(
        default=7,
        help_text="Operational reminder threshold: Days before flagging treated waste awaiting final residue disposal documentation."
    )
    verification_turnaround_days = models.PositiveIntegerField(
        default=3,
        help_text="Operational reminder threshold: Days before reminding administrators of pending disposal compliance audits."
    )
    discrepancy_escalation_hours = models.PositiveIntegerField(
        default=24,
        help_text="Operational reminder threshold: Hours before escalating open high or critical discrepancies to administrators."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether automated compliance exception monitoring is currently active."
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compliance_configs_updated"
    )

    class Meta:
        verbose_name = "Compliance Threshold Configuration"
        verbose_name_plural = "Compliance Threshold Configurations"

    def __str__(self):
        return f"Compliance Config (Treatment: {self.treatment_turnaround_hours}h, Disposal: {self.disposal_turnaround_days}d)"

    @classmethod
    def get_config(cls):
        """
        Retrieves the active threshold configuration, or initializes a default instance.
        """
        config = cls.objects.filter(is_active=True).first()
        if not config:
            config = cls.objects.create(
                treatment_turnaround_hours=48,
                disposal_turnaround_days=7,
                verification_turnaround_days=3,
                discrepancy_escalation_hours=24,
                is_active=True,
            )
        return config


class GeneratedReportLog(models.Model):
    """
    Audit record tracking the generation and export of statutory CPCB and operational reports.
    """
    class ReportType(models.TextChoices):
        ANNUAL_FORM_IV = "ANNUAL_FORM_IV", "Annual Bio-Medical Waste Return (Form IV Summary)"
        CATEGORY_SUMMARY = "CATEGORY_SUMMARY", "Category-Wise Waste Generation & Segregation"
        FACILITY_PERFORMANCE = "FACILITY_PERFORMANCE", "Treatment Plant Reception & Treatment Audit"
        DISPATCH_MANIFEST = "DISPATCH_MANIFEST", "Collection Dispatch & Consignment Transit Log"
        DISCREPANCY_AUDIT = "DISCREPANCY_AUDIT", "Exceptions & Discrepancy Reconciliation Audit"

    class ExportFormat(models.TextChoices):
        PREVIEW = "PREVIEW", "Interactive Web Preview"
        CSV = "CSV", "Comma-Separated Values (CSV)"
        PRINT = "PRINT", "Print-Ready Statutory Layout"

    report_type = models.CharField(
        max_length=50,
        choices=ReportType.choices,
        db_index=True,
        help_text="Standardized regulatory report classification."
    )
    reporting_period_start = models.DateField(db_index=True)
    reporting_period_end = models.DateField(db_index=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="generated_regulatory_reports",
        help_text="Authenticated officer who generated this compliance report."
    )
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)
    export_format = models.CharField(
        max_length=20,
        choices=ExportFormat.choices,
        default=ExportFormat.PREVIEW
    )
    parameters_summary = models.TextField(
        blank=True,
        help_text="Serialized filter parameters (e.g. category, facility, hospital)."
    )
    records_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of itemized operational records aggregated in this report."
    )

    class Meta:
        ordering = ["-generated_at"]
        verbose_name = "Generated Regulatory Report Log"
        verbose_name_plural = "Generated Regulatory Report Logs"

    def __str__(self):
        return f"{self.get_report_type_display()} [{self.reporting_period_start} to {self.reporting_period_end}] by {self.generated_by.username}"
