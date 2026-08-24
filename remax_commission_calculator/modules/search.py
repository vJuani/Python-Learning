from modules.cli_tenant import get_cli_organization_id

from modules.database import (
    get_agents,
    get_operations,
    get_properties,
    search_operations_by_agent,
    search_operations_by_date,
    search_operations_by_id,
    search_operations_by_property
)


def normalize_query(query):
    if query is None:
        return ""

    return query.strip()


def matches_partial_text(value, query_lower):
    return query_lower in str(value).lower()


def search_agents(query, organization_id):
    search_query = normalize_query(query)

    if search_query == "":
        return []

    query_lower = search_query.lower()
    results = []

    for agent in get_agents(organization_id):
        if (
            search_query.isdigit()
            and agent["id"] == int(search_query)
        ):
            results.append(agent)
            continue

        if matches_partial_text(
            agent["name"],
            query_lower
        ):
            results.append(agent)
            continue

        if matches_partial_text(
            agent["type"],
            query_lower
        ):
            results.append(agent)

    return results


def search_properties(query, organization_id):
    search_query = normalize_query(query)

    if search_query == "":
        return []

    properties = get_properties(organization_id)
    operations = get_operations(organization_id)
    filtered_ids = set()
    query_lower = search_query.lower()

    for property_data in properties:
        property_id = property_data["id"]

        if (
            search_query.isdigit()
            and property_id == int(search_query)
        ):
            filtered_ids.add(property_id)
            continue

        if matches_partial_text(
            property_data["address"],
            query_lower
        ):
            filtered_ids.add(property_id)
            continue

        if matches_partial_text(
            property_data["jurisdiction"],
            query_lower
        ):
            filtered_ids.add(property_id)

    for operation in operations:
        property_id = operation["property_db_id"]

        agent_match = matches_partial_text(
            operation["agent"],
            query_lower
        )

        price_match = (
            search_query
            in f"{operation['sale_price']:.2f}"
        ) or (
            search_query
            in str(operation["sale_price"])
        )

        if agent_match or price_match:
            filtered_ids.add(property_id)

    return [
        property_data
        for property_data in properties
        if property_data["id"] in filtered_ids
    ]


def operation_matches_query(
    operation,
    search_query,
    query_lower
):
    if (
        search_query.isdigit()
        and operation["db_id"] == int(search_query)
    ):
        return True

    searchable_values = [
        operation["id"],
        operation["date"],
        operation["agent"],
        operation["agent_type"],
        operation["property"],
        operation["property_id"],
        operation["jurisdiction"],
        operation["was_invoiced"],
        f"{operation['sale_price']:.2f}",
        str(operation["sale_price"]),
        f"{operation['commission_rate']:.2f}",
        str(operation["commission_rate"]),
        f"{operation['total_commission']:.2f}",
        str(operation["total_commission"]),
        f"{operation['agent_payment']:.2f}",
        f"{operation['office_payment']:.2f}",
        f"{operation['office_total']:.2f}",
    ]

    for value in searchable_values:
        if matches_partial_text(
            value,
            query_lower
        ):
            return True

    return False


def search_operations(query, organization_id):
    search_query = normalize_query(query)

    if search_query == "":
        return []

    matched_operations = {}
    query_lower = search_query.lower()

    for operation in search_operations_by_id(
        search_query,
        organization_id
    ):
        matched_operations[
            operation["db_id"]
        ] = operation

    if "/" in search_query:
        for operation in search_operations_by_date(
            search_query,
            organization_id
        ):
            matched_operations[
                operation["db_id"]
            ] = operation

    for operation in search_operations_by_agent(
        search_query,
        organization_id
    ):
        matched_operations[
            operation["db_id"]
        ] = operation

    for operation in search_operations_by_property(
        search_query,
        organization_id
    ):
        matched_operations[
            operation["db_id"]
        ] = operation

    for operation in get_operations(organization_id):
        if operation["db_id"] in matched_operations:
            continue

        if operation_matches_query(
            operation,
            search_query,
            query_lower
        ):
            matched_operations[
                operation["db_id"]
            ] = operation

    return list(
        matched_operations.values()
    )


def global_search(
    query,
    organization_id,
    agent_id=None
):
    search_query = normalize_query(query)

    if search_query == "":
        return {
            "query": "",
            "agents": [],
            "properties": [],
            "operations": [],
            "total_results": 0,
            "has_query": False
        }

    agents = search_agents(
        search_query,
        organization_id
    )
    properties = search_properties(
        search_query,
        organization_id
    )
    operations = search_operations(
        search_query,
        organization_id
    )

    if agent_id is not None:
        agents = [
            agent
            for agent in agents
            if agent["id"] == agent_id
        ]
        related_ops = [
            operation
            for operation in get_operations(
                organization_id
            )
            if operation["agent_db_id"] == agent_id
        ]
        allowed_property_ids = {
            operation["property_db_id"]
            for operation in related_ops
        }
        properties = [
            property_data
            for property_data in properties
            if property_data["id"]
            in allowed_property_ids
        ]
        operations = [
            operation
            for operation in operations
            if operation["agent_db_id"] == agent_id
        ]

    return {
        "query": search_query,
        "agents": agents,
        "properties": properties,
        "operations": operations,
        "total_results": (
            len(agents)
            + len(properties)
            + len(operations)
        ),
        "has_query": True
    }


# =========================================
# CLI SEARCH (unchanged behaviour)
# =========================================

from datetime import datetime

from modules.reports import show_result


def search_by_agent():
    agent_name = input(
        "\nEnter the agent's name to search: "
    ).strip()

    if agent_name == "":
        print("Search cannot be empty.")
        return

    operations = search_operations_by_agent(
        agent_name,
        get_cli_organization_id()
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
    operations = get_operations(
        get_cli_organization_id()
    )

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
    operations = get_operations(
        get_cli_organization_id()
    )

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
    operations = get_operations(
        get_cli_organization_id()
    )

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
