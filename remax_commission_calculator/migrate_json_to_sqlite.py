import json

from modules.cli_tenant import get_cli_organization_id

from modules.database import (
    add_agent,
    add_operation,
    add_property,
    get_agents,
    get_operations,
    get_properties
)


def load_json(file_name):
    try:
        with open(
            file_name,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except FileNotFoundError:
        return []


def migrate_agents():
    organization_id = get_cli_organization_id()

    json_agents = load_json(
        "agents.json"
    )

    database_agents = get_agents(organization_id)

    existing_agents = {
        agent["name"].lower()
        for agent in database_agents
    }

    migrated = 0

    for agent in json_agents:
        name = agent["name"]
        agent_type = agent["type"]

        if name.lower() not in existing_agents:
            add_agent(
                name,
                agent_type,
                organization_id
            )

            existing_agents.add(
                name.lower()
            )

            migrated += 1

    print(
        f"Agents migrated: {migrated}"
    )


def migrate_properties():
    organization_id = get_cli_organization_id()

    json_properties = load_json(
        "properties.json"
    )

    database_properties = get_properties(
        organization_id
    )

    existing_properties = {
        property_data["address"].lower()
        for property_data in database_properties
    }

    migrated = 0

    for property_data in json_properties:
        address = property_data[
            "address"
        ]

        jurisdiction = property_data[
            "jurisdiction"
        ]

        if (
            address.lower()
            not in existing_properties
        ):
            add_property(
                address,
                jurisdiction,
                organization_id
            )

            existing_properties.add(
                address.lower()
            )

            migrated += 1

    print(
        f"Properties migrated: {migrated}"
    )

def migrate_operations():
    organization_id = get_cli_organization_id()

    json_operations = load_json(
        "commission.json"
    )

    database_agents = get_agents(organization_id)
    database_properties = get_properties(
        organization_id
    )
    database_operations = get_operations(
        organization_id
    )

    agents_by_name = {
        agent["name"].lower(): agent["id"]
        for agent in database_agents
    }

    properties_by_address = {
        property_data["address"].lower():
        property_data["id"]
        for property_data in database_properties
    }

    existing_operations = {
        operation["id"]
        for operation in database_operations
    }

    migrated = 0
    skipped = 0

    for operation in json_operations:
        old_operation_id = operation["id"]

        if old_operation_id in existing_operations:
            skipped += 1
            continue

        agent_name = operation[
            "agent"
        ].lower()

        property_address = operation[
            "property"
        ].lower()

        agent_id = agents_by_name.get(
            agent_name
        )

        property_id = properties_by_address.get(
            property_address
        )

        if agent_id is None:
            print(
                f"Skipping {old_operation_id}: "
                f"agent not found."
            )
            skipped += 1
            continue

        if property_id is None:
            print(
                f"Skipping {old_operation_id}: "
                f"property not found."
            )
            skipped += 1
            continue

        add_operation(
            operation["date"],
            agent_id,
            property_id,
            operation["was_invoiced"],
            operation["vat_amount"],
            operation["sale_price"],
            operation["commission_rate"],
            operation["total_commission"],
            operation["commission_after_abao"],
            operation["abao"],
            operation["martillero"],
            operation["agent_payment"],
            operation["office_payment"],
            operation["office_total"],
            organization_id
        )

        migrated += 1

    print(
        f"Operations migrated: {migrated}"
    )

    print(
        f"Operations skipped: {skipped}"
    )    


def main():
    migrate_agents()
    migrate_properties()
    migrate_operations()

    print(
        "Migration completed successfully!"
    )

if __name__ == "__main__":
    main()