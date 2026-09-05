from modules.filter_helpers import (
    parse_filter_agent_id,
    resolve_scoped_agent_id,
)
from modules.cli_tenant import get_cli_organization_id

from modules.database import (
    get_properties,
    add_property as db_add_property,
    update_property as db_update_property,
    delete_property as db_delete_property,
    filter_properties as db_filter_properties,
    get_agents
)

from modules.listings_normalize import normalize_neighborhood
from modules.menus import choose_agent
from modules.property_types import (
    COMMERCIAL_STATUSES,
    LISTING_CURRENCIES,
    LISTING_PURPOSES,
    normalize_listing_purpose,
)

from modules.validators import (
    JURISDICTIONS,
    parse_positive_float
)


def empty_property_filters():
    return {
        "property_id": "",
        "address": "",
        "jurisdiction": "",
        "agent_id": "",
        "min_price": "",
        "max_price": "",
        "neighborhood": "",
        "listing_purpose": "",
        "commercial_status": "",
        "listing_currency": "",
    }


def parse_optional_positive_float(
    value,
    field_name
):
    if value is None:
        return None, None

    value = str(value).strip()

    if value == "":
        return None, None

    number, error = parse_positive_float(
        value,
        field_name
    )

    return number, error


def validate_property_filters(
    raw_filters,
    *,
    organization_id=None,
):
    errors = []
    parsed = {
        "property_id": None,
        "address": None,
        "jurisdiction": None,
        "filter_agent_id": None,
        "min_price": None,
        "max_price": None,
        "neighborhood": None,
        "listing_purpose": None,
        "commercial_status": None,
        "listing_currency": None,
    }

    property_id = raw_filters.get(
        "property_id",
        ""
    ).strip()

    if property_id != "":
        try:
            parsed["property_id"] = int(
                property_id
            )
        except ValueError:
            errors.append(
                "Property ID must be a valid number."
            )

    address = raw_filters.get(
        "address",
        ""
    ).strip()

    if address != "":
        parsed["address"] = address

    jurisdiction = raw_filters.get(
        "jurisdiction",
        ""
    ).strip()

    if jurisdiction != "":
        if jurisdiction not in JURISDICTIONS:
            errors.append(
                "You must select a valid jurisdiction."
            )
        else:
            parsed["jurisdiction"] = jurisdiction

    if organization_id is not None:
        parsed["filter_agent_id"] = parse_filter_agent_id(
            raw_filters.get("agent_id"),
            organization_id,
        )

    min_price, min_price_error = (
        parse_optional_positive_float(
            raw_filters.get("min_price"),
            "Minimum price"
        )
    )

    if min_price_error:
        errors.append(min_price_error)
    else:
        parsed["min_price"] = min_price

    max_price, max_price_error = (
        parse_optional_positive_float(
            raw_filters.get("max_price"),
            "Maximum price"
        )
    )

    if max_price_error:
        errors.append(max_price_error)
    else:
        parsed["max_price"] = max_price

    if (
        parsed["min_price"] is not None
        and parsed["max_price"] is not None
        and parsed["min_price"]
        > parsed["max_price"]
    ):
        errors.append(
            "Minimum price cannot be greater "
            "than maximum price."
        )

    neighborhood = normalize_neighborhood(
        raw_filters.get("neighborhood", "")
    )
    if neighborhood:
        parsed["neighborhood"] = neighborhood

    listing_purpose = normalize_listing_purpose(
        raw_filters.get("listing_purpose")
    )
    raw_purpose = str(
        raw_filters.get("listing_purpose") or ""
    ).strip()
    if raw_purpose:
        if listing_purpose not in LISTING_PURPOSES:
            errors.append("err_invalid_property_listing_purpose")
        else:
            parsed["listing_purpose"] = listing_purpose

    commercial_status = str(
        raw_filters.get("commercial_status") or ""
    ).strip()
    if commercial_status:
        if commercial_status not in COMMERCIAL_STATUSES:
            errors.append("err_invalid_commercial_status")
        else:
            parsed["commercial_status"] = commercial_status

    listing_currency = str(
        raw_filters.get("listing_currency") or ""
    ).strip().upper()
    if listing_currency:
        if listing_currency not in LISTING_CURRENCIES:
            errors.append("err_invalid_listing_currency")
        else:
            parsed["listing_currency"] = listing_currency

    return errors, parsed


def has_active_property_filters(parsed):
    return any(
        value is not None
        for value in parsed.values()
    )


def get_filtered_properties(
    raw_filters,
    organization_id,
    agent_id=None,
    include_all_statuses=False
):
    errors, parsed = validate_property_filters(
        raw_filters,
        organization_id=organization_id,
    )

    if len(errors) > 0:
        return errors, []

    effective_agent_id = resolve_scoped_agent_id(
        agent_id,
        parsed["filter_agent_id"],
    )

    if not has_active_property_filters(parsed):
        return [], get_properties(
            organization_id,
            agent_id=effective_agent_id,
            include_all_statuses=include_all_statuses
        )

    properties = db_filter_properties(
        organization_id,
        property_id=parsed["property_id"],
        address=parsed["address"],
        jurisdiction=parsed["jurisdiction"],
        min_listing_price=parsed["min_price"],
        max_listing_price=parsed["max_price"],
        neighborhood=parsed["neighborhood"],
        listing_purpose=parsed["listing_purpose"],
        commercial_status=parsed["commercial_status"],
        listing_currency=parsed["listing_currency"],
        agent_id=effective_agent_id,
        include_all_statuses=include_all_statuses,
    )

    return [], properties


def list_properties():
    properties = get_properties(
        get_cli_organization_id()
    )

    if len(properties) == 0:
        print("No properties found.")
        return

    print("\n======== PROPERTIES ========")

    for index, property_data in enumerate(
        properties,
        start=1
    ):
        print(
            f"{index} - "
            f"{property_data['address']} "
            f"({property_data['jurisdiction']})"
        )


def choose_jurisdiction(
    allow_keep=False
):
    while True:
        print("\n1 - CABA")
        print("2 - PBA")

        if allow_keep:
            print("3 - Keep current")

        try:
            option = int(
                input("Choose jurisdiction: ")
            )

            if option == 1:
                return "CABA"

            elif option == 2:
                return "PBA"

            elif option == 3 and allow_keep:
                return None

            if allow_keep:
                print(
                    "Invalid option. "
                    "Choose from 1 to 3."
                )
            else:
                print(
                    "Invalid option. "
                    "Choose 1 or 2."
                )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )


def add_property():
    print("\n======== ADD PROPERTY ========")

    organization_id = get_cli_organization_id()
    agents = get_agents(organization_id)

    if len(agents) == 0:
        print(
            "No agents found. "
            "Create an agent first."
        )
        return

    while True:
        address = input(
            "Property address: "
        ).strip()

        if address != "":
            break

        print(
            "Property address cannot be empty."
        )

    jurisdiction = choose_jurisdiction()
    agent_id, _name, _type = choose_agent(agents)

    db_add_property(
        address,
        jurisdiction,
        organization_id,
        agent_id=agent_id
    )

    print(
        "\nProperty added successfully!"
    )


def edit_property():
    organization_id = get_cli_organization_id()

    properties = get_properties(organization_id)

    if len(properties) == 0:
        print("No properties found.")
        return

    list_properties()

    while True:
        try:
            option = int(
                input(
                    "\nChoose the property to edit: "
                )
            )

            if 1 <= option <= len(properties):
                break

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(properties)}."
            )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )

    property_data = properties[
        option - 1
    ]

    print(
        f"\nCurrent address: "
        f"{property_data['address']}"
    )

    new_address = input(
        "New address "
        "(Enter to keep current): "
    ).strip()

    if new_address != "":
        property_data[
            "address"
        ] = new_address

    print(
        f"\nCurrent jurisdiction: "
        f"{property_data['jurisdiction']}"
    )

    new_jurisdiction = choose_jurisdiction(
        allow_keep=True
    )

    if new_jurisdiction is not None:
        property_data[
            "jurisdiction"
        ] = new_jurisdiction

    db_update_property(
        property_data["id"],
        property_data["address"],
        property_data["jurisdiction"],
        organization_id,
        agent_id=property_data.get("agent_id")
    )

    print(
        "\nProperty updated successfully!"
    )


def delete_property():
    organization_id = get_cli_organization_id()

    properties = get_properties(organization_id)

    if len(properties) == 0:
        print("No properties found.")
        return

    list_properties()

    while True:
        try:
            option = int(
                input(
                    "\nChoose the property to delete: "
                )
            )

            if 1 <= option <= len(properties):
                break

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(properties)}."
            )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )

    property_data = properties[
        option - 1
    ]

    print(
        f"\nYou selected: "
        f"{property_data['address']} "
        f"({property_data['jurisdiction']})"
    )

    while True:
        confirmation = input(
            "Are you sure you want to delete "
            "this property? (y/n): "
        ).strip().lower()

        if confirmation == "y":
            db_delete_property(
                property_data["id"],
                organization_id
            )

            print(
                "Property deleted successfully!"
            )
            break

        elif confirmation == "n":
            print(
                "Deletion cancelled."
            )
            break

        else:
            print(
                "Invalid option. Enter y or n."
            )