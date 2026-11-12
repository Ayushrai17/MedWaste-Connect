from django.contrib import admin
from .models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
    FacilityAuditHistory,
)


class FacilityBagReceiptInline(admin.TabularInline):
    model = FacilityBagReceipt
    extra = 0
    fields = (
        "bag",
        "is_verified",
        "verified_at",
        "actual_weight",
        "packaging_condition",
        "acceptance_decision",
        "rejection_reason",
    )
    readonly_fields = ("bag", "verified_at")


@admin.register(FacilityReceptionManifest)
class FacilityReceptionManifestAdmin(admin.ModelAdmin):
    list_display = (
        "reference_number",
        "facility",
        "collection_request",
        "status",
        "expected_bag_count",
        "accepted_bag_count",
        "rejected_bag_count",
        "gross_received_weight",
        "received_at",
    )
    list_filter = ("status", "facility", "capacity_override_authorized")
    search_fields = (
        "reference_number",
        "collection_request__reference_number",
        "facility__name",
        "facility__facility_code",
    )
    readonly_fields = ("reference_number", "created_at", "updated_at")
    inlines = [FacilityBagReceiptInline]


@admin.register(FacilityBagReceipt)
class FacilityBagReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "bag",
        "reception_manifest",
        "is_verified",
        "actual_weight",
        "packaging_condition",
        "acceptance_decision",
    )
    list_filter = ("acceptance_decision", "packaging_condition", "is_verified")
    search_fields = ("bag__bag_id", "reception_manifest__reference_number")


@admin.register(TreatmentBatch)
class TreatmentBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_number",
        "facility",
        "treatment_method",
        "status",
        "processing_weight",
        "operator",
        "start_time",
        "end_time",
    )
    list_filter = ("status", "treatment_method", "facility")
    search_fields = (
        "batch_number",
        "facility__name",
        "equipment_reference",
        "operator__username",
    )
    readonly_fields = ("batch_number", "created_at", "updated_at")
    filter_horizontal = ("bags",)


@admin.register(DisposalRecord)
class DisposalRecordAdmin(admin.ModelAdmin):
    list_display = (
        "reference_number",
        "facility",
        "treatment_batch",
        "final_disposal_method",
        "disposal_date",
        "status",
        "operator",
        "verified_by",
    )
    list_filter = ("status", "final_disposal_method", "facility")
    search_fields = (
        "reference_number",
        "treatment_batch__batch_number",
        "disposal_location",
        "facility__name",
    )
    readonly_fields = ("reference_number", "created_at", "updated_at")
    filter_horizontal = ("bags",)


@admin.register(FacilityAuditHistory)
class FacilityAuditHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "facility",
        "action_type",
        "entity_reference",
        "changed_by",
    )
    list_filter = ("action_type", "facility")
    search_fields = ("entity_reference", "description", "facility__name")
    readonly_fields = (
        "timestamp",
        "facility",
        "action_type",
        "entity_reference",
        "changed_by",
        "description",
    )
