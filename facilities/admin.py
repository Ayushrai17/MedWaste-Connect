from django.contrib import admin
from .models import TreatmentFacility


@admin.register(TreatmentFacility)
class TreatmentFacilityAdmin(admin.ModelAdmin):
    list_display = (
        "facility_code",
        "name",
        "operator_user",
        "district",
        "state",
        "daily_capacity_kg",
        "approval_status",
        "operational_availability",
        "is_active",
    )
    list_filter = (
        "approval_status",
        "operational_availability",
        "is_active",
        "facility_type",
        "state",
    )
    search_fields = (
        "facility_code",
        "name",
        "license_number",
        "contact_person",
        "operator_user__username",
        "operator_user__email",
    )
    readonly_fields = ("facility_code", "created_at", "updated_at")
    filter_horizontal = ("accepted_categories",)
