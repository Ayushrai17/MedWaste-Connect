import io
import logging
import uuid
import qrcode
from django.conf import settings
from django.core.files.base import ContentFile
from django.urls import reverse

logger = logging.getLogger(__name__)


def build_tracking_url(bag, request=None):
    """
    Constructs the public tracking and verification URL encoded into the physical QR code.
    Encodes the cryptographically secure UUID token (128 bits entropy), never database IDs.
    """
    if not bag or not bag.qr_token:
        raise ValueError("Cannot construct tracking URL for a bag without a valid UUID token.")

    rel_path = reverse("waste:track_bag", kwargs={"qr_token": str(bag.qr_token)})
    if request:
        return request.build_absolute_uri(rel_path)
    site_domain = getattr(settings, "SITE_DOMAIN", "http://127.0.0.1:8000")
    return f"{site_domain.rstrip('/')}{rel_path}"


def generate_bag_qr_code(bag, request=None, force=False):
    """
    Renders an encrypted QR code PNG in-memory and persists it to the configured storage backend.
    - Idempotent: Does not regenerate if already READY and the file exists on storage, unless force=True.
    - Requires valid UUID token.
    - Updates status: PENDING -> READY on success, FAILED on exception.
    - Safe to retry; does not corrupt or delete bag record on failure.
    """
    if not bag or not bag.qr_token:
        logger.error(f"Cannot generate QR code for bag {getattr(bag, 'id', None)}: missing UUID token.")
        return None

    # Idempotent check: return existing image if valid and not forcing regeneration
    if not force and bag.qr_status == bag.QRStatus.READY and bag.qr_image:
        try:
            if bag.qr_image.storage.exists(bag.qr_image.name):
                return bag.qr_image
        except Exception:
            pass

    try:
        payload = build_tracking_url(bag, request=request)

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"qr_{bag.bag_id}_{bag.qr_token.hex[:8]}.png"

        # If forcing regeneration, clean up existing file from storage if present
        if force and bag.qr_image:
            try:
                bag.qr_image.delete(save=False)
            except Exception as del_err:
                logger.warning(f"Could not delete old QR image for {bag.bag_id}: {del_err}")

        bag.qr_image.save(filename, ContentFile(buffer.getvalue()), save=False)
        bag.qr_status = bag.QRStatus.READY
        bag.save(update_fields=["qr_image", "qr_status", "updated_at"])

        try:
            from tracking.models import CustodyEvent
            from tracking.services import record_custody_event
            record_custody_event(
                bag=bag,
                event_type=CustodyEvent.EventType.QR_GENERATED,
                new_custody_status=bag.status,
                origin_location=bag.ward,
                verification_method="QR_CRYPTO_GENERATION",
                remarks="Dynamic cryptographic QR code label generated and affixed.",
            )
        except Exception as cust_err:
            logger.warning(f"Could not log QR_GENERATED custody event for {bag.bag_id}: {cust_err}")

        return bag.qr_image

    except Exception as exc:
        logger.error(f"QR generation failed for bag {bag.bag_id}: {exc}", exc_info=True)
        bag.qr_status = bag.QRStatus.FAILED
        try:
            bag.save(update_fields=["qr_status", "updated_at"])
        except Exception as save_err:
            logger.error(f"Could not save FAILED status for bag {bag.bag_id}: {save_err}")
        return None


def regenerate_bag_qr_code(bag, request=None):
    """
    Explicitly forces re-rendering of the QR code while preserving the existing UUID token.
    Safe retry mechanism for failed or missing images.
    """
    return generate_bag_qr_code(bag, request=request, force=True)


def get_or_create_qr_image(bag, request=None):
    """
    Safe accessor: returns existing file if present, or lazily generates it.
    """
    return generate_bag_qr_code(bag, request=request, force=False)

