from django.contrib import admin
from .models import CustodyEvent, CustodyHandover, CustodyDiscrepancy


@admin.register(CustodyEvent)
class CustodyEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_reference",
        "bag",
        "event_type",
        "actor",
        "actor_role",
        "timestamp",
        "verification_method",
        "is_compensating_event",
    )
    list_filter = ("event_type", "actor_role", "verification_method", "is_compensating_event", "timestamp")
    search_fields = ("event_reference", "bag__bag_id", "actor__username", "origin_location", "destination_location")
    readonly_fields = (
        "event_reference",
        "bag",
        "collection_request",
        "reception_manifest",
        "treatment_batch",
        "disposal_record",
        "event_type",
        "previous_custody_status",
        "new_custody_status",
        "actor",
        "actor_role",
        "timestamp",
        "origin_location",
        "destination_location",
        "verification_method",
        "weight_at_event",
        "remarks",
        "evidence_document",
        "related_event",
        "is_compensating_event",
        "created_at",
    )

    def has_add_permission(self, request):
        # Events are created via business services, but admins can record compensating events via specialized workflows
        return True

    def has_change_permission(self, request, obj=None):
        # Immutable: cannot be edited after creation
        return False

    def has_delete_permission(self, request, obj=None):
        # Immutable legal audit trail: cannot be deleted
        return False


@admin.register(CustodyHandover)
class CustodyHandoverAdmin(admin.ModelAdmin):
    list_display = (
        "handover_reference",
        "handover_type",
        "releasing_user",
        "receiving_user",
        "total_bags_verified",
        "actual_weight",
        "status",
        "handover_timestamp",
    )
    list_filter = ("handover_type", "status", "handover_timestamp")
    search_fields = (
        "handover_reference",
        "collection_request__request_id",
        "releasing_user__username",
        "receiving_user__username",
    )
    readonly_fields = ("handover_reference", "created_at")
    filter_horizontal = ("bags",)


@admin.register(CustodyDiscrepancy)
class CustodyDiscrepancyAdmin(admin.ModelAdmin):
    list_display = (
        "discrepancy_reference",
        "discrepancy_type",
        "severity",
        "status",
        "bag",
        "reported_by",
        "reported_at",
        "resolved_by",
    )
    list_filter = ("discrepancy_type", "severity", "status", "reported_at")
    search_fields = (
        "discrepancy_reference",
        "bag__bag_id",
        "collection_request__request_id",
        "reported_by__username",
        "resolved_by__username",
    )
    readonly_fields = ("discrepancy_reference", "reported_at", "created_at", "updated_at")
