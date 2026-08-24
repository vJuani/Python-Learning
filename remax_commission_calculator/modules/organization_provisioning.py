from zoneinfo import ZoneInfo

from modules.i18n import (
    SUPPORTED_LANGUAGES,
    normalize_language
)
from modules.organization_settings import (
    COMMON_TIMEZONES,
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE,
    SUPPORTED_CURRENCIES
)


def is_valid_timezone(timezone_name):
    if not timezone_name:
        return False

    try:
        ZoneInfo(timezone_name)
        return True

    except Exception:
        return False


def validate_organization_name(name):
    cleaned = name.strip()

    if cleaned == "":
        return "Organization name is required."

    if len(cleaned) > 120:
        return "Organization name is too long (max 120 characters)."

    return None


def validate_username(username, field_name="Username"):
    cleaned = username.strip()

    if cleaned == "":
        return f"{field_name} is required."

    if len(cleaned) > 64:
        return f"{field_name} is too long (max 64 characters)."

    if " " in cleaned:
        return f"{field_name} cannot contain spaces."

    return None


def validate_password(password, confirm_password):
    if password == "":
        return "Password is required."

    if password != confirm_password:
        return "Passwords do not match."

    if len(password) < 8:
        return "Password must have at least 8 characters."

    return None


def validate_provisioning_request(
    name,
    default_language,
    default_currency,
    timezone,
    admin_username,
    admin_password,
    admin_password_confirm,
    create_guest=False,
    guest_username="",
    guest_password="",
    guest_password_confirm=""
):
    errors = []

    name_error = validate_organization_name(name)

    if name_error is not None:
        errors.append(name_error)

    language = normalize_language(
        default_language
    )

    if language not in SUPPORTED_LANGUAGES:
        errors.append(
            "Default language must be 'es' or 'en'."
        )

    currency = default_currency.strip().upper()

    if currency not in SUPPORTED_CURRENCIES:
        errors.append(
            "Default currency must be 'USD' or 'ARS'."
        )

    timezone = timezone.strip()

    if not is_valid_timezone(timezone):
        errors.append("Timezone is not valid.")

    admin_username_error = validate_username(
        admin_username,
        "Admin username"
    )

    if admin_username_error is not None:
        errors.append(admin_username_error)

    password_error = validate_password(
        admin_password,
        admin_password_confirm
    )

    if password_error is not None:
        errors.append(password_error)

    if create_guest:
        guest_username_error = validate_username(
            guest_username,
            "Guest username"
        )

        if guest_username_error is not None:
            errors.append(guest_username_error)

        if (
            guest_username.strip().lower()
            == admin_username.strip().lower()
        ):
            errors.append(
                "Guest username must differ from admin username."
            )

        guest_password_error = validate_password(
            guest_password,
            guest_password_confirm
        )

        if guest_password_error is not None:
            errors.append(guest_password_error)

    if len(errors) > 0:
        return errors, None

    return [], {
        "name": name.strip(),
        "display_name": name.strip(),
        "default_language": language,
        "default_currency": currency,
        "timezone": timezone,
        "admin_username": admin_username.strip(),
        "create_guest": create_guest,
        "guest_username": (
            guest_username.strip()
            if create_guest
            else None
        )
    }


def prompt_yes_no(message, default=False):
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(message + suffix).strip().lower()

    if answer == "":
        return default

    return answer in (
        "y",
        "yes",
        "s",
        "si"
    )


def prompt_choice(message, choices, default):
    labels = "/".join(choices)
    answer = input(
        f"{message} ({labels}) [{default}]: "
    ).strip().lower()

    if answer == "":
        return default

    if answer in choices:
        return answer

    return default


def prompt_timezone(default=DEFAULT_TIMEZONE):
    print("Common timezones:")

    for timezone in COMMON_TIMEZONES:
        print(f"  - {timezone}")

    answer = input(
        f"Timezone [{default}]: "
    ).strip()

    if answer == "":
        return default

    return answer
