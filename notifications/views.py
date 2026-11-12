from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .models import Notification


def notification_list_view(request):
    """
    Displays full notification history for the authenticated user.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    notifications_qs = Notification.objects.filter(recipient=request.user)
    paginator = Paginator(notifications_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "notifications/notification_list.html", {"page_obj": page_obj})


@require_POST
def notification_mark_read_view(request, notification_id):
    """
    Marks a single notification as read and redirects to its destination link.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.mark_as_read()

    next_url = notification.link if notification.link else "notifications:list"
    return redirect(next_url)


@require_POST
def notification_mark_all_read_view(request):
    """
    Marks all notifications for authenticated user as read.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    messages.success(request, "All notifications marked as read.")
    return redirect("notifications:list")
