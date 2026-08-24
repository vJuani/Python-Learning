CURRENCIES = (
    "USD",
    "ARS"
)


def format_number(
    amount,
    language="es",
    decimals=2
):
    try:
        value = float(amount)
    except (TypeError, ValueError):
        value = 0.0

    if language == "en":
        return f"{value:,.{decimals}f}"

    formatted = f"{value:,.{decimals}f}"
    return (
        formatted
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def format_money(
    amount,
    currency="USD",
    language="es"
):
    number = format_number(
        amount,
        language=language
    )

    return f"{currency} {number}"


def convert_to_usd(
    amount,
    currency,
    exchange_rate
):
    if currency == "USD":
        return float(amount)

    rate = float(exchange_rate)

    if rate <= 0:
        raise ValueError(
            "Exchange rate must be greater than zero."
        )

    return float(amount) / rate


def convert_from_usd(
    usd_amount,
    currency,
    exchange_rate
):
    if currency == "USD":
        return float(usd_amount)

    return float(usd_amount) * float(exchange_rate)
