import logging
import os
import sys
from datetime import timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DEV_SECRET_KEY = "dev-secret-key"
DEPLOYED_ENVS = frozenset({"production", "staging"})
BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgres"


def load_dotenv_file():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(BASE_DIR / ".env")


def get_app_env():
    return os.environ.get(
        "APP_ENV",
        "development"
    ).strip().lower()


def is_production():
    return get_app_env() == "production"


def is_staging():
    return get_app_env() == "staging"


def is_deployed():
    """True for staging and production (not local development)."""
    return get_app_env() in DEPLOYED_ENVS


def is_development():
    return not is_deployed()


def get_flask_debug():
    if is_deployed():
        return False

    raw_value = os.environ.get(
        "FLASK_DEBUG",
        "1"
    ).strip().lower()

    return raw_value in (
        "1",
        "true",
        "yes",
        "on"
    )


def get_secret_key():
    secret_key = os.environ.get("SECRET_KEY")

    if is_deployed():
        if (
            not secret_key
            or secret_key == DEFAULT_DEV_SECRET_KEY
        ):
            raise RuntimeError(
                "SECRET_KEY must be set to a strong random "
                "value when APP_ENV is staging or production."
            )

        return secret_key

    return secret_key or DEFAULT_DEV_SECRET_KEY


def get_database_url():
    """
    PostgreSQL DSN when set (e.g. Railway DATABASE_URL).

    Presence of this variable selects the postgres backend.
    """
    raw_url = os.environ.get("DATABASE_URL", "").strip()

    if not raw_url:
        return None

    return raw_url


def get_database_backend():
    """
    Active database backend.

    - ``postgres`` when ``DATABASE_URL`` is set
    - ``sqlite`` otherwise (uses ``DATABASE_PATH`` / default file)
    """
    if get_database_url():
        return BACKEND_POSTGRES

    return BACKEND_SQLITE


def get_database_path():
    raw_path = os.environ.get("DATABASE_PATH")

    if raw_path:
        return str(
            Path(raw_path).expanduser()
        )

    return str(BASE_DIR / "commission.db")


def get_upload_root():
    raw_path = os.environ.get("UPLOAD_DIR")

    if raw_path:
        return Path(raw_path).expanduser()

    return BASE_DIR / "static" / "uploads"


def get_private_upload_root():
    """
    Private document storage (outside static/).
    Config name: PRIVATE_UPLOAD_ROOT
    """
    raw_path = os.environ.get("PRIVATE_UPLOAD_ROOT")

    if raw_path:
        return Path(raw_path).expanduser().resolve()

    return (BASE_DIR / "uploads").resolve()


def get_host():
    return os.environ.get(
        "HOST",
        "127.0.0.1"
    )


def get_port():
    raw_value = os.environ.get(
        "PORT",
        "5000"
    )

    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 5000


def get_log_level():
    default_level = (
        "INFO"
        if is_deployed()
        else "DEBUG"
    )

    raw_level = os.environ.get(
        "LOG_LEVEL",
        default_level
    ).strip().upper()

    return getattr(
        logging,
        raw_level,
        logging.INFO
    )


def get_session_cookie_secure(app):
    raw_value = os.environ.get(
        "SESSION_COOKIE_SECURE"
    )

    if raw_value is not None:
        return raw_value.strip().lower() in (
            "1",
            "true",
            "yes",
            "on"
        )

    return is_deployed()


def get_session_cookie_samesite():
    return os.environ.get(
        "SESSION_COOKIE_SAMESITE",
        "Lax"
    )


def apply_config(app):
    load_dotenv_file()

    app.config["APP_ENV"] = get_app_env()
    app.config["DEBUG"] = get_flask_debug()
    app.config["TESTING"] = False
    app.config["SECRET_KEY"] = get_secret_key()
    app.config["DATABASE_BACKEND"] = (
        get_database_backend()
    )
    app.config["DATABASE_PATH"] = get_database_path()
    app.config["DATABASE_URL"] = get_database_url()
    app.config["UPLOAD_ROOT"] = str(
        get_upload_root()
    )
    app.config["PRIVATE_UPLOAD_ROOT"] = str(
        get_private_upload_root()
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = (
        get_session_cookie_secure(app)
    )
    app.config["SESSION_COOKIE_SAMESITE"] = (
        get_session_cookie_samesite()
    )
    app.config["PERMANENT_SESSION_LIFETIME"] = (
        timedelta(days=7)
    )
    app.config["PROPAGATE_EXCEPTIONS"] = (
        app.config["DEBUG"]
    )

    app.secret_key = app.config["SECRET_KEY"]

    configure_logging(app)


def configure_logging(app):
    log_level = get_log_level()

    app.logger.setLevel(log_level)

    if app.logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s "
            "[%(name)s] %(message)s"
        )
    )

    app.logger.addHandler(handler)
    app.logger.propagate = False

    logging.getLogger("werkzeug").setLevel(
        log_level
    )
