from django.urls import path
from . import views

app_name = "waste"

urlpatterns = [
    # Hospital Inventory & Export
    path("", views.waste_inventory, name="inventory"),
    path("inventory/export/", views.inventory_export, name="inventory_export"),
    path("register/", views.waste_register, name="register"),

    # Batches
    path("batches/<str:batch_number>/", views.batch_detail, name="batch_detail"),
    path("batches/<str:batch_number>/success/", views.batch_success, name="batch_success"),
    path("batches/<str:batch_number>/labels/", views.batch_labels, name="batch_labels"),
    path("batches/<str:batch_number>/print-labels/", views.batch_labels, name="batch_print_labels"),

    # Individual Bag Detail & Actions
    path("bags/<str:bag_id>/", views.bag_detail, name="bag_detail"),
    path("bags/<str:bag_id>/label/", views.bag_label, name="bag_label"),
    path("bags/<str:bag_id>/qr-download/", views.qr_download, name="qr_download"),
    path("bags/<str:bag_id>/qr-regenerate/", views.qr_regenerate, name="qr_regenerate"),

    # Public Secure Tracking & Verification (UUID4 Payload)
    path("track/<uuid:qr_token>/", views.track_bag, name="track_bag"),
    path("qr/<uuid:qr_token>/", views.track_bag, name="qr_verify"),
]
