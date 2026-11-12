from decimal import Decimal
import csv
import os
import shutil
import tempfile
import uuid
from unittest.mock import patch
import PIL.Image
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from accounts.models import User
from waste.models import (
    WasteCategory,
    WasteRegistrationBatch,
    WasteBag,
    get_min_bag_weight,
    get_max_bag_weight,
)
from waste.forms import WasteBatchRegistrationForm
from waste.services.qr_service import (
    build_tracking_url,
    generate_bag_qr_code,
    regenerate_bag_qr_code,
    get_or_create_qr_image,
)


class WasteLoggingAndCategorizationTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Seed/retrieve the 4 regulatory categories
        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={
                "name": "Yellow",
                "display_color": "#FFD200",
                "container_type": "Yellow-coloured non-chlorinated plastic bags",
                "treatment_description": "Incineration or Plasma Pyrolysis",
                "is_active": True,
                "display_order": 1,
            },
        )
        self.cat_red, _ = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={
                "name": "Red",
                "display_color": "#E53935",
                "container_type": "Red-coloured non-chlorinated plastic bags",
                "treatment_description": "Autoclaving followed by shredding",
                "is_active": True,
                "display_order": 2,
            },
        )
        self.cat_white, _ = WasteCategory.objects.get_or_create(
            code="WHITE",
            defaults={
                "name": "White / Translucent",
                "display_color": "#F5F5F5",
                "container_type": "Puncture-proof container",
                "treatment_description": "Autoclaving followed by encapsulation",
                "is_active": True,
                "display_order": 3,
            },
        )
        self.cat_blue, _ = WasteCategory.objects.get_or_create(
            code="BLUE",
            defaults={
                "name": "Blue",
                "display_color": "#1E88E5",
                "container_type": "Puncture-proof boxes with blue marking",
                "treatment_description": "Disinfection by sodium hypochlorite or autoclaving",
                "is_active": True,
                "display_order": 4,
            },
        )

        # Hospital User A
        self.hospital_a = User.objects.create_user(
            username="hospital_alpha",
            password="AlphaPassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Alpha Memorial Hospital",
            phone="+91 91111 22222",
        )

        # Hospital User B
        self.hospital_b = User.objects.create_user(
            username="hospital_beta",
            password="BetaPassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Beta City Clinic",
            phone="+91 93333 44444",
        )

        # Collector User (Unauthorized for hospital waste logging)
        self.collector_user = User.objects.create_user(
            username="collector_dan",
            password="CollectorPass123!",
            role=User.Role.COLLECTOR,
            organization_name="SwiftWaste Logistics",
        )

        # Treatment Facility User
        self.facility_user = User.objects.create_user(
            username="facility_frank",
            password="FacilityPass123!",
            role=User.Role.FACILITY,
            organization_name="EcoTreat Plant 1",
        )

    # 1. Waste category creation and retrieval
    def test_01_waste_category_creation_and_retrieval(self):
        categories = WasteCategory.objects.filter(is_active=True)
        self.assertGreaterEqual(categories.count(), 4)

        yellow = WasteCategory.objects.get(code="YELLOW")
        self.assertEqual(yellow.name, "Yellow")
        self.assertEqual(yellow.display_color, "#FFD200")
        self.assertTrue(yellow.is_active)

        red = WasteCategory.objects.get(code="RED")
        self.assertEqual(red.name, "Red")

        white = WasteCategory.objects.get(code="WHITE")
        self.assertIn("White", white.name)

        blue = WasteCategory.objects.get(code="BLUE")
        self.assertEqual(blue.name, "Blue")

    # 2. Category master-data restrictions (immutability)
    def test_02_category_master_data_restrictions(self):
        yellow = WasteCategory.objects.get(code="YELLOW")
        yellow.code = "CHANGED_CODE"
        with self.assertRaises(ValidationError):
            yellow.save()

    # 3. Batch creation
    def test_03_batch_creation(self):
        batch = WasteRegistrationBatch.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            ward="ICU Ward 3",
            department="Intensive Care",
            bag_count=2,
            total_weight=Decimal("5.80"),
        )
        self.assertTrue(batch.batch_number.startswith("MWC-BAT-"))
        self.assertEqual(batch.hospital, self.hospital_a)
        self.assertEqual(batch.bag_count, 2)
        self.assertEqual(batch.total_weight, Decimal("5.80"))

    # 4. Individual bag creation
    def test_04_individual_bag_creation(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Surgery OT-1",
            department="General Surgery",
            weight=Decimal("3.45"),
        )
        self.assertTrue(bag.bag_id.startswith("MWC-BAG-"))
        self.assertIsInstance(bag.qr_token, uuid.UUID)
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.PENDING)
        self.assertEqual(bag.status, WasteBag.Status.REGISTERED)
        self.assertEqual(bag.weight, Decimal("3.45"))
        self.assertEqual(bag.weight_unit, "KG")

    # 5. Unique bag IDs
    def test_05_unique_bag_ids(self):
        bag_ids = set()
        for i in range(15):
            bag = WasteBag.objects.create(
                hospital=self.hospital_a,
                registered_by=self.hospital_a,
                category=self.cat_red,
                ward="Ward A",
                department="Emergency",
                weight=Decimal("1.50"),
            )
            bag_ids.add(bag.bag_id)
        self.assertEqual(len(bag_ids), 15)

    # 6. Unique UUID tokens
    def test_06_unique_uuid_tokens(self):
        tokens = set()
        for i in range(15):
            bag = WasteBag.objects.create(
                hospital=self.hospital_a,
                registered_by=self.hospital_a,
                category=self.cat_blue,
                ward="Ward B",
                department="Pediatrics",
                weight=Decimal("2.10"),
            )
            tokens.add(bag.qr_token)
        self.assertEqual(len(tokens), 15)

    # 7. Multiple-bag registration workflow
    def test_07_multiple_bag_registration(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        post_data = {
            "ward": "Trauma Unit",
            "department": "Emergency",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "3.50",
            "bag_category_1": self.cat_red.id,
            "bag_weight_1": "2.25",
            "bag_category_2": self.cat_white.id,
            "bag_weight_2": "0.75",
        }
        res = self.client.post(reverse("waste:register"), post_data)
        self.assertEqual(res.status_code, 302)

        batch = WasteRegistrationBatch.objects.filter(hospital=self.hospital_a).first()
        self.assertIsNotNone(batch)
        self.assertEqual(batch.bag_count, 3)
        self.assertEqual(batch.total_weight, Decimal("6.50"))
        self.assertEqual(batch.bags.count(), 3)

    # 8. Correct batch count and total weight (Decimal arithmetic)
    def test_08_correct_batch_count_and_total_weight(self):
        form_data = {
            "ward": "Cardiology Wing",
            "department": "Cardiology",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "1.25",
            "bag_category_1": self.cat_red.id,
            "bag_weight_1": "2.50",
            "bag_category_2": self.cat_white.id,
            "bag_weight_2": "3.75",
            "bag_category_3": self.cat_blue.id,
            "bag_weight_3": "4.50",
        }
        form = WasteBatchRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        batch, bags = form.save(hospital=self.hospital_a, registered_by=self.hospital_a)

        self.assertEqual(batch.bag_count, 4)
        self.assertEqual(batch.total_weight, Decimal("12.00"))
        self.assertEqual(len(bags), 4)

    # 9. Invalid weight rejection (zero, negative, non-numeric, below threshold)
    def test_09_invalid_weight_rejection(self):
        # Model-level validation
        with self.assertRaises(ValidationError):
            WasteBag(
                hospital=self.hospital_a,
                registered_by=self.hospital_a,
                category=self.cat_yellow,
                ward="Ward 1",
                department="OPD",
                weight=Decimal("0.00"),
            ).full_clean()

        with self.assertRaises(ValidationError):
            WasteBag(
                hospital=self.hospital_a,
                registered_by=self.hospital_a,
                category=self.cat_yellow,
                ward="Ward 1",
                department="OPD",
                weight=Decimal("-2.50"),
            ).full_clean()

        with self.assertRaises(ValidationError):
            WasteBag(
                hospital=self.hospital_a,
                registered_by=self.hospital_a,
                category=self.cat_yellow,
                ward="Ward 1",
                department="OPD",
                weight=Decimal("0.01"),  # Below min weight 0.05
            ).full_clean()

        # Form-level validation
        form_negative = WasteBatchRegistrationForm(data={
            "ward": "Ward 1",
            "department": "OPD",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "-1.50",
        })
        self.assertFalse(form_negative.is_valid())

        form_zero = WasteBatchRegistrationForm(data={
            "ward": "Ward 1",
            "department": "OPD",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "0.00",
        })
        self.assertFalse(form_zero.is_valid())

        form_alpha = WasteBatchRegistrationForm(data={
            "ward": "Ward 1",
            "department": "OPD",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "abc",
        })
        self.assertFalse(form_alpha.is_valid())

    # 10. Maximum weight validation (settings-configured limit)
    def test_10_maximum_weight_validation(self):
        max_limit = get_max_bag_weight()

        # Exceeding maximum weight
        with self.assertRaises(ValidationError):
            WasteBag(
                hospital=self.hospital_a,
                registered_by=self.hospital_a,
                category=self.cat_yellow,
                ward="Ward 1",
                department="OPD",
                weight=max_limit + Decimal("0.01"),
            ).full_clean()

        # Form-level rejection
        form_overweight = WasteBatchRegistrationForm(data={
            "ward": "Ward 1",
            "department": "OPD",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": str(max_limit + Decimal("5.00")),
        })
        self.assertFalse(form_overweight.is_valid())

        # Exact boundary weight is accepted
        valid_bag = WasteBag(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Ward 1",
            department="OPD",
            weight=max_limit,
        )
        valid_bag.full_clean()  # Should not raise

    # 11. Transaction rollback when one bag fails
    def test_11_transaction_rollback_when_one_bag_fails(self):
        initial_batches = WasteRegistrationBatch.objects.count()
        initial_bags = WasteBag.objects.count()

        form_data = {
            "ward": "ICU-3",
            "department": "Intensive Care",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "3.50",
            "bag_category_1": self.cat_red.id,
            "bag_weight_1": "-99.00",  # Invalid weight causes validation error
        }
        form = WasteBatchRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())

        with self.assertRaises(ValueError):
            form.save(hospital=self.hospital_a, registered_by=self.hospital_a)

        self.assertEqual(WasteRegistrationBatch.objects.count(), initial_batches)
        self.assertEqual(WasteBag.objects.count(), initial_bags)

    # 12. Hospital ownership injection (cannot spoof hospital)
    def test_12_hospital_ownership_injection(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        post_data = {
            "ward": "Spoof Ward",
            "department": "Spoof Dept",
            "hospital": self.hospital_b.id,  # Attempt to spoof hospital B
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "2.00",
        }
        res = self.client.post(reverse("waste:register"), post_data)
        self.assertEqual(res.status_code, 302)

        created_bag = WasteBag.objects.get(ward="Spoof Ward")
        # Ownership must be hospital_a from request.user, NOT the spoofed hospital_b
        self.assertEqual(created_bag.hospital, self.hospital_a)
        self.assertNotEqual(created_bag.hospital, self.hospital_b)

    # 13. Hospital A cannot access Hospital B's bags (HTTP 404 tenant isolation)
    def test_13_hospital_a_cannot_access_hospital_b_bags(self):
        batch_b = WasteRegistrationBatch.objects.create(
            hospital=self.hospital_b,
            registered_by=self.hospital_b,
            ward="Beta Ward",
            department="Beta Dept",
            bag_count=1,
            total_weight=Decimal("3.00"),
        )
        bag_b = WasteBag.objects.create(
            batch=batch_b,
            hospital=self.hospital_b,
            registered_by=self.hospital_b,
            category=self.cat_yellow,
            ward="Beta Ward",
            department="Beta Dept",
            weight=Decimal("3.00"),
        )

        # Login as Hospital A
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")

        # Attempt to access Hospital B's bag detail
        res_bag = self.client.get(reverse("waste:bag_detail", kwargs={"bag_id": bag_b.bag_id}))
        self.assertEqual(res_bag.status_code, 404)

        # Attempt to access Hospital B's batch detail
        res_batch = self.client.get(reverse("waste:batch_detail", kwargs={"batch_number": batch_b.batch_number}))
        self.assertEqual(res_batch.status_code, 404)

        # Attempt to access Hospital B's batch success page
        res_success = self.client.get(reverse("waste:batch_success", kwargs={"batch_number": batch_b.batch_number}))
        self.assertEqual(res_success.status_code, 404)

    # 14. Hospital inventory queryset isolation
    def test_14_hospital_inventory_queryset_isolation(self):
        # Create 2 bags for Hospital A
        WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Alpha Ward 1",
            department="Medicine",
            weight=Decimal("2.50"),
        )
        WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_red,
            ward="Alpha Ward 2",
            department="Surgery",
            weight=Decimal("4.00"),
        )

        # Create 3 bags for Hospital B
        for i in range(3):
            WasteBag.objects.create(
                hospital=self.hospital_b,
                registered_by=self.hospital_b,
                category=self.cat_blue,
                ward="Beta Ward",
                department="Pediatrics",
                weight=Decimal("1.50"),
            )

        # Login as Hospital A and view inventory
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)

        page_bags = res.context["page_obj"]
        self.assertEqual(len(page_bags), 2)
        for bag in page_bags:
            self.assertEqual(bag.hospital, self.hospital_a)

        self.assertEqual(res.context["stats_total_bags"], 2)
        self.assertEqual(res.context["stats_total_weight"], Decimal("6.50"))

    # 15. Authentication and role permissions
    def test_15_authentication_and_role_permissions(self):
        # Anonymous user redirected to login
        res_anon = self.client.get(reverse("waste:register"))
        self.assertRedirects(res_anon, f"{reverse('login')}?next={reverse('waste:register')}")

        # Collector role accessing hospital waste register raises 403 Forbidden
        self.client.login(username="collector_dan", password="CollectorPass123!")
        res_collector = self.client.get(reverse("waste:register"))
        self.assertEqual(res_collector.status_code, 403)

        # Facility role accessing hospital waste inventory raises 403 Forbidden
        self.client.login(username="facility_frank", password="FacilityPass123!")
        res_facility = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_facility.status_code, 403)

    # 16. Registration form validation
    def test_16_registration_form_validation(self):
        # Missing ward & department
        form_no_loc = WasteBatchRegistrationForm(data={
            "ward": "",
            "department": "",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "2.00",
        })
        self.assertFalse(form_no_loc.is_valid())
        self.assertIn("ward", form_no_loc.errors)
        self.assertIn("department", form_no_loc.errors)

        # Missing bags
        form_no_bags = WasteBatchRegistrationForm(data={
            "ward": "Maternity",
            "department": "Obstetrics",
        })
        self.assertFalse(form_no_bags.is_valid())

        # Non-existent category
        form_bad_cat = WasteBatchRegistrationForm(data={
            "ward": "Maternity",
            "department": "Obstetrics",
            "bag_category_0": 99999,
            "bag_weight_0": "2.00",
        })
        self.assertFalse(form_bad_cat.is_valid())

        # Valid form produces expected cleaned_data
        form_valid = WasteBatchRegistrationForm(data={
            "ward": "Maternity",
            "department": "Obstetrics",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "3.14159",
        })
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        self.assertEqual(form_valid.cleaned_data["bag_count"], 1)
        self.assertEqual(form_valid.cleaned_data["total_weight"], Decimal("3.14"))

    # 17. Batch deletion preserves waste bags (on_delete=models.SET_NULL)
    def test_17_batch_deletion_preserves_waste_bags(self):
        batch = WasteRegistrationBatch.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            ward="Orthopedics",
            department="Surgery",
            bag_count=2,
            total_weight=Decimal("5.00"),
        )
        bag1 = WasteBag.objects.create(
            batch=batch,
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Orthopedics",
            department="Surgery",
            weight=Decimal("2.00"),
        )
        bag2 = WasteBag.objects.create(
            batch=batch,
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_red,
            ward="Orthopedics",
            department="Surgery",
            weight=Decimal("3.00"),
        )

        batch_id = batch.id
        batch.delete()

        # Batch record is deleted
        self.assertFalse(WasteRegistrationBatch.objects.filter(id=batch_id).exists())

        # Child waste bags are preserved with batch = NULL
        bag1.refresh_from_db()
        bag2.refresh_from_db()
        self.assertIsNone(bag1.batch)
        self.assertIsNone(bag2.batch)
        self.assertEqual(WasteBag.objects.filter(id__in=[bag1.id, bag2.id]).count(), 2)

    # 18. QR status choices and defaults
    def test_18_qr_status_choices_and_defaults(self):
        # Verify exact QR status choices
        expected_choices = [
            ("PENDING", "Pending"),
            ("READY", "Ready"),
            ("FAILED", "Failed"),
        ]
        self.assertEqual(list(WasteBag.QRStatus.choices), expected_choices)

        # Newly created bag defaults to PENDING
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="ICU",
            department="Medicine",
            weight=Decimal("1.80"),
        )
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.PENDING)
        self.assertEqual(bag.get_qr_status_display(), "Pending")

        # Can transition to READY
        bag.qr_status = WasteBag.QRStatus.READY
        bag.save()
        bag.refresh_from_db()
        self.assertEqual(bag.qr_status, "READY")
        self.assertEqual(bag.get_qr_status_display(), "Ready")

        # Can transition to FAILED
        bag.qr_status = WasteBag.QRStatus.FAILED
        bag.save()
        bag.refresh_from_db()
        self.assertEqual(bag.qr_status, "FAILED")
        self.assertEqual(bag.get_qr_status_display(), "Failed")


class QRCodeSystemTests(TestCase):
    """
    Feature 2 Automated Verification:
    - QR Code Generation Engine & Storage
    - UUID-based Secure Tracking & Zero-PII Public Privacy
    - Thermal Label Printing (Single & Bulk)
    - Multi-Actor Authorization & Tenant Isolation
    Uses an isolated temporary media root to prevent permanent test file pollution.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_media_dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.test_media_dir, ignore_errors=True)

    def setUp(self):
        self.override = override_settings(MEDIA_ROOT=self.test_media_dir)
        self.override.enable()

        self.client = Client()

        # Seed categories
        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={
                "name": "Yellow",
                "display_color": "#FFD200",
                "container_type": "Yellow-coloured non-chlorinated plastic bags",
                "treatment_description": "Incineration or Plasma Pyrolysis",
                "is_active": True,
                "display_order": 1,
            },
        )
        self.cat_red, _ = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={
                "name": "Red",
                "display_color": "#E53935",
                "container_type": "Red-coloured non-chlorinated plastic bags",
                "treatment_description": "Autoclaving followed by shredding",
                "is_active": True,
                "display_order": 2,
            },
        )

        # Hospital A
        self.hospital_a = User.objects.create_user(
            username="hospital_alpha",
            password="AlphaPassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Alpha Memorial Hospital",
        )

        # Hospital B
        self.hospital_b = User.objects.create_user(
            username="hospital_beta",
            password="BetaPassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Beta General Clinic",
        )

        # Collector Dan
        self.collector = User.objects.create_user(
            username="collector_dan",
            password="DanPassword123!",
            role=User.Role.COLLECTOR,
            organization_name="SwiftWaste Logistics",
        )

    def tearDown(self):
        self.override.disable()

    # 1. QR generation succeeds for a valid bag & saves to storage
    def test_01_qr_generation_success_and_storage(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="ICU Ward",
            department="Intensive Care",
            weight=Decimal("3.20"),
        )
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.PENDING)

        qr_file = generate_bag_qr_code(bag)
        self.assertIsNotNone(qr_file)

        bag.refresh_from_db()
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.READY)
        self.assertTrue(bool(bag.qr_image))
        self.assertTrue(bag.qr_image.storage.exists(bag.qr_image.name))

        # Pillow Image Integrity Test
        with bag.qr_image.open("rb") as f:
            header = f.read(8)
            self.assertEqual(header, b"\x89PNG\r\n\x1a\n")  # Standard PNG header
            f.seek(0)
            img = PIL.Image.open(f)
            self.assertEqual(img.format, "PNG")
            self.assertGreater(img.size[0], 100)
            self.assertGreater(img.size[1], 100)

    # 2. Unique QR payload with UUID tracking URL (never database IDs)
    def test_02_unique_payload_uses_uuid_tracking_url(self):
        bag1 = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Ward A",
            department="Surgery",
            weight=Decimal("1.50"),
        )
        bag2 = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_red,
            ward="Ward B",
            department="OPD",
            weight=Decimal("2.50"),
        )

        url1 = build_tracking_url(bag1)
        url2 = build_tracking_url(bag2)

        self.assertIn(str(bag1.qr_token), url1)
        self.assertIn(str(bag2.qr_token), url2)
        self.assertNotEqual(url1, url2)
        # Ensure database primary key is not in payload
        self.assertNotIn(f"/bags/{bag1.id}/", url1)

    # 3. Repeated generation does not create duplicate files (idempotency)
    def test_03_idempotent_generation_avoids_duplicate_files(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Ward A",
            department="Surgery",
            weight=Decimal("2.00"),
        )
        generate_bag_qr_code(bag)
        bag.refresh_from_db()
        initial_filename = bag.qr_image.name

        # Second call without force=True should return existing image
        second_call = generate_bag_qr_code(bag, force=False)
        self.assertEqual(second_call.name, initial_filename)

    # 4. Missing or invalid token handled safely
    def test_04_missing_token_handled_safely(self):
        bag = WasteBag(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Ward A",
            department="Surgery",
            weight=Decimal("2.00"),
            qr_token=None,
        )
        result = generate_bag_qr_code(bag)
        self.assertIsNone(result)

    # 5. Generation failure sets FAILED and retains bag record; retry succeeds
    def test_05_generation_failure_and_retry(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Trauma",
            department="Emergency",
            weight=Decimal("4.10"),
        )

        # Mock exception during generation
        with patch("qrcode.QRCode.make_image", side_effect=IOError("Simulated write error")):
            result = generate_bag_qr_code(bag)
            self.assertIsNone(result)

        bag.refresh_from_db()
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.FAILED)
        # Bag record is preserved intact
        self.assertEqual(bag.weight, Decimal("4.10"))

        # Retry generation: should now succeed
        retry_result = regenerate_bag_qr_code(bag)
        self.assertIsNotNone(retry_result)
        bag.refresh_from_db()
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.READY)
        self.assertTrue(bag.qr_image.storage.exists(bag.qr_image.name))

    # 6. Valid UUID tracking URL resolves correctly (HTTP 200)
    def test_06_valid_uuid_tracking_url_resolves(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Cardiology",
            department="Cath Lab",
            weight=Decimal("3.75"),
        )
        url = reverse("waste:track_bag", kwargs={"qr_token": str(bag.qr_token)})
        res = self.client.get(url)

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, bag.bag_id)
        self.assertContains(res, "YELLOW")
        self.assertContains(res, "3.75")
        self.assertContains(res, "Authentic Registered Container")

    # 7. Invalid UUID returns safe unavailable response (HTTP 404, no stack trace)
    def test_07_invalid_uuid_returns_safe_unavailable(self):
        random_uuid = uuid.uuid4()
        url = reverse("waste:track_bag", kwargs={"qr_token": str(random_uuid)})
        res = self.client.get(url)

        self.assertEqual(res.status_code, 404)
        self.assertContains(res, "Invalid or Unverified Container Tag", status_code=404)

    # 8. Tracking page does NOT expose private hospital or staff PII
    def test_08_tracking_page_privacy_guarantee(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Secret Onco Isolation Unit 7",
            department="Confidential Department X",
            weight=Decimal("2.40"),
        )
        url = reverse("waste:track_bag", kwargs={"qr_token": str(bag.qr_token)})
        res = self.client.get(url)

        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # Must NOT leak hospital name, username, ward, or department
        self.assertNotIn("Alpha Memorial Hospital", content)
        self.assertNotIn("hospital_alpha", content)
        self.assertNotIn("Secret Onco Isolation Unit 7", content)
        self.assertNotIn("Confidential Department X", content)

    # 9. Hospital A cannot view Hospital B's private bag detail (HTTP 404)
    def test_09_hospital_a_cannot_view_hospital_b_bag_detail(self):
        bag_b = WasteBag.objects.create(
            hospital=self.hospital_b,
            registered_by=self.hospital_b,
            category=self.cat_red,
            ward="Beta Ward",
            department="Beta Surgery",
            weight=Decimal("1.90"),
        )
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:bag_detail", kwargs={"bag_id": bag_b.bag_id}))
        self.assertEqual(res.status_code, 404)

    # 10. Hospital A cannot print Hospital B's labels (HTTP 404)
    def test_10_hospital_a_cannot_print_hospital_b_labels(self):
        bag_b = WasteBag.objects.create(
            hospital=self.hospital_b,
            registered_by=self.hospital_b,
            category=self.cat_red,
            ward="Beta Ward",
            department="Beta Surgery",
            weight=Decimal("1.90"),
        )
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")

        res_single = self.client.get(reverse("waste:bag_label", kwargs={"bag_id": bag_b.bag_id}))
        self.assertEqual(res_single.status_code, 404)

    # 11. Unauthorized users cannot regenerate QR codes
    def test_11_unauthorized_users_cannot_regenerate_qr(self):
        bag_a = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Ward A",
            department="Surgery",
            weight=Decimal("2.00"),
        )
        url = reverse("waste:qr_regenerate", kwargs={"bag_id": bag_a.bag_id})

        # Anonymous user -> Redirect to login
        res_anon = self.client.post(url)
        self.assertEqual(res_anon.status_code, 302)

        # Collector user -> HTTP 403 Forbidden
        self.client.login(username="collector_dan", password="DanPassword123!")
        res_collector = self.client.post(url)
        self.assertEqual(res_collector.status_code, 403)

        # Foreign Hospital B -> HTTP 404 Not Found
        self.client.login(username="hospital_beta", password="BetaPassword123!")
        res_hosp_b = self.client.post(url)
        self.assertEqual(res_hosp_b.status_code, 404)

        # GET request is rejected (require_POST)
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res_get = self.client.get(url)
        self.assertEqual(res_get.status_code, 405)

    # 12. Individual label page renders correctly with 100mm x 75mm CSS & QR image
    def test_12_individual_label_renders_correctly(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Pediatrics ICU",
            department="Pediatrics",
            weight=Decimal("2.85"),
        )
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:bag_label", kwargs={"bag_id": bag.bag_id}))

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "100mm")
        self.assertContains(res, "75mm")
        self.assertContains(res, bag.bag_id)
        self.assertContains(res, "2.85")
        self.assertContains(res, "Alpha Memorial Hospital")

    # 13. Batch label printing includes all bags and enforces batch ownership
    def test_13_batch_label_printing_and_ownership(self):
        batch = WasteRegistrationBatch.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            ward="Surgery",
            department="OT",
            bag_count=2,
            total_weight=Decimal("5.00"),
        )
        bag1 = WasteBag.objects.create(
            batch=batch,
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Surgery",
            department="OT",
            weight=Decimal("2.00"),
        )
        bag2 = WasteBag.objects.create(
            batch=batch,
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_red,
            ward="Surgery",
            department="OT",
            weight=Decimal("3.00"),
        )

        # Hospital A prints batch labels
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:batch_labels", kwargs={"batch_number": batch.batch_number}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, bag1.bag_id)
        self.assertContains(res, bag2.bag_id)
        self.assertContains(res, "page-break-after: always")

        # Hospital B attempts to print Hospital A's batch labels -> 404
        self.client.login(username="hospital_beta", password="BetaPassword123!")
        res_hosp_b = self.client.get(reverse("waste:batch_labels", kwargs={"batch_number": batch.batch_number}))
        self.assertEqual(res_hosp_b.status_code, 404)

    # 14. Missing QR image is safely generated on-demand when label is accessed
    def test_14_missing_qr_image_safely_generated_on_label_access(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="General Ward",
            department="Medicine",
            weight=Decimal("1.25"),
        )
        # Bag starts with PENDING and no image
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.PENDING)
        self.assertFalse(bool(bag.qr_image))

        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:bag_label", kwargs={"bag_id": bag.bag_id}))
        self.assertEqual(res.status_code, 200)

        # Lazy generation triggered
        bag.refresh_from_db()
        self.assertEqual(bag.qr_status, WasteBag.QRStatus.READY)
        self.assertTrue(bool(bag.qr_image))

    # 15. QR PNG download view serves valid image file attachment
    def test_15_qr_png_download_view(self):
        bag = WasteBag.objects.create(
            hospital=self.hospital_a,
            registered_by=self.hospital_a,
            category=self.cat_yellow,
            ward="Ward 1",
            department="Medicine",
            weight=Decimal("2.00"),
        )
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:qr_download", kwargs={"bag_id": bag.bag_id}))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "image/png")
        self.assertIn("attachment", res["Content-Disposition"])
        self.assertIn(f"{bag.bag_id}_qr.png", res["Content-Disposition"])

    # 16. Registration workflow post-commit QR generation creates ready bags
    def test_16_registration_workflow_post_commit_generation(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        post_data = {
            "ward": "Trauma Ward",
            "department": "Casualty",
            "bag_category_0": self.cat_yellow.id,
            "bag_weight_0": "3.50",
            "bag_category_1": self.cat_red.id,
            "bag_weight_1": "2.10",
        }
        res = self.client.post(reverse("waste:register"), post_data)
        self.assertEqual(res.status_code, 302)

        batch = WasteRegistrationBatch.objects.get(ward="Trauma Ward")
        # In test client with atomic/on_commit, view batch success page to verify rendering
        res_success = self.client.get(reverse("waste:batch_success", kwargs={"batch_number": batch.batch_number}))
        self.assertEqual(res_success.status_code, 200)

        for bag in batch.bags.all():
            bag.refresh_from_db()
            self.assertEqual(bag.qr_status, WasteBag.QRStatus.READY)
            self.assertTrue(bool(bag.qr_image))


class HospitalInventoryDashboardTests(TestCase):
    """
    Test suite for Step 2 — Feature 3: Hospital Waste Inventory Dashboard.
    Verifies 31 distinct dashboard, aggregation, filtering, sorting, pagination,
    batch overview, CSV export, tenant isolation, and RBAC requirements.
    """

    @classmethod
    def setUpTestData(cls):
        cls.temp_media = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media)
        self.media_override.enable()

        self.client = Client()

        # Seed categories
        self.cat_yellow, _ = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={
                "name": "Yellow",
                "display_color": "#FFD200",
                "container_type": "Yellow bags",
                "treatment_description": "Incineration",
                "is_active": True,
                "display_order": 1,
            },
        )
        self.cat_red, _ = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={
                "name": "Red",
                "display_color": "#E53935",
                "container_type": "Red bags",
                "treatment_description": "Autoclaving",
                "is_active": True,
                "display_order": 2,
            },
        )
        self.cat_white, _ = WasteCategory.objects.get_or_create(
            code="WHITE",
            defaults={
                "name": "White",
                "display_color": "#FFFFFF",
                "container_type": "Puncture-proof",
                "treatment_description": "Shredding",
                "is_active": True,
                "display_order": 3,
            },
        )
        self.cat_blue, _ = WasteCategory.objects.get_or_create(
            code="BLUE",
            defaults={
                "name": "Blue",
                "display_color": "#1E88E5",
                "container_type": "Cardboard boxes",
                "treatment_description": "Disinfection",
                "is_active": True,
                "display_order": 4,
            },
        )

        # Hospital A User
        self.hospital_a = User.objects.create_user(
            username="hospital_alpha",
            email="alpha@hospital.org",
            password="AlphaPassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Alpha Medical Center",
            phone="9876543210",
        )

        # Hospital B User
        self.hospital_b = User.objects.create_user(
            username="hospital_beta",
            email="beta@hospital.org",
            password="BetaPassword123!",
            role=User.Role.HOSPITAL,
            organization_name="Beta General Hospital",
            phone="9876543211",
        )

        # Collector User
        self.collector_user = User.objects.create_user(
            username="collector_dan",
            email="dan@collector.org",
            password="DanPassword123!",
            role=User.Role.COLLECTOR,
        )

        # Treatment Facility User
        self.facility_user = User.objects.create_user(
            username="facility_frank",
            email="frank@facility.org",
            password="FrankPassword123!",
            role=User.Role.FACILITY,
        )

    def tearDown(self):
        self.media_override.disable()

    def create_bag(self, hospital, category, weight=Decimal("1.00"), ward="Ward 1", department="General", qr_status=WasteBag.QRStatus.PENDING, status=WasteBag.Status.REGISTERED, batch=None):
        return WasteBag.objects.create(
            hospital=hospital,
            registered_by=hospital,
            category=category,
            weight=weight,
            ward=ward,
            department=department,
            qr_status=qr_status,
            status=status,
            batch=batch,
        )

    def create_batch(self, hospital, ward="Surgery", department="General", bag_count=0, total_weight=Decimal("0.00")):
        return WasteRegistrationBatch.objects.create(
            hospital=hospital,
            registered_by=hospital,
            ward=ward,
            department=department,
            bag_count=bag_count,
            total_weight=total_weight,
        )

    # 1. Headline metric: Total bag count calculation
    def test_01_headline_metric_total_bag_count(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"))
        self.create_bag(self.hospital_a, self.cat_red, Decimal("3.00"))
        self.create_bag(self.hospital_b, self.cat_blue, Decimal("1.00"))

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["total_bags"], 2)

    # 2. Headline metric: Total weight calculation
    def test_02_headline_metric_total_weight(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"))
        self.create_bag(self.hospital_a, self.cat_red, Decimal("3.75"))

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["total_weight"], Decimal("6.25"))

    # 3. Headline metric: Total registration batches count
    def test_03_headline_metric_total_batches_count(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        b1 = self.create_batch(self.hospital_a, ward="ICU")
        b2 = self.create_batch(self.hospital_a, ward="OT")
        # Hospital B batch should not count
        self.create_batch(self.hospital_b, ward="Emergency")

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["total_batches"], 2)

    # 4. Headline metric: QR Ready bag count calculation
    def test_04_headline_metric_qr_ready_count(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.00"), qr_status=WasteBag.QRStatus.READY)
        self.create_bag(self.hospital_a, self.cat_red, Decimal("2.00"), qr_status=WasteBag.QRStatus.READY)
        self.create_bag(self.hospital_a, self.cat_white, Decimal("2.00"), qr_status=WasteBag.QRStatus.PENDING)

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["qr_ready_count"], 2)
        self.assertAlmostEqual(res.context["qr_ready_pct"], 66.7, places=1)

    # 5. Headline metric: QR Pending bag count calculation
    def test_05_headline_metric_qr_pending_count(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.00"), qr_status=WasteBag.QRStatus.PENDING)

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["qr_pending_count"], 1)
        self.assertEqual(res.context["qr_pending_pct"], 100.0)

    # 6. Headline metric: QR Failed bag count calculation
    def test_06_headline_metric_qr_failed_count(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.00"), qr_status=WasteBag.QRStatus.FAILED)

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["qr_failed_count"], 1)
        self.assertEqual(res.context["qr_failed_pct"], 100.0)

    # 7. Headline metric: Category distribution count & weight breakdown
    def test_07_headline_metric_category_distribution(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("5.00"))
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("5.00"))
        self.create_bag(self.hospital_a, self.cat_red, Decimal("10.00"))

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        cat_metrics = res.context["category_metrics"]
        
        yellow_metric = next(m for m in cat_metrics if m["code"] == "YELLOW")
        red_metric = next(m for m in cat_metrics if m["code"] == "RED")
        
        self.assertEqual(yellow_metric["count"], 2)
        self.assertEqual(yellow_metric["weight"], Decimal("10.00"))
        self.assertEqual(yellow_metric["pct_count"], 66.7)
        self.assertEqual(yellow_metric["pct_weight"], 50.0)
        
        self.assertEqual(red_metric["count"], 1)
        self.assertEqual(red_metric["weight"], Decimal("10.00"))
        self.assertEqual(red_metric["pct_count"], 33.3)
        self.assertEqual(red_metric["pct_weight"], 50.0)

    # 8. Headline metric: Zero-bag empty inventory handling
    def test_08_headline_metric_empty_inventory(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["total_bags"], 0)
        self.assertEqual(res.context["total_weight"], Decimal("0.00"))
        self.assertEqual(res.context["total_batches"], 0)
        self.assertEqual(res.context["qr_ready_pct"], 0.0)
        self.assertEqual(res.context["qr_pending_pct"], 0.0)
        self.assertEqual(res.context["qr_failed_pct"], 0.0)

    # 9. Table filter: Search by Bag ID exact / partial match
    def test_09_search_by_bag_id(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"))
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("2.50"))

        res = self.client.get(reverse("waste:inventory"), {"q": bag1.bag_id.split("-")[-1]})
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 10. Table filter: Search by Batch Number exact / partial match
    def test_10_search_by_batch_number(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        batch = self.create_batch(self.hospital_a, ward="ICU")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.00"), batch=batch)
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("2.00"))

        res = self.client.get(reverse("waste:inventory"), {"q": batch.batch_number.split("-")[-1]})
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 11. Table filter: Search by Ward or Department partial match
    def test_11_search_by_ward_or_department(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"), ward="Cardiology Care", department="Cardiology")
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("2.50"), ward="Pediatric 1", department="Pediatrics")

        res = self.client.get(reverse("waste:inventory"), {"q": "Cardio"})
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 12. Table filter: Filter by Waste Category
    def test_12_filter_by_waste_category(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"))
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("2.50"))

        res = self.client.get(reverse("waste:inventory"), {"category": "YELLOW"})
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 13. Table filter: Filter by QR Code Status
    def test_13_filter_by_qr_status(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"), qr_status=WasteBag.QRStatus.READY)
        bag2 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"), qr_status=WasteBag.QRStatus.PENDING)

        res = self.client.get(reverse("waste:inventory"), {"qr_status": "READY"})
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 14. Table filter: Filter by Custody Status
    def test_14_filter_by_custody_status(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"), status=WasteBag.Status.REGISTERED)
        bag2 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"), status=WasteBag.Status.IN_TRANSIT)

        res = self.client.get(reverse("waste:inventory"), {"status": "REGISTERED"})
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 15. Table filter: Filter by Date Range
    def test_15_filter_by_date_range(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"))
        
        # Query matching today
        today_str = bag1.registered_at.strftime("%Y-%m-%d")
        res = self.client.get(reverse("waste:inventory"), {"date_from": today_str, "date_to": today_str})
        self.assertEqual(res.status_code, 200)
        self.assertIn(bag1.bag_id, [b.bag_id for b in res.context["page_obj"]])

        # Query matching future date (no results)
        res_future = self.client.get(reverse("waste:inventory"), {"date_from": "2099-01-01", "date_to": "2099-01-02"})
        self.assertEqual(res_future.status_code, 200)
        self.assertEqual(len(res_future.context["page_obj"]), 0)

    # 16. Table filter: Multi-filter combination
    def test_16_multi_filter_combination(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(
            self.hospital_a,
            self.cat_yellow,
            weight=Decimal("1.50"),
            qr_status=WasteBag.QRStatus.READY,
            status=WasteBag.Status.REGISTERED,
            ward="ICU Ward",
        )
        bag2 = self.create_bag(
            self.hospital_a,
            self.cat_red,
            weight=Decimal("2.50"),
            qr_status=WasteBag.QRStatus.READY,
            status=WasteBag.Status.REGISTERED,
            ward="ICU Ward",
        )

        res = self.client.get(reverse("waste:inventory"), {
            "category": "YELLOW",
            "qr_status": "READY",
            "status": "REGISTERED",
            "q": "ICU",
        })
        self.assertEqual(res.status_code, 200)
        bag_ids = [b.bag_id for b in res.context["page_obj"]]
        self.assertIn(bag1.bag_id, bag_ids)
        self.assertNotIn(bag2.bag_id, bag_ids)

    # 17. Table filter: Invalid filter values handled gracefully
    def test_17_invalid_filter_values_handled_gracefully(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"))

        res = self.client.get(reverse("waste:inventory"), {
            "category": "NON_EXISTENT_CAT",
            "qr_status": "INVALID_QR",
            "status": "INVALID_STATUS",
            "date_from": "not-a-date",
            "date_to": "garbage-date",
            "sort": "unknown_sort_field",
        })
        self.assertEqual(res.status_code, 200)

    # 18. Table sorting: Sort by newest registration date (date_desc)
    def test_18_sort_by_newest_date(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.00"))
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("2.00"))

        res = self.client.get(reverse("waste:inventory"), {"sort": "date_desc"})
        self.assertEqual(res.status_code, 200)
        bags = list(res.context["page_obj"])
        self.assertEqual(bags[0].bag_id, bag2.bag_id)
        self.assertEqual(bags[1].bag_id, bag1.bag_id)

    # 19. Table sorting: Sort by oldest registration date (date_asc)
    def test_19_sort_by_oldest_date(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.00"))
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("2.00"))

        res = self.client.get(reverse("waste:inventory"), {"sort": "date_asc"})
        self.assertEqual(res.status_code, 200)
        bags = list(res.context["page_obj"])
        self.assertEqual(bags[0].bag_id, bag1.bag_id)
        self.assertEqual(bags[1].bag_id, bag2.bag_id)

    # 20. Table sorting: Sort by highest weight (weight_desc)
    def test_20_sort_by_highest_weight(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"))
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("8.20"))

        res = self.client.get(reverse("waste:inventory"), {"sort": "weight_desc"})
        self.assertEqual(res.status_code, 200)
        bags = list(res.context["page_obj"])
        self.assertEqual(bags[0].bag_id, bag2.bag_id)
        self.assertEqual(bags[1].bag_id, bag1.bag_id)

    # 21. Table sorting: Sort by lowest weight (weight_asc)
    def test_21_sort_by_lowest_weight(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.50"))
        bag2 = self.create_bag(self.hospital_a, self.cat_red, Decimal("8.20"))

        res = self.client.get(reverse("waste:inventory"), {"sort": "weight_asc"})
        self.assertEqual(res.status_code, 200)
        bags = list(res.context["page_obj"])
        self.assertEqual(bags[0].bag_id, bag1.bag_id)
        self.assertEqual(bags[1].bag_id, bag2.bag_id)

    # 22. Pagination: Exactly 15 bags per page
    def test_22_pagination_fifteen_per_page(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        for i in range(22):
            self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.00"))

        res_p1 = self.client.get(reverse("waste:inventory"), {"page": 1})
        self.assertEqual(res_p1.status_code, 200)
        self.assertEqual(len(res_p1.context["page_obj"]), 15)

        res_p2 = self.client.get(reverse("waste:inventory"), {"page": 2})
        self.assertEqual(res_p2.status_code, 200)
        self.assertEqual(len(res_p2.context["page_obj"]), 7)

    # 23. Pagination: Filter parameters preserved across page links
    def test_23_pagination_filter_parameters_preserved(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        for i in range(20):
            self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.00"))

        res = self.client.get(reverse("waste:inventory"), {"category": "YELLOW", "sort": "weight_desc", "page": 1})
        self.assertEqual(res.status_code, 200)
        preserved = res.context.get("preserved_query_string", "")
        self.assertIn("category=YELLOW", preserved)
        self.assertIn("sort=weight_desc", preserved)
        self.assertNotIn("page=", preserved)

    # 24. Batch Overview: Batch cards/table shows correct bag count and weight
    def test_24_batch_overview_bag_count_and_weight(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        batch = self.create_batch(self.hospital_a, ward="Surgery", bag_count=2, total_weight=Decimal("6.00"))
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"), batch=batch)
        self.create_bag(self.hospital_a, self.cat_red, Decimal("3.50"), batch=batch)

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        recent_batches = res.context["recent_batches"]
        self.assertEqual(len(recent_batches), 1)
        b = recent_batches[0]
        self.assertEqual(b.bag_count, 2)
        self.assertEqual(b.total_weight, Decimal("6.00"))

    # 25. Batch Overview: Batch cards display correct QR status breakdown
    def test_25_batch_overview_qr_status_breakdown(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        batch = self.create_batch(self.hospital_a, ward="Surgery")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.00"), qr_status=WasteBag.QRStatus.READY, batch=batch)
        self.create_bag(self.hospital_a, self.cat_red, Decimal("2.00"), qr_status=WasteBag.QRStatus.PENDING, batch=batch)

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        b = res.context["recent_batches"][0]
        self.assertEqual(b.ready_count, 1)
        self.assertEqual(b.pending_count, 1)
        self.assertEqual(b.failed_count, 0)

    # 26. Batch Overview: Links to batch details and batch label print views
    def test_26_batch_overview_action_links(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        batch = self.create_batch(self.hospital_a, ward="Surgery")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.00"), batch=batch)

        res = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res.status_code, 200)
        detail_url = reverse("waste:batch_detail", kwargs={"batch_number": batch.batch_number})
        labels_url = reverse("waste:batch_labels", kwargs={"batch_number": batch.batch_number})
        self.assertContains(res, detail_url)
        self.assertContains(res, labels_url)

    # 27. CSV Export: Correct column headers and row count
    def test_27_csv_export_headers_and_row_count(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"), ward="Ward A", department="Surgery")
        self.create_bag(self.hospital_a, self.cat_red, Decimal("3.00"), ward="Ward B", department="ICU")

        res = self.client.get(reverse("waste:inventory_export"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", res["Content-Disposition"])

        content = res.content.decode("utf-8").splitlines()
        reader = list(csv.reader(content))
        headers = reader[0]
        self.assertIn("Bag Tracking ID", headers)
        self.assertIn("Category Name", headers)
        self.assertIn("Weight (KG)", headers)
        self.assertIn("QR Status", headers)
        # Header + 2 data rows = 3 rows
        self.assertEqual(len(reader), 3)

    # 28. CSV Export: Active filters applied to export
    def test_28_csv_export_respects_active_filters(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        bag_yellow = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"))
        bag_red = self.create_bag(self.hospital_a, self.cat_red, Decimal("3.00"))

        res = self.client.get(reverse("waste:inventory_export"), {"category": "YELLOW"})
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")
        self.assertIn(bag_yellow.bag_id, content)
        self.assertNotIn(bag_red.bag_id, content)

    # 29. CSV Export: Formula injection protection
    def test_29_csv_export_formula_injection_protection(self):
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        # Ward starting with dangerous formula characters =, +, -, @
        self.create_bag(
            self.hospital_a,
            self.cat_yellow,
            weight=Decimal("2.50"),
            ward="=cmd|' /C calc'!A0",
            department="+SUM(A1:A10)",
        )

        res = self.client.get(reverse("waste:inventory_export"))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8").splitlines()
        reader = list(csv.reader(content))
        data_row = reader[1]
        
        # Check ward and department cells
        ward_cell = data_row[5]
        dept_cell = data_row[6]
        self.assertTrue(ward_cell.startswith("'="), f"Formula injection not escaped: {ward_cell}")
        self.assertTrue(dept_cell.startswith("'+"), f"Formula injection not escaped: {dept_cell}")

    # 30. Tenant Isolation: Hospital A cannot see Hospital B's bags in dashboard or export
    def test_30_cross_hospital_tenant_isolation(self):
        bag_a = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("2.50"), ward="Alpha Ward")
        bag_b = self.create_bag(self.hospital_b, self.cat_red, Decimal("4.50"), ward="Beta Ward")

        # Login as Hospital A
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")
        
        # Test dashboard view
        res_dash = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_dash.status_code, 200)
        dashboard_bag_ids = [b.bag_id for b in res_dash.context["page_obj"]]
        self.assertIn(bag_a.bag_id, dashboard_bag_ids)
        self.assertNotIn(bag_b.bag_id, dashboard_bag_ids)
        self.assertEqual(res_dash.context["total_bags"], 1)

        # Test CSV export
        res_export = self.client.get(reverse("waste:inventory_export"))
        self.assertEqual(res_export.status_code, 200)
        export_content = res_export.content.decode("utf-8")
        self.assertIn(bag_a.bag_id, export_content)
        self.assertNotIn(bag_b.bag_id, export_content)

    # 31. RBAC & Security: Anonymous user redirected (302), Collector/Facility denied access (403)
    def test_31_rbac_and_security(self):
        # 1. Anonymous user redirected to login
        res_anon_dash = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_anon_dash.status_code, 302)
        self.assertIn(reverse("login"), res_anon_dash["Location"])

        res_anon_export = self.client.get(reverse("waste:inventory_export"))
        self.assertEqual(res_anon_export.status_code, 302)
        self.assertIn(reverse("login"), res_anon_export["Location"])

        # 2. Collector forbidden (403)
        self.client.login(username="collector_dan", password="DanPassword123!")
        res_col_dash = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_col_dash.status_code, 403)
        res_col_export = self.client.get(reverse("waste:inventory_export"))
        self.assertEqual(res_col_export.status_code, 403)
        self.client.logout()

        # 3. Treatment Facility forbidden (403)
        self.client.login(username="facility_frank", password="FrankPassword123!")
        res_fac_dash = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_fac_dash.status_code, 403)
        res_fac_export = self.client.get(reverse("waste:inventory_export"))
        self.assertEqual(res_fac_export.status_code, 403)

    # 32. Weight Precision Regression: Aggregations render exact 2-decimal KG without float noise
    def test_32_net_weight_precision_no_float_artifacts(self):
        bag_1 = self.create_bag(self.hospital_a, self.cat_yellow, Decimal("1.10"))
        self.client.login(username="hospital_alpha", password="AlphaPassword123!")

        res_single = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_single.status_code, 200)
        self.assertEqual(res_single.context["stats_total_weight"], Decimal("1.10"))
        self.assertContains(res_single, '1.10 <span class="fs-6 text-muted">KG</span>', html=True)
        self.assertNotContains(res_single, "000000000")

        self.create_bag(self.hospital_a, self.cat_red, Decimal("5.50"))
        self.create_bag(self.hospital_a, self.cat_white, Decimal("0.05"))
        self.create_bag(self.hospital_a, self.cat_blue, Decimal("25.00"))

        res_multi = self.client.get(reverse("waste:inventory"))
        self.assertEqual(res_multi.status_code, 200)
        self.assertEqual(res_multi.context["stats_total_weight"], Decimal("31.65"))
        self.assertContains(res_multi, '31.65 <span class="fs-6 text-muted">KG</span>', html=True)
        self.assertNotContains(res_multi, "000000000")

        res_analytics = self.client.get(reverse("reports:hospital_analytics"))
        self.assertEqual(res_analytics.status_code, 200)
        self.assertNotContains(res_analytics, "000000000")

        # Database weights remain exact Decimal values
        bag_1.refresh_from_db()
        self.assertEqual(bag_1.weight, Decimal("1.10"))


