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


# Meses abreviados en es-MX. strftime %b depende del locale del proceso (en
# Heroku es C → "Jan"), así que el nombre se resuelve aquí y no en el sistema.
_MESES_CORTOS = (
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
)


def _format_es(value: datetime | date, with_time: bool) -> str:
    base = f"{value.day:02d} {_MESES_CORTOS[value.month - 1]} {value.year}"
    if with_time and isinstance(value, datetime):
        return f"{base}, {value:%H:%M}"
    return base


def format_datetime_local(value: datetime | None, fmt: str | None = None) -> str:
    """Fecha y hora legibles en es-MX ("30 ene 2026, 14:42").

    `fmt` conserva la salida strftime para los pocos usos que requieren un
    formato técnico (exportaciones, inputs); la UI usa el formato legible.
    """
    local_value = to_local_datetime(value)
    if local_value is None:
        return "-"
    if fmt:
        return local_value.strftime(fmt)
    return _format_es(local_value, with_time=True)


def format_date_local(value: datetime | date | None, fmt: str | None = None) -> str:
    """Fecha legible en es-MX ("30 ene 2026"). `fmt` fuerza strftime."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        local_value = to_local_datetime(value)
        if local_value is None:
            return "-"
        if fmt:
            return local_value.strftime(fmt)
        return _format_es(local_value, with_time=False)
    if fmt:
        return value.strftime(fmt)
    return _format_es(value, with_time=False)


def format_date_iso(value: datetime | date | None) -> str:
    """Valor para <input type="date">: siempre %Y-%m-%d, sin adorno."""
    return format_date_local(value, fmt="%Y-%m-%d") if value is not None else ""
