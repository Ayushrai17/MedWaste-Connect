import secrets
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def get_min_bag_weight():
    return getattr(settings, "MIN_WASTE_BAG_WEIGHT_KG", Decimal("0.05"))


def get_max_bag_weight():
    return getattr(settings, "MAX_WASTE_BAG_WEIGHT_KG", Decimal("25.00"))


class WasteCategory(models.Model):
    """
    Bio-Medical Waste Segregation Categories under Schedule I of the
    Bio-Medical Waste Management Rules 2016 (amended 2018, 2019).
    Centrally managed master data; immutable to ordinary hospital staff.
    """
    code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Standard statutory category code (e.g., YELLOW, RED, WHITE, BLUE)."
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable category name."
    )
    display_color = models.CharField(
        max_length=7,
        help_text="Hex color code (e.g. #FFD200, #E53935, #F5F5F5, #1E88E5)."
    )
    container_type = models.CharField(
        max_length=255,
        help_text="Mandated packaging/container (e.g. Non-chlorinated plastic bag, puncture-proof container)."
    )
    treatment_description = models.TextField(
        help_text="Statutory treatment & disposal method (incineration, autoclaving, shredding, recycling)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Active status for logging selection."
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Presentation ordering in UI lists."
    )

    class Meta:
        ordering = ["display_order", "code"]
        verbose_name = "Waste Category"
        verbose_name_plural = "Waste Categories"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        super().clean()
        if self.pk:
            original = WasteCategory.objects.filter(pk=self.pk).values("code").first()
            if original and original["code"] != self.code:
                raise ValidationError({"code": "Category code is immutable once established."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def generate_unique_batch_number():
    """
    Generates a collision-safe, human-readable batch identifier.
    Format: MWC-BAT-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-BAT-{date_str}-{suffix}"
        if not WasteRegistrationBatch.objects.filter(batch_number=candidate).exists():
            return candidate
    return f"MWC-BAT-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class WasteRegistrationBatch(models.Model):
    """
    Represents an atomic batch submission containing one or more physical waste bags.
    """
    batch_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_batch_number,
        help_text="Unique collision-safe batch registration identifier."
    )
    hospital = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="waste_batches",
        help_text="Hospital healthcare facility that generated this waste batch."
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="registered_batches",
        help_text="Hospital staff member who authenticated and performed this registration."
    )
    ward = models.CharField(
        max_length=100,
        help_text="Hospital ward, floor, or care unit where the waste originated."
    )
    department = models.CharField(
        max_length=100,
        help_text="Clinical department (e.g. Surgery, Intensive Care, Pathology, Emergency)."
    )
    bag_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of physical bags included in this registration batch."
    )
    total_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Aggregate net weight of all bags in this batch (in KG)."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when batch registration was created."
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Waste Registration Batch"
        verbose_name_plural = "Waste Registration Batches"
        indexes = [
            models.Index(fields=["hospital", "-created_at"]),
        ]

    def __str__(self):
        return f"Batch {self.batch_number} ({self.bag_count} bags, {self.total_weight} kg)"


def generate_unique_bag_id():
    """
    Generates a collision-safe, human-readable physical bag identifier.
    Format: MWC-BAG-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-BAG-{date_str}-{suffix}"
        if not WasteBag.objects.filter(bag_id=candidate).exists():
            return candidate
    return f"MWC-BAG-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class WasteBag(models.Model):
    """
    Individual physical biomedical waste bag.
    Every bag maintains an independent permanent bag_id and UUID qr_token.
    """
    class Status(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        IN_TRANSIT = "IN_TRANSIT", "In Transit"
        RECEIVED = "RECEIVED", "Received at Facility"
        TREATED = "TREATED", "Treated"
        DISPOSED = "DISPOSED", "Disposed"

    class QRStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"

    bag_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_bag_id,
        help_text="Unique collision-safe physical bag tracking identifier."
    )
    batch = models.ForeignKey(
        WasteRegistrationBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bags",
        help_text="Registration batch this bag was submitted under."
    )
    hospital = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="waste_bags",
        help_text="Hospital facility account that owns this waste bag."
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="registered_bags",
        help_text="Hospital staff member who registered this individual bag."
    )
    category = models.ForeignKey(
        WasteCategory,
        on_delete=models.PROTECT,
        related_name="bags",
        help_text="Bio-medical waste statutory color category."
    )
    ward = models.CharField(
        max_length=100,
        help_text="Ward or unit location of segregation."
    )
    department = models.CharField(
        max_length=100,
        help_text="Clinical department."
    )
    weight = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        help_text="Net weight in kilograms (KG)."
    )
    weight_unit = models.CharField(
        max_length=5,
        default="KG",
        editable=False,
        help_text="Measurement unit (statutory default is KG)."
    )

    # Fields reserved for Feature 2: Dynamic QR Code Generation
    qr_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Cryptographically secure unique identifier reserved for QR code payload."
    )
    qr_image = models.ImageField(
        upload_to="qr_codes/%Y/%m/",
        null=True,
        blank=True,
        editable=False,
        help_text="Reserved for Feature 2: rendered QR code PNG file."
    )
    qr_status = models.CharField(
        max_length=20,
        choices=QRStatus.choices,
        default=QRStatus.PENDING,
        help_text="QR code generation status lifecycle."
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.REGISTERED,
        help_text="Lifecycle custody status."
    )
    registered_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when the bag was officially logged."
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp of latest status update."
    )

    class Meta:
        ordering = ["-registered_at"]
        verbose_name = "Waste Bag"
        verbose_name_plural = "Waste Bags"
        indexes = [
            models.Index(fields=["hospital", "-registered_at"]),
            models.Index(fields=["category", "status"]),
            models.Index(fields=["batch", "category"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(weight__gt=0),
                name="wastebag_positive_weight",
            ),
        ]

    def __str__(self):
        return f"{self.bag_id} [{self.category.code}] - {self.weight} kg ({self.get_status_display()})"

    def clean(self):
        super().clean()
        if self.weight is not None:
            min_weight = get_min_bag_weight()
            max_weight = get_max_bag_weight()
            if self.weight < min_weight:
                raise ValidationError({
                    "weight": f"Weight must be at least {min_weight} kg (scale resolution threshold)."
                })
            if self.weight > max_weight:
                raise ValidationError({
                    "weight": f"Weight cannot exceed {max_weight} kg (approved single-operator safe manual handling SOP limit)."
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
