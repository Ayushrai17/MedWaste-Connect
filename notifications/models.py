from django.db import models
from django.conf import settings


class Notification(models.Model):
    """
    Database-backed in-app notification system for MedWaste Connect stakeholders.
    """
    class NotificationType(models.TextChoices):
        REQUEST_SUBMITTED = "REQUEST_SUBMITTED", "Pickup Request Submitted"
        REQUEST_APPROVED = "REQUEST_APPROVED", "Pickup Request Approved"
        REQUEST_REJECTED = "REQUEST_REJECTED", "Pickup Request Rejected"
        SCHEDULE_CONFIRMED = "SCHEDULE_CONFIRMED", "Pickup Schedule Confirmed"
        COLLECTOR_ASSIGNED = "COLLECTOR_ASSIGNED", "Collector Assigned"
        COLLECTOR_ACCEPTED = "COLLECTOR_ACCEPTED", "Assignment Accepted"
        COLLECTOR_REJECTED = "COLLECTOR_REJECTED", "Assignment Rejected"
        PICKUP_STARTED = "PICKUP_STARTED", "Pickup Journey Started"
        WASTE_COLLECTED = "WASTE_COLLECTED", "Biomedical Waste Collected"
        REQUEST_COMPLETED = "REQUEST_COMPLETED", "Collection Completed"
        REQUEST_CANCELLED = "REQUEST_CANCELLED", "Collection Cancelled"
        WASTE_ARRIVED = "WASTE_ARRIVED", "Waste Consignment Arrived"
        RECEPTION_VERIFIED = "RECEPTION_VERIFIED", "Reception Verified"
        RECEPTION_DISCREPANCY = "RECEPTION_DISCREPANCY", "Reception Discrepancy Flagged"
        RECEPTION_REJECTED = "RECEPTION_REJECTED", "Waste Consignment Rejected"
        TREATMENT_STARTED = "TREATMENT_STARTED", "Waste Treatment Started"
        TREATMENT_COMPLETED = "TREATMENT_COMPLETED", "Waste Treatment Completed"
        TREATMENT_FAILED = "TREATMENT_FAILED", "Treatment Cycle Failed or On Hold"
        DISPOSAL_DOCUMENTED = "DISPOSAL_DOCUMENTED", "Disposal Documentation Filed"
        DISPOSAL_VERIFIED = "DISPOSAL_VERIFIED", "Disposal Verified by Administrator"
        SYSTEM_ALERT = "SYSTEM_ALERT", "System Alert"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="User recipient of this notification."
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
        default=NotificationType.SYSTEM_ALERT,
        db_index=True
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    reference_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional identifier (e.g. collection reference number)."
    )
    link = models.CharField(
        max_length=255,
        blank=True,
        help_text="Relative URL destination when user clicks this notification."
    )
    is_read = models.BooleanField(
        default=False,
        db_index=True
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        status = "Read" if self.is_read else "Unread"
        return f"[{status}] {self.recipient.username}: {self.title}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])
