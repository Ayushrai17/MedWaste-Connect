import secrets
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def generate_unique_custody_reference():
    """
    Generates a collision-safe, human-readable custody event identifier.
    Format: MWC-CUST-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-CUST-{date_str}-{suffix}"
        if not CustodyEvent.objects.filter(event_reference=candidate).exists():
            return candidate
    return f"MWC-CUST-{date_str}-{uuid.uuid4().hex[:6].upper()}"


def generate_unique_handover_reference():
    """
    Generates a collision-safe custody handover reference.
    Format: MWC-HND-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-HND-{date_str}-{suffix}"
        if not CustodyHandover.objects.filter(handover_reference=candidate).exists():
            return candidate
    return f"MWC-HND-{date_str}-{uuid.uuid4().hex[:6].upper()}"


def generate_unique_discrepancy_reference():
    """
    Generates a collision-safe exception discrepancy reference.
    Format: MWC-DISC-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-DISC-{date_str}-{suffix}"
        if not CustodyDiscrepancy.objects.filter(discrepancy_reference=candidate).exists():
            return candidate
    return f"MWC-DISC-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class CustodyEvent(models.Model):
    """
    Centralized immutable append-only audit event capturing every chronological milestone
    in the lifecycle of an individual physical waste bag.
    """
    class EventType(models.TextChoices):
        BAG_REGISTERED = "BAG_REGISTERED", "Waste Bag Registered at Ward"
        QR_GENERATED = "QR_GENERATED", "Dynamic QR Code Label Generated"
        COLLECTION_REQUESTED = "COLLECTION_REQUESTED", "Pickup Requested by Hospital"
        COLLECTION_SCHEDULED = "COLLECTION_SCHEDULED", "Pickup Scheduled by Admin"
        COLLECTOR_ASSIGNED = "COLLECTOR_ASSIGNED", "Collector Driver Allocated"
        COLLECTOR_ACCEPTED = "COLLECTOR_ACCEPTED", "Dispatch Accepted by Driver"
        COLLECTOR_VERIFIED = "COLLECTOR_VERIFIED", "Scanned & Verified at Hospital Dock"
        CUSTODY_TRANSFERRED_TO_COLLECTOR = "CUSTODY_TRANSFERRED_TO_COLLECTOR", "Custody Transferred to Collector"
        TRANSIT_STARTED = "TRANSIT_STARTED", "Consignment In Transit to Facility"
        FACILITY_ARRIVED = "FACILITY_ARRIVED", "Consignment Arrived at Facility Gate"
        FACILITY_VERIFIED = "FACILITY_VERIFIED", "Scanned & Verified by Facility Inspector"
        FACILITY_ACCEPTED = "FACILITY_ACCEPTED", "Accepted for Prescribed Treatment"
        FACILITY_REJECTED = "FACILITY_REJECTED", "Rejected by Facility Inspector"
        FACILITY_MISSING = "FACILITY_MISSING", "Flagged Missing from Consignment"
        TREATMENT_BATCHED = "TREATMENT_BATCHED", "Allocated to Treatment Processing Run"
        TREATMENT_STARTED = "TREATMENT_STARTED", "Treatment Processing Commenced"
        TREATMENT_COMPLETED = "TREATMENT_COMPLETED", "Treatment Cycle Safely Concluded"
        TREATMENT_FAILED = "TREATMENT_FAILED", "Treatment Cycle Failed or On Hold"
        DISPOSAL_DOCUMENTED = "DISPOSAL_DOCUMENTED", "Residue Final Disposal Documented"
        DISPOSAL_VERIFIED = "DISPOSAL_VERIFIED", "Disposal Verified by Administrator"
        CUSTODY_CORRECTION = "CUSTODY_CORRECTION", "Compensating Custody Correction"

    event_reference = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_custody_reference,
        help_text="Unique collision-safe custody event tracking reference."
    )
    bag = models.ForeignKey(
        "waste.WasteBag",
        on_delete=models.PROTECT,
        related_name="custody_events",
        help_text="The individual waste bag tracked."
    )
    collection_request = models.ForeignKey(
        "collection.CollectionRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custody_events",
        help_text="Related collection dispatch request, if applicable."
    )
    reception_manifest = models.ForeignKey(
        "treatment.FacilityReceptionManifest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custody_events",
        help_text="Related treatment facility reception manifest, if applicable."
    )
    treatment_batch = models.ForeignKey(
        "treatment.TreatmentBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custody_events",
        help_text="Related treatment processing batch, if applicable."
    )
    disposal_record = models.ForeignKey(
        "treatment.DisposalRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custody_events",
        help_text="Related final residue disposal documentation, if applicable."
    )

    event_type = models.CharField(
        max_length=40,
        choices=EventType.choices,
        db_index=True,
        help_text="Operational lifecycle milestone."
    )
    previous_custody_status = models.CharField(
        max_length=30,
        blank=True,
        help_text="Bag custody status prior to this event."
    )
    new_custody_status = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Bag custody status resulting from this event."
    )

    # Actor information
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custody_events_authored",
        help_text="Authenticated user responsible for initiating or confirming this event."
    )
    actor_role = models.CharField(
        max_length=30,
        blank=True,
        help_text="Role of the actor at the time the event occurred."
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="Exact verified timestamp when the event transpired."
    )

    # Geolocation / Facility Locations
    origin_location = models.CharField(
        max_length=255,
        blank=True,
        help_text="Origin location (e.g. hospital ward or loading dock)."
    )
    destination_location = models.CharField(
        max_length=255,
        blank=True,
        help_text="Destination location (e.g. treatment facility gate or incinerator chamber)."
    )

    # Verification parameters
    verification_method = models.CharField(
        max_length=50,
        blank=True,
        help_text="Verification technique (e.g. QR_SCAN, MANUAL_ENTRY, SCALE_WEIGHMENT, SYSTEM_EVENT)."
    )
    weight_at_event = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Scale weight observed at this handover or reception point (in KG)."
    )
    remarks = models.TextField(
        blank=True,
        help_text="Operational field notes, seal condition, or observations."
    )
    evidence_document = models.FileField(
        upload_to="custody_evidence/%Y/%m/",
        null=True,
        blank=True,
        help_text="Signed weighment slip, delivery receipt, or visual evidence."
    )

    # Compensating event linkage (for immutable corrections)
    related_event = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compensating_events",
        help_text="Reference to original event if this is a compensating correction."
    )
    is_compensating_event = models.BooleanField(
        default=False,
        help_text="Whether this event represents an administrative audit correction."
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp", "created_at"]
        verbose_name = "Custody Event"
        verbose_name_plural = "Custody Events"
        indexes = [
            models.Index(fields=["bag", "timestamp"]),
            models.Index(fields=["event_type", "timestamp"]),
        ]

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.bag.bag_id} — {self.get_event_type_display()}"

    def save(self, *args, **kwargs):
        # Enforce append-only immutability: existing events cannot be altered
        if self.pk and not kwargs.pop("allow_update", False):
            raise ValidationError("Custody events are append-only and cannot be altered. Issue a CUSTODY_CORRECTION event instead.")
        if not self.actor_role and self.actor:
            self.actor_role = self.actor.get_role_display()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Custody events are permanent legal records and cannot be deleted.")


class CustodyHandover(models.Model):
    """
    Formal dual-party custody transfer record (e.g. Hospital dock to Transport Driver,
    or Transport Driver to Treatment Plant Inspector).
    """
    class HandoverType(models.TextChoices):
        HOSPITAL_TO_COLLECTOR = "HOSPITAL_TO_COLLECTOR", "Hospital Dock Handover to Collector"
        COLLECTOR_TO_FACILITY = "COLLECTOR_TO_FACILITY", "Collector Handover to Treatment Facility"

    class Status(models.TextChoices):
        INITIATED = "INITIATED", "Handover Initiated"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Handover Acknowledged & Signed"
        DISPUTED = "DISPUTED", "Handover Disputed / Discrepancy Flagged"

    handover_reference = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_handover_reference,
        help_text="Unique collision-safe handover reference."
    )
    handover_type = models.CharField(
        max_length=30,
        choices=HandoverType.choices,
        db_index=True,
        help_text="Operational junction of custody transfer."
    )
    collection_request = models.ForeignKey(
        "collection.CollectionRequest",
        on_delete=models.PROTECT,
        related_name="custody_handovers",
        help_text="Parent collection dispatch manifest."
    )
    reception_manifest = models.ForeignKey(
        "treatment.FacilityReceptionManifest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custody_handovers",
        help_text="Facility gate reception manifest, if facility handover."
    )

    # Releasing Party
    releasing_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="released_handovers",
        help_text="Authorized person handing over custody."
    )
    releasing_role = models.CharField(max_length=30)
    releasing_organization = models.CharField(max_length=150, blank=True)

    # Receiving Party
    receiving_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_handovers",
        help_text="Authorized person accepting custody."
    )
    receiving_role = models.CharField(max_length=30)
    receiving_organization = models.CharField(max_length=150, blank=True)

    handover_timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="Exact timestamp of physical transfer."
    )
    bags = models.ManyToManyField(
        "waste.WasteBag",
        related_name="custody_handovers",
        help_text="Bags transferred during this handover."
    )

    # Reconciliation metrics
    total_bags_expected = models.PositiveIntegerField(default=0)
    total_bags_verified = models.PositiveIntegerField(default=0)
    expected_weight = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    actual_weight = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACKNOWLEDGED,
        db_index=True,
        help_text="Acknowledgment and dispute status."
    )
    dispute_reason = models.TextField(
        blank=True,
        help_text="Mandatory explanation if handover was disputed."
    )
    releasing_signature_acknowledged = models.BooleanField(
        default=True,
        help_text="Digital sign-off by the releasing party."
    )
    receiving_signature_acknowledged = models.BooleanField(
        default=True,
        help_text="Digital sign-off by the receiving party."
    )
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-handover_timestamp"]
        verbose_name = "Custody Handover"
        verbose_name_plural = "Custody Handovers"

    def __str__(self):
        return f"Handover {self.handover_reference} ({self.get_handover_type_display()} — {self.get_status_display()})"


class CustodyDiscrepancy(models.Model):
    """
    Formal discrepancy and operational exception tracking system.
    Captures missing bags, packaging failures, leakage, weight mismatches, and rejections.
    """
    class DiscrepancyType(models.TextChoices):
        MISSING_AT_PICKUP = "MISSING_AT_PICKUP", "Bag Missing at Hospital Pickup"
        MISSING_AT_RECEPTION = "MISSING_AT_RECEPTION", "Bag Missing at Facility Reception"
        DAMAGED_PACKAGING = "DAMAGED_PACKAGING", "Damaged / Torn Biohazard Packaging"
        LEAKING_PACKAGING = "LEAKING_PACKAGING", "Liquid Biohazard Leakage"
        IMPROPER_CONTAINER = "IMPROPER_CONTAINER", "Non-Compliant Packaging Container"
        WEIGHT_MISMATCH = "WEIGHT_MISMATCH", "Net Weight Discrepancy"
        REJECTED_BY_FACILITY = "REJECTED_BY_FACILITY", "Waste Bag Rejected by Facility"
        UNACKNOWLEDGED_HANDOVER = "UNACKNOWLEDGED_HANDOVER", "Unacknowledged Custody Handover"
        DUPLICATE_VERIFICATION = "DUPLICATE_VERIFICATION", "Duplicate Scan / Verification Flag"
        INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION", "Invalid State Transition Attempted"
        OTHER = "OTHER", "Other Operational Exception"

    class Severity(models.TextChoices):
        LOW = "LOW", "Low Operational Notice"
        MEDIUM = "MEDIUM", "Medium Discrepancy"
        HIGH = "HIGH", "High Severity Exception"
        CRITICAL = "CRITICAL", "Critical Biohazard Violation"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open Exception"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Administrative Review"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    discrepancy_reference = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_discrepancy_reference,
        help_text="Unique collision-safe exception reference."
    )
    bag = models.ForeignKey(
        "waste.WasteBag",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discrepancies",
        help_text="Specific bag involved, if identifiable."
    )
    collection_request = models.ForeignKey(
        "collection.CollectionRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discrepancies",
        help_text="Related collection dispatch, if applicable."
    )
    reception_manifest = models.ForeignKey(
        "treatment.FacilityReceptionManifest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discrepancies",
        help_text="Related facility reception manifest, if applicable."
    )

    discrepancy_type = models.CharField(
        max_length=40,
        choices=DiscrepancyType.choices,
        db_index=True,
        help_text="Category of exception."
    )
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
        help_text="Risk and priority level."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        help_text="Investigation and resolution status."
    )

    description = models.TextField(help_text="Detailed description of the observed discrepancy.")
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_discrepancies",
        help_text="Staff member who flagged this exception."
    )
    reported_at = models.DateTimeField(default=timezone.now, db_index=True)

    resolution_notes = models.TextField(blank=True, help_text="Action taken to address or close the exception.")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_discrepancies",
        help_text="Administrator or authorized officer who verified resolution."
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reported_at"]
        verbose_name = "Custody Discrepancy"
        verbose_name_plural = "Custody Discrepancies"

    def __str__(self):
        return f"Exception {self.discrepancy_reference} [{self.severity}]: {self.get_discrepancy_type_display()} ({self.get_status_display()})"
