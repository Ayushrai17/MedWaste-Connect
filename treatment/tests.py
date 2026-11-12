import uuid
from datetime import time, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from waste.models import WasteCategory, WasteBag
from collection.models import CollectionRequest
from facilities.models import TreatmentFacility
from notifications.models import Notification
from treatment.models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
    FacilityAuditHistory,
)
from treatment.services import (
    receive_incoming_consignment,
    verify_reception_bag,
    record_bag_acceptance_decision,
    finalize_reception_manifest,
    create_treatment_batch,
    start_treatment_batch,
    complete_treatment_batch,
    fail_or_hold_treatment_batch,
    create_disposal_record,
    verify_disposal_record,
)

User = get_user_model()


class TreatmentBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()

        self.admin_user = User.objects.create_user(
            username="admin_director",
            email="admin@medwaste.example.com",
            password="SecurePassword123!",
            role=User.Role.ADMIN,
        )

        self.hospital_user = User.objects.create_user(
            username="city_hospital",
            email="cityhosp@example.com",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="City Multi-Speciality Hospital",
        )

        self.hospital_user_2 = User.objects.create_user(
            username="mercy_clinic",
            email="mercy@example.com",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Mercy Clinic",
        )

        self.collector_user = User.objects.create_user(
            username="driver_vikram",
            email="vikram@fleet.example.com",
            password="SecurePassword123!",
            role=User.Role.COLLECTOR,
        )

        self.facility_user_1 = User.objects.create_user(
            username="facility_operator_apex",
            email="apex@cbwtf.example.com",
            password="SecurePassword123!",
            role=User.Role.FACILITY,
            organization_name="Apex Bio-Clean Systems",
        )

        self.facility_user_2 = User.objects.create_user(
            username="facility_operator_enviro",
            email="enviro@cbwtf.example.com",
            password="SecurePassword123!",
            role=User.Role.FACILITY,
            organization_name="EnviroCare Treatment Unit",
        )

        # Waste Categories
        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={"name": "Yellow Category", "display_color": "#FFD200", "container_type": "Bag", "treatment_description": "Incineration"}
        )
        self.cat_red, _ = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={"name": "Red Category", "display_color": "#E53935", "container_type": "Bag", "treatment_description": "Autoclave"}
        )

        # Treatment Facilities
        self.facility_1 = TreatmentFacility.objects.create(
            name="Apex CBWTF Plant",
            operator_user=self.facility_user_1,
            license_number="SPCB/CTO/2026/01",
            address="Industrial Zone Phase 1",
            district="South",
            state="National Capital Region",
            postal_code="110020",
            contact_person="Ramesh Patel",
            contact_phone="+91 98765 11111",
            contact_email="apex@example.com",
            daily_capacity_kg=Decimal("100.00"),  # 100 kg daily limit for capacity test
            supports_incineration=True,
            supports_autoclaving=True,
            supports_shredding=True,
            supports_chemical_disinfection=True,
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
            operational_availability=TreatmentFacility.OperationalAvailability.AVAILABLE,
            is_active=True,
        )

        self.facility_2 = TreatmentFacility.objects.create(
            name="EnviroCare Treatment Plant",
            operator_user=self.facility_user_2,
            license_number="SPCB/CTO/2026/02",
            address="Industrial Zone Phase 2",
            district="North",
            state="National Capital Region",
            postal_code="110030",
            contact_person="Suresh Nair",
            contact_phone="+91 98765 22222",
            contact_email="enviro@example.com",
            daily_capacity_kg=Decimal("2000.00"),
            supports_incineration=False,  # Does not support incineration
            supports_autoclaving=True,
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
            operational_availability=TreatmentFacility.OperationalAvailability.AVAILABLE,
            is_active=True,
        )

        # Seed Bags
        self.bag_y1 = WasteBag.objects.create(
            hospital=self.hospital_user,
            registered_by=self.hospital_user,
            category=self.cat_yellow,
            ward="ICU Room 1",
            department="Critical Care",
            weight=Decimal("15.00"),
            status=WasteBag.Status.IN_TRANSIT,
        )
        self.bag_r1 = WasteBag.objects.create(
            hospital=self.hospital_user,
            registered_by=self.hospital_user,
            category=self.cat_red,
            ward="OT 3",
            department="Surgery",
            weight=Decimal("10.00"),
            status=WasteBag.Status.IN_TRANSIT,
        )

        # Seed Collection Request (Collected & In Transit)
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.collection_req = CollectionRequest.objects.create(
            hospital=self.hospital_user,
            preferred_pickup_date=tomorrow,
            preferred_pickup_time=time(10, 0),
            confirmed_pickup_date=tomorrow,
            confirmed_pickup_time=time(10, 30),
            pickup_address="Gate 2 Loading Bay",
            contact_person="Sister Mathew",
            contact_phone="+91 98765 43210",
            assigned_collector=self.collector_user,
            destination_facility=self.facility_1,
            status=CollectionRequest.Status.COLLECTED,
            actual_collection_time=timezone.now(),
        )
        self.collection_req.bags.add(self.bag_y1, self.bag_r1)
        self.collection_req.update_aggregates()


class IncomingReceptionWorkflowTests(TreatmentBaseTestCase):
    def test_receive_incoming_consignment_success(self):
        manifest = receive_incoming_consignment(
            facility_user=self.facility_user_1,
            collection_request=self.collection_req,
            gross_received_weight=Decimal("25.00"),
            remarks="Weighed on electronic weighbridge",
        )
        self.assertEqual(manifest.facility, self.facility_1)
        self.assertEqual(manifest.collection_request, self.collection_req)
        self.assertEqual(manifest.expected_bag_count, 2)
        self.assertEqual(manifest.gross_received_weight, Decimal("25.00"))
        self.assertEqual(manifest.status, FacilityReceptionManifest.Status.RECEIVED)

        # Check itemized bag receipts created
        receipts = manifest.bag_receipts.all()
        self.assertEqual(receipts.count(), 2)
        self.assertFalse(receipts.first().is_verified)

        # Check audit log created
        audits = FacilityAuditHistory.objects.filter(facility=self.facility_1)
        self.assertTrue(audits.exists())

    def test_capacity_overflow_prevention(self):
        # Facility 1 has daily capacity 100 kg.
        # Attempting to receive 150 kg without override must fail.
        with self.assertRaises(ValidationError) as ctx:
            receive_incoming_consignment(
                facility_user=self.facility_user_1,
                collection_request=self.collection_req,
                gross_received_weight=Decimal("150.00"),
                capacity_override_authorized=False,
            )
        self.assertIn("exceeds remaining daily capacity", str(ctx.exception))

    def test_capacity_override_with_reason_succeeds(self):
        manifest = receive_incoming_consignment(
            facility_user=self.facility_user_1,
            collection_request=self.collection_req,
            gross_received_weight=Decimal("150.00"),
            capacity_override_authorized=True,
            capacity_override_reason="Emergency hospital clearance approved by SPCB officer",
        )
        self.assertTrue(manifest.capacity_override_authorized)
        self.assertEqual(manifest.gross_received_weight, Decimal("150.00"))

    def test_cross_facility_reception_blocked(self):
        # Collection assigned to Facility 1; Facility 2 cannot receive it
        with self.assertRaises(PermissionDenied):
            receive_incoming_consignment(
                facility_user=self.facility_user_2,
                collection_request=self.collection_req,
            )

    def test_bag_verification_and_acceptance(self):
        manifest = receive_incoming_consignment(
            facility_user=self.facility_user_1,
            collection_request=self.collection_req,
            gross_received_weight=Decimal("25.00"),
        )

        # Verify bag 1 by Bag ID
        receipt1 = verify_reception_bag(
            reception_manifest=manifest,
            bag_identifier_or_token=self.bag_y1.bag_id,
            facility_user=self.facility_user_1,
            actual_weight=Decimal("15.20"),
        )
        self.assertTrue(receipt1.is_verified)
        self.assertEqual(receipt1.actual_weight, Decimal("15.20"))

        # Verify bag 2 by UUID token
        receipt2 = verify_reception_bag(
            reception_manifest=manifest,
            bag_identifier_or_token=str(self.bag_r1.qr_token),
            facility_user=self.facility_user_1,
            actual_weight=Decimal("9.80"),
        )
        self.assertTrue(receipt2.is_verified)

        # Acceptance Decision: Accept Bag 1 -> transitions status to RECEIVED
        record_bag_acceptance_decision(
            bag_receipt=receipt1,
            facility_user=self.facility_user_1,
            decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED,
        )
        self.bag_y1.refresh_from_db()
        self.assertEqual(self.bag_y1.status, WasteBag.Status.RECEIVED)

        # Rejection Decision on Bag 2 (requires reason)
        with self.assertRaises(ValidationError):
            record_bag_acceptance_decision(
                bag_receipt=receipt2,
                facility_user=self.facility_user_1,
                decision=FacilityBagReceipt.AcceptanceDecision.REJECTED,
                rejection_reason="",  # empty reason must fail
            )

        record_bag_acceptance_decision(
            bag_receipt=receipt2,
            facility_user=self.facility_user_1,
            decision=FacilityBagReceipt.AcceptanceDecision.REJECTED,
            rejection_reason="Torn outer packaging with hazardous leakage",
        )

        # Finalize manifest -> status becomes PARTIALLY_ACCEPTED
        finalize_reception_manifest(manifest, self.facility_user_1)
        manifest.refresh_from_db()
        self.assertEqual(manifest.status, FacilityReceptionManifest.Status.PARTIALLY_ACCEPTED)
        self.assertEqual(manifest.accepted_bag_count, 1)
        self.assertEqual(manifest.rejected_bag_count, 1)


class TreatmentProcessWorkflowTests(TreatmentBaseTestCase):
    def setUp(self):
        super().setUp()
        self.manifest = receive_incoming_consignment(
            facility_user=self.facility_user_1,
            collection_request=self.collection_req,
            gross_received_weight=Decimal("25.00"),
        )
        r1 = verify_reception_bag(self.manifest, self.bag_y1.bag_id, self.facility_user_1)
        r2 = verify_reception_bag(self.manifest, self.bag_r1.bag_id, self.facility_user_1)
        record_bag_acceptance_decision(r1, self.facility_user_1, "ACCEPTED")
        record_bag_acceptance_decision(r2, self.facility_user_1, "REJECTED", rejection_reason="Contaminated container")
        finalize_reception_manifest(self.manifest, self.facility_user_1)

    def test_rejected_bag_cannot_enter_treatment(self):
        # Attempting to include rejected bag_r1 in treatment must fail
        with self.assertRaises(ValidationError) as ctx:
            create_treatment_batch(
                facility_user=self.facility_user_1,
                treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
                bag_queryset=WasteBag.objects.filter(id=self.bag_r1.id),
            )
        self.assertIn("cannot enter treatment processing", str(ctx.exception))

    def test_unsupported_treatment_method_blocked(self):
        # Facility 2 does not support INCINERATION
        with self.assertRaises(ValidationError) as ctx:
            create_treatment_batch(
                facility_user=self.facility_user_2,
                treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
                bag_queryset=WasteBag.objects.filter(id=self.bag_y1.id),
            )
        self.assertIn("not licensed or configured", str(ctx.exception))

    def test_treatment_lifecycle_start_and_completion(self):
        # Create Treatment Batch with accepted bag_y1
        batch = create_treatment_batch(
            facility_user=self.facility_user_1,
            treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
            bag_queryset=WasteBag.objects.filter(id=self.bag_y1.id),
            equipment_reference="Dual-Chamber Incinerator Unit 1",
            chamber_temperature=Decimal("1055.0"),
            cycle_duration=60,
        )
        self.assertEqual(batch.status, TreatmentBatch.Status.PENDING)
        self.assertEqual(batch.processing_weight, Decimal("15.00"))

        # Start Treatment
        start_treatment_batch(batch, self.facility_user_1)
        batch.refresh_from_db()
        self.assertEqual(batch.status, TreatmentBatch.Status.IN_PROGRESS)
        self.assertIsNotNone(batch.start_time)

        # Complete Treatment
        complete_treatment_batch(
            treatment_batch=batch,
            facility_user=self.facility_user_1,
            chamber_temperature=Decimal("1060.0"),
            cycle_duration=65,
            remarks="Burn cycle concluded; bottom ash inspected.",
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, TreatmentBatch.Status.COMPLETED)
        self.assertIsNotNone(batch.end_time)

        # WasteBag status must now be TREATED
        self.bag_y1.refresh_from_db()
        self.assertEqual(self.bag_y1.status, WasteBag.Status.TREATED)

    def test_duplicate_active_treatment_batch_blocked(self):
        create_treatment_batch(
            facility_user=self.facility_user_1,
            treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
            bag_queryset=WasteBag.objects.filter(id=self.bag_y1.id),
        )
        # Attempting to put same bag in second treatment batch must raise ValidationError
        with self.assertRaises(ValidationError) as ctx:
            create_treatment_batch(
                facility_user=self.facility_user_1,
                treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
                bag_queryset=WasteBag.objects.filter(id=self.bag_y1.id),
            )
        self.assertIn("already part of treatment batch", str(ctx.exception))


class DisposalWorkflowTests(TreatmentBaseTestCase):
    def setUp(self):
        super().setUp()
        self.manifest = receive_incoming_consignment(
            facility_user=self.facility_user_1,
            collection_request=self.collection_req,
            gross_received_weight=Decimal("25.00"),
        )
        r1 = verify_reception_bag(self.manifest, self.bag_y1.bag_id, self.facility_user_1)
        record_bag_acceptance_decision(r1, self.facility_user_1, "ACCEPTED")

        self.batch = create_treatment_batch(
            facility_user=self.facility_user_1,
            treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
            bag_queryset=WasteBag.objects.filter(id=self.bag_y1.id),
        )

    def test_disposal_blocked_before_treatment_completed(self):
        # Batch is still PENDING
        with self.assertRaises(ValidationError) as ctx:
            create_disposal_record(
                facility_user=self.facility_user_1,
                treatment_batch=self.batch,
                final_disposal_method=DisposalRecord.DisposalMethod.SECURE_LANDFILL,
                disposal_location="Hazardous Landfill Sector 9",
            )
        self.assertIn("Treatment must be COMPLETED first", str(ctx.exception))

    def test_disposal_documentation_and_admin_verification(self):
        # Complete treatment
        start_treatment_batch(self.batch, self.facility_user_1)
        complete_treatment_batch(self.batch, self.facility_user_1)

        # Document Disposal
        disposal = create_disposal_record(
            facility_user=self.facility_user_1,
            treatment_batch=self.batch,
            final_disposal_method=DisposalRecord.DisposalMethod.SECURE_LANDFILL,
            disposal_location="Hazardous Landfill Sector 9, Pit 4",
            remarks="Weighment slip #89201",
        )
        self.assertEqual(disposal.status, DisposalRecord.Status.DOCUMENTED)

        # Bag status transitions to DISPOSED
        self.bag_y1.refresh_from_db()
        self.assertEqual(self.bag_y1.status, WasteBag.Status.DISPOSED)

        # Non-admin cannot verify disposal
        with self.assertRaises(PermissionDenied):
            verify_disposal_record(self.facility_user_1, disposal)

        # Admin verifies disposal
        verify_disposal_record(
            admin_user=self.admin_user,
            disposal_record=disposal,
            verification_notes="Consent CTO and landfill weighment slip verified against CPCB guidelines",
        )
        disposal.refresh_from_db()
        self.assertEqual(disposal.status, DisposalRecord.Status.VERIFIED)
        self.assertEqual(disposal.verified_by, self.admin_user)


class TreatmentViewSecurityTests(TreatmentBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_unauthenticated_redirects(self):
        res = self.client.get(reverse("treatment:facility_dashboard"))
        self.assertEqual(res.status_code, 302)

    def test_collector_cannot_access_facility_dashboard(self):
        self.client.force_login(self.collector_user)
        res = self.client.get(reverse("treatment:facility_dashboard"))
        self.assertEqual(res.status_code, 403)

    def test_hospital_cannot_modify_facility_reception(self):
        self.client.force_login(self.hospital_user)
        res = self.client.get(reverse("treatment:facility_incoming_manifests"))
        self.assertEqual(res.status_code, 403)

    def test_facility_dashboard_and_incoming_accessible(self):
        self.client.force_login(self.facility_user_1)
        res = self.client.get(reverse("treatment:facility_dashboard"))
        self.assertEqual(res.status_code, 200)

        res_incoming = self.client.get(reverse("treatment:facility_incoming_manifests"))
        self.assertEqual(res_incoming.status_code, 200)

    def test_admin_treatment_overview_accessible(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("treatment:admin_treatment_overview"))
        self.assertEqual(res.status_code, 200)
