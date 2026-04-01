from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings


@lru_cache
def get_app_timezone() -> ZoneInfo:
    tz_name = get_settings().APP_TIMEZONE
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def to_local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(get_app_timezone())


def format_datetime_local(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    local_value = to_local_datetime(value)
    if local_value is None:
        return "-"
    return local_value.strftime(fmt)


def format_date_local(value: datetime | date | None, fmt: str = "%Y-%m-%d") -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        local_value = to_local_datetime(value)
        if local_value is None:
            return "-"
        return local_value.strftime(fmt)
    return value.strftime(fmt)
