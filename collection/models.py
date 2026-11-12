import secrets
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from core.utils import quantize_weight


def generate_unique_collection_reference():
    """
    Generates a unique, collision-safe collection request reference identifier.
    Format: MWC-REQ-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-REQ-{date_str}-{suffix}"
        if not CollectionRequest.objects.filter(reference_number=candidate).exists():
            return candidate
    return f"MWC-REQ-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class CollectionRequest(models.Model):
    """
    Biomedical waste pickup request initiated by an authorized hospital,
    managed by platform administrators, and executed by assigned collectors.
    """
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
        APPROVED = "APPROVED", "Approved"
        SCHEDULED = "SCHEDULED", "Scheduled"
        ASSIGNED = "ASSIGNED", "Collector Assigned"
        ACCEPTED = "ACCEPTED", "Collector Accepted"
        COLLECTOR_REJECTED = "COLLECTOR_REJECTED", "Collector Rejected"
        PICKUP_IN_PROGRESS = "PICKUP_IN_PROGRESS", "Pickup in Progress"
        COLLECTED = "COLLECTED", "Collected"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    ACTIVE_STATUSES = [
        Status.REQUESTED,
        Status.UNDER_REVIEW,
        Status.APPROVED,
        Status.SCHEDULED,
        Status.ASSIGNED,
        Status.ACCEPTED,
        Status.PICKUP_IN_PROGRESS,
    ]

    PRE_COLLECTION_STATUSES = [
        Status.REQUESTED,
        Status.UNDER_REVIEW,
        Status.APPROVED,
        Status.SCHEDULED,
        Status.ASSIGNED,
        Status.COLLECTOR_REJECTED,
    ]

    reference_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_collection_reference,
        help_text="Unique collision-safe collection request reference."
    )
    hospital = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_requests",
        help_text="Hospital healthcare facility submitting this pickup request."
    )
    bags = models.ManyToManyField(
        "waste.WasteBag",
        related_name="collection_requests",
        help_text="Eligible biomedical waste bags assigned to this collection request."
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
        help_text="Current lifecycle state of the collection operation."
    )

    # Hospital preferred scheduling
    preferred_pickup_date = models.DateField(
        help_text="Hospital's preferred collection date."
    )
    preferred_pickup_time = models.TimeField(
        help_text="Hospital's preferred collection time window."
    )
    alternative_pickup_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional secondary collection date."
    )
    alternative_pickup_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional secondary collection time window."
    )

    # Administrator confirmed schedule
    confirmed_pickup_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Final scheduled pickup date confirmed by system administrator."
    )
    confirmed_pickup_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Final scheduled pickup time confirmed by system administrator."
    )

    # Location and contact info
    pickup_address = models.CharField(
        max_length=255,
        help_text="Hospital pickup dock, building wing, or bay address."
    )
    contact_person = models.CharField(
        max_length=120,
        help_text="Authorized hospital waste coordinator or nurse in charge."
    )
    contact_phone = models.CharField(
        max_length=30,
        help_text="Direct telephone contact for the collection driver."
    )
    special_instructions = models.TextField(
        blank=True,
        help_text="Dock directions, security gate protocols, or decontamination instructions."
    )
    operational_notes = models.TextField(
        blank=True,
        help_text="Additional operational remarks provided by the hospital."
    )
    admin_notes = models.TextField(
        blank=True,
        help_text="Internal dispatch remarks or logistics notes from administration."
    )
    rejection_reason = models.TextField(
        blank=True,
        help_text="Explanation if request was rejected by admin or collector."
    )
    collector_remarks = models.TextField(
        blank=True,
        help_text="Field notes recorded by the waste collector during pickup."
    )

    # Logistics assignment
    assigned_collector = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_collection_requests",
        help_text="Waste collection personnel or logistics driver assigned to this pickup."
    )
    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the collector was assigned by administration."
    )
    actual_collection_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Actual verified timestamp when waste bags were physically collected."
    )
    destination_facility = models.ForeignKey(
        "facilities.TreatmentFacility",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incoming_collections",
        help_text="Designated biomedical waste treatment facility destination for this collection."
    )

    # Summary metrics (cached for performance)
    total_bags = models.PositiveIntegerField(
        default=0,
        help_text="Count of physical waste bags associated with this request."
    )
    total_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Aggregate net weight (in KG) of all bags in this request."
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when the collection request was created."
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp of latest state change or update."
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Collection Request"
        verbose_name_plural = "Collection Requests"
        indexes = [
            models.Index(fields=["hospital", "status", "-created_at"]),
            models.Index(fields=["assigned_collector", "status"]),
            models.Index(fields=["confirmed_pickup_date", "status"]),
        ]

    def __str__(self):
        return f"{self.reference_number} ({self.hospital.organization_name or self.hospital.username}) - {self.get_status_display()}"

    def update_aggregates(self):
        """
        Recalculates cached total_bags and total_weight from the associated bags relation.
        """
        aggregate_data = self.bags.aggregate(
            count=models.Count("id"),
            weight=models.Sum("weight")
        )
        self.total_bags = aggregate_data.get("count") or 0
        self.total_weight = quantize_weight(aggregate_data.get("weight"))
        self.save(update_fields=["total_bags", "total_weight", "updated_at"])

    @property
    def is_cancellable(self):
        """
        Hospitals can cancel only while in a pre-collection state before active collection.
        """
        return self.status in self.PRE_COLLECTION_STATUSES

    @property
    def can_be_assigned(self):
        """
        Admins can assign or reassign collectors in these states.
        """
        return self.status in [
            self.Status.REQUESTED,
            self.Status.UNDER_REVIEW,
            self.Status.APPROVED,
            self.Status.SCHEDULED,
            self.Status.ASSIGNED,
            self.Status.COLLECTOR_REJECTED,
        ]

    @property
    def effective_pickup_date(self):
        """
        Returns confirmed date if available, otherwise preferred date.
        """
        return self.confirmed_pickup_date or self.preferred_pickup_date

    @property
    def effective_pickup_time(self):
        """
        Returns confirmed time if available, otherwise preferred time.
        """
        return self.confirmed_pickup_time or self.preferred_pickup_time

    def clean(self):
        super().clean()
        today = timezone.localdate()
        # Ensure preferred pickup date is not in the past
        if self.preferred_pickup_date and self.preferred_pickup_date < today:
            raise ValidationError({
                "preferred_pickup_date": "Preferred pickup date cannot be in the past."
            })
        if self.alternative_pickup_date and self.alternative_pickup_date < today:
            raise ValidationError({
                "alternative_pickup_date": "Alternative pickup date cannot be in the past."
            })
        if self.confirmed_pickup_date and self.confirmed_pickup_date < today:
            raise ValidationError({
                "confirmed_pickup_date": "Confirmed pickup date cannot be in the past."
            })


class CollectionStatusHistory(models.Model):
    """
    Immutable audit trail recording every state change and operational transition
    in a collection request's lifecycle.
    """
    request = models.ForeignKey(
        CollectionRequest,
        on_delete=models.CASCADE,
        related_name="status_history",
        help_text="Collection request subject to this transition."
    )
    previous_status = models.CharField(
        max_length=30,
        blank=True,
        help_text="Status prior to this transition."
    )
    new_status = models.CharField(
        max_length=30,
        help_text="New status resulting from this transition."
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Authenticated user responsible for this status transition."
    )
    remarks = models.TextField(
        blank=True,
        help_text="Reason, administrative note, or rejection explanation."
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this transition occurred."
    )

    class Meta:
        ordering = ["timestamp"]
        verbose_name = "Collection Status History"
        verbose_name_plural = "Collection Status Histories"

    def __str__(self):
        return f"{self.request.reference_number}: {self.previous_status} -> {self.new_status} at {self.timestamp}"


class BagVerificationRecord(models.Model):
    """
    Tracks bag-by-bag physical or digital verification by the collector
    during the pickup handover.
    """
    collection_request = models.ForeignKey(
        CollectionRequest,
        on_delete=models.CASCADE,
        related_name="bag_verifications",
        help_text="Associated collection request."
    )
    bag = models.ForeignKey(
        "waste.WasteBag",
        on_delete=models.CASCADE,
        related_name="collection_verifications",
        help_text="Waste bag being verified."
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Indicates whether this bag was verified by the collector."
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when verification occurred."
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Collector who conducted the verification."
    )
    notes = models.CharField(
        max_length=255,
        blank=True,
        help_text="Verification remarks (e.g. barcode verified, label intact)."
    )

    class Meta:
        unique_together = [("collection_request", "bag")]
        verbose_name = "Bag Verification Record"
        verbose_name_plural = "Bag Verification Records"

    def __str__(self):
        status = "Verified" if self.is_verified else "Unverified"
        return f"{self.collection_request.reference_number} - {self.bag.bag_id} [{status}]"
