import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from collection.models import CollectionRequest, BagVerificationRecord
from facilities.models import TreatmentFacility
from notifications.models import Notification
from reports.models import ComplianceThresholdConfig, GeneratedReportLog
from reports.services.alert_service import (
    dispatch_compliance_alert,
    run_compliance_exception_audit,
)
from reports.services.analytics_engine import (
    get_admin_analytics,
    get_collector_analytics,
    get_facility_analytics,
    get_hospital_analytics,
    parse_date_range,
)
from reports.services.compliance_engine import (
    ComplianceSeverity,
    ComplianceStatus,
    evaluate_compliance_checkpoints,
)
from reports.services.regulatory_reports import (
    generate_report_csv,
    get_category_summary_data,
    get_discrepancy_audit_data,
    get_dispatch_manifest_data,
    get_facility_performance_data,
    get_form_iv_data,
    sanitize_csv_cell,
)
from tracking.models import CustodyDiscrepancy, CustodyEvent, CustodyHandover
from treatment.models import (
    DisposalRecord,
    FacilityBagReceipt,
    FacilityReceptionManifest,
    TreatmentBatch,
)
from waste.models import WasteBag, WasteCategory, WasteRegistrationBatch

User = get_user_model()


class AnalyticsAndComplianceBaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # 1. Users
        cls.admin_user = User.objects.create_superuser(
            username="test_admin",
            email="admin@medwaste.org",
            password="adminpassword123",
            role=User.Role.ADMIN,
        )

        cls.hospital_user_1 = User.objects.create_user(
            username="hosp_city",
            email="city@hospital.org",
            password="hospword123",
            role=User.Role.HOSPITAL,
            organization_name="City General Hospital",
        )
        cls.hospital_1 = cls.hospital_user_1

        cls.hospital_user_2 = User.objects.create_user(
            username="hosp_apollo",
            email="apollo@hospital.org",
            password="apolloword123",
            role=User.Role.HOSPITAL,
            organization_name="Apollo Clinic",
        )
        cls.hospital_2 = cls.hospital_user_2

        cls.collector_user = User.objects.create_user(
            username="test_collector",
            email="collector@medwaste.org",
            password="collectorpassword123",
            role=User.Role.COLLECTOR,
        )

        cls.facility_user = User.objects.create_user(
            username="test_facility",
            email="facility@medwaste.org",
            password="facilitypassword123",
            role=User.Role.FACILITY,
        )

        cls.facility = TreatmentFacility.objects.create(
            name="Central Bio-Treatment Plant",
            license_number="CBWTF-MH-2026-99",
            facility_type=TreatmentFacility.FacilityType.CBWTF,
            address="Industrial Zone Sector 4",
            district="Thane",
            state="Maharashtra",
            postal_code="400601",
            daily_capacity_kg=Decimal("5000.00"),
            operator_user=cls.facility_user,
            is_active=True,
        )

        # 2. Categories
        cls.cat_yellow = WasteCategory.objects.get_or_create(
            code="YELLOW",
            defaults={"name": "Yellow (Incinerable)", "display_color": "#EAB308", "display_order": 1}
        )[0]
        cls.cat_red = WasteCategory.objects.get_or_create(
            code="RED",
            defaults={"name": "Red (Autoclavable)", "display_color": "#EF4444", "display_order": 2}
        )[0]

        # 3. Waste Bags
        cls.batch_h1 = WasteRegistrationBatch.objects.create(
            hospital=cls.hospital_1,
            batch_number="BATCH-H1-001",
            registered_by=cls.hospital_user_1,
            ward="Surgery Ward",
            department="Surgery",
            bag_count=2,
            total_weight=Decimal("15.50"),
        )

        cls.bag_h1_1 = WasteBag.objects.create(
            hospital=cls.hospital_1,
            batch=cls.batch_h1,
            bag_id="BAG-H1-Y1",
            category=cls.cat_yellow,
            weight=Decimal("10.00"),
            status=WasteBag.Status.TREATED,
            qr_status=WasteBag.QRStatus.READY,
            registered_by=cls.hospital_user_1,
            ward="Surgery Ward",
            department="Surgery",
        )

        cls.bag_h1_2 = WasteBag.objects.create(
            hospital=cls.hospital_1,
            batch=cls.batch_h1,
            bag_id="BAG-H1-R1",
            category=cls.cat_red,
            weight=Decimal("5.50"),
            status=WasteBag.Status.RECEIVED,
            qr_status=WasteBag.QRStatus.READY,
            registered_by=cls.hospital_user_1,
            ward="Surgery Ward",
            department="Surgery",
        )

        cls.bag_h2_1 = WasteBag.objects.create(
            hospital=cls.hospital_2,
            bag_id="BAG-H2-Y1",
            category=cls.cat_yellow,
            weight=Decimal("8.00"),
            status=WasteBag.Status.REGISTERED,
            qr_status=WasteBag.QRStatus.READY,
            registered_by=cls.hospital_user_2,
            ward="OPD",
            department="General",
        )

        # 4. Collection Request
        cls.col_req = CollectionRequest.objects.create(
            hospital=cls.hospital_1,
            reference_number="REQ-H1-COL-001",
            preferred_pickup_date=timezone.localdate(),
            preferred_pickup_time=timezone.now().time(),
            status=CollectionRequest.Status.COMPLETED,
            assigned_collector=cls.collector_user,
            destination_facility=cls.facility,
            pickup_address="100 Health Way Loading Dock",
            contact_person="Dr. Sharma",
            contact_phone="9876543210",
            actual_collection_time=timezone.now() - datetime.timedelta(hours=4),
            total_bags=2,
            total_weight=Decimal("15.50"),
        )
        cls.col_req.bags.add(cls.bag_h1_1, cls.bag_h1_2)

        cls.verif_1 = BagVerificationRecord.objects.create(
            collection_request=cls.col_req,
            bag=cls.bag_h1_1,
            is_verified=True,
            verified_at=timezone.now() - datetime.timedelta(hours=4),
            verified_by=cls.collector_user,
        )
        cls.verif_2 = BagVerificationRecord.objects.create(
            collection_request=cls.col_req,
            bag=cls.bag_h1_2,
            is_verified=True,
            verified_at=timezone.now() - datetime.timedelta(hours=4),
            verified_by=cls.collector_user,
        )

        # 5. Handover
        cls.handover = CustodyHandover.objects.create(
            collection_request=cls.col_req,
            handover_type=CustodyHandover.HandoverType.HOSPITAL_TO_COLLECTOR,
            releasing_user=cls.hospital_user_1,
            releasing_role="Hospital Staff",
            receiving_user=cls.collector_user,
            receiving_role="Waste Collector",
            handover_timestamp=timezone.now() - datetime.timedelta(hours=4),
            total_bags_expected=2,
            total_bags_verified=2,
            status=CustodyHandover.Status.ACKNOWLEDGED,
        )

        # 6. Facility Manifest & Receipt
        cls.manifest = FacilityReceptionManifest.objects.create(
            facility=cls.facility,
            collection_request=cls.col_req,
            status=FacilityReceptionManifest.Status.ACCEPTED,
            received_at=timezone.now() - datetime.timedelta(hours=3),
            received_by=cls.facility_user,
            expected_bag_count=2,
            accepted_bag_count=2,
            rejected_bag_count=0,
            missing_bag_count=0,
            gross_received_weight=Decimal("15.50"),
            expected_weight=Decimal("15.50"),
        )

        cls.receipt_1 = FacilityBagReceipt.objects.create(
            reception_manifest=cls.manifest,
            bag=cls.bag_h1_1,
            acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED,
            actual_weight=Decimal("10.00"),
            verified_by=cls.facility_user,
            verified_at=timezone.now() - datetime.timedelta(hours=3),
        )
        cls.receipt_2 = FacilityBagReceipt.objects.create(
            reception_manifest=cls.manifest,
            bag=cls.bag_h1_2,
            acceptance_decision=FacilityBagReceipt.AcceptanceDecision.ACCEPTED,
            actual_weight=Decimal("5.50"),
            verified_by=cls.facility_user,
            verified_at=timezone.now() - datetime.timedelta(hours=3),
        )

        # 7. Treatment Batch
        cls.batch = TreatmentBatch.objects.create(
            facility=cls.facility,
            batch_number="TB-2026-001",
            treatment_method=TreatmentBatch.TreatmentMethod.INCINERATION,
            status=TreatmentBatch.Status.COMPLETED,
            start_time=timezone.now() - datetime.timedelta(hours=2),
            end_time=timezone.now() - datetime.timedelta(hours=1),
            processing_weight=Decimal("10.00"),
            operator=cls.facility_user,
        )
        cls.batch.bags.add(cls.bag_h1_1)

        # 8. Disposal Record
        cls.disposal = DisposalRecord.objects.create(
            facility=cls.facility,
            treatment_batch=cls.batch,
            final_disposal_method=DisposalRecord.DisposalMethod.SECURE_LANDFILL,
            disposal_location="Central Landfill Sector 9",
            status=DisposalRecord.Status.VERIFIED,
            disposal_date=timezone.localdate(),
            operator=cls.facility_user,
            verified_by=cls.admin_user,
            verified_at=timezone.now(),
        )
        cls.disposal.bags.add(cls.bag_h1_1)


class AnalyticsEngineTests(AnalyticsAndComplianceBaseTestCase):
    def test_parse_date_range_presets(self):
        start_dt, end_dt, start_d, end_d, label = parse_date_range("today")
        self.assertEqual(start_d, timezone.localdate())
        self.assertEqual(end_d, timezone.localdate())
        self.assertEqual(label, "Today")

        start_dt, end_dt, start_d, end_d, label = parse_date_range("last_7_days")
        self.assertEqual(label, "Last 7 Days")

        start_dt, end_dt, start_d, end_d, label = parse_date_range("current_year")
        self.assertEqual(start_d.year, timezone.localdate().year)
        self.assertEqual(start_d.month, 1)

    def test_get_admin_analytics_aggregates(self):
        start_dt, end_dt, start_d, end_d, _ = parse_date_range("last_30_days")
        data = get_admin_analytics(start_dt, end_dt, start_d, end_d)

        # 3 total bags across hospitals: 10 + 5.5 + 8 = 23.50 kg
        self.assertEqual(data["waste_overview"]["total_bags"], 3)
        self.assertEqual(data["waste_overview"]["total_weight"], Decimal("23.50"))
        self.assertEqual(data["treatment_overview"]["completed_batches"], 1)
        self.assertEqual(data["treatment_overview"]["total_treated_weight"], Decimal("10.00"))
        self.assertEqual(data["collection_overview"]["completed_requests"], 1)

    def test_get_admin_analytics_hospital_filter(self):
        start_dt, end_dt, start_d, end_d, _ = parse_date_range("last_30_days")
        data_h1 = get_admin_analytics(start_dt, end_dt, start_d, end_d, hospital_id=self.hospital_1.id)
        # Hospital 1 has 2 bags (15.5 kg)
        self.assertEqual(data_h1["waste_overview"]["total_bags"], 2)
        self.assertEqual(data_h1["waste_overview"]["total_weight"], Decimal("15.50"))

    def test_get_hospital_analytics_tenant_isolation(self):
        start_dt, end_dt, start_d, end_d, _ = parse_date_range("last_30_days")
        # Hospital 1 view
        h1_data = get_hospital_analytics(self.hospital_user_1, start_dt, end_dt, start_d, end_d)
        self.assertEqual(h1_data["total_bags"], 2)
        self.assertEqual(h1_data["total_weight"], Decimal("15.50"))
        self.assertEqual(h1_data["facility_acceptance"]["accepted"], 2)
        self.assertEqual(h1_data["facility_acceptance"]["rejected"], 0)

        # Hospital 2 view
        h2_data = get_hospital_analytics(self.hospital_user_2, start_dt, end_dt, start_d, end_d)
        self.assertEqual(h2_data["total_bags"], 1)
        self.assertEqual(h2_data["total_weight"], Decimal("8.00"))

    def test_get_collector_analytics(self):
        start_dt, end_dt, start_d, end_d, _ = parse_date_range("last_30_days")
        col_data = get_collector_analytics(self.collector_user, start_dt, end_dt, start_d, end_d)
        self.assertEqual(col_data["req_aggs"]["total_assigned"], 1)
        self.assertEqual(col_data["req_aggs"]["completed"], 1)
        self.assertEqual(col_data["verified_bags_count"], 2)
        self.assertEqual(col_data["total_verified_weight"], Decimal("15.50"))
        self.assertEqual(col_data["facility_manifests_count"], 1)

    def test_get_facility_analytics(self):
        start_dt, end_dt, start_d, end_d, _ = parse_date_range("last_30_days")
        fac_data = get_facility_analytics(self.facility, start_dt, end_dt, start_d, end_d)
        self.assertEqual(fac_data["total_bags_received"], 2)
        self.assertEqual(fac_data["accepted_bags"], 2)
        self.assertEqual(fac_data["total_treated_weight"], Decimal("10.00"))
        self.assertEqual(fac_data["batch_aggs"]["completed"], 1)
        self.assertEqual(fac_data["disposal_aggs"]["verified"], 1)
        self.assertGreater(fac_data["period_capacity_utilization_pct"], 0.0)


class ComplianceEngineTests(AnalyticsAndComplianceBaseTestCase):
    def test_evaluate_12_checkpoints(self):
        audit = evaluate_compliance_checkpoints(self.admin_user)
        self.assertEqual(len(audit["checkpoints"]), 12)
        
        # Verify checkpoint codes CP-01 through CP-12 exist
        codes = [cp["code"] for cp in audit["checkpoints"]]
        expected_codes = [f"CP-{i:02d}" for i in range(1, 13)]
        self.assertEqual(codes, expected_codes)

        # Health score is a valid percentage
        self.assertGreaterEqual(audit["overall_score"], 0)
        self.assertLessEqual(audit["overall_score"], 100)

        # Statutory data gap notice is present
        self.assertIn("Statutory Data Gap Notice", audit["statutory_data_gap"])

    def test_checkpoint_severity_escalation(self):
        # Create a critical discrepancy
        disc = CustodyDiscrepancy.objects.create(
            bag=self.bag_h1_1,
            discrepancy_type=CustodyDiscrepancy.DiscrepancyType.WEIGHT_MISMATCH,
            severity=CustodyDiscrepancy.Severity.CRITICAL,
            status=CustodyDiscrepancy.Status.OPEN,
            reported_by=self.collector_user,
            description="Net weight mismatch: expected 10.00 kg, observed 6.00 kg",
        )
        audit = evaluate_compliance_checkpoints(self.admin_user)
        cp12 = next(cp for cp in audit["checkpoints"] if cp["code"] == "CP-12")
        self.assertEqual(cp12["severity"], ComplianceSeverity.CRITICAL)
        self.assertEqual(cp12["status"], ComplianceStatus.NEEDS_REVIEW)
        self.assertLess(audit["overall_score"], 100)

    def test_hospital_role_isolation_in_compliance(self):
        audit_h2 = evaluate_compliance_checkpoints(self.hospital_user_2)
        # Hospital 2 has no collection requests or receipts
        cp5 = next(cp for cp in audit_h2["checkpoints"] if cp["code"] == "CP-05")
        self.assertEqual(cp5["status"], ComplianceStatus.RECORDED)


class RegulatoryReportsTests(AnalyticsAndComplianceBaseTestCase):
    def test_form_iv_generation(self):
        data = get_form_iv_data(self.admin_user, timezone.localdate() - datetime.timedelta(days=30), timezone.localdate())
        self.assertIn("Form IV", data["title"])
        self.assertEqual(data["total_generated_bags"], 3)
        self.assertEqual(data["total_generated_weight"], Decimal("23.50"))
        self.assertEqual(data["treated_weight"], Decimal("10.00"))
        self.assertEqual(data["total_residue_weight"], Decimal("10.00"))

    def test_category_summary_generation(self):
        data = get_category_summary_data(self.admin_user, timezone.localdate() - datetime.timedelta(days=30), timezone.localdate())
        self.assertEqual(data["total_bags"], 3)
        self.assertEqual(len(data["rows"]), WasteCategory.objects.filter(is_active=True).count())

    def test_facility_performance_generation(self):
        data = get_facility_performance_data(self.admin_user, timezone.localdate() - datetime.timedelta(days=30), timezone.localdate())
        self.assertEqual(len(data["facilities"]), 1)
        fac_rep = data["facilities"][0]
        self.assertEqual(fac_rep["received_bags"], 2)
        self.assertEqual(fac_rep["completed_batches"], 1)

    def test_dispatch_manifest_generation(self):
        data = get_dispatch_manifest_data(self.admin_user, timezone.localdate() - datetime.timedelta(days=30), timezone.localdate())
        self.assertEqual(data["total_requests"], 1)
        self.assertEqual(data["items"][0]["tracking_number"], "REQ-H1-COL-001")

    def test_discrepancy_audit_generation(self):
        data = get_discrepancy_audit_data(self.admin_user, timezone.localdate() - datetime.timedelta(days=30), timezone.localdate())
        self.assertEqual(data["total_discrepancies"], 0)


class CSVExportSecurityTests(AnalyticsAndComplianceBaseTestCase):
    def test_formula_injection_sanitization(self):
        self.assertEqual(sanitize_csv_cell("=1+1"), "'=1+1")
        self.assertEqual(sanitize_csv_cell("+cmd|' /C calc'!A0"), "'+cmd|' /C calc'!A0")
        self.assertEqual(sanitize_csv_cell("-2+3"), "'-2+3")
        self.assertEqual(sanitize_csv_cell("@SUM(A1:A10)"), "'@SUM(A1:A10)")
        self.assertEqual(sanitize_csv_cell("Standard Value"), "Standard Value")
        self.assertEqual(sanitize_csv_cell(None), "")

    def test_generate_report_csv_logs_audit_entry(self):
        initial_log_count = GeneratedReportLog.objects.count()
        data = get_form_iv_data(self.admin_user, timezone.localdate() - datetime.timedelta(days=30), timezone.localdate())
        csv_str = generate_report_csv(GeneratedReportLog.ReportType.ANNUAL_FORM_IV, data, self.admin_user)

        self.assertIn("MEDWASTE CONNECT — STATUTORY & OPERATIONAL COMPLIANCE REPORT", csv_str)
        self.assertIn("Category Code", csv_str)
        self.assertEqual(GeneratedReportLog.objects.count(), initial_log_count + 1)

        latest_log = GeneratedReportLog.objects.first()
        self.assertEqual(latest_log.report_type, GeneratedReportLog.ReportType.ANNUAL_FORM_IV)
        self.assertEqual(latest_log.generated_by, self.admin_user)
        self.assertEqual(latest_log.export_format, GeneratedReportLog.ExportFormat.CSV)


class AlertServiceTests(AnalyticsAndComplianceBaseTestCase):
    def test_dispatch_alert_with_cooldown(self):
        # 1. First alert dispatches
        created = dispatch_compliance_alert(
            recipient=self.admin_user,
            title="Overdue Alert",
            message="Test message",
            reference_id="TEST_REF_001",
            cooldown_hours=24,
        )
        self.assertTrue(created)
        self.assertEqual(Notification.objects.filter(reference_id="TEST_REF_001").count(), 1)

        # 2. Immediate duplicate call within cooldown window is suppressed
        created_again = dispatch_compliance_alert(
            recipient=self.admin_user,
            title="Overdue Alert",
            message="Test message",
            reference_id="TEST_REF_001",
            cooldown_hours=24,
        )
        self.assertFalse(created_again)
        self.assertEqual(Notification.objects.filter(reference_id="TEST_REF_001").count(), 1)

    def test_run_compliance_exception_audit_stale_treatment(self):
        # Create a received bag that has been stale for 50 hours (> 48h default)
        stale_time = timezone.now() - datetime.timedelta(hours=50)
        stale_bag = WasteBag.objects.create(
            hospital=self.hospital_1,
            bag_id="BAG-STALE-TREATMENT",
            category=self.cat_yellow,
            weight=Decimal("12.00"),
            status=WasteBag.Status.RECEIVED,
            qr_status=WasteBag.QRStatus.READY,
            registered_by=self.hospital_user_1,
            ward="ICU",
            department="Critical Care",
        )
        WasteBag.objects.filter(id=stale_bag.id).update(updated_at=stale_time)

        result = run_compliance_exception_audit()
        self.assertEqual(result["status"], "success")
        self.assertGreaterEqual(result["alerts_dispatched"], 1)

        # Confirm notification exists for admin
        notif = Notification.objects.filter(
            recipient=self.admin_user,
            reference_id=f"ALERT_TREATMENT_OVERDUE_BAG_{stale_bag.id}"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Operational Alert: Waste Staging Overdue", notif.title)


class ViewsAndUrlsAccessControlTests(AnalyticsAndComplianceBaseTestCase):
    def setUp(self):
        self.client = Client()

    def test_admin_analytics_view_access(self):
        # Anonymous redirects to login
        response = self.client.get(reverse("reports:admin_analytics"))
        self.assertEqual(response.status_code, 302)

        # Hospital user is denied (HTTP 403)
        self.client.force_login(self.hospital_user_1)
        response = self.client.get(reverse("reports:admin_analytics"))
        self.assertEqual(response.status_code, 403)

        # Admin user is granted (HTTP 200)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("reports:admin_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform-Wide Analytics")

    def test_hospital_analytics_view_access(self):
        self.client.force_login(self.hospital_user_1)
        response = self.client.get(reverse("reports:hospital_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "City General Hospital")

        # Collector is denied
        self.client.force_login(self.collector_user)
        response = self.client.get(reverse("reports:hospital_analytics"))
        self.assertEqual(response.status_code, 403)

    def test_collector_analytics_view_access(self):
        self.client.force_login(self.collector_user)
        response = self.client.get(reverse("reports:collector_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Collector Logistics Analytics")

    def test_facility_analytics_view_access(self):
        self.client.force_login(self.facility_user)
        response = self.client.get(reverse("reports:facility_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central Bio-Treatment Plant")

    def test_compliance_monitor_view(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("reports:compliance_monitor"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "12-Point Biomedical Waste Compliance Monitor")
        self.assertContains(response, "CP-01")
        self.assertContains(response, "CP-12")

    def test_regulatory_reports_view_preview_and_print(self):
        self.client.force_login(self.admin_user)
        # Interactive preview
        response = self.client.get(reverse("reports:regulatory_reports") + "?report_type=ANNUAL_FORM_IV")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Form IV")

        # Print layout
        response_print = self.client.get(reverse("reports:regulatory_reports") + "?report_type=ANNUAL_FORM_IV&format=print")
        self.assertEqual(response_print.status_code, 200)
        self.assertContains(response_print, "Statutory Print View")

    def test_export_report_csv_download(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("reports:export_report_csv") + "?report_type=ANNUAL_FORM_IV")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertIn("MEDWASTE CONNECT — STATUTORY & OPERATIONAL COMPLIANCE REPORT", response.content.decode("utf-8"))
