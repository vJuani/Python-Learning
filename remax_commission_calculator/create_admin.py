import argparse
import getpass
import sys

from modules.auth import (
    ROLE_ADMIN,
    hash_password
)
from modules.database import (
    add_user,
    create_tables,
    get_organization_by_id,
    get_user_by_username
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create an admin user in one organization."
    )
    parser.add_argument(
        "--organization-id",
        type=int,
        required=True,
        help="Organization id. There is no default.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    create_tables()

    organization = get_organization_by_id(args.organization_id)
    if organization is None:
        print(
            f"Organization {args.organization_id} was not found."
        )
        sys.exit(1)

    organization_id = organization["id"]

    print("Create admin user")
    print("-----------------")
    print(f"Organization: {organization.get('name')} ({organization_id})")

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
