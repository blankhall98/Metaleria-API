from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.datetime_utils import format_date_local, format_datetime_local


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory="app/templates")
    templates.env.filters["datetime_local"] = format_datetime_local
    templates.env.filters["date_local"] = format_date_local
    templates.env.globals["app_timezone_name"] = get_settings().APP_TIMEZONE
    return templates
