from django.conf import settings


def site_settings(request):
    """Exposes global site settings to all templates."""
    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "MedWaste Connect"),
    }

