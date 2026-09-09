import logging
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_FIXED = {
    "utc": timezone.utc,
    "asia/makassar": timezone(timedelta(hours=8)),
    "asia/singapore": timezone(timedelta(hours=8)),
    "asia/jakarta": timezone(timedelta(hours=7)),
    "asia/jayapura": timezone(timedelta(hours=9)),
}


def resolve_zone(name: str) -> Optional[tzinfo]:
    """Return the zone, or None if the name cannot be resolved."""
    key = (name or "UTC").strip()
    fixed = _FIXED.get(key.lower())
    if fixed is not None:
        return fixed
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def zone_for(name: str) -> tzinfo:
    zone = resolve_zone(name)
    if zone is None:
        logger.warning(
            "Unknown timezone %r, falling back to UTC. Install tzdata for IANA zone support.",
            name,
        )
        return timezone.utc
    return zone


def now_in_zone(name: str) -> datetime:
    return datetime.now(zone_for(name))


def parse_hhmm(value: str) -> time:
    parts = str(value).replace(".", ":").split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour, minute)
