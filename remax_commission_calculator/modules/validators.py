from datetime import datetime


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

    try:
        datetime.strptime(
            value,
            "%d/%m/%Y"
        )

    except ValueError:
        return None, (
            f"{field_name} must use dd/mm/yyyy format."
        )

    return value, None


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

    return validate_date_format(
        value,
        field_name
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
    jurisdiction
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

    return errors
