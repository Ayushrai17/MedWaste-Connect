from django.urls import path
from . import views

app_name = "facilities"

urlpatterns = [
    path("admin/", views.admin_facility_list, name="admin_facility_list"),
    path("admin/new/", views.admin_facility_create, name="admin_facility_create"),
    path("admin/<str:facility_code>/", views.admin_facility_detail, name="admin_facility_detail"),
]

