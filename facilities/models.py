import secrets
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def generate_unique_facility_code():
    """
    Generates a collision-safe, human-readable treatment facility code.
    Format: MWC-FAC-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-FAC-{date_str}-{suffix}"
        if not TreatmentFacility.objects.filter(facility_code=candidate).exists():
            return candidate
    return f"MWC-FAC-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class TreatmentFacility(models.Model):
    """
    Common Bio-medical Waste Treatment Facility (CBWTF) or authorized treatment plant.
    Managed by platform administrators and operated by designated FACILITY role users.
    """
    class FacilityType(models.TextChoices):
        CBWTF = "CBWTF", "Common Bio-medical Waste Treatment Facility"
        CAPTIVE = "CAPTIVE", "Captive Healthcare Treatment Unit"
        SPECIALIZED = "SPECIALIZED", "Specialized Hazardous Treatment Plant"

    class ApprovalStatus(models.TextChoices):
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
        APPROVED = "APPROVED", "Approved & Verified"
        SUSPENDED = "SUSPENDED", "Suspended"
        REJECTED = "REJECTED", "Rejected"

    class OperationalAvailability(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        NEAR_CAPACITY = "NEAR_CAPACITY", "Near Capacity"
        FULL = "FULL", "Full"
        TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE", "Temporarily Unavailable"

    facility_code = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_facility_code,
        help_text="Unique collision-safe facility identification code."
    )
    name = models.CharField(
        max_length=150,
        help_text="Registered legal name of the Common Treatment Facility."
    )
    operator_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_facility",
        help_text="Authorized user account with FACILITY role responsible for this plant."
    )
    facility_type = models.CharField(
        max_length=50,
        choices=FacilityType.choices,
        default=FacilityType.CBWTF,
        help_text="Regulatory classification of the facility."
    )
    license_number = models.CharField(
        max_length=100,
        unique=True,
        help_text="State Pollution Control Board (SPCB) Consent to Operate (CTO) Authorization Number."
    )
    address = models.TextField(
        help_text="Complete physical plant address and industrial zone location."
    )
    district = models.CharField(
        max_length=100,
        help_text="Administrative district."
    )
    state = models.CharField(
        max_length=100,
        default="National Capital Region",
        help_text="State / Union Territory."
    )
    postal_code = models.CharField(
        max_length=20,
        help_text="Postal PIN code."
    )
    contact_person = models.CharField(
        max_length=120,
        help_text="Plant manager or environmental compliance officer."
    )
    contact_phone = models.CharField(
        max_length=30,
        help_text="Operational control room telephone contact."
    )
    contact_email = models.EmailField(
        help_text="Official statutory communication email."
    )

    # Treatment capabilities supported
    supports_incineration = models.BooleanField(
        default=True,
        help_text="High-temperature dual-chamber incineration (>1050°C) for Yellow Category waste."
    )
    supports_autoclaving = models.BooleanField(
        default=True,
        help_text="High-pressure steam autoclaving (>121°C/15 psi) for Red Category waste."
    )
    supports_shredding = models.BooleanField(
        default=True,
        help_text="Mechanical shredding of sterilized plastics and metals."
    )
    supports_chemical_disinfection = models.BooleanField(
        default=True,
        help_text="Sodium hypochlorite chemical treatment for Blue/White Category waste."
    )
    supports_encapsulation = models.BooleanField(
        default=False,
        help_text="Deep burial pit or concrete sharps encapsulation vault."
    )

    # Authorized waste categories
    accepted_categories = models.ManyToManyField(
        "waste.WasteCategory",
        related_name="authorized_facilities",
        help_text="Bio-medical waste statutory categories this plant is licensed to process."
    )

    # Daily capacity management (KG)
    daily_capacity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Maximum authorized processing capacity in KG per calendar day."
    )

    # Operating schedule
    operating_days = models.CharField(
        max_length=100,
        default="Monday - Saturday",
        help_text="Operational working days."
    )
    operating_hours = models.CharField(
        max_length=100,
        default="08:00 - 20:00",
        help_text="Daily working shift hours."
    )

    # Administrative and approval status
    approval_status = models.CharField(
        max_length=30,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING_APPROVAL,
        db_index=True,
        help_text="Administrative onboarding approval status."
    )
    operational_availability = models.CharField(
        max_length=30,
        choices=OperationalAvailability.choices,
        default=OperationalAvailability.AVAILABLE,
        db_index=True,
        help_text="Configured facility operational state."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Overall system active switch."
    )
    admin_notes = models.TextField(
        blank=True,
        help_text="Administrative oversight remarks and inspection logs."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Treatment Facility"
        verbose_name_plural = "Treatment Facilities"

    def __str__(self):
        return f"{self.name} ({self.facility_code})"

    def clean(self):
        super().clean()
        if self.daily_capacity_kg is not None and self.daily_capacity_kg <= Decimal("0.00"):
            raise ValidationError({"daily_capacity_kg": "Daily capacity must be greater than 0 KG."})

    def get_supported_methods_list(self):
        methods = []
        if self.supports_incineration:
            methods.append("Incineration (>1050°C)")
        if self.supports_autoclaving:
            methods.append("Autoclaving")
        if self.supports_shredding:
            methods.append("Shredding")
        if self.supports_chemical_disinfection:
            methods.append("Chemical Disinfection")
        if self.supports_encapsulation:
            methods.append("Encapsulation / Deep Burial")
        return methods

    def get_today_accepted_weight(self):
        """
        Calculates net weight of all accepted waste received on the current calendar date.
        Rejected bags do NOT count towards accepted capacity.
        """
        from treatment.models import FacilityBagReceipt
        today = timezone.localdate()
        result = FacilityBagReceipt.objects.filter(
            reception_manifest__facility=self,
            reception_manifest__received_at__date=today,
            acceptance_decision="ACCEPTED"
        ).aggregate(total=models.Sum("actual_weight"))
        return result["total"] or Decimal("0.00")

    def get_waste_awaiting_processing_weight(self):
        """
        Calculates total weight of accepted bags that have not yet entered an active treatment batch.
        """
        from treatment.models import FacilityBagReceipt
        result = FacilityBagReceipt.objects.filter(
            reception_manifest__facility=self,
            acceptance_decision="ACCEPTED",
            bag__treatment_batches__isnull=True
        ).aggregate(total=models.Sum("actual_weight"))
        return result["total"] or Decimal("0.00")

    def get_waste_currently_processing_weight(self):
        """
        Calculates total weight of waste currently inside active treatment batches (IN_PROGRESS).
        """
        from treatment.models import TreatmentBatch
        result = TreatmentBatch.objects.filter(
            facility=self,
            status=TreatmentBatch.Status.IN_PROGRESS
        ).aggregate(total=models.Sum("processing_weight"))
        return result["total"] or Decimal("0.00")

    def get_remaining_capacity(self):
        """
        Calculates remaining available treatment capacity in KG for today.
        """
        accepted_today = self.get_today_accepted_weight()
        rem = self.daily_capacity_kg - accepted_today
        return max(Decimal("0.00"), rem)

    def get_capacity_utilization_percentage(self):
        """
        Calculates daily capacity utilization percentage based on accepted waste.
        """
        if not self.daily_capacity_kg or self.daily_capacity_kg <= Decimal("0.00"):
            return 0.0
        accepted_today = self.get_today_accepted_weight()
        pct = (accepted_today / self.daily_capacity_kg) * Decimal("100.0")
        return min(100.0, float(round(pct, 1)))

    def get_dynamic_availability(self):
        """
        Returns dynamic operational state based on admin approval, active status,
        and real-time capacity thresholds.
        """
        if not self.is_active or self.approval_status != self.ApprovalStatus.APPROVED:
            return self.OperationalAvailability.TEMPORARILY_UNAVAILABLE

        if self.operational_availability == self.OperationalAvailability.TEMPORARILY_UNAVAILABLE:
            return self.OperationalAvailability.TEMPORARILY_UNAVAILABLE

        util = self.get_capacity_utilization_percentage()
        if util >= 100.0:
            return self.OperationalAvailability.FULL
        elif util >= 80.0:
            return self.OperationalAvailability.NEAR_CAPACITY
        return self.OperationalAvailability.AVAILABLE
