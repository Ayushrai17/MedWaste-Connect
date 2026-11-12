from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

WEIGHT_QUANT = Decimal("0.01")


def quantize_weight(value):
    """
    Normalizes aggregated or computed biomedical waste weight values to 2 decimal places
    without altering stored database precision. Avoids SQLite float-backed Sum() artifacts
    such as Decimal('1.1000000000000000').
    """
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(value)).quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")

