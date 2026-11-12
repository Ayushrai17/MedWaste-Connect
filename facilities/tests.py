from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from waste.models import WasteCategory
from facilities.models import TreatmentFacility
from facilities.services import (
    register_treatment_facility,
    update_facility_status,
    get_facility_capacity_metrics,
)

User = get_user_model()


class FacilityBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()

        self.admin_user = User.objects.create_user(
            username="admin_officer",
            email="admin@treatment.example.com",
            password="SecurePassword123!",
            role=User.Role.ADMIN,
        )

        self.facility_user = User.objects.create_user(
            username="apex_plant_operator",
            email="operator@apex.example.com",
            password="SecurePassword123!",
            role=User.Role.FACILITY,
            organization_name="Apex Bio-Clean Systems",
        )

        self.facility_user_2 = User.objects.create_user(
            username="enviro_plant_operator",
            email="operator@enviro.example.com",
            password="SecurePassword123!",
            role=User.Role.FACILITY,
            organization_name="EnviroCare Treatment Plant",
        )

        self.hospital_user = User.objects.create_user(
            username="general_hospital",
            email="hosp@example.com",
            password="SecurePassword123!",
            role=User.Role.HOSPITAL,
            organization_name="General Hospital",
        )

        self.collector_user = User.objects.create_user(
            username="driver_arun",
            email="arun@logistics.example.com",
            password="SecurePassword123!",
            role=User.Role.COLLECTOR,
        )

        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={"name": "Yellow Category", "display_color": "#FFD200", "container_type": "Bag", "treatment_description": "Incineration"}
        )

        self.facility = TreatmentFacility.objects.create(
            name="Apex Bio-Clean CBWTF",
            operator_user=self.facility_user,
            license_number="SPCB/CTO/2026/001",
            address="Industrial Zone Phase 2",
            district="South City",
            state="National Capital Region",
            postal_code="110020",
            contact_person="Ramesh Patel",
            contact_phone="+91 98765 00001",
            contact_email="apex@example.com",
            daily_capacity_kg=Decimal("5000.00"),
            supports_incineration=True,
            supports_autoclaving=True,
            supports_shredding=True,
            supports_chemical_disinfection=True,
            approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
            operational_availability=TreatmentFacility.OperationalAvailability.AVAILABLE,
            is_active=True,
        )
        self.facility.accepted_categories.add(self.cat_yellow)


class TreatmentFacilityModelAndServiceTests(FacilityBaseTestCase):
    def test_facility_code_format(self):
        self.assertTrue(self.facility.facility_code.startswith("MWC-FAC-"))
        self.assertEqual(len(self.facility.facility_code), 23)

    def test_daily_capacity_validation(self):
        self.facility.daily_capacity_kg = Decimal("-10.00")
        with self.assertRaises(ValidationError):
            self.facility.clean()

    def test_register_treatment_facility_by_admin(self):
        form_data = {
            "name": "EnviroCare Facility",
            "operator_user": self.facility_user_2,
            "license_number": "SPCB/CTO/2026/002",
            "address": "Eco Park Sector 8",
            "district": "North City",
            "state": "National Capital Region",
            "postal_code": "110040",
            "contact_person": "Sunil Verma",
            "contact_phone": "+91 98765 00002",
            "contact_email": "enviro@example.com",
            "daily_capacity_kg": Decimal("3500.00"),
            "approval_status": TreatmentFacility.ApprovalStatus.APPROVED,
        }
        new_fac = register_treatment_facility(self.admin_user, form_data, [self.cat_yellow])
        self.assertEqual(new_fac.operator_user, self.facility_user_2)
        self.assertEqual(new_fac.daily_capacity_kg, Decimal("3500.00"))

    def test_non_admin_cannot_register_facility(self):
        form_data = {
            "name": "Unauthorized Facility",
            "operator_user": self.facility_user_2,
            "license_number": "SPCB/CTO/2026/003",
            "address": "Test Address",
            "district": "City",
            "postal_code": "110001",
            "contact_person": "Person",
            "contact_phone": "+91 98765 00003",
            "contact_email": "test@example.com",
            "daily_capacity_kg": Decimal("1000.00"),
        }
        with self.assertRaises(PermissionDenied):
            register_treatment_facility(self.facility_user, form_data)

    def test_duplicate_operator_association_blocked(self):
        form_data = {
            "name": "Second Facility for Same Operator",
            "operator_user": self.facility_user,  # already operator of Apex
            "license_number": "SPCB/CTO/2026/004",
            "address": "Test Address",
            "district": "City",
            "postal_code": "110001",
            "contact_person": "Person",
            "contact_phone": "+91 98765 00004",
            "contact_email": "test@example.com",
            "daily_capacity_kg": Decimal("1000.00"),
        }
        with self.assertRaises(ValidationError) as ctx:
            register_treatment_facility(self.admin_user, form_data)
        self.assertIn("already the designated operator", str(ctx.exception))

    def test_capacity_metrics_and_availability(self):
        metrics = get_facility_capacity_metrics(self.facility)
        self.assertEqual(metrics["daily_capacity_kg"], Decimal("5000.00"))
        self.assertEqual(metrics["remaining_capacity_kg"], Decimal("5000.00"))
        self.assertEqual(metrics["utilization_percentage"], 0.0)
        self.assertEqual(metrics["dynamic_availability"], TreatmentFacility.OperationalAvailability.AVAILABLE)


class FacilityViewAccessSecurityTests(FacilityBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_unauthenticated_redirects(self):
        res = self.client.get(reverse("facilities:admin_facility_list"))
        self.assertEqual(res.status_code, 302)

    def test_non_admin_cannot_access_facility_directory(self):
        self.client.force_login(self.facility_user)
        res = self.client.get(reverse("facilities:admin_facility_list"))
        self.assertEqual(res.status_code, 403)

    def test_admin_can_access_facility_list_and_detail(self):
        self.client.force_login(self.admin_user)
        res_list = self.client.get(reverse("facilities:admin_facility_list"))
        self.assertEqual(res_list.status_code, 200)

        res_detail = self.client.get(reverse("facilities:admin_facility_detail", kwargs={"facility_code": self.facility.facility_code}))
        self.assertEqual(res_detail.status_code, 200)
