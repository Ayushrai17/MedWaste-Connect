import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from waste.models import WasteCategory, WasteRegistrationBatch, WasteBag
from collection.models import CollectionRequest
from collection.services import (
    create_collection_request,
    admin_schedule_and_assign,
    collector_accept_assignment,
    verify_bag_for_collection,
    collector_start_pickup,
    collector_complete_collection,
)
from facilities.models import TreatmentFacility
from treatment.models import (
    FacilityReceptionManifest,
    FacilityBagReceipt,
    TreatmentBatch,
    DisposalRecord,
)
from treatment.services import (
    receive_incoming_consignment,
    verify_reception_bag,
    record_bag_acceptance_decision,
    create_treatment_batch,
    start_treatment_batch,
    complete_treatment_batch,
    create_disposal_record,
    verify_disposal_record,
)
from tracking.models import CustodyEvent, CustodyHandover, CustodyDiscrepancy
from tracking.services import (
    record_custody_event,
    create_custody_handover,
    report_discrepancy,
    resolve_discrepancy,
    get_bag_timeline,
    can_user_access_bag_tracking,
)

User = get_user_model()


class TrackingAndCustodyEngineTests(TestCase):
    """
    Test suite for Step 5: Tracking & Centralized Chain of Custody.
    Verifies immutable append-only event logging, dual handovers, discrepancy lifecycle,
    role-based tenant isolation, and statutory reporting.
    """
    def setUp(self):
        # 1. Setup Users for each role
        self.hospital_user = User.objects.create_user(
            username="metro_general",
            email="ops@metro.hospital.in",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Metro General Hospital",
        )
        self.other_hospital = User.objects.create_user(
            username="apollo_clinic",
            email="ops@apollo.in",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Apollo Clinic",
        )
        self.collector_user = User.objects.create_user(
            username="driver_ramesh",
            email="ramesh@logistics.in",
            password="SecurePassword123!",
            role=User.Role.COLLECTOR,
            organization_name="CleanCity Waste Logistics",
        )
        self.other_collector = User.objects.create_user(
            username="driver_suresh",
            email="suresh@logistics.in",
            password="SecurePassword123!",
            role=User.Role.COLLECTOR,
        )
        self.facility_operator = User.objects.create_user(
            username="plant_operator",
            email="ops@ecotreat.in",
            password="SecurePassword123!",
            role=User.Role.FACILITY,
            organization_name="EcoTreat Solutions",
        )
        self.other_facility_operator = User.objects.create_user(
            username="other_operator",
            email="ops@otherplant.in",
            password="SecurePassword123!",
            role=User.Role.FACILITY,
        )
        self.admin_user = User.objects.create_superuser(
            username="cpcb_director",
            email="director@cpcb.gov.in",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
        )

        # 2. Setup Waste Categories (seeded by migration, get or create)
        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={
                "name": "Infectious Anatomical Waste",
                "display_color": "#FFD700",
                "container_type": "Non-chlorinated plastic bag",
                "treatment_description": "Incineration or Plasma Pyrolysis",
                "is_active": True,
                "display_order": 1,
            }
        )
        self.cat_red, _ = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={
                "name": "Contaminated Recyclable Waste",
                "display_color": "#DC3545",
                "container_type": "Non-chlorinated plastic bag",
                "treatment_description": "Autoclaving followed by shredding",
                "is_active": True,
                "display_order": 2,
            }
        )

        # 3. Setup Treatment Facilities
        self.facility = TreatmentFacility.objects.create(
            name="EcoTreat Central Plant",
            operator_user=self.facility_operator,
            license_number="PCB-PLANT-001",
            address="Plot 44, Eco Industrial Park",
            district="Central",
            state="Delhi",
            postal_code="110001",
            contact_person="R. K. Sharma",
            contact_phone="+911123456789",
            contact_email="ops@ecotreat.in",
            daily_capacity_kg=Decimal("5000.00"),
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
            is_active=True,
            supports_incineration=True,
            supports_autoclaving=True,
        )
        self.other_facility = TreatmentFacility.objects.create(
            name="GreenEarth CBWTF",
            operator_user=self.other_facility_operator,
            license_number="PCB-PLANT-002",
            address="Plot 88, Green Zone",
            district="North",
            state="Delhi",
            postal_code="110002",
            contact_person="Anita Verma",
            contact_phone="+911198765432",
            contact_email="ops@otherplant.in",
            daily_capacity_kg=Decimal("4000.00"),
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
            is_active=True,
            supports_incineration=True,
        )

        # 4. Setup Batch & Waste Bag
        self.batch = WasteRegistrationBatch.objects.create(
            hospital=self.hospital_user,
            registered_by=self.hospital_user,
            ward="ICU Wing B",
            department="Critical Care",
            bag_count=1,
            total_weight=Decimal("5.50"),
        )
        self.bag = WasteBag.objects.create(
            batch=self.batch,
            hospital=self.hospital_user,
            registered_by=self.hospital_user,
            category=self.cat_yellow,
            ward="ICU Wing B",
            department="Critical Care",
            weight=Decimal("5.50"),
            status=WasteBag.Status.REGISTERED,
            qr_status=WasteBag.QRStatus.READY,
        )

        self.client = Client()

    def test_immutable_custody_event_save_and_delete_protections(self):
        """
        Verify that CustodyEvent instances are strictly append-only and cannot be altered or deleted.
        """
        event = record_custody_event(
            bag=self.bag,
            event_type=CustodyEvent.EventType.BAG_REGISTERED,
            new_custody_status=WasteBag.Status.REGISTERED,
            actor=self.hospital_user,
            origin_location="Ward ICU",
            weight_at_event=Decimal("5.50"),
        )
        self.assertIsNotNone(event.pk)
        self.assertTrue(event.event_reference.startswith("MWC-CUST-"))

        # Verify modification is blocked
        event.remarks = "Attempted unauthorized change to historical audit record."
        with self.assertRaises(ValidationError) as cm:
            event.save()
        self.assertIn("append-only and cannot be altered", str(cm.exception))

        # Verify deletion is blocked
        with self.assertRaises(ValidationError) as del_cm:
            event.delete()
        self.assertIn("permanent legal records and cannot be deleted", str(del_cm.exception))

    def test_end_to_end_custody_lifecycle_workflow_and_event_emission(self):
        """
        Walks a physical waste bag through all 6 lifecycle stages:
        1. Ward Registration -> 2. Collection Dispatch -> 3. Handover & Transit ->
        4. Facility Reception -> 5. Treatment Run -> 6. Final Residue Disposal.
        Verifies events and handovers are logged at every operational transition.
        """
        # Stage 1: Registration Event
        record_custody_event(
            bag=self.bag,
            event_type=CustodyEvent.EventType.BAG_REGISTERED,
            new_custody_status=WasteBag.Status.REGISTERED,
            actor=self.hospital_user,
            origin_location=self.bag.ward,
            weight_at_event=self.bag.weight,
        )
        record_custody_event(
            bag=self.bag,
            event_type=CustodyEvent.EventType.QR_GENERATED,
            new_custody_status=WasteBag.Status.REGISTERED,
            actor=self.hospital_user,
        )

        # Stage 2: Hospital Requests Pickup
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        req = create_collection_request(
            hospital_user=self.hospital_user,
            form_data={
                "preferred_pickup_date": tomorrow,
                "preferred_pickup_time": datetime.time(10, 0),
                "pickup_address": "Main Hospital Dock A",
                "contact_person": "Dr. Sharma",
                "contact_phone": "+919876543210",
                "special_instructions": "Biohazard yellow bags ready.",
            },
            bag_queryset=WasteBag.objects.filter(id=self.bag.id),
        )
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.COLLECTION_REQUESTED).exists())

        # Admin schedules and assigns collector
        admin_schedule_and_assign(
            collection_request=req,
            admin_user=self.admin_user,
            confirmed_date=tomorrow,
            confirmed_time=datetime.time(11, 0),
            collector_user=self.collector_user,
            destination_facility=self.facility,
        )
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.COLLECTION_SCHEDULED).exists())
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.COLLECTOR_ASSIGNED).exists())

        # Collector accepts assignment
        collector_accept_assignment(collection_request=req, collector_user=self.collector_user)
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.COLLECTOR_ACCEPTED).exists())

        # Stage 3: Collector verifies bag and completes collection
        collector_start_pickup(collection_request=req, collector_user=self.collector_user)
        verify_bag_for_collection(collection_request=req, bag_identifier_or_token=self.bag.bag_id, collector_user=self.collector_user)
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.COLLECTOR_VERIFIED).exists())

        collector_complete_collection(collection_request=req, collector_user=self.collector_user)
        self.bag.refresh_from_db()
        self.assertEqual(self.bag.status, WasteBag.Status.IN_TRANSIT)

        # Verify Handover created for hospital to collector
        handover_hosp_col = CustodyHandover.objects.filter(
            collection_request=req,
            handover_type=CustodyHandover.HandoverType.HOSPITAL_TO_COLLECTOR,
        ).first()
        self.assertIsNotNone(handover_hosp_col)
        self.assertEqual(handover_hosp_col.releasing_user, self.hospital_user)
        self.assertEqual(handover_hosp_col.receiving_user, self.collector_user)

        # Stage 4: Facility Reception
        manifest = receive_incoming_consignment(
            facility_user=self.facility_operator,
            collection_request=req,
            gross_received_weight=Decimal("5.50"),
        )
        self.assertIsNotNone(manifest)
        # Verify Collector to Facility Handover
        handover_col_fac = CustodyHandover.objects.filter(
            reception_manifest=manifest,
            handover_type=CustodyHandover.HandoverType.COLLECTOR_TO_FACILITY,
        ).first()
        self.assertIsNotNone(handover_col_fac)

        # Facility gate scans & verifies bag
        receipt = verify_reception_bag(
            reception_manifest=manifest,
            bag_identifier_or_token=self.bag.bag_id,
            facility_user=self.facility_operator,
            actual_weight=Decimal("5.50"),
            packaging_condition=FacilityBagReceipt.PackagingCondition.INTACT,
        )
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.FACILITY_VERIFIED).exists())

        # Record acceptance decision
        record_bag_acceptance_decision(
            bag_receipt=receipt,
            facility_user=self.facility_operator,
            decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED,
        )
        self.bag.refresh_from_db()
        self.assertEqual(self.bag.status, WasteBag.Status.RECEIVED)
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.FACILITY_ACCEPTED).exists())

        # Stage 5: Treatment Run
        t_batch = create_treatment_batch(
            facility_user=self.facility_operator,
            treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
            bag_queryset=WasteBag.objects.filter(id=self.bag.id),
            equipment_reference="INCINERATOR-ALPHA-1",
            chamber_temperature=Decimal("1100.0"),
        )
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.TREATMENT_BATCHED).exists())

        start_treatment_batch(treatment_batch=t_batch, facility_user=self.facility_operator)
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.TREATMENT_STARTED).exists())

        complete_treatment_batch(treatment_batch=t_batch, facility_user=self.facility_operator)
        self.bag.refresh_from_db()
        self.assertEqual(self.bag.status, WasteBag.Status.TREATED)
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.TREATMENT_COMPLETED).exists())

        # Stage 6: Final Disposal Record
        disposal = create_disposal_record(
            facility_user=self.facility_operator,
            treatment_batch=t_batch,
            final_disposal_method=DisposalRecord.DisposalMethod.SECURE_LANDFILL,
            disposal_location="State PCB Secured Cell 4",
        )
        self.bag.refresh_from_db()
        self.assertEqual(self.bag.status, WasteBag.Status.DISPOSED)
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.DISPOSAL_DOCUMENTED).exists())

        # Verify compliance audit by administrator
        verify_disposal_record(admin_user=self.admin_user, disposal_record=disposal, verification_notes="Approved compliance review.")
        self.assertTrue(CustodyEvent.objects.filter(bag=self.bag, event_type=CustodyEvent.EventType.DISPOSAL_VERIFIED).exists())

        # Check total timeline compile
        timeline = get_bag_timeline(self.bag)
        self.assertEqual(timeline["progress_pct"], 100)
        self.assertEqual(timeline["current_stage_num"], 6)
        self.assertTrue(all(s["is_completed"] for s in timeline["stages"]))

    def test_discrepancy_reporting_and_resolution_flow(self):
        """
        Verify exception logging, administrative alerts, and resolution state changes.
        """
        discrepancy = report_discrepancy(
            discrepancy_type=CustodyDiscrepancy.DiscrepancyType.DAMAGED_PACKAGING,
            severity=CustodyDiscrepancy.Severity.HIGH,
            description="Torn biohazard bag leaking saline residue.",
            reported_by=self.collector_user,
            bag=self.bag,
        )
        self.assertTrue(discrepancy.discrepancy_reference.startswith("MWC-DISC-"))
        self.assertEqual(discrepancy.status, CustodyDiscrepancy.Status.OPEN)

        # Resolution by admin
        resolve_discrepancy(
            discrepancy=discrepancy,
            resolved_by=self.admin_user,
            resolution_notes="Bag placed into double secondary containment container and reweighed.",
            new_status=CustodyDiscrepancy.Status.RESOLVED,
        )
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, CustodyDiscrepancy.Status.RESOLVED)
        self.assertEqual(discrepancy.resolved_by, self.admin_user)
        self.assertIsNotNone(discrepancy.resolved_at)

    def test_tenant_isolation_and_rbac_bag_tracking(self):
        """
        Ensure strict role-based access control and tenant isolation:
        - Hospital A cannot view Hospital B's bags.
        - Collector cannot view bags not in their assigned requests.
        - Facility operator cannot view bags not manifested at their facility.
        - Admin has full visibility.
        """
        # Bag belongs to hospital_user
        self.assertTrue(can_user_access_bag_tracking(self.hospital_user, self.bag))
        self.assertFalse(can_user_access_bag_tracking(self.other_hospital, self.bag))

        # Other collector cannot access
        self.assertFalse(can_user_access_bag_tracking(self.other_collector, self.bag))

        # Admin has full access
        self.assertTrue(can_user_access_bag_tracking(self.admin_user, self.bag))

        # Test view access via HTTP Client
        # 1. Other hospital gets redirected or denied
        self.client.force_login(self.other_hospital)
        url = reverse("tracking:bag_tracking_detail", kwargs={"bag_id": self.bag.bag_id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)  # Redirects with error message

        # 2. Owner hospital gets 200 OK
        self.client.force_login(self.hospital_user)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.bag.bag_id)
        self.assertContains(resp, "Custody Lifecycle Progression")

        # 3. Admin gets 200 OK
        self.client.force_login(self.admin_user)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_public_qr_tracking_privacy_protection(self):
        """
        Verify that public QR tracking endpoint (unauthenticated) exposes verified
        biomedical category and status, but hides internal ward notes, patient info,
        and private operator details.
        """
        public_url = reverse("waste:track_bag", kwargs={"qr_token": str(self.bag.qr_token)})
        resp = self.client.get(public_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.bag.bag_id)
        self.assertContains(resp, "Authentic Registered Container")
        # Ensure private internal notes are not leaked
        self.assertNotContains(resp, "ICU Wing B")
        self.assertNotContains(resp, "Dr. Sharma")

    def test_statutory_custody_report_printable_views(self):
        """
        Verify that individual bag custody certificate and consignment manifest render successfully.
        """
        self.client.force_login(self.hospital_user)
        # Bag certificate
        cert_url = reverse("tracking:bag_custody_report", kwargs={"bag_id": self.bag.bag_id})
        resp = self.client.get(cert_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "STATUTORY CHAIN OF CUSTODY CERTIFICATE")
        self.assertContains(resp, self.bag.bag_id)

    def test_compensating_correction_admin_workflow(self):
        """
        Verify that an administrator can append an immutable compensating correction event.
        """
        self.client.force_login(self.admin_user)
        post_url = reverse("tracking:add_compensating_correction", kwargs={"bag_id": self.bag.bag_id})
        resp = self.client.post(post_url, {
            "remarks": "Administrative recalibration: Scale zero-point reset per calibration log #412.",
            "weight_at_event": "5.45",
        })
        self.assertEqual(resp.status_code, 302)

        correction_event = CustodyEvent.objects.filter(
            bag=self.bag,
            is_compensating_event=True,
        ).first()
        self.assertIsNotNone(correction_event)
        self.assertEqual(correction_event.weight_at_event, Decimal("5.45"))
        self.assertIn("Administrative recalibration", correction_event.remarks)
