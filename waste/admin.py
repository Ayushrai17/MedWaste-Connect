from django.contrib import admin
from .models import WasteCategory, WasteRegistrationBatch, WasteBag


@admin.register(WasteCategory)
class WasteCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "display_color", "container_type", "is_active", "display_order")
    list_editable = ("is_active", "display_order")
    search_fields = ("code", "name", "container_type")
    ordering = ("display_order", "code")

    def get_readonly_fields(self, request, obj=None):
        if obj:  # editing an existing category
            return ("code",)
        return ()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of master regulatory categories
        if obj and obj.code in ["YELLOW", "RED", "WHITE", "BLUE"]:
            return False
        return request.user.is_superuser


@admin.register(WasteRegistrationBatch)
class WasteRegistrationBatchAdmin(admin.ModelAdmin):
    list_display = ("batch_number", "hospital", "ward", "department", "bag_count", "total_weight", "created_at")
    list_filter = ("hospital", "created_at")
    search_fields = ("batch_number", "hospital__username", "hospital__organization_name", "ward", "department")
    readonly_fields = ("batch_number", "hospital", "registered_by", "bag_count", "total_weight", "created_at")
    ordering = ("-created_at",)

    def has_delete_permission(self, request, obj=None):
        # Restrict unrestricted deletion of historical batches
        return request.user.is_superuser


@admin.register(WasteBag)
class WasteBagAdmin(admin.ModelAdmin):
    list_display = ("bag_id", "hospital", "category", "weight", "ward", "department", "status", "qr_status", "batch", "registered_at")
    list_filter = ("status", "category", "qr_status", "hospital", "registered_at")
    search_fields = ("bag_id", "qr_token", "batch__batch_number", "hospital__username", "hospital__organization_name", "ward", "department")
    readonly_fields = ("bag_id", "qr_token", "weight_unit", "registered_at", "updated_at")
    ordering = ("-registered_at",)
    actions = None  # Disable bulk deletion of waste records for data preservation

    def has_delete_permission(self, request, obj=None):
        # Restrict individual deletion to protect audit chain
        return request.user.is_superuser
