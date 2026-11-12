import csv
from datetime import datetime
from decimal import Decimal
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.views import role_required
from core.utils import quantize_weight
from .forms import WasteBatchRegistrationForm
from .models import (
    WasteCategory,
    WasteRegistrationBatch,
    WasteBag,
    get_min_bag_weight,
    get_max_bag_weight,
)
from .services import (
    build_tracking_url,
    generate_bag_qr_code,
    regenerate_bag_qr_code,
    get_or_create_qr_image,
)


def _filter_waste_queryset(request, base_qs):
    """
    Applies search, category, QR status, custody status, date range, and sorting filters.
    Always operates on an already hospital-scoped base QuerySet.
    """
    qs = base_qs.select_related("category", "batch")

    # 1. Search Query
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(bag_id__icontains=q)
            | Q(batch__batch_number__icontains=q)
            | Q(ward__icontains=q)
            | Q(department__icontains=q)
        )

    # 2. Filter by Waste Category Code
    selected_category = request.GET.get("category", "").strip().upper()
    if selected_category:
        qs = qs.filter(category__code=selected_category)

    # 3. Filter by QR Status
    selected_qr_status = request.GET.get("qr_status", "").strip().upper()
    if selected_qr_status in [WasteBag.QRStatus.READY, WasteBag.QRStatus.PENDING, WasteBag.QRStatus.FAILED]:
        qs = qs.filter(qr_status=selected_qr_status)

    # 4. Filter by Custody Lifecycle Status
    selected_status = request.GET.get("status", "").strip().upper()
    if selected_status and selected_status in dict(WasteBag.Status.choices):
        qs = qs.filter(status=selected_status)

    # 5. Filter by Date Range (YYYY-MM-DD)
    date_from = request.GET.get("date_from", "").strip()
    if date_from:
        try:
            d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
            qs = qs.filter(registered_at__date__gte=d_from)
        except ValueError:
            pass

    date_to = request.GET.get("date_to", "").strip()
    if date_to:
        try:
            d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
            qs = qs.filter(registered_at__date__lte=d_to)
        except ValueError:
            pass

    # 6. Sorting Order
    sort_param = request.GET.get("sort", "date_desc").strip().lower()
    sort_mapping = {
        "date_desc": "-registered_at",
        "date_asc": "registered_at",
        "weight_desc": "-weight",
        "weight_asc": "weight",
        "bag_id_asc": "bag_id",
        "bag_id_desc": "-bag_id",
    }
    order_field = sort_mapping.get(sort_param, "-registered_at")
    return qs.order_by(order_field), {
        "q": q,
        "selected_category": selected_category,
        "selected_qr_status": selected_qr_status,
        "selected_status": selected_status,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort_param,
    }


@role_required(User.Role.HOSPITAL)
def waste_inventory(request):
    """
    Hospital Waste Inventory Operations Dashboard.
    Provides real-time aggregated metrics, category distribution visualizations,
    QR generation status health, batch summaries, and advanced filtered inventory tables.
    Strictly isolated to request.user (Hospital).
    """
    hospital_user = request.user
    all_hospital_bags = WasteBag.objects.filter(hospital=hospital_user)

    # A. KPI Summary Metrics (Single Database Aggregation)
    aggregates = all_hospital_bags.aggregate(
        total_bags=Count("id"),
        total_weight=Sum("weight"),
        qr_ready=Count("id", filter=Q(qr_status=WasteBag.QRStatus.READY)),
        qr_pending=Count("id", filter=Q(qr_status=WasteBag.QRStatus.PENDING)),
        qr_failed=Count("id", filter=Q(qr_status=WasteBag.QRStatus.FAILED)),
    )

    stats_total_bags = aggregates["total_bags"] or 0
    stats_total_weight = quantize_weight(aggregates["total_weight"])
    stats_qr_ready = aggregates["qr_ready"] or 0
    stats_qr_pending = aggregates["qr_pending"] or 0
    stats_qr_failed = aggregates["qr_failed"] or 0
    stats_total_batches = WasteRegistrationBatch.objects.filter(hospital=hospital_user).count()

    # Calculate QR percentages for visual health bars
    qr_ready_pct = round((stats_qr_ready / stats_total_bags) * 100, 1) if stats_total_bags > 0 else 0.0
    qr_pending_pct = round((stats_qr_pending / stats_total_bags) * 100, 1) if stats_total_bags > 0 else 0.0
    qr_failed_pct = round((stats_qr_failed / stats_total_bags) * 100, 1) if stats_total_bags > 0 else 0.0

    # B. Waste Category Distribution Analytics
    active_categories = list(WasteCategory.objects.filter(is_active=True).order_by("display_order"))
    category_aggregates = (
        all_hospital_bags.values("category__code")
        .annotate(count=Count("id"), weight=Sum("weight"))
    )
    cat_agg_map = {item["category__code"]: item for item in category_aggregates}

    category_distribution = []
    chart_labels = []
    chart_weights = []
    chart_counts = []
    chart_colors = []

    for cat in active_categories:
        agg = cat_agg_map.get(cat.code, {})
        cat_count = agg.get("count", 0)
        cat_weight = quantize_weight(agg.get("weight"))

        weight_pct = round((float(cat_weight) / float(stats_total_weight)) * 100, 1) if stats_total_weight > 0 else 0.0
        count_pct = round((cat_count / stats_total_bags) * 100, 1) if stats_total_bags > 0 else 0.0

        item = {
            "code": cat.code,
            "name": cat.name,
            "display_color": cat.display_color,
            "container_type": cat.container_type,
            "treatment_description": cat.treatment_description,
            "count": cat_count,
            "weight": cat_weight,
            "weight_pct": weight_pct,
            "pct_weight": weight_pct,
            "count_pct": count_pct,
            "pct_count": count_pct,
        }
        category_distribution.append(item)

        chart_labels.append(f"{cat.name} ({cat.code})")
        chart_weights.append(float(cat_weight))
        chart_counts.append(cat_count)
        chart_colors.append(cat.display_color)

    # C. Advanced Filtering and Pagination on Table QuerySet
    filtered_qs, filter_params = _filter_waste_queryset(request, all_hospital_bags)

    paginator = Paginator(filtered_qs, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Build preserved query string for pagination links (excludes 'page')
    qp = request.GET.copy()
    if "page" in qp:
        del qp["page"]
    preserved_query_string = qp.urlencode()

    # D. Recent Registration Batches with QR Distribution
    recent_batches = (
        WasteRegistrationBatch.objects.filter(hospital=hospital_user)
        .annotate(
            ready_count=Count("bags", filter=Q(bags__qr_status=WasteBag.QRStatus.READY)),
            pending_count=Count("bags", filter=Q(bags__qr_status=WasteBag.QRStatus.PENDING)),
            failed_count=Count("bags", filter=Q(bags__qr_status=WasteBag.QRStatus.FAILED)),
        )
        .order_by("-created_at")[:6]
    )

    context = {
        # KPI Cards
        "stats_total_bags": stats_total_bags,
        "total_bags": stats_total_bags,
        "stats_total_weight": stats_total_weight,
        "total_weight": stats_total_weight,
        "stats_total_batches": stats_total_batches,
        "total_batches": stats_total_batches,
        "stats_qr_ready": stats_qr_ready,
        "qr_ready_count": stats_qr_ready,
        "stats_qr_pending": stats_qr_pending,
        "qr_pending_count": stats_qr_pending,
        "stats_qr_failed": stats_qr_failed,
        "qr_failed_count": stats_qr_failed,
        "qr_ready_pct": qr_ready_pct,
        "qr_pending_pct": qr_pending_pct,
        "qr_failed_pct": qr_failed_pct,

        # Category Analytics
        "categories": active_categories,
        "category_distribution": category_distribution,
        "category_metrics": category_distribution,
        "chart_labels_json": json.dumps(chart_labels),
        "chart_weights_json": json.dumps(chart_weights),
        "chart_counts_json": json.dumps(chart_counts),
        "chart_colors_json": json.dumps(chart_colors),

        # Table & Pagination
        "page_obj": page_obj,
        "preserved_query_string": preserved_query_string,
        "status_choices": WasteBag.Status.choices,
        "qr_status_choices": WasteBag.QRStatus.choices,

        # Filter Form State
        **filter_params,

        # Batch Operations
        "recent_batches": recent_batches,
        "last_refreshed": timezone.now(),
    }
    return render(request, "waste/inventory.html", context)


@role_required(User.Role.HOSPITAL)
def inventory_export(request):
    """
    Exports filtered hospital waste inventory as a CSV file.
    Security Guarantees:
    - Scoped strictly to request.user (Hospital). Cross-hospital records excluded.
    - Prevents CSV formula injection by prepending ' to values beginning with =, +, -, @.
    - Excludes internal UUID tokens, private staff identities, and file paths.
    """
    hospital_user = request.user
    base_qs = WasteBag.objects.filter(hospital=hospital_user)
    filtered_qs, _ = _filter_waste_queryset(request, base_qs)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"waste_inventory_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    # Header Row
    writer.writerow([
        "Bag Tracking ID",
        "Category Code",
        "Category Name",
        "Weight (KG)",
        "Unit",
        "Ward / Unit",
        "Clinical Department",
        "Batch Number",
        "QR Status",
        "Custody Status",
        "Registration Date",
    ])

    def sanitize(val):
        if val is None:
            return ""
        s = str(val).strip()
        # Neutralize CSV formula injection characters
        if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{s}"
        return s

    # Stream or iterate records efficiently
    for bag in filtered_qs.iterator(chunk_size=1000):
        writer.writerow([
            sanitize(bag.bag_id),
            sanitize(bag.category.code),
            sanitize(bag.category.name),
            sanitize(f"{bag.weight:.2f}"),
            sanitize(bag.weight_unit),
            sanitize(bag.ward),
            sanitize(bag.department),
            sanitize(bag.batch.batch_number if bag.batch else "Independent"),
            sanitize(bag.get_qr_status_display()),
            sanitize(bag.get_status_display()),
            sanitize(bag.registered_at.strftime("%Y-%m-%d %H:%M:%S")),
        ])

    return response


@role_required(User.Role.HOSPITAL)
def waste_register(request):
    """
    Hospital waste registration interface.
    Atomic registration with post-commit QR generation hook.
    """
    categories = WasteCategory.objects.filter(is_active=True).order_by("display_order")
    min_weight = get_min_bag_weight()
    max_weight = get_max_bag_weight()

    if request.method == "POST":
        form = WasteBatchRegistrationForm(request.POST)
        if form.is_valid():
            batch, created_bags = form.save(
                hospital=request.user,
                registered_by=request.user,
            )

            # Post-commit hook: Decouples file generation from the database transaction
            transaction.on_commit(
                lambda: [generate_bag_qr_code(b, request=request) for b in created_bags]
            )

            messages.success(
                request,
                f"Successfully registered batch {batch.batch_number} containing {batch.bag_count} "
                f"waste bag(s) totaling {batch.total_weight} kg."
            )
            return redirect("waste:batch_success", batch_number=batch.batch_number)
        else:
            messages.error(request, "Please correct the highlighted errors before submitting.")
    else:
        form = WasteBatchRegistrationForm()

    context = {
        "form": form,
        "categories": categories,
        "min_weight": min_weight,
        "max_weight": max_weight,
    }
    return render(request, "waste/register.html", context)


@role_required(User.Role.HOSPITAL)
def batch_success(request, batch_number):
    """
    Confirmation view for a newly registered batch.
    Ensures QR images are available for immediate label printing.
    """
    batch = get_object_or_404(
        WasteRegistrationBatch.objects.select_related("hospital", "registered_by"),
        batch_number=batch_number,
        hospital=request.user,
    )
    bags = list(batch.bags.select_related("category").order_by("bag_id"))

    for b in bags:
        get_or_create_qr_image(b, request=request)

    context = {
        "batch": batch,
        "bags": bags,
    }
    return render(request, "waste/batch_success.html", context)


@role_required(User.Role.HOSPITAL)
def bag_detail(request, bag_id):
    """
    Detailed audit record for an individual physical waste bag.
    Renders high-resolution QR code, label print CTA, and PNG download.
    """
    bag = get_object_or_404(
        WasteBag.objects.select_related("category", "batch", "hospital", "registered_by"),
        bag_id=bag_id,
        hospital=request.user,
    )

    get_or_create_qr_image(bag, request=request)
    tracking_url = build_tracking_url(bag, request=request)

    context = {
        "bag": bag,
        "tracking_url": tracking_url,
    }
    return render(request, "waste/bag_detail.html", context)


@role_required(User.Role.HOSPITAL)
def batch_detail(request, batch_number):
    """
    Detailed view for an entire registration batch and all of its bags.
    """
    batch = get_object_or_404(
        WasteRegistrationBatch.objects.select_related("hospital", "registered_by"),
        batch_number=batch_number,
        hospital=request.user,
    )
    bags = batch.bags.select_related("category").order_by("bag_id")

    context = {
        "batch": batch,
        "bags": bags,
    }
    return render(request, "waste/batch_detail.html", context)


@role_required(User.Role.HOSPITAL)
def bag_label(request, bag_id):
    """
    Single-bag printable label formatted for 100mm x 75mm physical adhesive printers.
    """
    bag = get_object_or_404(
        WasteBag.objects.select_related("category", "hospital", "batch"),
        bag_id=bag_id,
        hospital=request.user,
    )
    get_or_create_qr_image(bag, request=request)

    context = {
        "bags": [bag],
        "is_batch": False,
        "title": f"Label — {bag.bag_id}",
    }
    return render(request, "waste/bag_label.html", context)


@role_required(User.Role.HOSPITAL)
def batch_labels(request, batch_number):
    """
    Bulk label printing for all waste bags contained within a batch.
    """
    batch = get_object_or_404(
        WasteRegistrationBatch.objects.select_related("hospital"),
        batch_number=batch_number,
        hospital=request.user,
    )
    bags = list(batch.bags.select_related("category", "hospital").order_by("bag_id"))

    for b in bags:
        get_or_create_qr_image(b, request=request)

    context = {
        "bags": bags,
        "batch": batch,
        "is_batch": True,
        "title": f"Labels — Batch {batch.batch_number}",
    }
    return render(request, "waste/bag_label.html", context)


@role_required(User.Role.HOSPITAL)
def qr_download(request, bag_id):
    """
    Serves the individual bag's PNG QR code file as an attachment download.
    """
    bag = get_object_or_404(WasteBag, bag_id=bag_id, hospital=request.user)
    qr_img = get_or_create_qr_image(bag, request=request)

    if not qr_img or not qr_img.storage.exists(qr_img.name):
        raise Http404("QR code image file is unavailable.")

    response = FileResponse(
        qr_img.open("rb"),
        content_type="image/png",
        as_attachment=True,
        filename=f"{bag.bag_id}_qr.png",
    )
    return response


@require_POST
@role_required(User.Role.HOSPITAL, User.Role.ADMIN)
def qr_regenerate(request, bag_id):
    """
    Safe retry / regenerate endpoint for hospital staff and platform admins.
    """
    if request.user.role == User.Role.ADMIN:
        bag = get_object_or_404(WasteBag, bag_id=bag_id)
    else:
        bag = get_object_or_404(WasteBag, bag_id=bag_id, hospital=request.user)
    result = regenerate_bag_qr_code(bag, request=request)

    if result:
        messages.success(request, f"QR code for bag {bag.bag_id} was successfully regenerated.")
    else:
        messages.error(request, f"Failed to regenerate QR code for bag {bag.bag_id}. Please check system logs.")

    return redirect("waste:bag_detail", bag_id=bag.bag_id)


def track_bag(request, qr_token):
    """
    Public and Secure Bag Tracking & Verification Page.
    """
    try:
        bag = get_object_or_404(
            WasteBag.objects.select_related("category"),
            qr_token=qr_token,
        )
    except (Http404, ValueError):
        return render(
            request,
            "waste/track_unavailable.html",
            {"error_message": "Invalid or expired biohazard bag verification token."},
            status=404,
        )

    context = {
        "bag_id": bag.bag_id,
        "category_code": bag.category.code,
        "category_name": bag.category.name,
        "category_color": bag.category.display_color,
        "container_type": bag.category.container_type,
        "treatment_description": bag.category.treatment_description,
        "weight": bag.weight,
        "weight_unit": bag.weight_unit,
        "status_display": bag.get_status_display(),
        "status_code": bag.status,
        "qr_status": bag.get_qr_status_display(),
        "registered_at": bag.registered_at,
    }
    return render(request, "waste/track_bag.html", context)
