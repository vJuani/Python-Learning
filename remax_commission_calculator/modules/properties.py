from modules.database import (
    get_properties,
    add_property as db_add_property,
    update_property as db_update_property,
    delete_property as db_delete_property
)


def list_properties():
    properties = get_properties()

    if len(properties) == 0:
        print("No properties found.")
        return

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


def choose_jurisdiction(
    allow_keep=False
):
    while True:
        print("\n1 - CABA")
        print("2 - PBA")

        if allow_keep:
            print("3 - Keep current")

        try:
            option = int(
                input("Choose jurisdiction: ")
            )

            if option == 1:
                return "CABA"

            elif option == 2:
                return "PBA"

            elif option == 3 and allow_keep:
                return None

            if allow_keep:
                print(
                    "Invalid option. "
                    "Choose from 1 to 3."
                )
            else:
                print(
                    "Invalid option. "
                    "Choose 1 or 2."
                )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )


def add_property():
    print("\n======== ADD PROPERTY ========")

    while True:
        address = input(
            "Property address: "
        ).strip()

        if address != "":
            break

        print(
            "Property address cannot be empty."
        )

    jurisdiction = choose_jurisdiction()

    db_add_property(
        address,
        jurisdiction
    )

    print(
        "\nProperty added successfully!"
    )


def edit_property():
    properties = get_properties()

    if len(properties) == 0:
        print("No properties found.")
        return

    list_properties()

    while True:
        try:
            option = int(
                input(
                    "\nChoose the property to edit: "
                )
            )

            if 1 <= option <= len(properties):
                break

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(properties)}."
            )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )

    property_data = properties[
        option - 1
    ]

    print(
        f"\nCurrent address: "
        f"{property_data['address']}"
    )

    new_address = input(
        "New address "
        "(Enter to keep current): "
    ).strip()

    if new_address != "":
        property_data[
            "address"
        ] = new_address

    print(
        f"\nCurrent jurisdiction: "
        f"{property_data['jurisdiction']}"
    )

    new_jurisdiction = choose_jurisdiction(
        allow_keep=True
    )

    if new_jurisdiction is not None:
        property_data[
            "jurisdiction"
        ] = new_jurisdiction

    db_update_property(
        property_data["id"],
        property_data["address"],
        property_data["jurisdiction"]
    )

    print(
        "\nProperty updated successfully!"
    )


def delete_property():
    properties = get_properties()

    if len(properties) == 0:
        print("No properties found.")
        return

    list_properties()

    while True:
        try:
            option = int(
                input(
                    "\nChoose the property to delete: "
                )
            )

            if 1 <= option <= len(properties):
                break

            print(
                f"Invalid option. Choose a number "
                f"between 1 and {len(properties)}."
            )

        except ValueError:
            print(
                "Invalid input. Enter a number."
            )

    property_data = properties[
        option - 1
    ]

    print(
        f"\nYou selected: "
        f"{property_data['address']} "
        f"({property_data['jurisdiction']})"
    )

    while True:
        confirmation = input(
            "Are you sure you want to delete "
            "this property? (y/n): "
        ).strip().lower()

        if confirmation == "y":
            db_delete_property(
                property_data["id"]
            )

            print(
                "Property deleted successfully!"
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