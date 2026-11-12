import secrets
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from core.utils import quantize_weight


def generate_unique_reception_reference():
    """
    Generates a collision-safe reception manifest reference.
    Format: MWC-REC-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-REC-{date_str}-{suffix}"
        if not FacilityReceptionManifest.objects.filter(reference_number=candidate).exists():
            return candidate
    return f"MWC-REC-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class FacilityReceptionManifest(models.Model):
    """
    Formal incoming waste reception manifest created when an in-transit collection arrives
    at an authorized treatment facility.
    """
    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received & Under Inspection"
        PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED", "Partially Accepted"
        ACCEPTED = "ACCEPTED", "Accepted for Treatment"
        REJECTED = "REJECTED", "Consignment Rejected"
        DISCREPANCY_REPORTED = "DISCREPANCY_REPORTED", "Discrepancy Reported"

    reference_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_reception_reference,
        help_text="Unique collision-safe reception reference."
    )
    facility = models.ForeignKey(
        "facilities.TreatmentFacility",
        on_delete=models.PROTECT,
        related_name="reception_manifests",
        help_text="Treatment plant receiving the waste."
    )
    collection_request = models.ForeignKey(
        "collection.CollectionRequest",
        on_delete=models.PROTECT,
        related_name="facility_receptions",
        help_text="Associated collection dispatch manifest."
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_manifests",
        help_text="Facility staff member who performed gate check-in."
    )
    received_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="Timestamp when vehicle entered treatment facility gate."
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.RECEIVED,
        db_index=True,
        help_text="Overall outcome of reception inspection and verification."
    )

    # Weight metrics (KG)
    gross_received_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Actual gross scale weight recorded at facility gate (in KG)."
    )
    expected_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Expected total net weight as recorded in originating collection request (in KG)."
    )
    weight_discrepancy = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Weight variance: (gross_received_weight - expected_weight)."
    )

    # Bag count metrics
    expected_bag_count = models.PositiveIntegerField(
        default=0,
        help_text="Total number of bags documented in collection manifest."
    )
    accepted_bag_count = models.PositiveIntegerField(
        default=0,
        help_text="Total number of individual bags verified and accepted for treatment."
    )
    rejected_bag_count = models.PositiveIntegerField(
        default=0,
        help_text="Total number of individual bags rejected."
    )
    missing_bag_count = models.PositiveIntegerField(
        default=0,
        help_text="Count of bags expected but not physically found in consignment."
    )

    # Administrative capacity override
    capacity_override_authorized = models.BooleanField(
        default=False,
        help_text="Whether an administrator authorized receiving this consignment exceeding daily plant capacity."
    )
    capacity_override_reason = models.TextField(
        blank=True,
        help_text="Mandatory explanation if capacity override was granted by administration."
    )
    capacity_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authorized_capacity_overrides",
        help_text="Administrator who authorized the capacity override."
    )

    remarks = models.TextField(
        blank=True,
        help_text="General reception notes, vehicle registration, and gate observations."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Facility Reception Manifest"
        verbose_name_plural = "Facility Reception Manifests"

    def __str__(self):
        return f"Reception {self.reference_number} — {self.facility.name} ({self.get_status_display()})"

    def update_metrics(self):
        """
        Recalculates bag counts and weight metrics from child FacilityBagReceipt records.
        """
        receipts = self.bag_receipts.all()
        self.expected_bag_count = receipts.count()
        self.accepted_bag_count = receipts.filter(acceptance_decision="ACCEPTED").count()
        self.rejected_bag_count = receipts.filter(acceptance_decision="REJECTED").count()
        self.missing_bag_count = receipts.filter(acceptance_decision="MISSING").count()

        # Compute accepted net weight
        accepted_receipts = receipts.filter(acceptance_decision="ACCEPTED")
        accepted_weight = sum((r.actual_weight or Decimal("0.00")) for r in accepted_receipts)
        self.gross_received_weight = accepted_weight

        # Weight discrepancy calculation
        self.weight_discrepancy = self.gross_received_weight - self.expected_weight

        # Determine manifest status based on individual decisions
        if self.rejected_bag_count > 0 and self.accepted_bag_count > 0:
            self.status = self.Status.PARTIALLY_ACCEPTED
        elif self.rejected_bag_count > 0 and self.accepted_bag_count == 0:
            self.status = self.Status.REJECTED
        elif self.missing_bag_count > 0 or abs(self.weight_discrepancy) > Decimal("2.00"):
            self.status = self.Status.DISCREPANCY_REPORTED
        elif self.accepted_bag_count == self.expected_bag_count and self.expected_bag_count > 0:
            self.status = self.Status.ACCEPTED

        self.save()


class FacilityBagReceipt(models.Model):
    """
    Individual bag-level inspection record during facility reception.
    """
    class PackagingCondition(models.TextChoices):
        INTACT = "INTACT", "Intact & Properly Sealed"
        DAMAGED = "DAMAGED", "Torn / Damaged Outer Packaging"
        LEAKING = "LEAKING", "Liquid Leakage Observed"
        IMPROPER_CONTAINER = "IMPROPER_CONTAINER", "Non-Compliant Packaging Container"

    class AcceptanceDecision(models.TextChoices):
        PENDING = "PENDING", "Pending Decision"
        ACCEPTED = "ACCEPTED", "Accepted for Treatment"
        REJECTED = "REJECTED", "Rejected"
        MISSING = "MISSING", "Missing from Consignment"

    reception_manifest = models.ForeignKey(
        FacilityReceptionManifest,
        on_delete=models.CASCADE,
        related_name="bag_receipts",
        help_text="Parent reception manifest."
    )
    bag = models.ForeignKey(
        "waste.WasteBag",
        on_delete=models.PROTECT,
        related_name="facility_receipts",
        help_text="Waste bag being verified."
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Whether bag QR code or physical Bag ID was verified at reception."
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of scan-in verification."
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_bag_receipts",
        help_text="Staff member who scanned this bag."
    )
    actual_weight = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual scale weight verified upon reception (in KG)."
    )
    packaging_condition = models.CharField(
        max_length=30,
        choices=PackagingCondition.choices,
        default=PackagingCondition.INTACT,
        help_text="Physical integrity and seal status of bag upon gate arrival."
    )
    acceptance_decision = models.CharField(
        max_length=30,
        choices=AcceptanceDecision.choices,
        default=AcceptanceDecision.PENDING,
        db_index=True,
        help_text="Whether bag is accepted for treatment or rejected."
    )
    rejection_reason = models.TextField(
        blank=True,
        help_text="Mandatory explanation if bag is rejected."
    )
    reception_remarks = models.TextField(
        blank=True,
        help_text="Inspection notes (e.g. sharp protrusion, barcode torn)."
    )

    class Meta:
        ordering = ["bag__bag_id"]
        verbose_name = "Facility Bag Receipt"
        verbose_name_plural = "Facility Bag Receipts"
        unique_together = ["reception_manifest", "bag"]

    def __str__(self):
        return f"Receipt: {self.bag.bag_id} — {self.get_acceptance_decision_display()}"


def generate_unique_treatment_batch_number():
    """
    Generates a collision-safe treatment batch identifier.
    Format: MWC-TRT-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-TRT-{date_str}-{suffix}"
        if not TreatmentBatch.objects.filter(batch_number=candidate).exists():
            return candidate
    return f"MWC-TRT-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class TreatmentBatch(models.Model):
    """
    Record of a treatment run (e.g. Incinerator Burn Cycle #14, Autoclave Batch #8).
    """
    class TreatmentMethod(models.TextChoices):
        INCINERATION = "INCINERATION", "High-Temperature Incineration (>1050°C)"
        AUTOCLAVING = "AUTOCLAVING", "High-Pressure Steam Autoclaving"
        SHREDDING = "SHREDDING", "Mechanical Sterilized Shredding"
        CHEMICAL_DISINFECTION = "CHEMICAL_DISINFECTION", "Chemical Disinfection & Neutralization"
        ENCAPSULATION = "ENCAPSULATION", "Deep Burial / Encapsulation"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Execution"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        ON_HOLD = "ON_HOLD", "On Hold"

    batch_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_treatment_batch_number,
        help_text="Unique collision-safe treatment batch reference number."
    )
    facility = models.ForeignKey(
        "facilities.TreatmentFacility",
        on_delete=models.PROTECT,
        related_name="treatment_batches",
        help_text="Facility executing this treatment operation."
    )
    reception_manifest = models.ForeignKey(
        FacilityReceptionManifest,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="treatment_batches",
        help_text="Optional link to parent reception manifest."
    )
    treatment_method = models.CharField(
        max_length=30,
        choices=TreatmentMethod.choices,
        help_text="Prescribed bio-medical treatment technology employed."
    )
    waste_category = models.ForeignKey(
        "waste.WasteCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="treatment_batches",
        help_text="Primary statutory category treated in this batch."
    )
    bags = models.ManyToManyField(
        "waste.WasteBag",
        related_name="treatment_batches",
        help_text="Accepted bags processed in this treatment batch."
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        help_text="Current state of the treatment cycle."
    )
    start_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when treatment processing commenced."
    )
    end_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when treatment processing was concluded."
    )
    processing_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total aggregate weight of bags inside this treatment batch (in KG)."
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="operated_treatment_batches",
        help_text="Authorized plant technician or operator who conducted this cycle."
    )
    equipment_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Unit ID (e.g. 'Dual-Chamber Incinerator Unit #1', 'Tuttnauer Autoclave 5075')."
    )

    # Operational parameters (actual operator-entered values)
    chamber_temperature_celsius = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Recorded operating temperature in °C."
    )
    chamber_pressure_psi = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Recorded autoclave pressure in psi / bar."
    )
    cycle_duration_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total duration of treatment run in minutes."
    )

    failure_or_hold_reason = models.TextField(
        blank=True,
        help_text="Mandatory explanation if treatment cycle failed or was halted."
    )
    treatment_remarks = models.TextField(
        blank=True,
        help_text="Operational logs, emissions check, scrubber status."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Treatment Batch"
        verbose_name_plural = "Treatment Batches"

    def __str__(self):
        return f"Batch {self.batch_number} — {self.get_treatment_method_display()} ({self.get_status_display()})"

    def recalculate_processing_weight(self):
        total = quantize_weight(self.bags.aggregate(total=models.Sum("weight"))["total"])
        self.processing_weight = total
        self.save(update_fields=["processing_weight", "updated_at"])


def generate_unique_disposal_reference():
    """
    Generates a collision-safe final disposal record identifier.
    Format: MWC-DSP-YYYYMMDD-XXXXXX
    """
    date_str = timezone.now().strftime("%Y%m%d")
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"MWC-DSP-{date_str}-{suffix}"
        if not DisposalRecord.objects.filter(reference_number=candidate).exists():
            return candidate
    return f"MWC-DSP-{date_str}-{uuid.uuid4().hex[:6].upper()}"


class DisposalRecord(models.Model):
    """
    Final documentation of post-treatment residue disposal (e.g. incinerator ash to landfill,
    shredded plastic to registered recycler).
    """
    class DisposalMethod(models.TextChoices):
        SECURE_LANDFILL = "SECURE_LANDFILL", "Hazardous Waste Secure Landfill (Incinerator Ash)"
        AUTHORIZED_RECYCLER = "AUTHORIZED_RECYCLER", "Authorized Plastic/Glass Recycler"
        SHARPS_BURIAL_PIT = "SHARPS_BURIAL_PIT", "Sharps Concrete Pit / Encapsulation Vault"
        EFFLUENT_DISCHARGE = "EFFLUENT_DISCHARGE", "ETP Neutralized Effluent Discharge"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Documentation"
        DOCUMENTED = "DOCUMENTED", "Documented by Facility"
        VERIFIED = "VERIFIED", "Verified by Administrator"

    reference_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        default=generate_unique_disposal_reference,
        help_text="Unique collision-safe disposal reference."
    )
    treatment_batch = models.ForeignKey(
        TreatmentBatch,
        on_delete=models.PROTECT,
        related_name="disposal_records",
        help_text="Completed treatment batch being documented."
    )
    facility = models.ForeignKey(
        "facilities.TreatmentFacility",
        on_delete=models.PROTECT,
        related_name="disposal_records",
        help_text="Facility responsible for documenting this final disposal."
    )
    bags = models.ManyToManyField(
        "waste.WasteBag",
        related_name="disposal_records",
        help_text="Treated waste bags finally disposed of."
    )
    disposal_date = models.DateField(
        default=timezone.localdate,
        help_text="Date of final residue handover or disposal."
    )
    disposal_time = models.TimeField(
        default=timezone.localtime,
        help_text="Time of final disposal."
    )
    final_disposal_method = models.CharField(
        max_length=50,
        choices=DisposalMethod.choices,
        help_text="Designated statutory disposal destination for treated residue."
    )
    disposal_location = models.CharField(
        max_length=255,
        help_text="Name and physical location of authorized landfill, pit, or recycling partner."
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_disposals",
        help_text="Facility staff member who filed this disposal documentation."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DOCUMENTED,
        db_index=True,
        help_text="Administrative verification status of disposal."
    )
    supporting_document = models.FileField(
        upload_to="disposal_documents/%Y/%m/",
        null=True,
        blank=True,
        help_text="Landfill receipt, recycling weighment slip, or signed handover manifest."
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_disposals",
        help_text="Administrator who verified this disposal documentation."
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of administrative compliance verification."
    )
    verification_notes = models.TextField(
        blank=True,
        help_text="Auditing notes recorded by the verifying administrator."
    )
    remarks = models.TextField(
        blank=True,
        help_text="Field remarks and consignment seal notes."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-disposal_date", "-disposal_time"]
        verbose_name = "Disposal Record"
        verbose_name_plural = "Disposal Records"

    def __str__(self):
        return f"Disposal {self.reference_number} — {self.get_final_disposal_method_display()} ({self.get_status_display()})"


class FacilityAuditHistory(models.Model):
    """
    Append-only immutable audit trail for all facility lifecycle operations:
    reception, acceptance decisions, treatment cycles, and disposal documentation.
    """
    facility = models.ForeignKey(
        "facilities.TreatmentFacility",
        on_delete=models.CASCADE,
        related_name="audit_logs"
    )
    action_type = models.CharField(max_length=50)
    entity_reference = models.CharField(max_length=100)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT
    )
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Facility Audit History"
        verbose_name_plural = "Facility Audit History Logs"

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.action_type}: {self.entity_reference}"
