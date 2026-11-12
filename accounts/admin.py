from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, AdminActionAudit, PlatformSettings


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    list_display = (
        "username",
        "email",
        "role",
        "account_status",
        "organization_name",
        "phone",
        "is_staff",
        "is_active",
    )
    list_filter = ("role", "account_status", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "first_name", "last_name", "email", "organization_name", "phone")

    fieldsets = UserAdmin.fieldsets + (
        (
            "Medical Waste System Profile",
            {
                "fields": ("role", "account_status", "organization_name", "phone", "suspension_reason", "admin_remarks", "approved_by", "approved_at"),
            },
        ),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Medical Waste System Profile",
            {
                "fields": ("role", "account_status", "organization_name", "phone"),
            },
        ),
    )


@admin.register(AdminActionAudit)
class AdminActionAuditAdmin(admin.ModelAdmin):
    list_display = (
        "action_reference",
        "action_type",
        "actor",
        "affected_model",
        "affected_record_id",
        "timestamp",
    )
    list_filter = ("action_type", "actor_role", "affected_model", "timestamp")
    search_fields = ("action_reference", "actor__username", "affected_record_id", "reason", "affected_organization")
    readonly_fields = [f.name for f in AdminActionAudit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "platform_name",
        "hospital_requires_approval",
        "default_page_size",
        "enable_in_app_notifications",
        "enable_system_alerts",
        "maintenance_mode",
        "last_updated",
    )
