from datetime import date

from modules.calculations import (
    calculate_abao,
    calculate_agent_payment,
    calculate_martillero,
    calculate_office_payment,
    calculate_office_total,
    calculate_total_commission
)

from modules.cli_tenant import get_cli_organization_id

from modules.database import (
    add_operation,
    create_parties_for_new_operation,
    delete_operation,
    filter_operations as db_filter_operations,
    get_agent_record,
    get_agents,
    get_operations,
    get_organization_settings,
    get_properties,
    get_property_record,
    is_property_available_for_operation,
    update_operation,
    update_operation_status,
)

from modules.menus import (
    choose_agent,
    choose_property,
    invoiced_menu
)

from modules.reports import show_result

from modules.formatting import (
    CURRENCIES,
    convert_to_usd
)

from modules.validators import (
    JURISDICTIONS,
    date_to_sortable,
    get_float,
    parse_optional_date,
    parse_optional_positive_float,
    parse_positive_float,
    validate_date_format,
    validate_invoice_full_commission,
)

from modules.vat_billing_calculator import minimum_vat

VALID_REFERRED_SIDES = frozenset({
    "seller",
    "buyer",
    "both",
})


def _checkbox_enabled(raw_value):
    return str(raw_value or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _parse_commission_rate(raw_value, label):
    if raw_value is None or str(raw_value).strip() == "":
        return None, None
    return parse_positive_float(raw_value, label)


def _suggest_vat_for_commission(commission_amount):
    if commission_amount is None or commission_amount <= 0:
        return 0.0

    try:
        result = minimum_vat(commission_amount)
        suggested = result.get("iva_suggested")
        return float(suggested) if suggested is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_new_operation_form_defaults(organization_id):
    settings = get_organization_settings(
        organization_id
    )
    return {
        "buyer_commission_rate": str(
            settings.get(
                "default_buyer_commission_percent",
                3.0,
            )
        ),
        "seller_commission_rate": str(
            settings.get(
                "default_seller_commission_percent",
                3.0,
            )
        ),
        "currency": settings.get(
            "default_currency",
            "USD",
        ),
    }


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
    property_display_id=None,
    currency="USD",
    original_amount=None,
    exchange_rate=1
):
    if operation_date is None:
        operation_date = date.today().strftime(
            "%d/%m/%Y"
        )

    if original_amount is None:
        original_amount = sale_price

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
        "office_total": office_total,
        "currency": currency,
        "original_amount": original_amount,
        "exchange_rate": exchange_rate
    }

    if operation_display_id is not None:
        operation["id"] = operation_display_id

    if property_display_id is not None:
        operation["property_id"] = property_display_id

    return operation


def get_agent_and_property(
    agent_id,
    property_id,
    organization_id
):
    agent = get_agent_record(
        agent_id,
        organization_id
    )

    property_data = get_property_record(
        property_id,
        organization_id
    )

    return agent, property_data


def build_operation_from_selection(
    agent_id,
    property_id,
    organization_id,
    sale_price,
    commission_rate,
    was_invoiced,
    vat_amount=0,
    operation_date=None,
    operation_display_id=None,
    currency="USD",
    original_amount=None,
    exchange_rate=1
):
    agent, property_data = get_agent_and_property(
        agent_id,
        property_id,
        organization_id
    )

    if agent is None:
        raise ValueError(
            "Selected agent was not found."
        )

    if property_data is None:
        raise ValueError(
            "Selected property was not found."
        )

    if original_amount is None:
        original_amount = sale_price

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
        ),
        currency=currency,
        original_amount=original_amount,
        exchange_rate=exchange_rate
    )


def validate_operation_inputs(
    agent_id,
    property_id,
    organization_id,
    original_amount,
    commission_rate,
    was_invoiced,
    vat_amount,
    operation_date=None,
    currency="USD",
    exchange_rate="",
    invoice_full_commission="no",
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

    if currency not in CURRENCIES:
        errors.append(
            "Invalid currency. Use USD or ARS."
        )
    else:
        parsed["currency"] = currency

    original_value, original_error = (
        parse_positive_float(
            original_amount,
            "Original amount"
        )
    )

    if original_error:
        errors.append(original_error)
    else:
        parsed["original_amount"] = (
            original_value
        )

    if currency == "USD":
        parsed["exchange_rate"] = 1.0
    elif currency == "ARS":
        rate_value, rate_error = (
            parse_positive_float(
                exchange_rate,
                "Exchange rate"
            )
        )

        if rate_error:
            errors.append(rate_error)
        else:
            parsed["exchange_rate"] = (
                rate_value
            )

    commission_rate_value, commission_rate_error = (
        parse_positive_float(
            commission_rate,
            "Commission rate"
        )
    )

    if commission_rate_error:
        errors.append(commission_rate_error)
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

    invoice_error = validate_invoice_full_commission(
        invoice_full_commission
    )

    if invoice_error:
        errors.append(invoice_error)
    else:
        parsed["invoice_full_commission"] = (
            invoice_full_commission
        )

    if was_invoiced == "yes":
        vat_value, vat_error = (
            parse_positive_float(
                vat_amount,
                "VAT amount"
            )
        )

        if vat_error:
            errors.append(vat_error)
        else:
            parsed["vat_amount_original"] = (
                vat_value
            )
    else:
        parsed["vat_amount_original"] = 0

    if operation_date is None:
        parsed["operation_date"] = (
            date.today().strftime(
                "%d/%m/%Y"
            )
        )
    else:
        validated_date, date_error = (
            validate_date_format(
                operation_date,
                "Operation date"
            )
        )

        if date_error:
            errors.append(date_error)
        else:
            parsed["operation_date"] = (
                validated_date
            )

    if (
        "original_amount" in parsed
        and "currency" in parsed
        and "exchange_rate" in parsed
        and "vat_amount_original" in parsed
    ):
        try:
            parsed["sale_price"] = convert_to_usd(
                parsed["original_amount"],
                parsed["currency"],
                parsed["exchange_rate"]
            )
            parsed["vat_amount"] = convert_to_usd(
                parsed["vat_amount_original"],
                parsed["currency"],
                parsed["exchange_rate"]
            )
        except ValueError as error:
            errors.append(str(error))

    if (
        "agent_id" in parsed
        and "property_id" in parsed
        and len(errors) == 0
    ):
        agent, property_data = get_agent_and_property(
            parsed["agent_id"],
            parsed["property_id"],
            organization_id
        )

        if agent is None:
            errors.append(
                "Selected agent was not found."
            )

        if property_data is None:
            errors.append(
                "Selected property was not found."
            )
        elif (
            agent is not None
            and property_data.get("agent_id")
            != parsed["agent_id"]
        ):
            errors.append(
                "Property does not belong to the selected agent."
            )

    return errors, parsed


def prepare_operation_from_form(
    form_values,
    organization_id,
    operation_display_id=None
):
    errors, parsed = validate_operation_inputs(
        form_values.get("agent_id", ""),
        form_values.get("property_id", ""),
        organization_id,
        form_values.get(
            "original_amount",
            form_values.get("sale_price", "")
        ),
        form_values.get("commission_rate", ""),
        form_values.get("was_invoiced", "no"),
        form_values.get("vat_amount", "0"),
        form_values.get("operation_date", ""),
        currency=form_values.get(
            "currency",
            "USD"
        ),
        exchange_rate=form_values.get(
            "exchange_rate",
            ""
        ),
        invoice_full_commission=form_values.get(
            "invoice_full_commission",
            "no",
        ),
    )

    if len(errors) > 0:
        return errors, None, parsed

    try:
        operation = build_operation_from_selection(
            parsed["agent_id"],
            parsed["property_id"],
            organization_id,
            parsed["sale_price"],
            parsed["commission_rate"],
            parsed["was_invoiced"],
            parsed["vat_amount"],
            operation_date=parsed["operation_date"],
            operation_display_id=operation_display_id,
            currency=parsed["currency"],
            original_amount=parsed[
                "original_amount"
            ],
            exchange_rate=parsed[
                "exchange_rate"
            ]
        )

    except ValueError as error:
        errors.append(str(error))
        return errors, None, parsed

    operation["invoice_full_commission"] = parsed[
        "invoice_full_commission"
    ]

    return errors, operation, parsed


def validate_new_operation_inputs(
    form_values,
    organization_id,
):
    errors = []
    parsed = {}

    agent_id = form_values.get("agent_id", "")
    property_id = form_values.get("property_id", "")

    if not agent_id:
        errors.append("You must select an agent.")
    else:
        try:
            parsed["agent_id"] = int(agent_id)
        except (TypeError, ValueError):
            errors.append("Invalid agent selection.")

    if not property_id:
        errors.append("You must select a property.")
    else:
        try:
            parsed["property_id"] = int(property_id)
        except (TypeError, ValueError):
            errors.append("Invalid property selection.")

    currency = form_values.get("currency", "USD")
    if currency not in CURRENCIES:
        errors.append("Invalid currency. Use USD or ARS.")
    else:
        parsed["currency"] = currency

    original_value, original_error = parse_positive_float(
        form_values.get("original_amount", ""),
        "Operation value",
    )
    if original_error:
        errors.append(original_error)
    else:
        parsed["original_amount"] = original_value

    if currency == "USD":
        parsed["exchange_rate"] = 1.0
    else:
        rate_value, rate_error = parse_positive_float(
            form_values.get("exchange_rate", ""),
            "Exchange rate",
        )
        if rate_error:
            errors.append(rate_error)
        else:
            parsed["exchange_rate"] = rate_value

    parsed["seller_side_active"] = _checkbox_enabled(
        form_values.get("seller_side_active")
    )
    parsed["buyer_side_active"] = _checkbox_enabled(
        form_values.get("buyer_side_active")
    )

    if not parsed["seller_side_active"] and not parsed[
        "buyer_side_active"
    ]:
        errors.append("operation_side_required")

    parsed["is_referred"] = _checkbox_enabled(
        form_values.get("is_referred")
    )
    referred_side = str(
        form_values.get("referred_side", "")
    ).strip().lower()
    if parsed["is_referred"]:
        if referred_side not in VALID_REFERRED_SIDES:
            errors.append("operation_referred_side_required")
        else:
            parsed["referred_side"] = referred_side
    else:
        parsed["referred_side"] = None

    seller_rate = 0.0
    buyer_rate = 0.0

    if parsed["seller_side_active"]:
        seller_rate_value, seller_rate_error = (
            _parse_commission_rate(
                form_values.get("seller_commission_rate"),
                "Seller commission rate",
            )
        )
        if seller_rate_error:
            errors.append(seller_rate_error)
        else:
            seller_rate = seller_rate_value or 0.0
            parsed["seller_commission_rate"] = seller_rate
    else:
        parsed["seller_commission_rate"] = 0.0

    if parsed["buyer_side_active"]:
        buyer_rate_value, buyer_rate_error = (
            _parse_commission_rate(
                form_values.get("buyer_commission_rate"),
                "Buyer commission rate",
            )
        )
        if buyer_rate_error:
            errors.append(buyer_rate_error)
        else:
            buyer_rate = buyer_rate_value or 0.0
            parsed["buyer_commission_rate"] = buyer_rate
    else:
        parsed["buyer_commission_rate"] = 0.0

    seller_vat = 0.0
    buyer_vat = 0.0

    if parsed["seller_side_active"]:
        seller_vat_value, seller_vat_error = (
            parse_optional_positive_float(
                form_values.get("seller_vat_amount", "0"),
                "Seller VAT amount",
            )
        )
        if seller_vat_error:
            errors.append(seller_vat_error)
        else:
            seller_vat = seller_vat_value or 0.0
            parsed["seller_vat_original"] = seller_vat
    else:
        parsed["seller_vat_original"] = 0.0

    if parsed["buyer_side_active"]:
        buyer_vat_value, buyer_vat_error = (
            parse_optional_positive_float(
                form_values.get("buyer_vat_amount", "0"),
                "Buyer VAT amount",
            )
        )
        if buyer_vat_error:
            errors.append(buyer_vat_error)
        else:
            buyer_vat = buyer_vat_value or 0.0
            parsed["buyer_vat_original"] = buyer_vat
    else:
        parsed["buyer_vat_original"] = 0.0

    operation_date = form_values.get(
        "operation_date",
        "",
    )
    if not operation_date:
        parsed["operation_date"] = date.today().strftime(
            "%d/%m/%Y"
        )
    else:
        validated_date, date_error = validate_date_format(
            operation_date,
            "Operation date",
        )
        if date_error:
            errors.append(date_error)
        else:
            parsed["operation_date"] = validated_date

    if (
        "original_amount" in parsed
        and "currency" in parsed
        and "exchange_rate" in parsed
    ):
        try:
            parsed["sale_price"] = convert_to_usd(
                parsed["original_amount"],
                parsed["currency"],
                parsed["exchange_rate"],
            )
            parsed["vat_amount"] = convert_to_usd(
                seller_vat + buyer_vat,
                parsed["currency"],
                parsed["exchange_rate"],
            )
        except ValueError as error:
            errors.append(str(error))

    seller_commission = 0.0
    buyer_commission = 0.0

    if (
        "original_amount" in parsed
        and parsed["seller_side_active"]
    ):
        seller_commission = (
            parsed["original_amount"]
            * parsed["seller_commission_rate"]
            / 100.0
        )
    if (
        "original_amount" in parsed
        and parsed["buyer_side_active"]
    ):
        buyer_commission = (
            parsed["original_amount"]
            * parsed["buyer_commission_rate"]
            / 100.0
        )

    parsed["seller_commission_amount"] = seller_commission
    parsed["buyer_commission_amount"] = buyer_commission
    parsed["total_commission_original"] = (
        seller_commission + buyer_commission
    )

    if parsed.get("sale_price"):
        if parsed["sale_price"] > 0:
            parsed["commission_rate"] = (
                parsed["total_commission_original"]
                / parsed["original_amount"]
                * 100.0
            )
        else:
            parsed["commission_rate"] = 0.0

    parsed["was_invoiced"] = "no"
    parsed["invoice_full_commission"] = "no"

    if (
        "agent_id" in parsed
        and "property_id" in parsed
        and len(errors) == 0
    ):
        agent, property_data = get_agent_and_property(
            parsed["agent_id"],
            parsed["property_id"],
            organization_id,
        )

        if agent is None:
            errors.append("Selected agent was not found.")

        if property_data is None:
            errors.append("Selected property was not found.")
        else:
            if (
                agent is not None
                and property_data.get("agent_id")
                != parsed["agent_id"]
            ):
                errors.append(
                    "Property does not belong to the selected agent."
                )

            if not is_property_available_for_operation(
                parsed["property_id"],
                organization_id,
            ):
                errors.append(
                    "property_already_used_in_operation"
                )

    return errors, parsed


def prepare_new_operation_from_form(
    form_values,
    organization_id,
    operation_display_id=None,
):
    errors, parsed = validate_new_operation_inputs(
        form_values,
        organization_id,
    )

    if len(errors) > 0:
        return errors, None, parsed

    try:
        operation = build_operation_from_selection(
            parsed["agent_id"],
            parsed["property_id"],
            organization_id,
            parsed["sale_price"],
            parsed["commission_rate"],
            parsed["was_invoiced"],
            parsed["vat_amount"],
            operation_date=parsed["operation_date"],
            operation_display_id=operation_display_id,
            currency=parsed["currency"],
            original_amount=parsed["original_amount"],
            exchange_rate=parsed["exchange_rate"],
        )
    except ValueError as error:
        errors.append(str(error))
        return errors, None, parsed

    operation["invoice_full_commission"] = "no"
    operation["is_referred"] = parsed["is_referred"]
    operation["referred_side"] = parsed["referred_side"]
    operation["seller_vat_original"] = parsed[
        "seller_vat_original"
    ]
    operation["buyer_vat_original"] = parsed[
        "buyer_vat_original"
    ]
    operation["seller_side_active"] = parsed[
        "seller_side_active"
    ]
    operation["buyer_side_active"] = parsed[
        "buyer_side_active"
    ]
    operation["seller_commission_rate"] = parsed[
        "seller_commission_rate"
    ]
    operation["buyer_commission_rate"] = parsed[
        "buyer_commission_rate"
    ]
    operation["seller_commission_amount"] = parsed[
        "seller_commission_amount"
    ]
    operation["buyer_commission_amount"] = parsed[
        "buyer_commission_amount"
    ]
    operation["side_data"] = {
        "seller_active": parsed["seller_side_active"],
        "buyer_active": parsed["buyer_side_active"],
        "seller_commission_percent": parsed[
            "seller_commission_rate"
        ],
        "buyer_commission_percent": parsed[
            "buyer_commission_rate"
        ],
        "seller_commission_amount": parsed[
            "seller_commission_amount"
        ],
        "buyer_commission_amount": parsed[
            "buyer_commission_amount"
        ],
    }

    return errors, operation, parsed


def save_calculated_operation(
    agent_id,
    property_id,
    organization_id,
    operation,
    status="approved",
    created_by_user_id=None,
    require_property_owner=False
):
    if operation.get("side_data") and not is_property_available_for_operation(
        property_id,
        organization_id,
    ):
        raise ValueError("property_already_used_in_operation")

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
        operation["office_total"],
        organization_id,
        currency=operation.get(
            "currency",
            "USD"
        ),
        original_amount=operation.get(
            "original_amount",
            operation["sale_price"]
        ),
        exchange_rate=operation.get(
            "exchange_rate",
            1
        ),
        status=status,
        created_by_user_id=created_by_user_id,
        require_property_owner=require_property_owner,
        invoice_full_commission=operation.get(
            "invoice_full_commission",
            "no",
        ),
        is_referred=operation.get("is_referred", False),
        referred_side=operation.get("referred_side"),
        seller_vat_original=operation.get(
            "seller_vat_original",
            0,
        ),
        buyer_vat_original=operation.get(
            "buyer_vat_original",
            0,
        ),
    )

    side_data = operation.get("side_data")
    if side_data:
        create_parties_for_new_operation(
            organization_id,
            operation_id,
            seller_active=side_data["seller_active"],
            buyer_active=side_data["buyer_active"],
            seller_commission_percent=side_data[
                "seller_commission_percent"
            ],
            buyer_commission_percent=side_data[
                "buyer_commission_percent"
            ],
            seller_commission_amount=side_data[
                "seller_commission_amount"
            ],
            buyer_commission_amount=side_data[
                "buyer_commission_amount"
            ],
        )

    operation["id"] = (
        f"COM-{operation_id:06d}"
    )
    operation["property_id"] = (
        f"PROP-{property_id:06d}"
    )
    operation["status"] = status

    if status == "approved":
        from modules.agent_wallet import (
            post_wallet_for_approved_operation,
        )

        post_wallet_for_approved_operation(
            organization_id,
            operation_id,
        )

    return operation_id, operation


def update_calculated_operation(
    operation_id,
    agent_id,
    property_id,
    organization_id,
    operation,
    status=None,
    rejection_reason=None,
    require_property_owner=False
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
        operation["office_total"],
        organization_id,
        currency=operation.get(
            "currency",
            "USD"
        ),
        original_amount=operation.get(
            "original_amount",
            operation["sale_price"]
        ),
        exchange_rate=operation.get(
            "exchange_rate",
            1
        ),
        status=status,
        rejection_reason=rejection_reason,
        require_property_owner=require_property_owner,
        invoice_full_commission=operation.get(
            "invoice_full_commission",
            "no",
        ),
    )

    operation["id"] = (
        f"COM-{operation_id:06d}"
    )
    operation["property_id"] = (
        f"PROP-{property_id:06d}"
    )

    if status is not None:
        operation["status"] = status

        from modules.agent_wallet import (
            sync_wallet_for_operation_status,
        )

        sync_wallet_for_operation_status(
            organization_id,
            operation_id,
            status,
        )

    return operation


def remove_operation(
    operation_id,
    organization_id
):
    from modules.agent_wallet import (
        reverse_wallet_for_operation,
    )
    from modules.database.operations_repository import (
        get_operation_record,
    )

    existing = get_operation_record(
        operation_id,
        organization_id,
    )

    if (
        existing is not None
        and (existing.get("status") or "") == "approved"
    ):
        reverse_wallet_for_operation(
            organization_id,
            operation_id,
        )

    delete_operation(
        operation_id,
        organization_id
    )


def change_operation_status(
    operation_id,
    organization_id,
    status,
    reviewed_by_user_id=None,
    reviewed_at=None,
    rejection_reason=None,
):
    from modules.agent_wallet import (
        sync_wallet_for_operation_status,
    )

    update_operation_status(
        operation_id,
        organization_id,
        status,
        reviewed_by_user_id=reviewed_by_user_id,
        reviewed_at=reviewed_at,
        rejection_reason=rejection_reason,
    )

    return sync_wallet_for_operation_status(
        organization_id,
        operation_id,
        status,
    )


def parse_operation_id_filter(value):
    value = value.strip()

    if value == "":
        return None, None

    normalized = value.upper().replace(
        "COM-",
        ""
    )

    try:
        return int(normalized), None

    except ValueError:
        return None, (
            "Operation ID must be a number "
            "or COM-000001 format."
        )


def validate_operation_filters(raw_filters):
    errors = []
    parsed = {
        "operation_id": None,
        "agent_name": None,
        "property_address": None,
        "min_amount": None,
        "max_amount": None,
        "date_from": None,
        "date_to": None,
        "was_invoiced": None,
        "jurisdiction": None,
        "status": None
    }

    operation_id, operation_id_error = (
        parse_operation_id_filter(
            raw_filters.get(
                "operation_id",
                ""
            )
        )
    )

    if operation_id_error:
        errors.append(operation_id_error)
    else:
        parsed["operation_id"] = operation_id

    agent_name = raw_filters.get(
        "agent",
        ""
    ).strip()

    if agent_name != "":
        parsed["agent_name"] = agent_name

    property_address = raw_filters.get(
        "property",
        ""
    ).strip()

    if property_address != "":
        parsed["property_address"] = (
            property_address
        )

    min_amount, min_amount_error = (
        parse_optional_positive_float(
            raw_filters.get("min_amount"),
            "Minimum amount"
        )
    )

    if min_amount_error:
        errors.append(min_amount_error)
    else:
        parsed["min_amount"] = min_amount

    max_amount, max_amount_error = (
        parse_optional_positive_float(
            raw_filters.get("max_amount"),
            "Maximum amount"
        )
    )

    if max_amount_error:
        errors.append(max_amount_error)
    else:
        parsed["max_amount"] = max_amount

    if (
        parsed["min_amount"] is not None
        and parsed["max_amount"] is not None
        and parsed["min_amount"]
        > parsed["max_amount"]
    ):
        errors.append(
            "Minimum amount cannot be greater "
            "than maximum amount."
        )

    date_from, date_from_error = (
        parse_optional_date(
            raw_filters.get("date_from"),
            "Start date"
        )
    )

    if date_from_error:
        errors.append(date_from_error)
    else:
        parsed["date_from"] = date_from

    date_to, date_to_error = (
        parse_optional_date(
            raw_filters.get("date_to"),
            "End date"
        )
    )

    if date_to_error:
        errors.append(date_to_error)
    else:
        parsed["date_to"] = date_to

    if (
        parsed["date_from"] is not None
        and parsed["date_to"] is not None
        and date_to_sortable(
            parsed["date_from"]
        )
        > date_to_sortable(
            parsed["date_to"]
        )
    ):
        errors.append(
            "Start date cannot be after end date."
        )

    was_invoiced = raw_filters.get(
        "was_invoiced",
        ""
    ).strip()

    if was_invoiced != "":
        if was_invoiced not in (
            "yes",
            "no"
        ):
            errors.append(
                "Invalid invoiced option."
            )
        else:
            parsed["was_invoiced"] = (
                was_invoiced
            )

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

    status = raw_filters.get(
        "status",
        ""
    ).strip()

    if status != "":
        from modules.workflow import (
            is_valid_status
        )

        if not is_valid_status(status):
            errors.append(
                "Invalid operation status."
            )
        else:
            parsed["status"] = status

    return errors, parsed


def has_active_operation_filters(parsed):
    return any(
        value is not None
        for value in parsed.values()
    )


def get_filtered_operations(
    raw_filters,
    organization_id,
    agent_id=None
):
    errors, parsed = validate_operation_filters(
        raw_filters
    )

    if len(errors) > 0:
        return errors, []

    if not has_active_operation_filters(parsed):
        return [], db_filter_operations(
            organization_id,
            agent_id=agent_id
        )

    date_from = None
    date_to = None

    if parsed["date_from"] is not None:
        date_from = date_to_sortable(
            parsed["date_from"]
        )

    if parsed["date_to"] is not None:
        date_to = date_to_sortable(
            parsed["date_to"]
        )

    operations = db_filter_operations(
        organization_id,
        operation_id=parsed["operation_id"],
        agent_name=parsed["agent_name"],
        property_address=parsed[
            "property_address"
        ],
        min_amount=parsed["min_amount"],
        max_amount=parsed["max_amount"],
        date_from=date_from,
        date_to=date_to,
        was_invoiced=parsed["was_invoiced"],
        jurisdiction=parsed["jurisdiction"],
        agent_id=agent_id,
        status=parsed["status"]
    )

    return [], operations


def new_calculation():
    organization_id = get_cli_organization_id()

    agents = get_agents(organization_id)

    if len(agents) == 0:
        print(
            "No agents were found. "
            "Add an agent first."
        )
        return

    properties = get_properties(organization_id)

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
        organization_id,
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
            organization_id,
            operation
        )
    )

    show_result(
        operation
    )

    print(
        "Operation saved successfully!"
    )
