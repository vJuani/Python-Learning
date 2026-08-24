from functools import wraps

from flask import (
    abort,
    flash,
    g,
    redirect,
    request,
    session,
    url_for
)

from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)

from modules.database.tenant import (
    TenantError,
    require_organization_id
)
from modules.database.organization_settings_repository import (
    get_organization_settings
)
from modules.database.guest_access_repository import (
    get_guest_access_by_token_hash
)
from modules.database.users_repository import (
    get_user_by_id,
    get_user_by_username
)
from modules.i18n import DEFAULT_LANGUAGE


ROLE_ADMIN = "admin"
ROLE_AGENT = "agent"
ROLE_GUEST = "guest"

USER_ROLES = (
    ROLE_ADMIN,
    ROLE_AGENT
)

LOGIN_ROLES = (
    ROLE_ADMIN,
    ROLE_AGENT
)


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(
        password_hash,
        password
    )


def authenticate_user(username, password):
    matched = get_user_by_username(username)

    if matched is None:
        return None, "login_invalid"

    if isinstance(matched, list):
        candidates = matched
    else:
        candidates = [matched]

    verified = []

    for user in candidates:
        if not verify_password(
            user["password_hash"],
            password
        ):
            continue

        verified.append(user)

    if len(verified) == 0:
        return None, "login_invalid"

    if len(verified) > 1:
        return None, "login_ambiguous"

    user = verified[0]

    if user["role"] == ROLE_GUEST:
        return None, "login_guest_disabled"

    if not user["is_active"] or user["account_status"] != "active":
        return None, "login_inactive"

    if user["role"] not in LOGIN_ROLES:
        return None, "login_invalid"

    return user, None


def login_user(user):
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True

    settings = get_organization_settings(
        user["organization_id"]
    )

    if settings is not None:
        session["language"] = settings[
            "default_language"
        ]
    else:
        session["language"] = DEFAULT_LANGUAGE


def login_guest_access(access, token_hash):
    session.clear()
    session["guest_access_id"] = access["id"]
    session["guest_organization_id"] = access[
        "organization_id"
    ]
    session["guest_token_hash"] = token_hash
    session.permanent = True

    settings = get_organization_settings(
        access["organization_id"]
    )

    if settings is not None:
        session["language"] = settings[
            "default_language"
        ]
    else:
        session["language"] = DEFAULT_LANGUAGE


def logout_user():
    session.pop("user_id", None)
    session.pop("guest_access_id", None)
    session.pop("guest_organization_id", None)


def load_logged_in_user():
    user_id = session.get("user_id")
    guest_access_id = session.get("guest_access_id")

    g.user = None
    g.guest_access = None

    if user_id is not None:
        user = get_user_by_id(user_id)

        if (
            user is None
            or not user["is_active"]
            or user["account_status"] != "active"
            or user["role"] not in LOGIN_ROLES
        ):
            session.pop("user_id", None)
            g.user = None
        else:
            g.user = user
            return user

    if guest_access_id is not None:
        from modules.access_codes import hash_access_secret

        token_hash = session.get("guest_token_hash")
        access = None

        if token_hash:
            access = get_guest_access_by_token_hash(
                token_hash
            )

        if (
            access is None
            or access["id"] != guest_access_id
            or access["revoked_at"] is not None
        ):
            session.pop("guest_access_id", None)
            session.pop("guest_organization_id", None)
            session.pop("guest_token_hash", None)
            g.guest_access = None
        else:
            g.guest_access = access
            return None

    return g.user


def get_current_user():
    return getattr(g, "user", None)


def get_guest_access():
    return getattr(g, "guest_access", None)


def is_guest_session():
    return get_guest_access() is not None


def is_admin(user=None):
    user = user or get_current_user()
    return (
        user is not None
        and user["role"] == ROLE_ADMIN
    )


def is_agent(user=None):
    user = user or get_current_user()
    return (
        user is not None
        and user["role"] == ROLE_AGENT
    )


def is_guest(user=None):
    if get_guest_access() is not None:
        return True

    user = user or get_current_user()
    return (
        user is not None
        and user["role"] == ROLE_GUEST
    )


def can_write(user=None):
    if get_guest_access() is not None:
        return False

    return is_admin(user) or is_agent(user)


def can_manage_users(user=None):
    if get_guest_access() is not None:
        return False

    return is_admin(user)


def can_delete(user=None):
    if get_guest_access() is not None:
        return False

    return is_admin(user)


def can_approve(user=None):
    if get_guest_access() is not None:
        return False

    return is_admin(user)


def scoped_organization_id(user=None):
    user = user or get_current_user()

    if user is not None:
        return require_organization_id(
            user.get("organization_id")
        )

    guest = get_guest_access()

    if guest is not None:
        return require_organization_id(
            guest.get("organization_id")
        )

    raise TenantError(
        "organization_id is required"
    )


def scoped_agent_id(user=None):
    user = user or get_current_user()

    if user is None:
        return None

    if user["role"] != ROLE_AGENT:
        return None

    return user.get("agent_id")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if (
            get_current_user() is None
            and get_guest_access() is None
        ):
            flash("login_required", "error")
            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()

        if user is None:
            flash("login_required", "error")
            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        if not is_admin(user):
            abort(403)

        return view(*args, **kwargs)

    return wrapped


def write_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()

        if user is None:
            flash("login_required", "error")
            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        if not can_write(user):
            abort(403)

        return view(*args, **kwargs)

    return wrapped
