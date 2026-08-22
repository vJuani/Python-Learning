from datetime import datetime

from modules.database import (
    get_operations,
    search_operations_by_agent
)

from modules.reports import show_result


def search_by_agent():
    agent_name = input(
        "\nEnter the agent's name to search: "
    ).strip()

    if agent_name == "":
        print("Search cannot be empty.")
        return

    operations = search_operations_by_agent(
        agent_name
    )

    if len(operations) == 0:
        print(
            f"No operations found for: "
            f"{agent_name}"
        )
        return

    print(
        f"\nFound {len(operations)} "
        f"operation(s) for '{agent_name}'."
    )

    for operation in operations:
        show_result(operation)


def search_by_id():
    operations = get_operations()

    if len(operations) == 0:
        print("No operations saved yet.")
        return

    operation_id = input(
        "\nEnter the operation ID: "
    ).strip().upper()

    if operation_id == "":
        print("Search cannot be empty.")
        return

    for operation in operations:
        if operation["id"].upper() == operation_id:
            show_result(operation)
            return

    print(
        f"No operation found with ID: "
        f"{operation_id}"
    )


def search_by_property():
    operations = get_operations()

    if len(operations) == 0:
        print("No operations saved yet.")
        return

    property_search = input(
        "\nEnter the property address to search: "
    ).strip()

    if property_search == "":
        print("Search cannot be empty.")
        return

    property_operations = [
        operation
        for operation in operations
        if property_search.lower()
        in operation["property"].lower()
    ]

    if len(property_operations) == 0:
        print(
            f"No operations found for property: "
            f"{property_search}"
        )
        return

    print(
        f"\nFound {len(property_operations)} "
        f"operation(s) for '{property_search}'."
    )

    for operation in property_operations:
        show_result(operation)


def search_by_date():
    operations = get_operations()

    if len(operations) == 0:
        print("No operations saved yet.")
        return

    date_search = input(
        "\nEnter the date (dd/mm/yyyy): "
    ).strip()

    try:
        datetime.strptime(
            date_search,
            "%d/%m/%Y"
        )

    except ValueError:
        print(
            "Invalid date format. "
            "Use dd/mm/yyyy."
        )
        return

    date_operations = [
        operation
        for operation in operations
        if operation["date"] == date_search
    ]

    if len(date_operations) == 0:
        print(
            f"No operations found for date: "
            f"{date_search}"
        )
        return

    print(
        f"\nFound {len(date_operations)} "
        f"operation(s) for {date_search}."
    )

    for operation in date_operations:
        show_result(operation)