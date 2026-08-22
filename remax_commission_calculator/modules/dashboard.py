from modules.database import (
    get_agent_ranking as get_agent_ranking_db,
    get_agents,
    get_dashboard_metrics,
    get_properties
)


def calculate_dashboard_metrics(operations):
    total_operations = len(operations)

    gross_commission = 0
    office_revenue = 0
    agent_payments = 0
    highest_commission = 0

    for operation in operations:
        total_commission = operation[
            "total_commission"
        ]

        office_payment = operation[
            "office_payment"
        ]

        agent_payment = operation[
            "agent_payment"
        ]

        gross_commission += total_commission
        office_revenue += office_payment
        agent_payments += agent_payment

        if total_commission > highest_commission:
            highest_commission = total_commission

    if total_operations > 0:
        average_commission = (
            gross_commission / total_operations
        )
    else:
        average_commission = 0

    return {
        "total_operations": total_operations,
        "gross_commission": gross_commission,
        "office_revenue": office_revenue,
        "agent_payments": agent_payments,
        "highest_commission": highest_commission,
        "average_commission": average_commission
    }


def get_agent_ranking(operations):
    agent_commissions = {}

    for operation in operations:
        agent_name = operation["agent"]

        total_commission = operation[
            "total_commission"
        ]

        if agent_name in agent_commissions:
            agent_commissions[
                agent_name
            ] += total_commission

        else:
            agent_commissions[
                agent_name
            ] = total_commission

    return sorted(
        agent_commissions.items(),
        key=lambda item: item[1],
        reverse=True
    )


def show_agent_ranking():
    ranking = get_agent_ranking_db(
        limit=3
    )

    print("\n======== TOP AGENTS ========")

    if len(ranking) == 0:
        print("No agent data available.")
        return

    for index, (
        agent_name,
        commission
    ) in enumerate(
        ranking,
        start=1
    ):
        print(
            f"{index} - "
            f"{agent_name} "
            f"(USD {commission:.2f})"
        )


def show_dashboard():
    metrics = get_dashboard_metrics()

    agents = get_agents()
    properties = get_properties()

    print("\n=========================================")
    print("                DASHBOARD")
    print("=========================================")

    print(
        f"Operations : "
        f"{metrics['total_operations']}"
    )

    print(
        f"Agents     : "
        f"{len(agents)}"
    )

    print(
        f"Properties : "
        f"{len(properties)}"
    )

    print("-----------------------------------------")

    print(
        f"Gross Commission : "
        f"USD {metrics['gross_commission']:.2f}"
    )

    print(
        f"Office Revenue   : "
        f"USD {metrics['office_revenue']:.2f}"
    )

    print(
        f"Agent Payments   : "
        f"USD {metrics['agent_payments']:.2f}"
    )

    print("-----------------------------------------")

    print(
        f"Highest Commission : "
        f"USD {metrics['highest_commission']:.2f}"
    )

    print(
        f"Average Commission : "
        f"USD {metrics['average_commission']:.2f}"
    )

    print("=========================================")

    show_agent_ranking()