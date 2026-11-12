from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User


class HomePageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_home_page_status_and_sections(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        # Brand & Global Context
        self.assertContains(response, "MedWaste Connect")

        # Section 1: Hero & Cinematic 5-Layer Scene
        self.assertContains(response, "BIOMEDICAL WASTE MANAGEMENT PLATFORM")
        self.assertContains(response, "A Smarter Way to")
        self.assertContains(response, "Connect hospitals, waste collectors, and treatment facilities")
        self.assertContains(response, "Request a Pickup")
        self.assertContains(response, "Track Waste")
        self.assertContains(response, "hero-animation.css")
        self.assertContains(response, "hero-animation.js")
        self.assertContains(response, "gsap.min.js")
        self.assertContains(response, "hospital.png")
        self.assertContains(response, "collection-van.png")
        self.assertContains(response, "waste-bins.png")
        self.assertContains(response, "ground-shadow.png")
        self.assertContains(response, "driveway.png")

        # Section 2: Network Analytics
        self.assertContains(response, "Today's Analytics")
        self.assertContains(response, "Tracking every kilogram from source to safe disposal.")
        self.assertContains(response, "mini-stats-map")
        self.assertContains(response, "Waste Collected (kg)")
        self.assertContains(response, "Active Facilities")

        # Section 3: How Our Platform Works
        self.assertContains(response, "How Our Platform Works")
        self.assertContains(response, "SEGREGATE")
        self.assertContains(response, "COLLECT")
        self.assertContains(response, "TRANSPORT")
        self.assertContains(response, "TREAT")

        # Section 4: Platform Features
        self.assertContains(response, "Everything Connected. One Intelligent Platform.")
        self.assertContains(response, "Smart Waste Segregation")
        self.assertContains(response, "QR-Based Waste Identification")
        self.assertContains(response, "Digital Chain of Custody")

        # Section 5: Stakeholder Roles
        self.assertContains(response, "One Platform. Four Stakeholders.")
        self.assertContains(response, "Hospital / Clinic")
        self.assertContains(response, "Waste Collector")
        self.assertContains(response, "Treatment Facility")
        self.assertContains(response, "Administrator")

        # Section 6: Why Choose Our Platform
        self.assertContains(response, "Engineered for Total Regulatory Trust and Environmental Safety.")
        self.assertContains(response, "Transparent Waste Movement")
        self.assertContains(response, "Improved Accountability")

        # Section 7: Final CTA
        self.assertContains(response, "Building a Safer and More Responsible Healthcare Environment.")
        self.assertContains(response, "Get Started")

        # Section 8: Footer
        self.assertContains(response, "College Project — Medical Waste Management")
        self.assertContains(response, "CPCB Compliant Architecture")
        self.assertContains(response, "footer-pill-badge")
        self.assertNotContains(response, "bg-secondary-subtle text-white-50")


class AuthenticationAndRegistrationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_login_page_renders(self):
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Sign In to")
        self.assertContains(res, "id_password")
        self.assertContains(res, "togglePasswordBtn")
        self.assertContains(res, "remember_me")

    def test_registration_page_renders(self):
        res = self.client.get(reverse("register"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Hospital / Clinic Registration")
        self.assertContains(res, "organization_name")

    def test_hospital_registration_flow(self):
        reg_data = {
            "organization_name": "Apollo Care Specialty",
            "phone": "+91 99887 76655",
            "email": "apollo@care.org",
            "username": "apollo_care_user",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
        }
        res = self.client.post(reverse("register"), reg_data)
        # Should redirect to hospital dashboard upon successful registration
        self.assertRedirects(res, reverse("dashboard_hospital"))

        # Verify user created with HOSPITAL role
        user = User.objects.get(username="apollo_care_user")
        self.assertEqual(user.role, User.Role.HOSPITAL)
        self.assertEqual(user.organization_name, "Apollo Care Specialty")
        self.assertTrue(user.check_password("SecurePassword123!"))


class RoleBasedAccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.hospital_user = User.objects.create_user(
            username="test_hospital",
            password="Password123!",
            role=User.Role.HOSPITAL,
            first_name="HospitalStaff",
            organization_name="General Care Ward",
        )
        self.admin_user = User.objects.create_user(
            username="test_admin",
            password="Password123!",
            role=User.Role.ADMIN,
            first_name="DirectorAdmin",
            organization_name="Health Board",
        )
        self.collector_user = User.objects.create_user(
            username="test_collector",
            password="Password123!",
            role=User.Role.COLLECTOR,
            first_name="TransportDriver",
            organization_name="EcoFleet",
        )
        self.facility_user = User.objects.create_user(
            username="test_facility",
            password="Password123!",
            role=User.Role.FACILITY,
            first_name="PlantOperator",
            organization_name="Incinerator 1",
        )

    def test_unauthenticated_dashboards_redirect_to_login(self):
        for url_name in ["dashboard_admin", "dashboard_hospital", "dashboard_collector", "dashboard_facility"]:
            url = reverse(url_name)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response.url)

    def test_all_four_roles_authorized_access(self):
        # Hospital access
        self.client.login(username="test_hospital", password="Password123!")
        res = self.client.get(reverse("dashboard_hospital"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "General Care Ward")
        self.assertContains(res, "Logged Waste Bags")
        self.assertNotContains(res, "coming soon")
        self.assertNotContains(res, "Future modules")
        self.client.logout()

        # Collector access
        self.client.login(username="test_collector", password="Password123!")
        res = self.client.get(reverse("dashboard_collector"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Recent Assigned Consignments")
        self.assertContains(res, "Pending Acceptance")
        self.assertNotContains(res, "coming soon")
        self.assertNotContains(res, "Future modules")
        self.client.logout()

        # Facility access
        self.client.login(username="test_facility", password="Password123!")
        res = self.client.get(reverse("dashboard_facility"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Recent Treatment Batches")
        self.assertContains(res, "Awaiting Reception")
        self.assertNotContains(res, "coming soon")
        self.assertNotContains(res, "Future modules")
        self.client.logout()

        # Admin access
        self.client.login(username="test_admin", password="Password123!")
        res = self.client.get(reverse("dashboard_admin"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Central Administration Control Panel")
        self.client.logout()

    def test_cross_role_access_returns_403_forbidden(self):
        # Hospital cannot access Admin, Collector, or Facility
        self.client.login(username="test_hospital", password="Password123!")
        self.assertEqual(self.client.get(reverse("dashboard_admin")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_collector")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_facility")).status_code, 403)
        self.client.logout()

        # Collector cannot access Admin, Hospital, or Facility
        self.client.login(username="test_collector", password="Password123!")
        self.assertEqual(self.client.get(reverse("dashboard_admin")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_hospital")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_facility")).status_code, 403)
        self.client.logout()

        # Facility cannot access Admin, Hospital, or Collector
        self.client.login(username="test_facility", password="Password123!")
        self.assertEqual(self.client.get(reverse("dashboard_admin")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_hospital")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_collector")).status_code, 403)
        self.client.logout()

        # Admin cannot access Hospital, Collector, or Facility
        self.client.login(username="test_admin", password="Password123!")
        self.assertEqual(self.client.get(reverse("dashboard_hospital")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_collector")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard_facility")).status_code, 403)
        self.client.logout()

    def test_role_based_login_redirection(self):
        login_res = self.client.post(reverse("login"), {
            "username": "test_facility",
            "password": "Password123!"
        })
        self.assertRedirects(login_res, reverse("dashboard_facility"))
