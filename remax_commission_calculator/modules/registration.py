import re
from datetime import datetime

from modules.access_codes import (
    generate_email_verification_code,
    generate_guest_token,
    generate_registration_code,
    hash_access_secret
)
from modules.auth import (
    ROLE_AGENT,
    hash_password
)
from modules.database import (
    STATUS_EMAIL_PENDING,
    STATUS_PENDING_APPROVAL,
    add_agent,
    add_user,
    create_email_verification_token,
    create_guest_access,
    create_registration_request,
    delete_pending_registration_request,
    get_active_verification_token,
    get_agents,
    get_guest_access_by_token_hash,
    get_organization_settings,
    get_registration_request,
    get_registration_request_by_email,
    get_user_by_email,
    increment_verification_attempt,
    mark_email_verified,
    mark_registration_approved,
    reject_registration_request,
    set_registration_code,
    touch_guest_access
)
from modules.config import is_production
from modules.email_delivery import (
    send_registration_approved_email,
    send_registration_rejected_email,
    send_verification_code_email
)
from modules.passwords import validate_password_policy


EMAIL_PATTERN = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
)

CODE_EXPIRY_MINUTES = 10
MAX_VERIFICATION_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


def mask_email(email):
    email = (email or "").strip()

    if "@" not in email:
        return email

    local, domain = email.split("@", 1)

    if len(local) <= 2:
        visible = local[:1]
    else:
        visible = local[:2]

    return f"{visible}***@{domain}"


def validate_registration_form(form_values):
    errors = []

    first_name = form_values.get("first_name", "").strip()
    last_name = form_values.get("last_name", "").strip()
    email = form_values.get("email", "").strip().lower()
    phone = form_values.get("phone", "").strip()
    organization_code = form_values.get(
        "organization_code",
        ""
    ).strip()
    password = form_values.get("password", "")
    confirm_password = form_values.get(
        "confirm_password",
        ""
    )

    if first_name == "":
        errors.append("err_first_name_required")

    if last_name == "":
        errors.append("err_last_name_required")

    if email == "":
        errors.append("err_email_required")
    elif EMAIL_PATTERN.match(email) is None:
        errors.append("err_email_invalid")

    if organization_code == "":
        errors.append("err_org_code_required")

    password_error = validate_password_policy(
        password,
        confirm_password
    )

    if password_error is not None:
        errors.append(password_error)

    return errors, {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "organization_code": organization_code,
        "password": password
    }


def resolve_organization_from_code(organization_code):
    code_hash = hash_access_secret(organization_code)

    if code_hash is None:
        return None

    from modules.database.organization_settings_repository import (
        find_organization_by_registration_code_hash
    )

    return find_organization_by_registration_code_hash(
        code_hash
    )


def _issue_verification_code(request_id, email, language="es"):
    raw_code = generate_email_verification_code()
    create_email_verification_token(
        request_id,
        hash_access_secret(raw_code),
        minutes=CODE_EXPIRY_MINUTES
    )
    send_verification_code_email(
        email,
        raw_code,
        language=language
    )

    return raw_code


def submit_agent_registration(parsed, language="es"):
    settings = resolve_organization_from_code(
        parsed["organization_code"]
    )

    if settings is None:
        return ["err_org_code_invalid"], None

    organization_id = settings["organization_id"]

    existing_user = get_user_by_email(
        parsed["email"],
        organization_id
    )

    if existing_user is not None:
        return ["err_email_already_registered"], None

    existing_request = get_registration_request_by_email(
        parsed["email"],
        organization_id
    )

    if existing_request is not None:
        status = existing_request["status"]

        if status == STATUS_EMAIL_PENDING:
            return [], {
                "action": "continue_verification",
                "request_id": existing_request["id"],
                "organization_id": organization_id,
                "email": existing_request["email"],
                "masked_email": mask_email(
                    existing_request["email"]
                )
            }

        if status == STATUS_PENDING_APPROVAL:
            return [], {
                "action": "awaiting_approval",
                "request_id": existing_request["id"],
                "organization_id": organization_id,
                "email": existing_request["email"],
                "masked_email": mask_email(
                    existing_request["email"]
                )
            }

        # rejected / approved without an active user:
        # allow a new request (no duplicate pending rows).

    request_id = create_registration_request(
        organization_id,
        parsed["first_name"],
        parsed["last_name"],
        parsed["email"],
        parsed["phone"],
        hash_password(parsed["password"])
    )

    _issue_verification_code(
        request_id,
        parsed["email"],
        language=language
    )

    return [], {
        "action": "created",
        "request_id": request_id,
        "organization_id": organization_id,
        "email": parsed["email"],
        "masked_email": mask_email(parsed["email"])
    }


def resume_email_verification(email, organization_code):
    settings = resolve_organization_from_code(
        organization_code
    )

    if settings is None:
        return None, "err_org_code_invalid"

    organization_id = settings["organization_id"]
    existing_request = get_registration_request_by_email(
        email.strip().lower(),
        organization_id
    )

    if existing_request is None:
        return None, "err_verify_invalid"

    if existing_request["status"] == STATUS_EMAIL_PENDING:
        return {
            "action": "continue_verification",
            "request_id": existing_request["id"],
            "email": existing_request["email"],
            "masked_email": mask_email(
                existing_request["email"]
            )
        }, None

    if existing_request["status"] == STATUS_PENDING_APPROVAL:
        return {
            "action": "awaiting_approval",
            "request_id": existing_request["id"],
            "email": existing_request["email"],
            "masked_email": mask_email(
                existing_request["email"]
            )
        }, None

    return None, "err_verify_invalid"


def cancel_pending_registration_for_dev(
    request_id=None,
    email=None,
    organization_id=None
):
    """
    Development-only helper to remove a pending registration
    request so the same email can register again.
    Never deletes users.
    """
    if is_production():
        return None, "err_dev_only"

    target = None

    if request_id is not None:
        target = get_registration_request(request_id)
    elif email is not None:
        if organization_id is None:
            return None, "err_org_required_for_email_cancel"

        target = get_registration_request_by_email(
            email,
            organization_id
        )
    else:
        return None, "err_cancel_target_required"

    if target is None:
        return None, "err_request_not_found"

    if target["status"] not in (
        STATUS_EMAIL_PENDING,
        STATUS_PENDING_APPROVAL
    ):
        return None, "err_request_not_pending_cancel"

    deleted = delete_pending_registration_request(
        target["id"]
    )

    if deleted is None:
        return None, "err_request_not_pending_cancel"

    return deleted, None


def verify_registration_code(request_id, raw_code):
    request_data = get_registration_request(request_id)

    if request_data is None:
        return False, "err_verify_invalid"

    if request_data["status"] != STATUS_EMAIL_PENDING:
        return False, "err_verify_invalid"

    token = get_active_verification_token(request_id)

    if token is None:
        return False, "err_verify_invalid"

    if token["used_at"] is not None:
        return False, "err_verify_used"

    if token["invalidated_at"] is not None:
        return False, "err_verify_invalid"

    if token["attempt_count"] >= MAX_VERIFICATION_ATTEMPTS:
        return False, "err_verify_too_many_attempts"

    now = datetime.utcnow().replace(
        microsecond=0
    ).isoformat()

    if token["expires_at"] < now:
        return False, "err_verify_expired"

    code = (raw_code or "").strip()

    if len(code) != 6 or not code.isdigit():
        increment_verification_attempt(token["id"])
        return False, "err_verify_code_invalid"

    expected_hash = hash_access_secret(code)

    if expected_hash != token["token_hash"]:
        # Do not burn attempts on an older invalidated code.
        from modules.database.connection import get_connection

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, used_at, invalidated_at
            FROM email_verification_tokens
            WHERE registration_request_id = ?
                AND token_hash = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                request_id,
                expected_hash
            )
        )
        older = cursor.fetchone()
        connection.close()

        if older is not None:
            if older[1] is not None:
                return False, "err_verify_used"

            if older[2] is not None:
                return False, "err_verify_invalid"

        attempts = increment_verification_attempt(token["id"])

        if attempts >= MAX_VERIFICATION_ATTEMPTS:
            return False, "err_verify_too_many_attempts"

        return False, "err_verify_code_invalid"

    ok = mark_email_verified(
        request_id,
        token["id"]
    )

    if not ok:
        return False, "err_verify_used"

    return True, None


def resend_verification_code(request_id, language="es"):
    request_data = get_registration_request(request_id)

    if request_data is None:
        return False, "err_verify_invalid"

    if request_data["status"] != STATUS_EMAIL_PENDING:
        return False, "err_verify_invalid"

    token = get_active_verification_token(request_id)

    if token is not None and token.get("last_sent_at"):
        try:
            last_sent = datetime.fromisoformat(
                token["last_sent_at"]
            )
            elapsed = (
                datetime.utcnow() - last_sent
            ).total_seconds()

            if elapsed < RESEND_COOLDOWN_SECONDS:
                wait = int(
                    RESEND_COOLDOWN_SECONDS - elapsed
                ) + 1
                return False, "err_verify_resend_cooldown"
        except ValueError:
            pass

    _issue_verification_code(
        request_id,
        request_data["email"],
        language=language
    )

    return True, None


def rotate_organization_registration_code(organization_id):
    raw_code = generate_registration_code()
    set_registration_code(
        organization_id,
        hash_access_secret(raw_code),
        enabled=True
    )

    return raw_code


def create_organization_guest_link(
    organization_id,
    created_by_user_id,
    label=None
):
    raw_token = generate_guest_token()
    access_id = create_guest_access(
        organization_id,
        hash_access_secret(raw_token),
        created_by_user_id,
        label=label
    )

    from modules.email_delivery import get_app_base_url

    guest_url = (
        f"{get_app_base_url()}/guest/{raw_token}"
    )

    return {
        "access_id": access_id,
        "guest_url": guest_url,
        "raw_token": raw_token
    }


def open_guest_access(raw_token):
    token_hash = hash_access_secret(raw_token)
    access = get_guest_access_by_token_hash(token_hash)

    if access is None:
        return None, "err_guest_link_invalid"

    if access["revoked_at"] is not None:
        return None, "err_guest_link_revoked"

    if (
        access["expires_at"] is not None
        and access["expires_at"] < datetime.utcnow().replace(
            microsecond=0
        ).isoformat()
    ):
        return None, "err_guest_link_expired"

    touch_guest_access(access["id"])

    return access, None


def approve_registration_request(
    request_id,
    organization_id,
    reviewed_by_user_id,
    agent_id=None,
    create_agent=False,
    agent_type="Alto",
    language="es"
):
    request_data = get_registration_request(
        request_id,
        organization_id
    )

    if request_data is None:
        return ["record_not_found"], None

    if request_data["status"] != STATUS_PENDING_APPROVAL:
        return ["err_request_not_pending"], None

    existing_user = get_user_by_email(
        request_data["email"],
        organization_id
    )

    if existing_user is not None:
        return ["err_email_already_registered"], None

    full_name = (
        f"{request_data['first_name']} "
        f"{request_data['last_name']}"
    ).strip()

    if create_agent or agent_id is None:
        agent_id = add_agent(
            full_name,
            agent_type,
            organization_id
        )

    user_id = add_user(
        request_data["email"],
        request_data["password_hash"],
        ROLE_AGENT,
        organization_id,
        agent_id=agent_id,
        is_active=True,
        email=request_data["email"],
        first_name=request_data["first_name"],
        last_name=request_data["last_name"],
        phone=request_data["phone"],
        account_status="active"
    )

    mark_registration_approved(
        request_id,
        organization_id,
        reviewed_by_user_id,
        user_id,
        agent_id
    )

    organization_name = None
    settings = get_organization_settings(organization_id)

    if settings is not None:
        organization_name = settings.get("display_name")

    send_registration_approved_email(
        request_data["email"],
        language=language,
        first_name=request_data["first_name"],
        organization_name=organization_name
    )

    return [], {
        "user_id": user_id,
        "agent_id": agent_id
    }


def reject_access_request(
    request_id,
    organization_id,
    reviewed_by_user_id,
    reason,
    language="es"
):
    if reason.strip() == "":
        return False

    request_data = get_registration_request(
        request_id,
        organization_id
    )

    if request_data is None:
        return False

    updated = reject_registration_request(
        request_id,
        organization_id,
        reviewed_by_user_id,
        reason
    )

    if updated:
        send_registration_rejected_email(
            request_data["email"],
            language=language,
            first_name=request_data["first_name"],
            reason=reason
        )

    return updated


def suggested_agents_for_request(request_data, organization_id):
    agents = get_agents(organization_id)
    full_name = (
        f"{request_data['first_name']} "
        f"{request_data['last_name']}"
    ).strip().lower()

    matches = []

    for agent in agents:
        if agent["name"].strip().lower() == full_name:
            matches.append(agent)

    return matches
