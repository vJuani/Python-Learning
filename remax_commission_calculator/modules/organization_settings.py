import re
from zoneinfo import ZoneInfo

from modules.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    normalize_language
)

from modules.database.organization_settings_repository import (
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE
)
from modules.invoicing import (
    PAYMENT_CONDITIONS,
    TAX_CONDITIONS,
    validate_cuit,
)


SUPPORTED_CURRENCIES = (
    "USD",
    "ARS"
)

HEX_COLOR_PATTERN = re.compile(
    r"^#[0-9A-Fa-f]{6}$"
)

LOGO_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp"
}

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024

COMMON_TIMEZONES = (
    "America/Argentina/Buenos_Aires",
    "America/Argentina/Cordoba",
    "America/Argentina/Mendoza",
    "America/Montevideo",
    "America/Santiago",
    "America/Sao_Paulo",
    "America/New_York",
    "America/Los_Angeles",
    "Europe/Madrid",
    "UTC"
)


def is_valid_timezone(timezone_name):
    if not timezone_name:
        return False

    try:
        ZoneInfo(timezone_name)
        return True

    except Exception:
        return False


def normalize_accent_color(value):
    if value is None:
        return None

    cleaned = value.strip()

    if cleaned == "":
        return None

    if not HEX_COLOR_PATTERN.match(cleaned):
        return None

    return cleaned.lower()


def darken_hex_color(hex_color, factor=0.85):
    hex_color = hex_color.lstrip("#")
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)

    red = max(0, min(255, int(red * factor)))
    green = max(0, min(255, int(green * factor)))
    blue = max(0, min(255, int(blue * factor)))

    return f"#{red:02x}{green:02x}{blue:02x}"


def accent_soft_rgba(hex_color, alpha=0.12):
    hex_color = hex_color.lstrip("#")
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)

    return f"rgba({red}, {green}, {blue}, {alpha})"


def build_branding_css(accent_color):
    if accent_color is None:
        return None

    return {
        "accent": accent_color,
        "accent_hover": darken_hex_color(
            accent_color
        ),
        "accent_soft": accent_soft_rgba(
            accent_color
        ),
        "accent_ink": darken_hex_color(
            accent_color,
            factor=0.75
        )
    }


def validate_organization_settings_form(
    form_data,
    logo_file=None,
    remove_logo=False
):
    errors = []

    display_name = form_data.get(
        "display_name",
        ""
    ).strip()

    if display_name == "":
        errors.append(
            "settings_display_name_required"
        )

    default_language = normalize_language(
        form_data.get(
            "default_language",
            DEFAULT_LANGUAGE
        )
    )

    if default_language not in SUPPORTED_LANGUAGES:
        errors.append(
            "settings_language_invalid"
        )

    default_currency = form_data.get(
        "default_currency",
        DEFAULT_CURRENCY
    ).strip().upper()

    if default_currency not in SUPPORTED_CURRENCIES:
        errors.append(
            "settings_currency_invalid"
        )

    timezone = form_data.get(
        "timezone",
        DEFAULT_TIMEZONE
    ).strip()

    if not is_valid_timezone(timezone):
        errors.append(
            "settings_timezone_invalid"
        )

    accent_color = normalize_accent_color(
        form_data.get(
            "accent_color",
            ""
        )
    )

    if (
        form_data.get(
            "accent_color",
            ""
        ).strip() != ""
        and accent_color is None
    ):
        errors.append(
            "settings_accent_invalid"
        )

    logo_error = validate_logo_upload(
        logo_file
    )

    if logo_error is not None:
        errors.append(logo_error)

    legal_name = form_data.get("legal_name", "").strip()
    tax_id = form_data.get("tax_id", "").strip()
    tax_condition = form_data.get(
        "tax_condition",
        "",
    ).strip()
    fiscal_address = form_data.get(
        "fiscal_address",
        "",
    ).strip()
    trade_name = form_data.get("trade_name", "").strip()
    billing_email = form_data.get(
        "billing_email",
        "",
    ).strip()
    default_payment_condition = form_data.get(
        "default_payment_condition",
        "cuenta_corriente",
    ).strip() or "cuenta_corriente"

    if tax_id and not validate_cuit(tax_id):
        errors.append("settings_billing_cuit_invalid")

    if (
        tax_condition
        and tax_condition not in TAX_CONDITIONS
    ):
        errors.append("settings_billing_tax_condition_invalid")

    if (
        default_payment_condition
        not in PAYMENT_CONDITIONS
    ):
        errors.append(
            "settings_billing_payment_condition_invalid"
        )

    if len(errors) > 0:
        return errors, None

    return [], {
        "display_name": display_name,
        "default_language": default_language,
        "default_currency": default_currency,
        "timezone": timezone,
        "accent_color": accent_color,
        "remove_logo": remove_logo,
        "legal_name": legal_name,
        "tax_id": tax_id,
        "tax_condition": tax_condition,
        "fiscal_address": fiscal_address,
        "trade_name": trade_name,
        "billing_email": billing_email,
        "default_payment_condition": (
            default_payment_condition
        ),
    }


def validate_logo_upload(logo_file):
    if logo_file is None:
        return None

    if not logo_file.filename:
        return None

    filename = logo_file.filename.lower()
    extension = None

    for allowed in LOGO_EXTENSIONS:
        if filename.endswith(allowed):
            extension = allowed
            break

    if extension is None:
        return "settings_logo_invalid_type"

    logo_file.stream.seek(0, 2)
    size = logo_file.stream.tell()
    logo_file.stream.seek(0)

    if size > MAX_LOGO_SIZE_BYTES:
        return "settings_logo_too_large"

    return None
