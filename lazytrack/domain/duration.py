from decimal import ROUND_HALF_UP, Decimal


def format_hours(hours: Decimal) -> str:
    minutes = (hours * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    whole = int(minutes // 60)
    leftover = int(minutes % 60)
    if leftover:
        return f"{whole}h {leftover}m"
    return f"{whole}h"
