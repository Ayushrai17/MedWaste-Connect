import secrets
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from accounts.models import User


class Command(BaseCommand):
    help = "Seeds local development demo users for each of the 4 medical waste management roles (DEBUG=True only)."

    @staticmethod
    def _resolve_password(env_var: str) -> tuple[str, str]:
        """Return (password, display_source) from env var or generate a unique random secret."""
        configured = config(env_var, default="").strip()
        if configured:
            return configured, f"(from {env_var})"
        generated = secrets.token_urlsafe(14)
        return generated, generated

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Refusing to run seed_users when DEBUG=False. Demo seeding is restricted to local development."
            )

        self.stdout.write(self.style.MIGRATE_HEADING("--- Seeding Local Demo Users (DEBUG=True) ---"))

        demo_accounts = [
            {
                "username": "admin_demo",
                "email": "admin@medwaste.local",
                "password_env": "MEDWASTE_DEMO_ADMIN_PASSWORD",
                "first_name": "System",
                "last_name": "Administrator",
                "role": User.Role.ADMIN,
                "phone": "+91 98765 00001",
                "organization_name": "Central Health Directorate",
                "is_staff": True,
                "is_superuser": True,
            },
            {
                "username": "hospital_demo",
                "email": "hospital@medwaste.local",
                "password_env": "MEDWASTE_DEMO_HOSPITAL_PASSWORD",
                "first_name": "Dr. Sarah",
                "last_name": "Sharma",
                "role": User.Role.HOSPITAL,
                "phone": "+91 98765 00002",
                "organization_name": "Apex Super-Specialty Hospital",
                "is_staff": False,
                "is_superuser": False,
            },
            {
                "username": "collector_demo",
                "email": "collector@medwaste.local",
                "password_env": "MEDWASTE_DEMO_COLLECTOR_PASSWORD",
                "first_name": "Rajesh",
                "last_name": "Kumar",
                "role": User.Role.COLLECTOR,
                "phone": "+91 98765 00003",
                "organization_name": "EcoLogistics Transport Fleet #4",
                "is_staff": False,
                "is_superuser": False,
            },
            {
                "username": "facility_demo",
                "email": "facility@medwaste.local",
                "password_env": "MEDWASTE_DEMO_FACILITY_PASSWORD",
                "first_name": "Dr. Vikram",
                "last_name": "Mehta",
                "role": User.Role.FACILITY,
                "phone": "+91 98765 00004",
                "organization_name": "Metro Biomedical Incineration Plant",
                "is_staff": False,
                "is_superuser": False,
            },
        ]

        created_users = []
        for account in demo_accounts:
            env_var = account.pop("password_env")
            password, display_secret = self._resolve_password(env_var)
            user, created = User.objects.get_or_create(
                username=account["username"],
                defaults=account,
            )
            # Refresh profile fields and password for consistent local demo runs
            for key, value in account.items():
                setattr(user, key, value)
            user.set_password(password)
            user.save()

            created_users.append((account["username"], display_secret, account["role"], account["organization_name"]))

        # Ensure facility_demo has an approved TreatmentFacility registered
        from decimal import Decimal
        from facilities.models import TreatmentFacility
        from waste.models import WasteCategory
        fac_user = User.objects.filter(username="facility_demo").first()
        if fac_user and not TreatmentFacility.objects.filter(operator_user=fac_user).exists():
            demo_facility = TreatmentFacility.objects.create(
                name=fac_user.organization_name or "Metro Biomedical Incineration Plant",
                operator_user=fac_user,
                facility_type=TreatmentFacility.FacilityType.CBWTF,
                license_number="SPCB-CBWTF-DL-2026-0881",
                address="Plot 42, Okhla Industrial Area Phase-II",
                district="South East Delhi",
                state="Delhi",
                postal_code="110020",
                contact_person="Dr. Vikram Mehta",
                contact_phone="+91 98765 00004",
                contact_email="facility@medwaste.local",
                daily_capacity_kg=Decimal("5000.00"),
                approval_status=TreatmentFacility.ApprovalStatus.APPROVED,
                operational_availability=TreatmentFacility.OperationalAvailability.AVAILABLE,
                supports_incineration=True,
                supports_autoclaving=True,
                supports_shredding=True,
                supports_chemical_disinfection=True,
            )
            demo_facility.accepted_categories.set(WasteCategory.objects.all())

        self.stdout.write(self.style.SUCCESS("\nSuccessfully seeded 4 local demo accounts!\n"))
        self.stdout.write("=" * 105)
        self.stdout.write(f"{'ROLE':<12} | {'USERNAME':<16} | {'PASSWORD / ENV SOURCE':<38} | {'ORGANIZATION'}")
        self.stdout.write("-" * 105)
        for username, display_secret, role, org in created_users:
            self.stdout.write(f"{role:<12} | {username:<16} | {display_secret:<38} | {org}")
        self.stdout.write("=" * 105)
        self.stdout.write(self.style.WARNING("NOTE: Restricted to DEBUG=True local development only.\n"))

