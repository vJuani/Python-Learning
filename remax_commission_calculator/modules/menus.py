def get_menu_option(
    title,
    options
):
    while True:
        print(
            f"\n======== {title} ========"
        )

        for index, option_text in enumerate(
            options,
            start=1
        ):
            print(
                f"{index} - {option_text}"
            )

        try:
            option = int(
                input("Choose an option: ")
            )

            if 1 <= option <= len(options):
                return option

            print(
                f"Invalid option. "
                f"Choose from 1 to {len(options)}."
            )

        except ValueError:
            print(
                "Invalid input. "
                "Enter a number."
            )


def menu():
    return get_menu_option(
        "COMMISSION MODULE",
        [
            "New Calculation",
            "Show History",
            "Agents",
            "Properties",
            "Dashboard",
            "Search",
            "Reports",
            "Exit"
        ]
    )


def agents_menu():
    return get_menu_option(
        "AGENTS MENU",
        [
            "List Agents",
            "Add Agent",
            "Edit Agent",
            "Delete Agent",
            "Back"
        ]
    )


def properties_menu():
    return get_menu_option(
        "PROPERTIES MENU",
        [
            "List Properties",
            "Add Property",
            "Edit Property",
            "Delete Property",
            "Back"
        ]
    )


def search_menu():
    return get_menu_option(
        "SEARCH",
        [
            "Search by Agent",
            "Search by ID",
            "Search by Property",
            "Search by Date",
            "Back"
        ]
    )


def reports_menu():
    return get_menu_option(
        "REPORTS",
        [
            "Export Full History CSV",
            "Export Full History Excel",
            "Export Agent Report",
            "Export Property Report",
            "Export Date Report",
            "Back"
        ]
    )


def choose_agent(agents):
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

    while True:
        try:
            option = int(
                input(
                    "Choose an agent by number: "
                )
            )

            if 1 <= option <= len(agents):
                selected_agent = agents[
                    option - 1
                ]

                return (
                    selected_agent["id"],
                    selected_agent["name"],
                    selected_agent["type"]
                    )

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(agents)}."
            )

        except ValueError:
            print(
                "Invalid input. "
                "Enter a number."
            )


def choose_property(properties):
    print("\n======== PROPERTIES ========")

    for index, property_data in enumerate(
        properties,
        start=1
    ):
        print(
            f"{index} - "
            f"{property_data['address']} "
            f"({property_data['jurisdiction']})"
        )

    while True:
        try:
            option = int(
                input(
                    "Choose a property by number: "
                )
            )

            if 1 <= option <= len(properties):
                selected_property = properties[
                    option - 1
                ]

                return (
                    selected_property["id"],
                    selected_property["address"],
                    selected_property["jurisdiction"]
                )

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(properties)}."
            )

        except ValueError:
            print(
                "Invalid input. "
                "Enter a number."
            )


def invoiced_menu():
    option = get_menu_option(
        "INVOICED",
        [
            "Yes",
            "No"
        ]
    )

    if option == 1:
        return "yes"

    return "no"