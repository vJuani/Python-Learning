from datetime import datetime

from modules.property_types import (
    INVOICE_FULL_COMMISSION_VALUES,
    LISTING_PURPOSES,
    PROPERTY_TYPES,
)


AGENT_TYPES = (
    "Alto",
    "Puro",
    "Junior",
    "RAPP"
)

JURISDICTIONS = (
    "CABA",
    "PBA"
)


def get_float(message):
    while True:
        try:
            value = float(input(message))

            if value < 0:
                print("The value cannot be negative.")
            else:
                return value

        except ValueError:
            print(
                "Invalid input. "
                "Please enter a valid number."
            )


def parse_positive_float(
    value,
    field_name="Value"
):
    if value is None:
        return None, (
            f"{field_name} is required."
        )

    if isinstance(value, str):
        value = value.strip()

        if value == "":
            return None, (
                f"{field_name} is required."
            )

    try:
        number = float(value)

        if number < 0:
            return None, (
                f"{field_name} cannot be negative."
            )

        return number, None

    except (TypeError, ValueError):
        return None, (
            f"{field_name} must be a valid number."
        )


def validate_required_text(
    value,
    field_name
):
    if value is None:
        return (
            f"{field_name} cannot be empty."
        )

    if value.strip() == "":
        return (
            f"{field_name} cannot be empty."
        )

    return None


def validate_choice(
    value,
    allowed_values,
    field_name
):
    if value not in allowed_values:
        return (
            f"You must select a valid {field_name}."
        )

    return None


def validate_date_format(
    value,
    field_name="Date"
):
    if value is None:
        return None, (
            f"{field_name} cannot be empty."
        )

    value = value.strip()

    if value == "":
        return None, (
            f"{field_name} cannot be empty."
        )

    normalized, error = normalize_date_input(
        value,
        field_name,
    )

    if error:
        return None, error

    return normalized, None


def normalize_date_input(
    value,
    field_name="Date",
):
    value = str(value).strip()

    if value == "":
        return None, None

    for date_format in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(
                value,
                date_format,
            )

        except ValueError:
            continue

        return parsed.strftime("%d/%m/%Y"), None

    return None, (
        f"{field_name} must use dd/mm/yyyy format."
    )


def date_display_to_iso(value):
    if value is None:
        return ""

    value = str(value).strip()

    if value == "":
        return ""

    normalized, error = normalize_date_input(value)

    if error or normalized is None:
        return ""

    day, month, year = normalized.split("/")

    return f"{year}-{month}-{day}"


def parse_optional_positive_float(
    value,
    field_name
):
    if value is None:
        return None, None

    value = str(value).strip()

    if value == "":
        return None, None

    return parse_positive_float(
        value,
        field_name
    )


def parse_optional_date(
    value,
    field_name
):
    if value is None:
        return None, None

    value = str(value).strip()

    if value == "":
        return None, None

    return normalize_date_input(
        value,
        field_name,
    )


def date_to_sortable(value):
    day, month, year = value.split("/")

    return f"{year}{month}{day}"


def validate_agent_form(
    name,
    agent_type
):
    errors = []

    name_error = validate_required_text(
        name,
        "Agent name"
    )

    if name_error:
        errors.append(name_error)

    type_error = validate_choice(
        agent_type,
        AGENT_TYPES,
        "agent type"
    )

    if type_error:
        errors.append(type_error)

    return errors


def validate_property_form(
    address,
    jurisdiction,
    property_type=None,
    listing_price=None,
    listing_purpose=None,
):
    errors = []

    address_error = validate_required_text(
        address,
        "Property address"
    )

    if address_error:
        errors.append(address_error)

    jurisdiction_error = validate_choice(
        jurisdiction,
        JURISDICTIONS,
        "jurisdiction"
    )

    if jurisdiction_error:
        errors.append(jurisdiction_error)

    if property_type is None or str(property_type).strip() == "":
        errors.append("err_property_type_required")
    else:
        type_error = validate_choice(
            property_type,
            PROPERTY_TYPES,
            "property type"
        )

        if type_error:
            errors.append("err_invalid_property_type")

    price_value, price_error = parse_positive_float(
        listing_price,
        "Property listing price",
    )

    if price_error:
        if (
            listing_price is None
            or str(listing_price).strip() == ""
        ):
            errors.append("err_property_listing_price_required")
        else:
            errors.append("err_invalid_property_listing_price")

    if listing_purpose is None or str(listing_purpose).strip() == "":
        errors.append("err_property_listing_purpose_required")
    elif listing_purpose not in LISTING_PURPOSES:
        errors.append("err_invalid_property_listing_purpose")

    return errors


def validate_invoice_full_commission(value):
    if value not in INVOICE_FULL_COMMISSION_VALUES:
        return "err_invalid_invoice_full_commission"

    return None
