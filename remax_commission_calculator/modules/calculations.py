def calculate_total_commission(
    sale_price,
    commission_rate
):
    return sale_price * (commission_rate / 100)


def calculate_abao(
    was_invoiced,
    jurisdiction,
    vat_amount
):
    if was_invoiced == "yes" and jurisdiction == "PBA":
        invoice_net_amount = vat_amount / 0.21
        return invoice_net_amount * 0.06

    return 0


def calculate_martillero(
    commission_after_abao
):
    return commission_after_abao * 0.04


def calculate_agent_payment(
    agent_type,
    commission_after_abao,
    martillero
):
    if agent_type == "Alto":
        net_commission = (
            commission_after_abao - martillero
        )
        return net_commission * 0.60

    elif agent_type == "Puro":
        net_commission = (
            commission_after_abao - martillero
        )
        return net_commission * 0.80

    elif agent_type in (
        "Junior",
        "RAPP"
    ):
        return (
            commission_after_abao * 0.45
        )

    else:
        raise ValueError(
            "Invalid agent type. "
            "Must be Alto, Puro, Junior or RAPP."
        )


def calculate_office_payment(
    commission_after_abao,
    martillero,
    agent_payment
):
    return (
        commission_after_abao
        - martillero
        - agent_payment
    )


def calculate_office_total(
    office_payment,
    vat_amount,
    abao
):
    return (
        office_payment
        + vat_amount
        + abao
    )