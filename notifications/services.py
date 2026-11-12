import logging
from django.contrib.auth import get_user_model
from .models import Notification

logger = logging.getLogger(__name__)
User = get_user_model()


def send_notification(recipient, notification_type, title, message, reference_id="", link=""):
    """
    Creates an in-app notification for a designated user.
    """
    if not recipient or not recipient.is_authenticated:
        return None
    try:
        notification = Notification.objects.create(
            recipient=recipient,
            notification_type=notification_type,
            title=title,
            message=message,
            reference_id=reference_id,
            link=link,
        )
        return notification
    except Exception as e:
        logger.error(f"Failed to create notification for {recipient.username}: {e}", exc_info=True)
        return None


def notify_administrators(notification_type, title, message, reference_id="", link=""):
    """
    Dispatches a notification to all active system administrators.
    """
    admin_users = User.objects.filter(role=User.Role.ADMIN, is_active=True)
    created_notifications = []
    for admin in admin_users:
        n = send_notification(
            recipient=admin,
            notification_type=notification_type,
            title=title,
            message=message,
            reference_id=reference_id,
            link=link,
        )
        if n:
            created_notifications.append(n)
    return created_notifications

