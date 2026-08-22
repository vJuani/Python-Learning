from datetime import date

from modules.calculations import (
    calculate_abao,
    calculate_agent_payment,
    calculate_martillero,
    calculate_office_payment,
    calculate_office_total,
    calculate_total_commission
)

from modules.database import (
    add_operation,
    delete_operation,
    get_agents,
    get_properties,
    update_operation
)

from modules.menus import (
    choose_agent,
    choose_property,
    invoiced_menu
)

from modules.reports import show_result

from modules.validators import (
    get_float,
    parse_positive_float
)


def calculate_operation_details(
    agent_name,
    agent_type,
    property_address,
    jurisdiction,
    sale_price,
    commission_rate,
    was_invoiced,
    vat_amount=0,
    operation_date=None,
    operation_display_id=None,
    property_display_id=None
):
    if operation_date is None:
        operation_date = date.today().strftime(
            "%d/%m/%Y"
        )

    total_commission = calculate_total_commission(
        sale_price,
        commission_rate
    )

    abao = calculate_abao(
        was_invoiced,
        jurisdiction,
        vat_amount
    )

    commission_after_abao = (
        total_commission - abao
    )

    if agent_type in (
        "Alto",
        "Puro"
    ):
        martillero = calculate_martillero(
            commission_after_abao
        )
    else:
        martillero = 0

    agent_payment = calculate_agent_payment(
        agent_type,
        commission_after_abao,
        martillero
    )

    office_payment = calculate_office_payment(
        commission_after_abao,
        martillero,
        agent_payment
    )

    office_total = calculate_office_total(
        office_payment,
        vat_amount,
        abao
    )

    operation = {
        "date": operation_date,
        "agent": agent_name,
        "agent_type": agent_type,
        "property": property_address,
        "jurisdiction": jurisdiction,
        "was_invoiced": was_invoiced,
        "vat_amount": vat_amount,
        "sale_price": sale_price,
        "commission_rate": commission_rate,
        "total_commission": total_commission,
        "commission_after_abao": commission_after_abao,
        "abao": abao,
        "martillero": martillero,
        "agent_payment": agent_payment,
        "office_payment": office_payment,
        "office_total": office_total
    }

    if operation_display_id is not None:
        operation["id"] = operation_display_id

    if property_display_id is not None:
        operation["property_id"] = property_display_id

    return operation


def get_agent_and_property(
    agent_id,
    property_id
):
    agent = None
    property_data = None

    for item in get_agents():
        if item["id"] == agent_id:
            agent = item
            break

    for item in get_properties():
        if item["id"] == property_id:
            property_data = item
            break

    return agent, property_data


def build_operation_from_selection(
    agent_id,
    property_id,
    sale_price,
    commission_rate,
    was_invoiced,
    vat_amount=0,
    operation_date=None,
    operation_display_id=None
):
    agent, property_data = get_agent_and_property(
        agent_id,
        property_id
    )

    if agent is None:
        raise ValueError(
            "Selected agent was not found."
        )

    if property_data is None:
        raise ValueError(
            "Selected property was not found."
        )

    return calculate_operation_details(
        agent["name"],
        agent["type"],
        property_data["address"],
        property_data["jurisdiction"],
        sale_price,
        commission_rate,
        was_invoiced,
        vat_amount,
        operation_date=operation_date,
        operation_display_id=operation_display_id,
        property_display_id=(
            f"PROP-{property_id:06d}"
        )
    )


def validate_operation_inputs(
    agent_id,
    property_id,
    sale_price,
    commission_rate,
    was_invoiced,
    vat_amount,
    operation_date=None
):
    errors = []
    parsed = {}

    if not agent_id:
        errors.append(
            "You must select an agent."
        )
    else:
        try:
            parsed["agent_id"] = int(agent_id)
        except (TypeError, ValueError):
            errors.append(
                "Invalid agent selection."
            )

    if not property_id:
        errors.append(
            "You must select a property."
        )
    else:
        try:
            parsed["property_id"] = int(
                property_id
            )
        except (TypeError, ValueError):
            errors.append(
                "Invalid property selection."
            )

    sale_price_value, sale_price_error = (
        parse_positive_float(
            sale_price
        )
    )

    if sale_price_error:
        errors.append(
            f"Sale price: {sale_price_error}"
        )
    else:
        parsed["sale_price"] = sale_price_value

    commission_rate_value, commission_rate_error = (
        parse_positive_float(
            commission_rate
        )
    )

    if commission_rate_error:
        errors.append(
            "Commission rate: "
            f"{commission_rate_error}"
        )
    else:
        parsed["commission_rate"] = (
            commission_rate_value
        )

    if was_invoiced not in (
        "yes",
        "no"
    ):
        errors.append(
            "Invalid invoiced option."
        )
    else:
        parsed["was_invoiced"] = was_invoiced

    if was_invoiced == "yes":
        vat_value, vat_error = (
            parse_positive_float(
                vat_amount
            )
        )

        if vat_error:
            errors.append(
                f"VAT amount: {vat_error}"
            )
        else:
            parsed["vat_amount"] = vat_value
    else:
        parsed["vat_amount"] = 0

    if operation_date is None:
        parsed["operation_date"] = (
            date.today().strftime(
                "%d/%m/%Y"
            )
        )
    else:
        operation_date = operation_date.strip()

        if operation_date == "":
            errors.append(
                "Operation date cannot be empty."
            )
        else:
            parsed["operation_date"] = (
                operation_date
            )

    return errors, parsed


def save_calculated_operation(
    agent_id,
    property_id,
    operation
):
    operation_id = add_operation(
        operation["date"],
        agent_id,
        property_id,
        operation["was_invoiced"],
        operation["vat_amount"],
        operation["sale_price"],
        operation["commission_rate"],
        operation["total_commission"],
        operation["commission_after_abao"],
        operation["abao"],
        operation["martillero"],
        operation["agent_payment"],
        operation["office_payment"],
        operation["office_total"]
    )

    operation["id"] = (
        f"COM-{operation_id:06d}"
    )
    operation["property_id"] = (
        f"PROP-{property_id:06d}"
    )

    return operation_id, operation


def update_calculated_operation(
    operation_id,
    agent_id,
    property_id,
    operation
):
    update_operation(
        operation_id,
        operation["date"],
        agent_id,
        property_id,
        operation["was_invoiced"],
        operation["vat_amount"],
        operation["sale_price"],
        operation["commission_rate"],
        operation["total_commission"],
        operation["commission_after_abao"],
        operation["abao"],
        operation["martillero"],
        operation["agent_payment"],
        operation["office_payment"],
        operation["office_total"]
    )

    operation["id"] = (
        f"COM-{operation_id:06d}"
    )
    operation["property_id"] = (
        f"PROP-{property_id:06d}"
    )

    return operation


def remove_operation(
    operation_id
):
    delete_operation(
        operation_id
    )


def new_calculation():
    agents = get_agents()

    if len(agents) == 0:
        print(
            "No agents were found. "
            "Add an agent first."
        )
        return

    properties = get_properties()

    if len(properties) == 0:
        print(
            "No properties were found. "
            "Add a property first."
        )
        return

    operation_date = date.today().strftime(
        "%d/%m/%Y"
    )

    (
        agent_id,
        agent_name,
        agent_type
    ) = choose_agent(
        agents
    )

    (
        property_id,
        property_address,
        jurisdiction
    ) = choose_property(
        properties
    )

    sale_price = get_float(
        "Enter the sale price: "
    )

    commission_rate = get_float(
        "Enter the commission rate percentage: "
    )

    was_invoiced = invoiced_menu()

    vat_amount = 0

    if was_invoiced == "yes":
        vat_amount = get_float(
            "Enter the VAT amount: "
        )

    operation = build_operation_from_selection(
        agent_id,
        property_id,
        sale_price,
        commission_rate,
        was_invoiced,
        vat_amount,
        operation_date=operation_date
    )

    database_operation_id, operation = (
        save_calculated_operation(
            agent_id,
            property_id,
            operation
        )
    )

    show_result(
        operation
    )

    print(
        "Operation saved successfully!"
    )
