import json

def save_properties(properties):
    with open("properties.json", "w", encoding="utf-8") as file:
        json.dump(
            properties,
            file,
            ensure_ascii=False,
            indent=4
        )


def load_properties():
    try:
        with open("properties.json", "r", encoding="utf-8") as file:
            return json.load(file)

    except:
        return []


def menu():
    print("\n======== Real Estate Assistant IA ========")
    print("1 - Add Property")
    print("2 - Show Properties")
    print("3 - Search Property")
    print("4 - Delete Property")
    print("5 - Edit Property")
    print("6 - Exit")

    option = int(input("Choose an option: "))
    return option


def add_property(properties):
    name = input("Enter the property name: ")
    price = int(input("Enter the property price: "))
    agent = input("Enter the agent's name: ")

    new_property = {
        "name": name,
        "price": price,
        "agent": agent
    }

    properties.append(new_property)
    save_properties(properties)

    print("Property added successfully!")


def show_properties(properties):
    print("\nAvailable Properties:")

    if len(properties) == 0:
        print("No properties available.")
        return

    number = 1

    for current_property in properties:
        print(
            number,
            "- 🏠", current_property["name"],
            "💰 USD", current_property["price"],
            "👤", current_property["agent"]
        )

        number += 1


def search_property(properties):
    search = input("Enter property name or location: ")
    found = False

    for current_property in properties:
        if search in current_property["name"]:
            print(
                "🏠", current_property["name"],
                "💰 USD", current_property["price"],
                "👤", current_property["agent"]
            )

            found = True

    if found == False:
        print("Property not found.")


def delete_property(properties):
    name = input("Enter the property name to delete: ")

    for current_property in properties:
        if current_property["name"] == name:
            properties.remove(current_property)
            save_properties(properties)

            print("Property deleted successfully!")
            return

    print("Property not found.")


def edit_property(properties):
    name = input("Enter the property name to edit: ")

    for current_property in properties:
        if current_property["name"] == name:
            new_name = input("Enter the new property name: ")
            new_price = int(input("Enter the new property price: "))
            new_agent = input("Enter the new agent's name: ")

            current_property["name"] = new_name
            current_property["price"] = new_price
            current_property["agent"] = new_agent

            save_properties(properties)

            print("Property updated successfully!")
            return

    print("Property not found.")


def main():
    properties = load_properties()

    option = menu()

    while option != 6:

        if option == 1:
            add_property(properties)

        elif option == 2:
            show_properties(properties)

        elif option == 3:
            search_property(properties)

        elif option == 4:
            delete_property(properties)

        elif option == 5:
            edit_property(properties)

        else:
            print("Invalid option")

        option = menu()

    print("Goodbye!")


if __name__ == "__main__":
    main()