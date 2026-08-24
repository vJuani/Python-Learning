import getpass
import sys

from modules.access_codes import (
    generate_registration_code,
    hash_access_secret
)
from modules.auth import (
    ROLE_ADMIN,
    hash_password
)
from modules.database import (
    OrganizationProvisioningError,
    create_tables,
    get_organization_by_id,
    get_organization_settings,
    get_organizations,
    provision_organization
)
from modules.i18n import DEFAULT_LANGUAGE
from modules.organization_provisioning import (
    prompt_choice,
    prompt_timezone,
    validate_provisioning_request
)
from modules.organization_settings import (
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE
)
from modules.passwords import validate_password_policy


def print_header():
    print()
    print("Create new organization")
    print("=====================")
    print(
        "This tool is for internal onboarding only."
    )
    print(
        "It creates an organization, settings, "
        "registration code and the first admin."
    )
    print()


def print_existing_organizations():
    organizations = get_organizations()

    if len(organizations) == 0:
        print("No organizations found yet.")
        return

    print("Existing organizations:")

    for organization in organizations:
        status = (
            "active"
            if organization["is_active"]
            else "inactive"
        )
        print(
            f"  - id={organization['id']} "
            f"name={organization['name']} "
            f"({status})"
        )

    print()


def collect_inputs():
    name = input(
        "Organization name: "
    ).strip()

    default_language = prompt_choice(
        "Default language",
        ("es", "en"),
        DEFAULT_LANGUAGE
    )

    default_currency = prompt_choice(
        "Default currency",
        ("usd", "ars"),
        DEFAULT_CURRENCY.lower()
    ).upper()

    timezone = prompt_timezone(DEFAULT_TIMEZONE)

    print()
    print("First admin user")
    print("----------------")

    admin_username = input(
        "Admin username / email: "
    ).strip()

    admin_password = getpass.getpass(
        "Admin password: "
    )
    admin_password_confirm = getpass.getpass(
        "Confirm admin password: "
    )

    return {
        "name": name,
        "default_language": default_language,
        "default_currency": default_currency,
        "timezone": timezone,
        "admin_username": admin_username,
        "admin_password": admin_password,
        "admin_password_confirm": admin_password_confirm
    }


def main():
    create_tables()
    print_header()
    print_existing_organizations()

    inputs = collect_inputs()

    errors, parsed = validate_provisioning_request(
        inputs["name"],
        inputs["default_language"],
        inputs["default_currency"],
        inputs["timezone"],
        inputs["admin_username"],
        inputs["admin_password"],
        inputs["admin_password_confirm"],
        create_guest=False
    )

    password_error = validate_password_policy(
        inputs["admin_password"],
        inputs["admin_password_confirm"]
    )

    if password_error is not None:
        errors.append(password_error)

    if len(errors) > 0:
        print()
        print("Validation failed:")

        for error in errors:
            print(f"  - {error}")

        sys.exit(1)

    registration_code = generate_registration_code()

    print()
    print("Creating organization...")

    try:
        result = provision_organization(
            name=parsed["name"],
            display_name=parsed["display_name"],
            default_language=parsed["default_language"],
            default_currency=parsed["default_currency"],
            timezone=parsed["timezone"],
            admin_username=parsed["admin_username"],
            admin_password_hash=hash_password(
                inputs["admin_password"]
            ),
            admin_role=ROLE_ADMIN,
            registration_code_hash=hash_access_secret(
                registration_code
            )
        )

    except OrganizationProvisioningError as error:
        print()
        print(str(error))
        sys.exit(1)

    organization = get_organization_by_id(
        result["organization_id"]
    )
    settings = get_organization_settings(
        result["organization_id"]
    )

    print()
    print("Organization created successfully.")
    print(f"  Organization id: {organization['id']}")
    print(f"  Name: {organization['name']}")
    print(
        f"  Display name: {settings['display_name']}"
    )
    print(
        f"  Default language: {settings['default_language']}"
    )
    print(
        f"  Default currency: {settings['default_currency']}"
    )
    print(f"  Timezone: {settings['timezone']}")
    print(f"  Admin username: {parsed['admin_username']}")
    print()
    print("IMPORTANT — save this registration code now:")
    print(f"  Registration code: {registration_code}")
    print(
        "Agents will use this code to register. "
        "It will not be shown again."
    )
    print()
    print(
        "Guest access is created later from Settings "
        "as a shareable link (no guest user account)."
    )


if __name__ == "__main__":
    main()
