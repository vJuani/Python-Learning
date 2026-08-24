import getpass
import sys

from modules.auth import (
    ROLE_ADMIN,
    hash_password
)
from modules.database import (
    DEFAULT_ORGANIZATION_ID,
    add_user,
    create_tables,
    get_organizations,
    get_user_by_username
)


def resolve_organization_id():
    organizations = get_organizations()

    for organization in organizations:
        if organization["id"] == DEFAULT_ORGANIZATION_ID:
            return DEFAULT_ORGANIZATION_ID

    if len(organizations) == 0:
        print("No organization was found.")
        sys.exit(1)

    return organizations[0]["id"]


def main():
    create_tables()

    organization_id = resolve_organization_id()

    print("Create first admin user")
    print("-----------------------")

    username = input("Username: ").strip()

    if username == "":
        print("Username is required.")
        sys.exit(1)

    existing = get_user_by_username(
        username,
        organization_id=organization_id
    )

    if existing is not None:
        print(
            f"User '{username}' already exists."
        )
        sys.exit(1)

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass(
        "Confirm password: "
    )

    if password == "":
        print("Password is required.")
        sys.exit(1)

    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)

    if len(password) < 8:
        print(
            "Password must have at least 8 characters."
        )
        sys.exit(1)

    add_user(
        username,
        hash_password(password),
        ROLE_ADMIN,
        organization_id,
        agent_id=None,
        is_active=True
    )

    print(
        f"Admin user '{username}' created successfully."
    )


if __name__ == "__main__":
    main()
