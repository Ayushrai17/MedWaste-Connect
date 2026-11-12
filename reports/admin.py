from django.contrib import admin
from .models import ComplianceThresholdConfig, GeneratedReportLog


@admin.register(ComplianceThresholdConfig)
class ComplianceThresholdConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "treatment_turnaround_hours",
        "disposal_turnaround_days",
        "verification_turnaround_days",
        "discrepancy_escalation_hours",
        "is_active",
        "updated_at",
        "updated_by",
    )
    list_editable = ("is_active",)
    readonly_fields = ("updated_at",)

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(GeneratedReportLog)
class GeneratedReportLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "report_type",
        "reporting_period_start",
        "reporting_period_end",
        "export_format",
        "records_count",
        "generated_by",
        "generated_at",
    )
    list_filter = ("report_type", "export_format", "generated_at")
    search_fields = ("generated_by__username", "parameters_summary")
    readonly_fields = (
        "report_type",
        "reporting_period_start",
        "reporting_period_end",
        "generated_by",
        "generated_at",
        "export_format",
        "parameters_summary",
        "records_count",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Statutory audit logs cannot be deleted
        return False
