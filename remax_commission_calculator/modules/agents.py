from modules.cli_tenant import get_cli_organization_id

from modules.database import (
    get_agents,
    add_agent as db_add_agent,
    update_agent as db_update_agent,
    delete_agent as db_delete_agent
)


def list_agents():
    agents = get_agents(
        get_cli_organization_id()
    )

    if len(agents) == 0:
        print("No agents found.")
        return

    print("\n======== AGENTS ========")

    for index, agent in enumerate(
        agents,
        start=1
    ):
        print(
            f"{index} - "
            f"{agent['name']} "
            f"({agent['type']})"
        )


def choose_agent_type(
    allow_keep=False
):
    while True:
        print("\n1 - Alto")
        print("2 - Puro")
        print("3 - Junior")
        print("4 - RAPP")

        if allow_keep:
            print("5 - Keep current")

        try:
            option = int(
                input("Choose agent type: ")
            )

            if option == 1:
                return "Alto"

            elif option == 2:
                return "Puro"

            elif option == 3:
                return "Junior"

            elif option == 4:
                return "RAPP"

            elif option == 5 and allow_keep:
                return None

            if allow_keep:
                print(
                    "Invalid option. "
                    "Choose from 1 to 5."
                )
            else:
                print(
                    "Invalid option. "
                    "Choose from 1 to 4."
                )

        except ValueError:
            print(
                "Invalid input. "
                "Enter a number."
            )


def add_agent():
    print("\n======== ADD AGENT ========")

    while True:
        name = input(
            "Enter the agent's full name: "
        ).strip()

        if name != "":
            break

        print(
            "Agent name cannot be empty."
        )

    agent_type = choose_agent_type()

    db_add_agent(
        name,
        agent_type,
        get_cli_organization_id()
    )

    print(
        "Agent added successfully!"
    )


def edit_agent():
    organization_id = get_cli_organization_id()

    agents = get_agents(organization_id)

    if len(agents) == 0:
        print("No agents found.")
        return

    list_agents()

    while True:
        try:
            option = int(
                input(
                    "\nChoose the agent to edit: "
                )
            )

            if 1 <= option <= len(agents):
                break

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(agents)}."
            )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )

    agent = agents[option - 1]

    print(
        f"\nCurrent name: "
        f"{agent['name']}"
    )

    new_name = input(
        "New name (Enter to keep current): "
    ).strip()

    if new_name != "":
        agent["name"] = new_name

    print(
        f"\nCurrent type: "
        f"{agent['type']}"
    )

    new_type = choose_agent_type(
        allow_keep=True
    )

    if new_type is not None:
        agent["type"] = new_type

    db_update_agent(
        agent["id"],
        agent["name"],
        agent["type"],
        organization_id
    )

    print(
        "\nAgent updated successfully!"
    )


def delete_agent():
    organization_id = get_cli_organization_id()

    agents = get_agents(organization_id)

    if len(agents) == 0:
        print("No agents found.")
        return

    list_agents()

    while True:
        try:
            option = int(
                input(
                    "\nChoose the agent to delete: "
                )
            )

            if 1 <= option <= len(agents):
                break

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(agents)}."
            )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )

    agent = agents[option - 1]

    print(
        f"\nYou selected: "
        f"{agent['name']} "
        f"({agent['type']})"
    )

    while True:
        confirmation = input(
            "Are you sure you want to delete "
            "this agent? (y/n): "
        ).strip().lower()

        if confirmation == "y":
            db_delete_agent(
                agent["id"],
                organization_id
            )

            print(
                "Agent deleted successfully!"
            )
            break

        elif confirmation == "n":
            print(
                "Deletion cancelled."
            )
            break

        else:
            print(
                "Invalid option. Enter y or n."
            )