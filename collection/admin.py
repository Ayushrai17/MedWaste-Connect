from django.contrib import admin
from .models import CollectionRequest, CollectionStatusHistory, BagVerificationRecord


class CollectionStatusHistoryInline(admin.TabularInline):
    model = CollectionStatusHistory
    extra = 0
    readonly_fields = ("previous_status", "new_status", "changed_by", "remarks", "timestamp")
    can_delete = False


class BagVerificationRecordInline(admin.TabularInline):
    model = BagVerificationRecord
    extra = 0
    readonly_fields = ("bag", "is_verified", "verified_at", "verified_by", "notes")
    can_delete = False


@admin.register(CollectionRequest)
class CollectionRequestAdmin(admin.ModelAdmin):
    list_display = (
        "reference_number",
        "hospital",
        "status",
        "preferred_pickup_date",
        "confirmed_pickup_date",
        "assigned_collector",
        "total_bags",
        "total_weight",
        "created_at",
    )
    list_filter = ("status", "preferred_pickup_date", "confirmed_pickup_date", "hospital")
    search_fields = (
        "reference_number",
        "hospital__organization_name",
        "hospital__username",
        "contact_person",
        "assigned_collector__username",
    )
    readonly_fields = (
        "reference_number",
        "total_bags",
        "total_weight",
        "created_at",
        "updated_at",
        "actual_collection_time",
    )
    inlines = [CollectionStatusHistoryInline, BagVerificationRecordInline]


@admin.register(CollectionStatusHistory)
class CollectionStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("request", "previous_status", "new_status", "changed_by", "timestamp")
    list_filter = ("new_status", "timestamp")
    search_fields = ("request__reference_number", "changed_by__username", "remarks")
    readonly_fields = ("request", "previous_status", "new_status", "changed_by", "remarks", "timestamp")


@admin.register(BagVerificationRecord)
class BagVerificationRecordAdmin(admin.ModelAdmin):
    list_display = ("collection_request", "bag", "is_verified", "verified_at", "verified_by")
    list_filter = ("is_verified", "verified_at")
    search_fields = ("collection_request__reference_number", "bag__bag_id", "verified_by__username")
