CURRENCIES = (
    "USD",
    "ARS"
)

_MONTHS_ES = (
    "",
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)

_MONTHS_EN = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
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


def format_short_date(value, language="es"):
    """Format DD/MM/YYYY or ISO date for UI (language-aware)."""
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    day = month = year = None

    if "/" in text:
        parts = text.split("/")
        if len(parts) == 3:
            day, month, year = parts
    elif "-" in text:
        parts = text.split("-")
        if len(parts) >= 3:
            year, month, day = parts[0], parts[1], parts[2][:2]

    try:
        day_i = int(day)
        month_i = int(month)
        year_i = int(year)
    except (TypeError, ValueError):
        return text

    if not (1 <= month_i <= 12):
        return text

    if language == "en":
        return f"{_MONTHS_EN[month_i]} {day_i}, {year_i}"

    return f"{day_i} {_MONTHS_ES[month_i]} {year_i}"


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
