from decimal import Decimal

from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.datetime_utils import format_date_iso, format_date_local, format_datetime_local


def format_precio(value) -> str:
    """Precio legible: $20.00, no $20.00000.

    Los precios se almacenan con 5 decimales; solo se muestran los que
    existen de verdad, con un mínimo de 2 ($0.8535 se respeta, $20 → $20.00).
    """
    amount = Decimal(str(value or 0))
    quantized = amount.quantize(Decimal("0.01"))
    if quantized == amount:
        return f"${quantized:,.2f}"
    text = f"{amount:,.5f}".rstrip("0")
    return f"${text}"


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory="app/templates")
    templates.env.filters["datetime_local"] = format_datetime_local
    templates.env.filters["date_local"] = format_date_local
    templates.env.filters["date_iso"] = format_date_iso
    templates.env.filters["precio"] = format_precio
    templates.env.globals["app_timezone_name"] = get_settings().APP_TIMEZONE
    return templates
