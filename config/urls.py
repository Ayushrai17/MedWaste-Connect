"""
URL configuration for Medical Waste Collection and Segregation System.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    # Core landing page
    path("", include("core.urls")),
    # Accounts & Dashboards
    path("", include("accounts.urls")),
    path("accounts/", include("accounts.urls")),
    # Waste Management Operations
    path("waste/", include("waste.urls")),
    # Collection & Logistics Operations
    path("collection/", include("collection.urls")),
    # In-App Notifications
    path("notifications/", include("notifications.urls")),
    # Treatment Facilities & Capacity Management
    path("facilities/", include("facilities.urls")),
    # Treatment Operations, Reception & Disposal
    path("treatment/", include("treatment.urls")),
    # Tracking & Centralized Chain of Custody
    path("tracking/", include("tracking.urls")),
    # Analytics & Regulatory Compliance Reporting
    path("reports/", include("reports.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
