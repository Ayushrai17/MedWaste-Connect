import secrets
import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def generate_unique_admin_action_reference():
    """
    Generates a collision-safe, human-readable administrative action tracking reference.
    Format: MWC-ADM-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-ADM-{date_str}-{suffix}"
        if not AdminActionAudit.objects.filter(action_reference=candidate).exists():
            return candidate
    return f"MWC-ADM-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        HOSPITAL = "HOSPITAL", "Hospital / Clinic"
        COLLECTOR = "COLLECTOR", "Waste Collector"
        FACILITY = "FACILITY", "Treatment Facility"

    class AccountStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
        SUSPENDED = "SUSPENDED", "Suspended"
        INACTIVE = "INACTIVE", "Inactive"
        REJECTED = "REJECTED", "Rejected"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.HOSPITAL,
        help_text="Designated stakeholder role in medical waste lifecycle."
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Contact telephone or mobile number."
    )
    organization_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Hospital, municipal authority, or facility name."
    )

    # Step 7: Administrative Status & Onboarding Controls
    account_status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        db_index=True,
        help_text="Operational lifecycle state of user account."
    )
    suspension_reason = models.TextField(
        blank=True,
        help_text="Mandatory administrative justification if account is suspended."
    )
    admin_remarks = models.TextField(
        blank=True,
        help_text="Internal administrative review notes."
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_users",
        help_text="Administrator who verified and approved this account."
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of administrative approval."
    )

    def __str__(self):
        full_name = self.get_full_name()
        display_name = full_name if full_name else self.username
        if self.organization_name:
            return f"{display_name} ({self.organization_name}) - {self.get_role_display()}"
        return f"{display_name} - {self.get_role_display()}"

    @property
    def is_suspended(self):
        return self.account_status == self.AccountStatus.SUSPENDED

    @property
    def is_pending_approval(self):
        return self.account_status == self.AccountStatus.PENDING_APPROVAL

    @property
    def is_fully_active(self):
        return self.is_active and self.account_status == self.AccountStatus.ACTIVE


class AdminActionAudit(models.Model):
    """
    Append-only immutable audit trail for administrative interventions,
    user lifecycle events, facility approvals, configuration changes, and overrides.
    """
    class ActionType(models.TextChoices):
        USER_ACTIVATION = "USER_ACTIVATION", "User Account Activated"
        USER_DEACTIVATION = "USER_DEACTIVATION", "User Account Deactivated"
        USER_SUSPENSION = "USER_SUSPENSION", "User Account Suspended"
        USER_REACTIVATION = "USER_REACTIVATION", "User Account Reactivated"
        USER_APPROVAL = "USER_APPROVAL", "User Onboarding Approved"
        USER_REJECTION = "USER_REJECTION", "User Onboarding Rejected"
        HOSPITAL_SUSPENSION = "HOSPITAL_SUSPENSION", "Hospital Organization Suspended"
        HOSPITAL_REACTIVATION = "HOSPITAL_REACTIVATION", "Hospital Organization Reactivated"
        COLLECTOR_SUSPENSION = "COLLECTOR_SUSPENSION", "Collector Account Suspended"
        COLLECTOR_REACTIVATION = "COLLECTOR_REACTIVATION", "Collector Account Reactivated"
        FACILITY_APPROVAL = "FACILITY_APPROVAL", "Treatment Facility Approved"
        FACILITY_REJECTION = "FACILITY_REJECTION", "Treatment Facility Rejected"
        FACILITY_SUSPENSION = "FACILITY_SUSPENSION", "Treatment Facility Suspended"
        FACILITY_REINSTATEMENT = "FACILITY_REINSTATEMENT", "Treatment Facility Reinstated"
        CONFIG_UPDATE = "CONFIG_UPDATE", "Platform Configuration Modified"
        COMPLIANCE_CONFIG_UPDATE = "COMPLIANCE_CONFIG_UPDATE", "Compliance Thresholds Modified"
        NOTIFICATION_BROADCAST = "NOTIFICATION_BROADCAST", "System Alert Broadcasted"
        NOTIFICATION_REVIEW = "NOTIFICATION_REVIEW", "Alert Marked Reviewed"
        OPERATIONAL_OVERRIDE = "OPERATIONAL_OVERRIDE", "Operational Override Executed"

    action_reference = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_admin_action_reference,
        help_text="Unique collision-safe administrative action tracking reference."
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="performed_admin_actions",
        help_text="Administrator who initiated this action."
    )
    actor_role = models.CharField(max_length=30, blank=True)
    action_type = models.CharField(
        max_length=50,
        choices=ActionType.choices,
        db_index=True
    )
    affected_model = models.CharField(
        max_length=50,
        help_text="Target entity model name (e.g. User, TreatmentFacility, PlatformSettings)."
    )
    affected_record_id = models.CharField(
        max_length=100,
        help_text="Primary key or unique code of affected entity."
    )
    affected_record_display = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable descriptor of the entity."
    )
    affected_organization = models.CharField(
        max_length=150,
        blank=True,
        help_text="Organization name of the target entity, if applicable."
    )
    previous_state = models.TextField(
        blank=True,
        help_text="Prior state or configuration serialized as text/JSON."
    )
    new_state = models.TextField(
        blank=True,
        help_text="New state or configuration serialized as text/JSON."
    )
    reason = models.TextField(
        help_text="Mandatory administrative justification or operational remarks."
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Client IP address recorded at the time of administrative action."
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Verified timestamp when the administrative action occurred."
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Admin Action Audit"
        verbose_name_plural = "Admin Action Audits"
        indexes = [
            models.Index(fields=["action_type", "-timestamp"]),
            models.Index(fields=["actor", "-timestamp"]),
        ]

    def __str__(self):
        return f"[{self.action_reference}] {self.get_action_type_display()} by {self.actor.username}"

    def save(self, *args, **kwargs):
        # Enforce append-only: existing entries cannot be updated
        if self.pk and not kwargs.pop("allow_update", False):
            raise ValidationError("Admin action audit logs are append-only and cannot be altered.")
        if not self.actor_role and self.actor:
            self.actor_role = self.actor.get_role_display()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Admin action audit logs are permanent legal records and cannot be deleted.")


class PlatformSettings(models.Model):
    """
    Singleton system-wide operational preferences and platform runtime controls.
    """
    platform_name = models.CharField(
        max_length=100,
        default="MedWaste Connect",
        help_text="Public and administrative platform branding name."
    )
    hospital_requires_approval = models.BooleanField(
        default=False,
        help_text="When enabled, newly self-registered hospitals require administrative approval before accessing waste logging."
    )
    default_page_size = models.PositiveIntegerField(
        default=25,
        help_text="Default number of records per page across administrative tables (10 - 100)."
    )
    enable_in_app_notifications = models.BooleanField(
        default=True,
        help_text="Toggle global dispatch of database-backed in-app notifications."
    )
    enable_system_alerts = models.BooleanField(
        default=True,
        help_text="Toggle automated generation of compliance deadline alerts."
    )
    stale_collection_hours = models.PositiveIntegerField(
        default=24,
        help_text="Threshold in hours after which an unassigned pickup request is flagged as stale."
    )
    auto_escalate_critical_discrepancies = models.BooleanField(
        default=True,
        help_text="Automatically notify administrators when a CRITICAL severity discrepancy is logged."
    )
    maintenance_mode = models.BooleanField(
        default=False,
        help_text="Informational banner indicating non-admin users that scheduled maintenance is underway."
    )
    maintenance_message = models.TextField(
        blank=True,
        default="System scheduled maintenance in progress. Operational workflows remain accessible.",
        help_text="Message displayed to users when maintenance mode is active."
    )
    last_updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="platform_settings_updated"
    )

    class Meta:
        verbose_name = "Platform Configuration"
        verbose_name_plural = "Platform Configurations"

    def __str__(self):
        return f"Platform Settings ({self.platform_name}) — Page Size: {self.default_page_size}"

    @classmethod
    def get_settings(cls):
        settings_obj = cls.objects.first()
        if not settings_obj:
            settings_obj = cls.objects.create(
                platform_name="MedWaste Connect",
                hospital_requires_approval=False,
                default_page_size=25,
                enable_in_app_notifications=True,
                enable_system_alerts=True,
                stale_collection_hours=24,
                auto_escalate_critical_discrepancies=True,
                maintenance_mode=False,
            )
        return settings_obj
