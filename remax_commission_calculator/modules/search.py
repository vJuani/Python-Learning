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

import unicodedata


def normalize_query(query):
    if query is None:
        return ""

    return query.strip()


def fold_text(value):
    """Case/accent-insensitive fold for search matching."""
    text = str(value or "")
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(
        char for char in normalized
        if unicodedata.category(char) != "Mn"
    )
    return " ".join(without_marks.lower().split())


def matches_partial_text(value, query_folded):
    if not query_folded:
        return False
    return query_folded in fold_text(value)


def token_edit_distance(left, right, limit=2):
    """Small Levenshtein with an early exit. No extra dependency."""
    a = fold_text(left)
    b = fold_text(right)
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        row_min = i
        for j, char_b in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (char_a != char_b)
            value = min(insert_cost, delete_cost, replace_cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def fuzzy_token_match(value, query, *, max_dist=2):
    query_folded = fold_text(query)
    haystack = fold_text(value)
    if not query_folded or not haystack:
        return False
    if query_folded in haystack:
        return True
    if len(query_folded) < 4:
        return False
    return any(
        token_edit_distance(token, query_folded, limit=max_dist) <= max_dist
        for token in haystack.split()
        if len(token) >= 4
    )


def search_agents(query, organization_id, limit=None):
    """Search agents by name/type — not by internal ID."""
    search_query = normalize_query(query)

    if search_query == "":
        return []

    query_folded = fold_text(search_query)
    results = []

    for agent in get_agents(organization_id):
        if matches_partial_text(agent["name"], query_folded):
            results.append(agent)
        elif matches_partial_text(agent["type"], query_folded):
            results.append(agent)

        if limit is not None and len(results) >= limit:
            break

    if limit is not None:
        return results[:limit]

    return results


def score_agent_name(name, query):
    """Rank a stored agent name against a typed query. Organization-agnostic."""
    name_folded = fold_text(name)
    query_folded = fold_text(query)
    if not query_folded or not name_folded:
        return 0
    name_tokens = name_folded.split()
    query_tokens = query_folded.split()
    if name_folded == query_folded:
        return 100
    exact = 0
    prefix = 0
    fuzzy = 0
    for qt in query_tokens:
        if qt in name_tokens:
            exact += 1
            continue
        if any(
            (nt.startswith(qt) or qt.startswith(nt))
            for nt in name_tokens
            if len(qt) >= 3 and len(nt) >= 3
        ):
            prefix += 1
            continue
        max_dist = 1 if len(qt) <= 5 else 2
        if len(qt) >= 4 and any(
            len(nt) >= 4
            and token_edit_distance(qt, nt, limit=max_dist) <= max_dist
            for nt in name_tokens
        ):
            fuzzy += 1
    accounted = exact + prefix + fuzzy
    if accounted == len(query_tokens) and exact == len(query_tokens):
        return 80 if len(query_tokens) == 1 else 70
    if accounted == len(query_tokens) and exact:
        return 60
    if query_folded in name_folded:
        return 50
    if accounted == len(query_tokens) and prefix and not fuzzy:
        return 50
    if accounted == len(query_tokens) and fuzzy:
        return 40
    if exact or prefix or fuzzy:
        return min(45, 18 * exact + 10 * prefix + 8 * fuzzy)
    return 0


def rank_agents(query, organization_id, limit=8, *, min_score=20):
    """Score org agents by name. Never crosses organization_id."""
    scored = []
    for agent in get_agents(organization_id):
        score = score_agent_name(agent.get("name"), query)
        if score < min_score:
            continue
        scored.append((score, fold_text(agent.get("name")), agent))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[: max(int(limit or 8), 1)]


def collapse_agent_matches(scored, *, margin=15, unique_min=55):
    """Keep a unique winner, or everyone still close to the top score."""
    if not scored:
        return []
    normalized = []
    for item in scored:
        if len(item) == 3:
            normalized.append((item[0], item[2]))
        else:
            normalized.append((item[0], item[1]))
    if len(normalized) == 1:
        return [normalized[0][1]] if normalized[0][0] >= 20 else []
    top_score = normalized[0][0]
    second = normalized[1][0]
    if top_score >= unique_min and top_score - second >= margin:
        return [normalized[0][1]]
    floor = max(top_score - margin, 20)
    return [agent for score, agent in normalized if score >= floor]


def search_agents_flexible(query, organization_id, limit=8):
    """Token / prefix / fuzzy ranking. Does not require a contiguous full name."""
    ranked = rank_agents(query, organization_id, limit=max(int(limit or 8), 8))
    return collapse_agent_matches(ranked)[:limit]


def suggest_agents(query, organization_id, limit=8):
    """Autocomplete suggestions ranked by name prefix match."""
    search_query = normalize_query(query)

    if len(fold_text(search_query)) < 1:
        return []

    query_folded = fold_text(search_query)
    scored = []

    for agent in get_agents(organization_id):
        name_folded = fold_text(agent["name"])
        if query_folded not in name_folded:
            continue

        score = 0 if name_folded.startswith(query_folded) else 1
        scored.append((score, agent["name"].lower(), agent))

    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def search_properties(query, organization_id):
    search_query = normalize_query(query)

    if search_query == "":
        return []

    properties = get_properties(organization_id)
    operations = get_operations(organization_id)
    filtered_ids = set()
    query_folded = fold_text(search_query)

    for property_data in properties:
        property_id = property_data["id"]

        if (
            search_query.isdigit()
            and property_id == int(search_query)
        ):
            filtered_ids.add(property_id)
            continue

        external_id = (property_data.get("external_id") or "").strip()
        if external_id and fold_text(external_id) == query_folded:
            filtered_ids.add(property_id)
            continue

        if matches_partial_text(
            property_data["address"],
            query_folded
        ):
            filtered_ids.add(property_id)
            continue

        if matches_partial_text(
            property_data.get("agent_name") or "",
            query_folded
        ):
            filtered_ids.add(property_id)
            continue

        if matches_partial_text(
            property_data["jurisdiction"],
            query_folded
        ):
            filtered_ids.add(property_id)

    for operation in operations:
        property_id = operation["property_db_id"]

        agent_match = matches_partial_text(
            operation["agent"],
            query_folded
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
    query_folded
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
        operation.get("property_external_id") or "",
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
            query_folded
        ):
            return True

    return False


def search_operations(query, organization_id):
    search_query = normalize_query(query)

    if search_query == "":
        return []

    matched_operations = {}
    query_folded = fold_text(search_query)

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
            query_folded
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
