import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from waste.models import WasteCategory, WasteRegistrationBatch, WasteBag
from collection.models import CollectionRequest, CollectionStatusHistory, BagVerificationRecord
from collection.services import (
    get_active_bag_ids_for_hospital,
    get_eligible_bags_for_hospital,
    check_collector_overlap,
    create_collection_request,
    cancel_collection_request,
    admin_review_request,
    admin_approve_request,
    admin_reject_request,
    admin_schedule_and_assign,
    collector_accept_assignment,
    collector_reject_assignment,
    collector_start_pickup,
    collector_complete_collection,
    verify_bag_for_collection,
)
from notifications.models import Notification

User = get_user_model()


class CollectionTestBase(TestCase):
    """Base setup with seeded users, waste categories, and test bags."""

    def setUp(self):
        super().setUp()

        # Seed Users
        self.hospital_user = User.objects.create_user(
            username="st_marys",
            email="stmarys@example.com",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="St. Mary's General Hospital",
            phone="+91 98765 43210",
        )

        self.hospital_user_2 = User.objects.create_user(
            username="apollo_hospital",
            email="apollo@example.com",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Apollo Clinic",
            phone="+91 98765 11111",
        )

        self.admin_user = User.objects.create_user(
            username="admin_dispatcher",
            email="admin@medwaste.example.com",
            password="SecurePassword123!",
            role=User.Role.ADMIN,
            organization_name="MedWaste Operations",
        )

        self.collector_user = User.objects.create_user(
            username="driver_rajesh",
            email="rajesh@logistics.example.com",
            password="SecurePassword123!",
            role=User.Role.COLLECTOR,
            first_name="Rajesh",
            last_name="Kumar",
            organization_name="EcoFleet Logistics",
            phone="+91 98765 22222",
        )

        self.collector_user_2 = User.objects.create_user(
            username="driver_amit",
            email="amit@logistics.example.com",
            password="SecurePassword123!",
            role=User.Role.COLLECTOR,
            first_name="Amit",
            last_name="Sharma",
            organization_name="EcoFleet Logistics",
            phone="+91 98765 33333",
        )

        # Statutory Waste Categories (already seeded by migration 0002)
        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={
                "name": "Yellow - Anatomical / Soiled Waste",
                "display_color": "#FFD200",
                "container_type": "Non-chlorinated yellow plastic bag",
                "treatment_description": "Incineration or plasma pyrolysis",
                "display_order": 1,
            }
        )

        self.cat_red, _ = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={
                "name": "Red - Contaminated Recyclable Waste",
                "display_color": "#E53935",
                "container_type": "Red non-chlorinated plastic bag",
                "treatment_description": "Autoclaving followed by shredding",
                "display_order": 2,
            }
        )

        # Seed Waste Bags for Hospital 1
        self.bag1 = WasteBag.objects.create(
            hospital=self.hospital_user,
            registered_by=self.hospital_user,
            category=self.cat_yellow,
            ward="ICU Wing B",
            department="Intensive Care",
            weight=Decimal("4.50"),
            status=WasteBag.Status.REGISTERED,
        )

        self.bag2 = WasteBag.objects.create(
            hospital=self.hospital_user,
            registered_by=self.hospital_user,
            category=self.cat_red,
            ward="Surgical Theater 2",
            department="Surgery",
            weight=Decimal("3.20"),
            status=WasteBag.Status.REGISTERED,
        )

        # Seed Waste Bag for Hospital 2
        self.bag_h2 = WasteBag.objects.create(
            hospital=self.hospital_user_2,
            registered_by=self.hospital_user_2,
            category=self.cat_yellow,
            ward="Emergency Ward",
            department="Casualty",
            weight=Decimal("2.10"),
            status=WasteBag.Status.REGISTERED,
        )

        self.tomorrow = timezone.localdate() + timedelta(days=1)
        self.day_after = timezone.localdate() + timedelta(days=2)


class CollectionModelTests(CollectionTestBase):
    """Test model methods, validations, and reference generation."""

    def test_reference_number_generation(self):
        req = CollectionRequest.objects.create(
            hospital=self.hospital_user,
            preferred_pickup_date=self.tomorrow,
            preferred_pickup_time=time(10, 0),
            pickup_address="Rear Loading Dock B, Ground Floor",
            contact_person="Sister Mary",
            contact_phone="+91 98765 43210",
        )
        self.assertTrue(req.reference_number.startswith("MWC-REQ-"))
        self.assertEqual(len(req.reference_number), 23)

    def test_properties_and_aggregates(self):
        req = CollectionRequest.objects.create(
            hospital=self.hospital_user,
            preferred_pickup_date=self.tomorrow,
            preferred_pickup_time=time(10, 0),
            pickup_address="Rear Dock",
            contact_person="Sister Mary",
            contact_phone="+91 98765 43210",
            status=CollectionRequest.Status.REQUESTED,
        )
        req.bags.add(self.bag1, self.bag2)
        req.update_aggregates()

        self.assertEqual(req.total_bags, 2)
        self.assertEqual(req.total_weight, Decimal("7.70"))
        self.assertTrue(req.is_cancellable)
        self.assertTrue(req.can_be_assigned)
        self.assertEqual(req.effective_pickup_date, self.tomorrow)
        self.assertEqual(req.effective_pickup_time, time(10, 0))

    def test_confirmed_schedule_precedence(self):
        req = CollectionRequest.objects.create(
            hospital=self.hospital_user,
            preferred_pickup_date=self.tomorrow,
            preferred_pickup_time=time(10, 0),
            confirmed_pickup_date=self.day_after,
            confirmed_pickup_time=time(14, 30),
            pickup_address="Rear Dock",
            contact_person="Sister Mary",
            contact_phone="+91 98765 43210",
        )
        self.assertEqual(req.effective_pickup_date, self.day_after)
        self.assertEqual(req.effective_pickup_time, time(14, 30))

    def test_past_date_validation(self):
        past_date = timezone.localdate() - timedelta(days=1)
        req = CollectionRequest(
            hospital=self.hospital_user,
            preferred_pickup_date=past_date,
            preferred_pickup_time=time(10, 0),
            pickup_address="Rear Dock",
            contact_person="Sister Mary",
            contact_phone="+91 98765 43210",
        )
        with self.assertRaises(ValidationError):
            req.clean()


class HospitalWorkflowServiceTests(CollectionTestBase):
    """Test hospital collection request creation and isolation."""

    def test_create_collection_request_success(self):
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(9, 30),
            "alternative_pickup_date": self.day_after,
            "alternative_pickup_time": time(11, 0),
            "pickup_address": "Gate 4, Biomedical Loading Dock",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
            "special_instructions": "Wear protective gear",
            "operational_notes": "Handle yellow bags with care",
        }
        bags_qs = WasteBag.objects.filter(id__in=[self.bag1.id, self.bag2.id])
        req = create_collection_request(self.hospital_user, form_data, bags_qs)

        self.assertEqual(req.hospital, self.hospital_user)
        self.assertEqual(req.status, CollectionRequest.Status.REQUESTED)
        self.assertEqual(req.total_bags, 2)
        self.assertEqual(req.total_weight, Decimal("7.70"))

        # Verify BagVerificationRecord created
        verifications = BagVerificationRecord.objects.filter(collection_request=req)
        self.assertEqual(verifications.count(), 2)
        self.assertFalse(verifications.first().is_verified)

        # Verify CollectionStatusHistory created
        history = CollectionStatusHistory.objects.filter(request=req)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().new_status, CollectionRequest.Status.REQUESTED)
        self.assertEqual(history.first().changed_by, self.hospital_user)

        # Verify Notifications created
        admin_notif = Notification.objects.filter(
            notification_type=Notification.NotificationType.REQUEST_SUBMITTED,
            reference_id=req.reference_number
        ).exclude(recipient=self.hospital_user)
        self.assertTrue(admin_notif.exists())

        hospital_notif = Notification.objects.filter(
            recipient=self.hospital_user,
            notification_type=Notification.NotificationType.REQUEST_SUBMITTED,
            reference_id=req.reference_number
        )
        self.assertTrue(hospital_notif.exists())

    def test_duplicate_active_bag_prevention(self):
        # Create first request with bag1
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(9, 30),
            "pickup_address": "Gate 4",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        create_collection_request(self.hospital_user, form_data, WasteBag.objects.filter(id=self.bag1.id))

        # Attempt to create second request with bag1 while first request is active
        with self.assertRaises(ValidationError) as ctx:
            create_collection_request(self.hospital_user, form_data, WasteBag.objects.filter(id=self.bag1.id))
        self.assertIn("already part of an active collection request", str(ctx.exception))

    def test_tenant_isolation_cannot_request_other_hospital_bags(self):
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(9, 30),
            "pickup_address": "Gate 4",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        # Attempt to request bag owned by hospital 2
        with self.assertRaises(ValidationError) as ctx:
            create_collection_request(self.hospital_user, form_data, WasteBag.objects.filter(id=self.bag_h2.id))
        self.assertIn("do not belong to your hospital", str(ctx.exception))

    def test_eligible_bags_filtering(self):
        eligible = get_eligible_bags_for_hospital(self.hospital_user)
        self.assertEqual(eligible.count(), 2)

        # Commit bag1 to an active request
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(9, 30),
            "pickup_address": "Gate 4",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        create_collection_request(self.hospital_user, form_data, WasteBag.objects.filter(id=self.bag1.id))

        # Bag 1 should now be excluded from eligible bags
        eligible_now = get_eligible_bags_for_hospital(self.hospital_user)
        self.assertEqual(eligible_now.count(), 1)
        self.assertEqual(eligible_now.first().id, self.bag2.id)

    def test_cancel_collection_request(self):
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(9, 30),
            "pickup_address": "Gate 4",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        req = create_collection_request(self.hospital_user, form_data, WasteBag.objects.filter(id=self.bag1.id))

        cancel_collection_request(req, self.hospital_user, cancellation_reason="Ward schedule revised")
        req.refresh_from_db()

        self.assertEqual(req.status, CollectionRequest.Status.CANCELLED)
        self.assertEqual(req.rejection_reason, "Ward schedule revised")

        # After cancellation, bag1 is eligible again
        eligible = get_eligible_bags_for_hospital(self.hospital_user)
        self.assertIn(self.bag1, eligible)


class AdminOperationsServiceTests(CollectionTestBase):
    """Test administrative review, approval, rejection, and conflict-safe scheduling."""

    def setUp(self):
        super().setUp()
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(10, 0),
            "pickup_address": "Central Dock",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        self.req = create_collection_request(
            self.hospital_user,
            form_data,
            WasteBag.objects.filter(id__in=[self.bag1.id, self.bag2.id])
        )

    def test_admin_review_and_approve(self):
        admin_review_request(self.req, self.admin_user)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, CollectionRequest.Status.UNDER_REVIEW)

        admin_approve_request(self.req, self.admin_user, remarks="Approved for morning route")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, CollectionRequest.Status.APPROVED)
        self.assertEqual(self.req.admin_notes, "Approved for morning route")

    def test_admin_reject_request(self):
        admin_reject_request(self.req, self.admin_user, rejection_reason="Dock under maintenance")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, CollectionRequest.Status.CANCELLED)
        self.assertEqual(self.req.rejection_reason, "Dock under maintenance")

    def test_admin_schedule_and_assign_preserves_preferences(self):
        admin_schedule_and_assign(
            collection_request=self.req,
            admin_user=self.admin_user,
            confirmed_date=self.tomorrow,
            confirmed_time=time(11, 0),
            collector_user=self.collector_user,
            remarks="Allocated to Vehicle 04",
        )
        self.req.refresh_from_db()

        self.assertEqual(self.req.status, CollectionRequest.Status.ASSIGNED)
        self.assertEqual(self.req.assigned_collector, self.collector_user)
        self.assertEqual(self.req.confirmed_pickup_date, self.tomorrow)
        self.assertEqual(self.req.confirmed_pickup_time, time(11, 0))
        # Hospital preferred fields remain untouched
        self.assertEqual(self.req.preferred_pickup_date, self.tomorrow)
        self.assertEqual(self.req.preferred_pickup_time, time(10, 0))

    def test_collector_overlap_prevention(self):
        # Schedule first request at 11:00 on tomorrow
        admin_schedule_and_assign(
            collection_request=self.req,
            admin_user=self.admin_user,
            confirmed_date=self.tomorrow,
            confirmed_time=time(11, 0),
            collector_user=self.collector_user,
        )

        # Create a second request from hospital 2
        form_data_2 = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(11, 15),
            "pickup_address": "Apollo Gate 1",
            "contact_person": "Dr. Kumar",
            "contact_phone": "+91 98765 11111",
        }
        req2 = create_collection_request(
            self.hospital_user_2,
            form_data_2,
            WasteBag.objects.filter(id=self.bag_h2.id)
        )

        # Attempt to assign same collector within 45 min window (e.g., 11:20)
        with self.assertRaises(ValidationError) as ctx:
            admin_schedule_and_assign(
                collection_request=req2,
                admin_user=self.admin_user,
                confirmed_date=self.tomorrow,
                confirmed_time=time(11, 20),
                collector_user=self.collector_user,
            )
        self.assertIn("already assigned to active request", str(ctx.exception))

        # Assigning to collector 2 at 11:20 succeeds
        admin_schedule_and_assign(
            collection_request=req2,
            admin_user=self.admin_user,
            confirmed_date=self.tomorrow,
            confirmed_time=time(11, 20),
            collector_user=self.collector_user_2,
        )
        req2.refresh_from_db()
        self.assertEqual(req2.assigned_collector, self.collector_user_2)

        # Assigning collector 1 with > 45 minutes separation (e.g., 12:00) succeeds
        is_overlapping, _ = check_collector_overlap(self.collector_user, self.tomorrow, time(12, 0))
        self.assertFalse(is_overlapping)


class CollectorWorkflowServiceTests(CollectionTestBase):
    """Test collector acceptance, rejection, journey start, handover, and bag verification."""

    def setUp(self):
        super().setUp()
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(10, 0),
            "pickup_address": "Central Dock",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        self.req = create_collection_request(
            self.hospital_user,
            form_data,
            WasteBag.objects.filter(id__in=[self.bag1.id, self.bag2.id])
        )
        admin_schedule_and_assign(
            collection_request=self.req,
            admin_user=self.admin_user,
            confirmed_date=self.tomorrow,
            confirmed_time=time(11, 0),
            collector_user=self.collector_user,
        )

    def test_collector_accept_assignment(self):
        collector_accept_assignment(self.req, self.collector_user, remarks="Will arrive on schedule.")
        self.req.refresh_from_db()

        self.assertEqual(self.req.status, CollectionRequest.Status.ACCEPTED)
        self.assertIn("Will arrive", self.req.collector_remarks)

    def test_collector_reject_assignment_returns_to_queue(self):
        collector_reject_assignment(self.req, self.collector_user, rejection_reason="Vehicle tire puncture")
        self.req.refresh_from_db()

        self.assertEqual(self.req.status, CollectionRequest.Status.COLLECTOR_REJECTED)
        self.assertIsNone(self.req.assigned_collector)
        self.assertEqual(self.req.rejection_reason, "Vehicle tire puncture")
        self.assertTrue(self.req.can_be_assigned)

    def test_collector_pickup_and_completion_lifecycle(self):
        # Accept
        collector_accept_assignment(self.req, self.collector_user)

        # Start Pickup
        collector_start_pickup(self.req, self.collector_user, remarks="Departed depot")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, CollectionRequest.Status.PICKUP_IN_PROGRESS)

        # Verify bags
        verify_bag_for_collection(self.req, self.bag1.bag_id, self.collector_user)
        verify_bag_for_collection(self.req, str(self.bag2.qr_token), self.collector_user)

        v1 = BagVerificationRecord.objects.get(collection_request=self.req, bag=self.bag1)
        v2 = BagVerificationRecord.objects.get(collection_request=self.req, bag=self.bag2)
        self.assertTrue(v1.is_verified)
        self.assertTrue(v2.is_verified)

        # Complete Collection
        collector_complete_collection(self.req, self.collector_user, remarks="Bags loaded securely")
        self.req.refresh_from_db()

        self.assertEqual(self.req.status, CollectionRequest.Status.COLLECTED)
        self.assertIsNotNone(self.req.actual_collection_time)

        # Verify physical bag custody state transitioned to IN_TRANSIT
        self.bag1.refresh_from_db()
        self.bag2.refresh_from_db()
        self.assertEqual(self.bag1.status, WasteBag.Status.IN_TRANSIT)
        self.assertEqual(self.bag2.status, WasteBag.Status.IN_TRANSIT)

    def test_foreign_bag_verification_fails(self):
        with self.assertRaises(ValidationError) as ctx:
            verify_bag_for_collection(self.req, self.bag_h2.bag_id, self.collector_user)
        self.assertIn("is not part of collection request", str(ctx.exception))


class CollectionViewSecurityAndIsolationTests(CollectionTestBase):
    """Test RBAC, multi-tenant HTTP isolation, and view protections."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        form_data = {
            "preferred_pickup_date": self.tomorrow,
            "preferred_pickup_time": time(10, 0),
            "pickup_address": "Central Dock",
            "contact_person": "Sister Mary",
            "contact_phone": "+91 98765 43210",
        }
        self.req = create_collection_request(
            self.hospital_user,
            form_data,
            WasteBag.objects.filter(id__in=[self.bag1.id, self.bag2.id])
        )
        admin_schedule_and_assign(
            collection_request=self.req,
            admin_user=self.admin_user,
            confirmed_date=self.tomorrow,
            confirmed_time=time(11, 0),
            collector_user=self.collector_user,
        )

    def test_unauthenticated_access_redirects(self):
        res = self.client.get(reverse("collection:hospital_request_list"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)

    def test_cross_role_access_forbidden(self):
        # Hospital cannot access admin dashboard
        self.client.force_login(self.hospital_user)
        res = self.client.get(reverse("collection:admin_dashboard"))
        self.assertEqual(res.status_code, 403)

        # Collector cannot access hospital request creation
        self.client.force_login(self.collector_user)
        res = self.client.get(reverse("collection:hospital_request_create"))
        self.assertEqual(res.status_code, 403)

    def test_hospital_tenant_isolation(self):
        # Hospital 2 cannot access Hospital 1's request detail
        self.client.force_login(self.hospital_user_2)
        res = self.client.get(reverse("collection:hospital_request_detail", kwargs={"reference_number": self.req.reference_number}))
        self.assertEqual(res.status_code, 403)

        # Hospital 1 can view its own request
        self.client.force_login(self.hospital_user)
        res = self.client.get(reverse("collection:hospital_request_detail", kwargs={"reference_number": self.req.reference_number}))
        self.assertEqual(res.status_code, 200)

    def test_collector_isolation(self):
        # Collector 2 cannot view Collector 1's assigned request
        self.client.force_login(self.collector_user_2)
        res = self.client.get(reverse("collection:collector_request_detail", kwargs={"reference_number": self.req.reference_number}))
        self.assertEqual(res.status_code, 403)

        # Assigned Collector 1 can view it
        self.client.force_login(self.collector_user)
        res = self.client.get(reverse("collection:collector_request_detail", kwargs={"reference_number": self.req.reference_number}))
        self.assertEqual(res.status_code, 200)

    def test_admin_has_full_access(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("collection:admin_request_detail", kwargs={"reference_number": self.req.reference_number}))
        self.assertEqual(res.status_code, 200)
        res_mgmt = self.client.get(reverse("collection:admin_dashboard"))
        self.assertEqual(res_mgmt.status_code, 200)

    def test_notifications_views(self):
        self.client.force_login(self.hospital_user)
        res = self.client.get(reverse("notifications:list"))
        self.assertEqual(res.status_code, 200)

        notif = Notification.objects.filter(recipient=self.hospital_user).first()
        if notif:
            mark_res = self.client.post(reverse("notifications:mark_read", kwargs={"notification_id": notif.id}))
            self.assertEqual(mark_res.status_code, 302)
            notif.refresh_from_db()
            self.assertTrue(notif.is_read)

        mark_all = self.client.post(reverse("notifications:mark_all_read"))
        self.assertEqual(mark_all.status_code, 302)
        self.assertFalse(Notification.objects.filter(recipient=self.hospital_user, is_read=False).exists())


class CollectionRegressionBugfixTests(CollectionTestBase):
    """Regression coverage for GET /collection/requests/new/ without User.address."""

    def test_request_create_get_and_submit_without_user_address_attr(self):
        self.assertFalse(hasattr(self.hospital_user, "address"))
        self.client.force_login(self.hospital_user)

        # GET /collection/requests/new/ succeeds with HTTP 200 and only shows own bags
        res = self.client.get(reverse("collection:hospital_request_create"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.bag1.bag_id)
        self.assertNotContains(res, self.bag_h2.bag_id)

        # Submitting valid collection request saves address & updates bag status
        post_res = self.client.post(
            reverse("collection:hospital_request_create"),
            {
                "bags": [self.bag1.id],
                "preferred_pickup_date": self.tomorrow.isoformat(),
                "preferred_pickup_time": "10:30",
                "pickup_address": "Loading Dock Gate 4, Main Wing",
                "contact_person": "Nurse Coordinator",
                "contact_phone": "+91 98765 43210",
            },
        )
        self.assertEqual(post_res.status_code, 302)
        created_req = CollectionRequest.objects.get(hospital=self.hospital_user)
        self.assertEqual(created_req.pickup_address, "Loading Dock Gate 4, Main Wing")
        self.assertEqual(created_req.total_weight, Decimal("4.50"))

        # Subsequent GET pre-fills previous pickup_address safely
        res_next = self.client.get(reverse("collection:hospital_request_create"))
        self.assertEqual(res_next.status_code, 200)
        self.assertEqual(res_next.context["form"].initial.get("pickup_address"), "Loading Dock Gate 4, Main Wing")

    def test_request_create_rejects_foreign_hospital_bag(self):
        self.client.force_login(self.hospital_user)
        res = self.client.post(
            reverse("collection:hospital_request_create"),
            {
                "bags": [self.bag_h2.id],
                "preferred_pickup_date": self.tomorrow.isoformat(),
                "preferred_pickup_time": "11:00",
                "pickup_address": "Spoofed Bay",
                "contact_person": "Nurse",
                "contact_phone": "+91 98765 43210",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(CollectionRequest.objects.filter(hospital=self.hospital_user).count(), 0)
