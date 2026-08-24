from modules.data_manager import load_history


def show_result(operation):
    print("\n======== COMMISSION RESULT ========")

    print(f"Operation ID: {operation['id']}")
    print(f"Date: {operation['date']}")
    print(f"Agent: {operation['agent']}")
    print(f"Agent type: {operation['agent_type']}")
    print(f"Property ID: {operation['property_id']}")
    print(f"Property: {operation['property']}")
    print(f"Jurisdiction: {operation['jurisdiction']}")
    print(f"Invoiced: {operation['was_invoiced']}")

    print("-----------------------------------")

    print(
        f"Currency: "
        f"{operation.get('currency', 'USD')}"
    )
    print(
        f"Original amount: "
        f"{operation.get('currency', 'USD')} "
        f"{operation.get('original_amount', operation['sale_price']):.2f}"
    )

    if operation.get("currency") == "ARS":
        print(
            "Exchange rate (ARS/USD): "
            f"{operation.get('exchange_rate', 1):.4f}"
        )

    print(
        f"USD equivalent: "
        f"USD {operation['sale_price']:.2f}"
    )

    print(
        f"Commission rate: "
        f"{operation['commission_rate']:.2f}%"
    )

    print(
        f"Total commission: "
        f"USD {operation['total_commission']:.2f}"
    )

    print(
        f"ABAO: "
        f"USD {operation['abao']:.2f}"
    )

    print(
        f"Commission after ABAO: "
        f"USD {operation['commission_after_abao']:.2f}"
    )

    print(
        f"Martillero: "
        f"USD {operation['martillero']:.2f}"
    )

    print("-----------------------------------")

    print(
        f"Agent payment: "
        f"USD {operation['agent_payment']:.2f}"
    )

    print(
        f"Office net payment: "
        f"USD {operation['office_payment']:.2f}"
    )

    print(
        f"VAT: "
        f"USD {operation['vat_amount']:.2f}"
    )

    print(
        f"Office total managed: "
        f"USD {operation['office_total']:.2f}"
    )

    print("===================================")


def show_history():
    history = load_history()

    if len(history) == 0:
        print("No operations saved yet.")
        return

    for operation in history:
        show_result(operation)