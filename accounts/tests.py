import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, AdminActionAudit, PlatformSettings
from accounts.services.admin_service import (
    toggle_user_active_status,
    suspend_user,
    reactivate_user,
    approve_user_onboarding,
    reject_user_onboarding,
    broadcast_system_notification,
    get_platform_kpis,
)
from facilities.models import TreatmentFacility
from facilities.services import (
    register_treatment_facility,
    approve_treatment_facility,
    reject_treatment_facility,
    suspend_treatment_facility,
    reinstate_treatment_facility,
)
from collection.models import CollectionRequest
from collection.services import admin_schedule_and_assign
from waste.models import WasteCategory, WasteRegistrationBatch, WasteBag
from tracking.models import CustodyDiscrepancy, CustodyHandover
from reports.models import ComplianceThresholdConfig
from notifications.models import Notification

User = get_user_model()


class Step7AdminBaseTestCase(TestCase):
    """
    Base test fixture providing administrators, hospitals, collectors, facilities,
    waste categories, and initial operational entities.
    """
    @classmethod
    def setUpTestData(cls):
        # 1. Primary & Secondary Admins
        cls.admin_user = User.objects.create_user(
            username="director_admin",
            email="director@medwaste.local",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Director",
            last_name="Officer",
            is_staff=True,
            is_active=True,
        )
        cls.admin_user_2 = User.objects.create_user(
            username="deputy_admin",
            email="deputy@medwaste.local",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Deputy",
            last_name="Admin",
            is_staff=True,
            is_active=True,
        )

        # 2. Hospital Stakeholders
        cls.hospital_user_1 = User.objects.create_user(
            username="apollo_hospital",
            email="waste@apollo.example",
            password="HospitalPass123!",
            role=User.Role.HOSPITAL,
            organization_name="Apollo Multi-Specialty Hospital",
            phone="+91-11-23456789",
            is_active=True,
        )
        cls.hospital_user_2 = User.objects.create_user(
            username="fortis_clinic",
            email="biohazard@fortis.example",
            password="HospitalPass123!",
            role=User.Role.HOSPITAL,
            organization_name="Fortis Healthcare Clinic",
            phone="+91-11-98765432",
            is_active=True,
        )

        # 3. Collector Drivers
        cls.collector_user_1 = User.objects.create_user(
            username="driver_ramesh",
            email="ramesh@logistics.example",
            password="CollectorPass123!",
            role=User.Role.COLLECTOR,
            first_name="Ramesh",
            last_name="Kumar",
            organization_name="Green Logistics Transport Fleet",
            phone="+91-9811122233",
            is_active=True,
        )
        cls.collector_user_2 = User.objects.create_user(
            username="driver_suresh",
            email="suresh@logistics.example",
            password="CollectorPass123!",
            role=User.Role.COLLECTOR,
            first_name="Suresh",
            last_name="Singh",
            organization_name="Green Logistics Transport Fleet",
            phone="+91-9822233344",
            is_active=True,
        )

        # 4. Facility Operators & Treatment Plants
        cls.facility_operator_1 = User.objects.create_user(
            username="cbwtf_plant_manager",
            email="manager@cbwtf.example",
            password="FacilityPass123!",
            role=User.Role.FACILITY,
            first_name="Vikas",
            last_name="Sharma",
            organization_name="Central Bio-Clean Incinerator Plant",
            is_active=True,
        )
        cls.facility_operator_2 = User.objects.create_user(
            username="ecocare_operator",
            email="operator@ecocare.example",
            password="FacilityPass123!",
            role=User.Role.FACILITY,
            first_name="Anita",
            last_name="Roy",
            organization_name="EcoCare Autoclave Facility",
            is_active=True,
        )

        cls.facility_1 = TreatmentFacility.objects.create(
            name="Central Bio-Clean Incinerator Plant",
            operator_user=cls.facility_operator_1,
            facility_type=TreatmentFacility.FacilityType.CBWTF,
            license_number="SPCB-CTO-2026-0099",
            address="Plot 44, Industrial Area Phase 2",
            district="South East Delhi",
            state="National Capital Region",
            postal_code="110020",
            contact_person="Vikas Sharma",
            contact_phone="+91-11-44556677",
            contact_email="manager@cbwtf.example",
            daily_capacity_kg=Decimal("5000.00"),
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
            operational_availability=TreatmentFacility.OperationalAvailability.AVAILABLE,
            is_active=True,
        )

        cls.facility_pending = TreatmentFacility.objects.create(
            name="EcoCare Autoclave Facility",
            operator_user=cls.facility_operator_2,
            facility_type=TreatmentFacility.FacilityType.CBWTF,
            license_number="SPCB-CTO-2026-0155",
            address="Sector 18, Industrial Estate",
            district="Gurugram",
            state="Haryana",
            postal_code="122015",
            contact_person="Anita Roy",
            contact_phone="+91-124-99887766",
            contact_email="operator@ecocare.example",
            daily_capacity_kg=Decimal("2500.00"),
            approval_status=TreatmentFacility.ApprovalStatus.PENDING_APPROVAL,
            operational_availability=TreatmentFacility.OperationalAvailability.AVAILABLE,
            is_active=True,
        )

        # 5. Waste Categories
        cls.cat_yellow = WasteCategory.objects.filter(code="YELLOW").first()
        if not cls.cat_yellow:
            cls.cat_yellow = WasteCategory.objects.create(
                code="YELLOW",
                name="Anatomical & Chemical Waste",
                container_type="Non-chlorinated plastic bags",
                display_color="#FACC15",
                treatment_description="High-temperature dual chamber incineration (>1050°C)",
            )
        cls.cat_red = WasteCategory.objects.filter(code="RED").first()
        if not cls.cat_red:
            cls.cat_red = WasteCategory.objects.create(
                code="RED",
                name="Contaminated Recyclable Plastics",
                container_type="Autoclave-safe plastic bags",
                display_color="#EF4444",
                treatment_description="Autoclaving followed by sterilized shredding",
            )

        # 6. Waste Batch & Bags
        cls.batch_1 = WasteRegistrationBatch.objects.create(
            hospital=cls.hospital_user_1,
            registered_by=cls.hospital_user_1,
            ward="Surgical Ward 4",
            department="General Surgery",
            bag_count=1,
            total_weight=Decimal("12.50"),
        )
        cls.bag_1 = WasteBag.objects.create(
            hospital=cls.hospital_user_1,
            registered_by=cls.hospital_user_1,
            batch=cls.batch_1,
            category=cls.cat_yellow,
            ward="Surgical Ward 4",
            department="General Surgery",
            weight=Decimal("12.50"),
            status=WasteBag.Status.REGISTERED,
        )

        # 7. Collection Request
        cls.col_req = CollectionRequest.objects.create(
            hospital=cls.hospital_user_1,
            preferred_pickup_date=timezone.localdate() + datetime.timedelta(days=1),
            preferred_pickup_time=datetime.time(10, 0),
            pickup_address="Gate 3, Loading Dock, Apollo Hospital",
            contact_person="Sister Mary",
            contact_phone="+91-11-23456789",
            status=CollectionRequest.Status.REQUESTED,
        )
        cls.col_req.bags.add(cls.bag_1)

        # 8. Platform Configuration Initializers
        cls.platform_settings = PlatformSettings.get_settings()
        cls.compliance_config = ComplianceThresholdConfig.get_config()

    def setUp(self):
        self.client = Client()


class AdminUserManagementTests(Step7AdminBaseTestCase):
    def test_admin_user_listing_and_filtering(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_user_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "director_admin")
        self.assertContains(response, "apollo_hospital")

        # Test search filter
        response_search = self.client.get(f"{url}?q=Ramesh")
        self.assertEqual(response_search.status_code, 200)
        self.assertContains(response_search, "driver_ramesh")
        self.assertNotContains(response_search, "apollo_hospital")

        # Test role filter
        response_role = self.client.get(f"{url}?role=HOSPITAL")
        self.assertEqual(response_role.status_code, 200)
        self.assertContains(response_role, "apollo_hospital")
        self.assertNotContains(response_role, "driver_ramesh")

    def test_admin_user_detail_view(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_user_detail", kwargs={"user_id": self.hospital_user_1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apollo Multi-Specialty Hospital")
        self.assertContains(response, "Hospital Operational Footprint")

    def test_user_activation_and_deactivation(self):
        # Deactivate collector 2
        toggle_user_active_status(
            admin_user=self.admin_user,
            target_user=self.collector_user_2,
            is_active=False,
            reason="License renewal audit in progress",
        )
        self.collector_user_2.refresh_from_db()
        self.assertFalse(self.collector_user_2.is_active)
        self.assertEqual(self.collector_user_2.account_status, User.AccountStatus.INACTIVE)

        # Audit entry exists
        audit = AdminActionAudit.objects.filter(
            affected_record_id=str(self.collector_user_2.id),
            action_type=AdminActionAudit.ActionType.USER_DEACTIVATION,
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.admin_user)

        # Reactivate
        toggle_user_active_status(
            admin_user=self.admin_user,
            target_user=self.collector_user_2,
            is_active=True,
            reason="License renewed and verified",
        )
        self.collector_user_2.refresh_from_db()
        self.assertTrue(self.collector_user_2.is_active)
        self.assertEqual(self.collector_user_2.account_status, User.AccountStatus.ACTIVE)

    def test_user_suspension_requires_mandatory_reason(self):
        # Missing reason must raise ValidationError
        with self.assertRaises(ValidationError):
            suspend_user(self.admin_user, self.collector_user_1, reason="")

        with self.assertRaises(ValidationError):
            suspend_user(self.admin_user, self.collector_user_1, reason="   ")

        # Valid suspension
        suspend_user(
            self.admin_user,
            self.collector_user_1,
            reason="Critical protocol violation: Consignment seal missing"
        )
        self.collector_user_1.refresh_from_db()
        self.assertTrue(self.collector_user_1.is_suspended)
        self.assertFalse(self.collector_user_1.is_active)
        self.assertIn("seal missing", self.collector_user_1.suspension_reason)

        # Verify audit created
        audit = AdminActionAudit.objects.filter(
            action_type=AdminActionAudit.ActionType.COLLECTOR_SUSPENSION,
            affected_record_id=str(self.collector_user_1.id)
        ).first()
        self.assertIsNotNone(audit)

        # Verify notification sent to suspended user
        notif = Notification.objects.filter(recipient=self.collector_user_1).first()
        self.assertIsNotNone(notif)
        self.assertIn("Account Suspended", notif.title)

    def test_protection_against_self_deactivation(self):
        # An admin cannot deactivate themselves
        with self.assertRaises(ValidationError) as ctx:
            toggle_user_active_status(
                admin_user=self.admin_user,
                target_user=self.admin_user,
                is_active=False,
                reason="Testing self deactivation"
            )
        self.assertIn("cannot deactivate your own active administrator account", str(ctx.exception))

    def test_protection_against_self_suspension(self):
        # An admin cannot suspend themselves
        with self.assertRaises(ValidationError) as ctx:
            suspend_user(
                admin_user=self.admin_user,
                target_user=self.admin_user,
                reason="Testing self suspension"
            )
        self.assertIn("cannot suspend your own active administrator account", str(ctx.exception))

    def test_protection_of_last_active_administrator(self):
        # Deactivate admin 2 first
        toggle_user_active_status(self.admin_user, self.admin_user_2, is_active=False, reason="Testing")

        # Attempting to deactivate the sole remaining active admin must fail
        with self.assertRaises(ValidationError) as ctx:
            toggle_user_active_status(
                admin_user=self.admin_user,
                target_user=self.admin_user,
                is_active=False,
                reason="Trying to deactivate last admin"
            )
        # Self-deactivation catches it first or last-admin check catches it
        self.assertIn("Safety violation", str(ctx.exception))

    def test_user_reactivation_restores_active_state(self):
        suspend_user(self.admin_user, self.hospital_user_2, reason="Pending biohazard audit")
        self.hospital_user_2.refresh_from_db()
        self.assertTrue(self.hospital_user_2.is_suspended)

        reactivate_user(self.admin_user, self.hospital_user_2, reason="Audit cleared successfully")
        self.hospital_user_2.refresh_from_db()
        self.assertFalse(self.hospital_user_2.is_suspended)
        self.assertTrue(self.hospital_user_2.is_active)


class AdminHospitalAndCollectorManagementTests(Step7AdminBaseTestCase):
    def test_hospital_list_and_detail_view(self):
        self.client.force_login(self.admin_user)
        response_list = self.client.get(reverse("admin_hospital_list"))
        self.assertEqual(response_list.status_code, 200)
        self.assertContains(response_list, "Apollo Multi-Specialty Hospital")

        response_detail = self.client.get(reverse("admin_hospital_detail", kwargs={"hospital_id": self.hospital_user_1.id}))
        self.assertEqual(response_detail.status_code, 200)
        self.assertContains(response_detail, "Biomedical Waste Generation Breakdown")
        self.assertContains(response_detail, "12.50 kg")

    def test_hospital_suspension_via_post_endpoint(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_user_suspend", kwargs={"user_id": self.hospital_user_1.id})
        response = self.client.post(url, {"reason": "Non-compliance with CPCB barcoding rules"})
        self.assertEqual(response.status_code, 302)
        self.hospital_user_1.refresh_from_db()
        self.assertTrue(self.hospital_user_1.is_suspended)

    def test_collector_list_and_detail_view(self):
        self.client.force_login(self.admin_user)
        response_list = self.client.get(reverse("admin_collector_list"))
        self.assertEqual(response_list.status_code, 200)
        self.assertContains(response_list, "Ramesh Kumar")

        response_detail = self.client.get(reverse("admin_collector_detail", kwargs={"collector_id": self.collector_user_1.id}))
        self.assertEqual(response_detail.status_code, 200)
        self.assertContains(response_detail, "Total Assignments")

    def test_collector_suspension_prevents_dispatch_assignment(self):
        # Suspend collector 1
        suspend_user(self.admin_user, self.collector_user_1, reason="Medical leave")
        self.collector_user_1.refresh_from_db()
        self.assertTrue(self.collector_user_1.is_suspended)

        # Attempting to assign suspended collector to collection request must fail
        with self.assertRaises(ValidationError) as ctx:
            admin_schedule_and_assign(
                collection_request=self.col_req,
                admin_user=self.admin_user,
                confirmed_date=timezone.localdate() + datetime.timedelta(days=1),
                confirmed_time=datetime.time(11, 0),
                collector_user=self.collector_user_1,
                destination_facility=self.facility_1,
            )
        self.assertIn("inactive or suspended", str(ctx.exception))


class AdminFacilityApprovalWorkflowTests(Step7AdminBaseTestCase):
    def test_facility_approval_workflow(self):
        self.assertEqual(self.facility_pending.approval_status, TreatmentFacility.ApprovalStatus.PENDING_APPROVAL)

        # Approve facility
        approve_treatment_facility(self.admin_user, self.facility_pending, remarks="SPCB license verified against registry")
        self.facility_pending.refresh_from_db()
        self.assertEqual(self.facility_pending.approval_status, TreatmentFacility.ApprovalStatus.APPROVED)
        self.assertTrue(self.facility_pending.is_active)

        # Notification sent to operator
        notif = Notification.objects.filter(recipient=self.facility_operator_2).first()
        self.assertIsNotNone(notif)
        self.assertIn("Facility Approved", notif.title)

    def test_facility_rejection_requires_mandatory_reason(self):
        with self.assertRaises(ValidationError):
            reject_treatment_facility(self.admin_user, self.facility_pending, reason="")

        reject_treatment_facility(self.admin_user, self.facility_pending, reason="Failed autoclave spore test documentation")
        self.facility_pending.refresh_from_db()
        self.assertEqual(self.facility_pending.approval_status, TreatmentFacility.ApprovalStatus.REJECTED)
        self.assertFalse(self.facility_pending.is_active)

    def test_facility_suspension_requires_mandatory_reason(self):
        with self.assertRaises(ValidationError):
            suspend_treatment_facility(self.admin_user, self.facility_1, reason="")

        suspend_treatment_facility(self.admin_user, self.facility_1, reason="Air scrubber emissions exceeding permissible limits")
        self.facility_1.refresh_from_db()
        self.assertEqual(self.facility_1.approval_status, TreatmentFacility.ApprovalStatus.SUSPENDED)
        self.assertEqual(self.facility_1.operational_availability, TreatmentFacility.OperationalAvailability.TEMPORARILY_UNAVAILABLE)

        # Reinstate
        reinstate_treatment_facility(self.admin_user, self.facility_1, remarks="Scrubber filter replaced and re-tested")
        self.facility_1.refresh_from_db()
        self.assertEqual(self.facility_1.approval_status, TreatmentFacility.ApprovalStatus.APPROVED)

    def test_unapproved_or_suspended_facility_blocks_collection_assignment(self):
        # Pending facility cannot be assigned
        with self.assertRaises(ValidationError) as ctx:
            admin_schedule_and_assign(
                collection_request=self.col_req,
                admin_user=self.admin_user,
                confirmed_date=timezone.localdate() + datetime.timedelta(days=1),
                confirmed_time=datetime.time(11, 0),
                collector_user=self.collector_user_2,
                destination_facility=self.facility_pending,
            )
        self.assertIn("Pending Approval", str(ctx.exception))

        # Suspended facility cannot be assigned
        suspend_treatment_facility(self.admin_user, self.facility_1, reason="Plant maintenance")
        with self.assertRaises(ValidationError) as ctx2:
            admin_schedule_and_assign(
                collection_request=self.col_req,
                admin_user=self.admin_user,
                confirmed_date=timezone.localdate() + datetime.timedelta(days=1),
                confirmed_time=datetime.time(11, 0),
                collector_user=self.collector_user_2,
                destination_facility=self.facility_1,
            )
        self.assertIn("Suspended", str(ctx2.exception))


class AdminOperationalOversightTests(Step7AdminBaseTestCase):
    def test_operational_oversight_tabs_access(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_operational_oversight")

        for tab in ["collection", "facility", "treatment", "custody"]:
            response = self.client.get(f"{url}?tab={tab}")
            self.assertEqual(response.status_code, 200)

    def test_platform_kpis_calculation(self):
        kpis = get_platform_kpis()
        self.assertEqual(kpis["total_hospitals"], 2)
        self.assertEqual(kpis["total_collectors"], 2)
        self.assertEqual(kpis["total_facilities"], 2)
        self.assertEqual(kpis["pending_facility_approvals"], 1)
        self.assertEqual(kpis["active_collection_requests"], 1)


class AdminPlatformConfigTests(Step7AdminBaseTestCase):
    def test_platform_settings_update_logs_audit(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_platform_config")

        response = self.client.post(url, {
            "config_type": "platform_settings",
            "platform_name": "MedWaste Connect Gov",
            "default_page_size": 50,
            "stale_collection_hours": 36,
            "hospital_requires_approval": True,
            "enable_in_app_notifications": True,
            "enable_system_alerts": True,
            "auto_escalate_critical_discrepancies": True,
            "maintenance_mode": False,
            "maintenance_message": "Scheduled upgrade underway",
            "change_reason": "Quarterly operational policy adjustment",
        })
        self.assertEqual(response.status_code, 302)

        config = PlatformSettings.get_settings()
        self.assertEqual(config.platform_name, "MedWaste Connect Gov")
        self.assertEqual(config.default_page_size, 50)
        self.assertTrue(config.hospital_requires_approval)

        audit = AdminActionAudit.objects.filter(
            action_type=AdminActionAudit.ActionType.CONFIG_UPDATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.admin_user)
        self.assertIn("Quarterly operational policy", audit.reason)

    def test_platform_settings_range_validation(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_platform_config")

        # Page size > 100 must fail
        response = self.client.post(url, {
            "config_type": "platform_settings",
            "platform_name": "MedWaste Connect",
            "default_page_size": 200,
            "stale_collection_hours": 24,
            "change_reason": "Testing invalid range",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page size must be between 10 and 100")

    def test_compliance_threshold_range_validation(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin_platform_config")

        # Treatment turnaround hours < 12 must fail
        response = self.client.post(url, {
            "config_type": "compliance_thresholds",
            "treatment_turnaround_hours": 5,
            "disposal_turnaround_days": 7,
            "verification_turnaround_days": 3,
            "discrepancy_escalation_hours": 24,
            "is_active": True,
            "change_reason": "Testing invalid threshold",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "between 12 and 168 hours")


class AdminAuditLogAndNotificationTests(Step7AdminBaseTestCase):
    def test_admin_action_audit_append_only_enforcement(self):
        audit = AdminActionAudit.objects.create(
            actor=self.admin_user,
            action_type=AdminActionAudit.ActionType.OPERATIONAL_OVERRIDE,
            affected_model="WasteBag",
            affected_record_id="BAG-1234",
            reason="Sample audit reason",
        )
        # Attempting to edit existing record must raise ValidationError
        with self.assertRaises(ValidationError):
            audit.reason = "Altered reason"
            audit.save()

        # Attempting to delete must raise ValidationError
        with self.assertRaises(ValidationError):
            audit.delete()

    def test_broadcast_system_notification(self):
        initial_notif_count = Notification.objects.count()
        count = broadcast_system_notification(
            admin_user=self.admin_user,
            target_role="HOSPITAL",
            title="Biohazard Protocol Revision",
            message="Please review revised Form IV guidelines by Friday."
        )
        self.assertEqual(count, 2)  # Two active hospitals
        self.assertEqual(Notification.objects.count(), initial_notif_count + 2)

        # Audit record created
        audit = AdminActionAudit.objects.filter(
            action_type=AdminActionAudit.ActionType.NOTIFICATION_BROADCAST
        ).first()
        self.assertIsNotNone(audit)


class AdminSecurityAndAccessControlTests(Step7AdminBaseTestCase):
    def test_non_admin_users_cannot_access_admin_views(self):
        # Hospital user accessing admin control panel must receive 403 Forbidden
        self.client.force_login(self.hospital_user_1)
        response = self.client.get(reverse("dashboard_admin"))
        self.assertEqual(response.status_code, 403)

        # Collector user accessing user list must receive 403 Forbidden
        self.client.force_login(self.collector_user_1)
        response_collector = self.client.get(reverse("admin_user_list"))
        self.assertEqual(response_collector.status_code, 403)

        # Facility user accessing platform config must receive 403 Forbidden
        self.client.force_login(self.facility_operator_1)
        response_facility = self.client.get(reverse("admin_platform_config"))
        self.assertEqual(response_facility.status_code, 403)

    def test_unauthenticated_user_redirected_to_login(self):
        response = self.client.get(reverse("dashboard_admin"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_post_only_views_reject_get_requests(self):
        self.client.force_login(self.admin_user)
        # GET on suspend must return 405 Method Not Allowed
        response = self.client.get(reverse("admin_user_suspend", kwargs={"user_id": self.hospital_user_1.id}))
        self.assertEqual(response.status_code, 405)


class HospitalOnboardingVisibilityRegressionTests(Step7AdminBaseTestCase):
    """Regression coverage for newly self-registered hospitals & approval enforcement."""

    def test_self_registered_hospital_visible_across_admin_consoles(self):
        settings_obj = PlatformSettings.get_settings()
        settings_obj.hospital_requires_approval = False
        settings_obj.save()

        client = Client()
        res = client.post(
            reverse("register"),
            {
                "organization_name": "Medanta Care",
                "phone": "+91 98111 22334",
                "email": "medanta_test@example.org",
                "username": "medanta_test_hosp",
                "password": "StrongPassword123!",
                "password_confirm": "StrongPassword123!",
            },
        )
        self.assertRedirects(res, reverse("dashboard_hospital"))

        new_hosp = User.objects.get(username="medanta_test_hosp")
        self.assertEqual(new_hosp.account_status, User.AccountStatus.ACTIVE)

        # Admin notification emitted on hospital registration
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.admin_user,
                reference_id=f"HOSP-{new_hosp.id}",
            ).exists()
        )

        # Admin visibility across Control Panel, Approvals Queue, Hospitals List, and Analytics dropdown
        self.client.force_login(self.admin_user)
        res_overview = self.client.get(reverse("dashboard_admin"))
        self.assertEqual(res_overview.status_code, 200)
        self.assertContains(res_overview, "Recently Registered Hospitals")
        self.assertContains(res_overview, "Medanta Care")

        res_queue = self.client.get(reverse("admin_approvals_queue"))
        self.assertEqual(res_queue.status_code, 200)
        self.assertContains(res_queue, "Approved &amp; Registered Hospitals")
        self.assertContains(res_queue, "Medanta Care")

        res_list = self.client.get(reverse("admin_hospital_list"))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "Medanta Care")

        res_analytics = self.client.get(reverse("reports:admin_analytics"))
        self.assertEqual(res_analytics.status_code, 200)
        self.assertContains(res_analytics, ">Medanta Care</option>")

    def test_hospital_requires_approval_workflow_blocks_waste_until_approved(self):
        settings_obj = PlatformSettings.get_settings()
        settings_obj.hospital_requires_approval = True
        settings_obj.save()

        client = Client()
        res = client.post(
            reverse("register"),
            {
                "organization_name": "Apex Approval Care",
                "phone": "+91 98222 33445",
                "email": "apex_approval@example.org",
                "username": "apex_approval_hosp",
                "password": "StrongPassword123!",
                "password_confirm": "StrongPassword123!",
            },
        )
        self.assertRedirects(res, reverse("dashboard_hospital"))

        pending_hosp = User.objects.get(username="apex_approval_hosp")
        self.assertEqual(pending_hosp.account_status, User.AccountStatus.PENDING_APPROVAL)

        # Dashboard renders pending verification notice while operational modules return 403
        res_dash = client.get(reverse("dashboard_hospital"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "Hospital Account Pending Administrative Approval")
        self.assertEqual(client.get(reverse("waste:register")).status_code, 403)
        self.assertEqual(client.get(reverse("collection:hospital_request_create")).status_code, 403)

        # Appears in admin pending queue and works cleanly once approved
        self.client.force_login(self.admin_user)
        res_queue = self.client.get(reverse("admin_approvals_queue"))
        self.assertEqual(res_queue.status_code, 200)
        self.assertContains(res_queue, "Apex Approval Care")

        approve_user_onboarding(self.admin_user, pending_hosp, remarks="Verified registration")
        pending_hosp.refresh_from_db()
        self.assertEqual(pending_hosp.account_status, User.AccountStatus.ACTIVE)

        self.assertEqual(client.get(reverse("waste:register")).status_code, 200)


class HospitalDashboardDiscrepancyRegressionTests(Step7AdminBaseTestCase):
    """Regression coverage for Hospital dashboard CustodyDiscrepancy scoping & RBAC."""

    def test_hospital_dashboard_zero_and_scoped_discrepancies(self):
        from treatment.models import FacilityReceptionManifest

        # 1 & 2: Hospital dashboard GET returns HTTP 200 with zero discrepancies
        self.client.force_login(self.hospital_user_1)
        res_empty = self.client.get(reverse("dashboard_hospital"))
        self.assertEqual(res_empty.status_code, 200)
        self.assertEqual(res_empty.context["open_discrepancies_count"], 0)
        self.assertContains(res_empty, "0 Open Exceptions")

        # 3: Create discrepancies linked via bag (OPEN), collection_request (UNDER_REVIEW),
        # reception_manifest (OPEN), plus CLOSED (excluded from open count)
        manifest_h1 = FacilityReceptionManifest.objects.create(
            facility=self.facility_1,
            collection_request=self.col_req,
            received_by=self.facility_operator_1,
            expected_bag_count=1,
            expected_weight=Decimal("12.50"),
            gross_received_weight=Decimal("10.00"),
        )
        CustodyDiscrepancy.objects.create(
            bag=self.bag_1,
            discrepancy_type=CustodyDiscrepancy.DiscrepancyType.DAMAGED_PACKAGING,
            severity=CustodyDiscrepancy.Severity.HIGH,
            status=CustodyDiscrepancy.Status.OPEN,
            description="Torn outer liner at ward bay",
            reported_by=self.collector_user_1,
        )
        CustodyDiscrepancy.objects.create(
            collection_request=self.col_req,
            discrepancy_type=CustodyDiscrepancy.DiscrepancyType.MISSING_AT_PICKUP,
            severity=CustodyDiscrepancy.Severity.MEDIUM,
            status=CustodyDiscrepancy.Status.UNDER_REVIEW,
            description="Manifest count mismatch under review",
            reported_by=self.admin_user,
        )
        CustodyDiscrepancy.objects.create(
            reception_manifest=manifest_h1,
            discrepancy_type=CustodyDiscrepancy.DiscrepancyType.WEIGHT_MISMATCH,
            severity=CustodyDiscrepancy.Severity.CRITICAL,
            status=CustodyDiscrepancy.Status.OPEN,
            description="Gate weighbridge variance",
            reported_by=self.facility_operator_1,
        )
        CustodyDiscrepancy.objects.create(
            bag=self.bag_1,
            discrepancy_type=CustodyDiscrepancy.DiscrepancyType.OTHER,
            severity=CustodyDiscrepancy.Severity.LOW,
            status=CustodyDiscrepancy.Status.CLOSED,
            description="Closed historical notice",
            reported_by=self.admin_user,
        )

        res_populated = self.client.get(reverse("dashboard_hospital"))
        self.assertEqual(res_populated.status_code, 200)
        self.assertEqual(res_populated.context["open_discrepancies_count"], 3)
        self.assertContains(res_populated, "3 Open Exception(s)")

        # 4: Other hospital sees strictly 0 discrepancies from Hospital 1
        self.client.force_login(self.hospital_user_2)
        res_other = self.client.get(reverse("dashboard_hospital"))
        self.assertEqual(res_other.status_code, 200)
        self.assertEqual(res_other.context["open_discrepancies_count"], 0)
        self.assertContains(res_other, "0 Open Exceptions")

    def test_hospital_dashboard_rbac_and_all_four_dashboards_load(self):
        # 5: Anonymous redirects; unauthorized roles return 403
        res_anon = self.client.get(reverse("dashboard_hospital"))
        self.assertEqual(res_anon.status_code, 302)

        for unauthorized_user in (self.collector_user_1, self.facility_operator_1, self.admin_user):
            self.client.force_login(unauthorized_user)
            self.assertEqual(self.client.get(reverse("dashboard_hospital")).status_code, 403)

        # 6: All four role dashboards load HTTP 200 cleanly
        role_dashboards = [
            (self.admin_user, "dashboard_admin"),
            (self.hospital_user_1, "dashboard_hospital"),
            (self.collector_user_1, "dashboard_collector"),
            (self.facility_operator_1, "dashboard_facility"),
        ]
        for account, url_name in role_dashboards:
            self.client.force_login(account)
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, f"Failed loading {url_name}")


class PublicSecurityConfigurationTests(TestCase):
    """Verify production SECRET_KEY fail-safe and local-only demo seeding controls."""

    def test_resolve_secret_key_fails_closed_in_production(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings import resolve_secret_key

        for bad_value in (
            "",
            "   ",
            "django-insecure-placeholder-key-that-must-fail-in-production",
            "change-this-to-a-secure-random-50-character-secret-key-in-production",
            "too-short-key",
        ):
            with self.assertRaises(ImproperlyConfigured):
                resolve_secret_key(False, bad_value)

        strong_key = "A9#mK2$pQ7!vZ4&nX8*wL5@rT3^cB6+yJ1_hF0=dS9~uE"
        self.assertEqual(resolve_secret_key(False, strong_key), strong_key)

    def test_resolve_secret_key_supports_local_dev_fallback(self):
        from config.settings import resolve_secret_key

        ephemeral = resolve_secret_key(True, "")
        self.assertGreaterEqual(len(ephemeral), 32)
        self.assertEqual(resolve_secret_key(True, "local-dev-custom-key"), "local-dev-custom-key")

    def test_seed_users_refuses_production_execution(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.test import override_settings

        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_users")

    def test_seed_users_uses_env_or_random_passwords_in_debug(self):
        import io
        import os
        from unittest.mock import patch
        from django.core.management import call_command
        from django.test import override_settings

        env_overrides = {
            "MEDWASTE_DEMO_ADMIN_PASSWORD": "EnvAdmin#Test987",
            "MEDWASTE_DEMO_HOSPITAL_PASSWORD": "EnvHospital#Test987",
            "MEDWASTE_DEMO_COLLECTOR_PASSWORD": "EnvCollector#Test987",
            "MEDWASTE_DEMO_FACILITY_PASSWORD": "EnvFacility#Test987",
        }
        with override_settings(DEBUG=True):
            with patch.dict(os.environ, env_overrides, clear=False):
                out = io.StringIO()
                call_command("seed_users", stdout=out)
                admin = User.objects.get(username="admin_demo")
                self.assertEqual(admin.first_name, "System")
                self.assertTrue(admin.check_password("EnvAdmin#Test987"))

            # When env vars are cleared, unique random passwords are generated each run
            cleared = {k: "" for k in env_overrides}
            with patch.dict(os.environ, cleared, clear=False):
                out_rand = io.StringIO()
                call_command("seed_users", stdout=out_rand)
                admin.refresh_from_db()
                self.assertFalse(admin.check_password("EnvAdmin#Test987"))
                self.assertIn("admin_demo", out_rand.getvalue())
