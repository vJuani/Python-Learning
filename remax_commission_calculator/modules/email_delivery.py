import logging
import os
import smtplib
from email.message import EmailMessage

from jinja2 import Environment, FileSystemLoader, select_autoescape

from modules.config import BASE_DIR, is_deployed


logger = logging.getLogger(__name__)

BRAND_NAME = "Commission Calculator"


class EmailDeliveryError(Exception):
    """Raised when outbound email cannot be delivered."""

    def __init__(self, error_key, *, detail=None):
        super().__init__(error_key)
        self.error_key = error_key
        self.detail = (detail or "")[:300]


def _mask_email_for_log(email):
    address = (email or "").strip().lower()

    if "@" not in address:
        return address

    local, domain = address.split("@", 1)

    if len(local) <= 2:
        visible = local[:1]
    else:
        visible = local[:2]

    return f"{visible}***@{domain}"


def _normalize_recipient(email):
    return (email or "").strip().lower()

EMAIL_COPY = {
    "es": {
        "footer_tagline": "Calculá. Gestioná. Crecé.",
        "verify_subject": "Tu código de verificación",
        "verify_title": "Verificá tu correo",
        "verify_intro": (
            "Usá este código para completar tu registro:"
        ),
        "verify_expiry": "Este código vence en 10 minutos.",
        "verify_ignore": (
            "Si vos no solicitaste este código, "
            "podés ignorar este correo."
        ),
        "approved_subject": "Tu cuenta fue aprobada",
        "approved_title": "¡Tu cuenta fue aprobada!",
        "approved_greeting": "Hola {name},",
        "approved_body": (
            "Ya podés empezar a usar Commission Calculator."
        ),
        "approved_org_label": "Inmobiliaria:",
        "approved_cta": "Iniciar sesión",
        "rejected_subject": "Actualización sobre tu solicitud",
        "rejected_title": "Actualización sobre tu solicitud",
        "rejected_greeting": "Hola {name},",
        "rejected_body": (
            "Tu solicitud para acceder a Commission Calculator "
            "fue rechazada por tu inmobiliaria."
        ),
        "rejected_reason_label": "Motivo",
    },
    "en": {
        "footer_tagline": "Calculate. Manage. Grow.",
        "verify_subject": "Your verification code",
        "verify_title": "Verify your email",
        "verify_intro": (
            "Use this code to complete your registration:"
        ),
        "verify_expiry": "This code expires in 10 minutes.",
        "verify_ignore": (
            "If you did not request this code, "
            "you can ignore this email."
        ),
        "approved_subject": "Your account was approved",
        "approved_title": "Your account was approved!",
        "approved_greeting": "Hi {name},",
        "approved_body": (
            "You can start using Commission Calculator now."
        ),
        "approved_org_label": "Brokerage:",
        "approved_cta": "Sign in",
        "rejected_subject": "Update on your request",
        "rejected_title": "Update on your request",
        "rejected_greeting": "Hi {name},",
        "rejected_body": (
            "Your request to access Commission Calculator "
            "was rejected by your brokerage."
        ),
        "rejected_reason_label": "Reason",
    },
}


def get_email_backend():
    raw = os.environ.get(
        "EMAIL_BACKEND",
        "console"
    ).strip().lower()

    if raw in ("mock", "console"):
        return raw

    return raw


def is_console_email_backend():
    return get_email_backend() in ("console", "mock")


def get_app_base_url():
    return os.environ.get(
        "APP_BASE_URL",
        "http://127.0.0.1:5000"
    ).rstrip("/")


def get_email_logo_url():
    """
    Public URL for the product logo in emails.
    Localhost URLs are ignored because mail clients cannot load them.
    """
    raw = os.environ.get("EMAIL_LOGO_URL", "").strip()

    if raw == "":
        return None

    lower = raw.lower()

    if "127.0.0.1" in lower or "localhost" in lower:
        logger.warning(
            "EMAIL_LOGO_URL ignored: mail clients cannot load "
            "localhost images. Use a public URL."
        )
        return None

    return raw


def _brand_context(language, subject):
    copy = _copy(language)

    return {
        "language": language,
        "subject": subject,
        "brand_name": BRAND_NAME,
        "footer_tagline": copy["footer_tagline"],
        "logo_url": get_email_logo_url(),
    }


def _normalize_language(language):
    if (language or "").strip().lower().startswith("en"):
        return "en"

    return "es"


def _copy(language):
    return EMAIL_COPY[_normalize_language(language)]


def _email_env():
    return Environment(
        loader=FileSystemLoader(
            str(BASE_DIR / "templates")
        ),
        autoescape=select_autoescape(
            enabled_extensions=("html", "xml")
        ),
    )


def render_email_html(template_name, **context):
    template = _email_env().get_template(template_name)
    return template.render(**context)


def _append_inbox(to_email, subject, body):
    inbox_path = BASE_DIR / "tmp_email_inbox.txt"
    flat_body = " ".join(
        str(body).splitlines()
    ).strip()

    with open(inbox_path, "a", encoding="utf-8") as inbox:
        inbox.write(
            f"{to_email}\t{subject}\t{flat_body}\n"
        )


def _smtp_error_detail(exc):
    code = getattr(exc, "smtp_code", None)
    error = getattr(exc, "smtp_error", None)

    if isinstance(error, (bytes, bytearray)):
        error = error.decode("utf-8", errors="replace")

    if code is None and error is None:
        return str(exc)

    return f"smtp_code={code} smtp_error={error}"


def _send_email(to_email, subject, text_body, html_body=None):
    backend = get_email_backend()
    to_email = _normalize_recipient(to_email)

    logger.info(
        "verification_email_send_start backend=%s to=%s subject=%s html=%s",
        backend,
        _mask_email_for_log(to_email),
        subject,
        bool(html_body),
    )

    if is_deployed() and backend in ("console", "mock"):
        detail = (
            f"EMAIL_BACKEND={backend} is not allowed when "
            "APP_ENV is staging/production"
        )
        logger.error(
            "verification_email_send_failed to=%s detail=%s",
            _mask_email_for_log(to_email),
            detail,
        )
        raise EmailDeliveryError(
            "err_verify_email_not_configured",
            detail=detail,
        )

    try:
        if backend == "smtp":
            _send_smtp_email(
                to_email,
                subject,
                text_body,
                html_body=html_body,
            )
        else:
            logger.info(
                "verification_email_send_console to=%s "
                "(dev/test only — not delivered)",
                _mask_email_for_log(to_email),
            )
            _send_console_email(
                to_email,
                subject,
                text_body,
                html_body=html_body,
            )
    except EmailDeliveryError:
        raise
    except Exception as exc:
        detail = (
            _smtp_error_detail(exc)
            if isinstance(exc, smtplib.SMTPException)
            else str(exc)[:200]
        )
        logger.error(
            "verification_email_send_failed to=%s backend=%s detail=%s",
            _mask_email_for_log(to_email),
            backend,
            detail,
        )
        raise EmailDeliveryError(
            "err_verify_email_send_failed",
            detail=detail,
        ) from exc

    logger.info(
        "verification_email_send_success backend=%s to=%s subject=%s",
        backend,
        _mask_email_for_log(to_email),
        subject,
    )


def _send_console_email(
    to_email,
    subject,
    text_body,
    html_body=None
):
    print()
    print("=== EMAIL (console backend) ===")
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print("--- text/plain ---")
    print(text_body)

    if html_body:
        print("--- text/html ---")
        print(f"(html length={len(html_body)} chars)")

    print("================================")
    print()

    _append_inbox(to_email, subject, text_body)


def _send_smtp_email(
    to_email,
    subject,
    text_body,
    html_body=None
):
    to_email = _normalize_recipient(to_email)
    host = os.environ.get("SMTP_HOST", "").strip()
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("EMAIL_FROM", "").strip()

    if sender == "":
        sender = os.environ.get("SMTP_FROM", username).strip()

    if host == "" or sender == "":
        logger.error(
            "SMTP not configured: host_set=%s from_set=%s "
            "(set SMTP_HOST and EMAIL_FROM in .env)",
            bool(host),
            bool(sender),
        )
        raise RuntimeError(
            "SMTP is not configured. Set SMTP_HOST and EMAIL_FROM."
        )

    if password.strip() == "":
        logger.error(
            "SMTP rejected locally: SMTP_PASSWORD is empty "
            "(set it in .env, not .env.example)"
        )
        raise RuntimeError(
            "SMTP is not configured. Set SMTP_PASSWORD."
        )

    port = int(os.environ.get("SMTP_PORT", "587"))
    use_tls = os.environ.get(
        "SMTP_USE_TLS",
        "1"
    ).strip().lower() in ("1", "true", "yes", "on")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(text_body)

    if html_body:
        message.add_alternative(
            html_body,
            subtype="html"
        )

    logger.info(
        "SMTP connecting host=%s port=%s tls=%s "
        "username_set=%s from=%s to=%s",
        host,
        port,
        use_tls,
        bool(username),
        sender,
        to_email,
    )

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()

            if username != "":
                smtp.login(username, password)

            refused = smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "SMTP authentication rejected by provider: %s",
            _smtp_error_detail(exc),
        )
        raise
    except smtplib.SMTPRecipientsRefused as exc:
        logger.error(
            "SMTP provider refused all recipients to=%s detail=%s",
            to_email,
            _smtp_error_detail(exc),
        )
        raise
    except smtplib.SMTPSenderRefused as exc:
        logger.error(
            "SMTP provider refused sender from=%s detail=%s",
            sender,
            _smtp_error_detail(exc),
        )
        raise
    except smtplib.SMTPDataError as exc:
        logger.error(
            "SMTP provider rejected message data: %s",
            _smtp_error_detail(exc),
        )
        raise
    except smtplib.SMTPException as exc:
        logger.error(
            "SMTP provider error: %s",
            _smtp_error_detail(exc),
        )
        raise
    except OSError as exc:
        logger.error(
            "SMTP connection failed host=%s port=%s error=%s",
            host,
            port,
            exc,
        )
        raise

    if refused:
        logger.error(
            "SMTP provider accepted connection but refused "
            "recipient(s): %s",
            refused,
        )
        raise RuntimeError(
            f"SMTP refused recipient(s): {refused}"
        )

    logger.info(
        "SMTP provider accepted message from=%s to=%s subject=%s",
        sender,
        to_email,
        subject,
    )


def send_verification_code_email(to_email, code, language="es"):
    language = _normalize_language(language)
    copy = _copy(language)
    to_email = _normalize_recipient(to_email)
    code_text = str(code).strip()
    code_digits = list(code_text)

    subject = copy["verify_subject"]
    text_body = (
        f"{BRAND_NAME}\n\n"
        f"{copy['verify_title']}\n\n"
        f"{copy['verify_intro']}\n\n"
        f"{code_text}\n\n"
        f"{copy['verify_expiry']}\n"
        f"{copy['verify_ignore']}\n\n"
        f"{BRAND_NAME}\n"
        f"{copy['footer_tagline']}\n"
    )

    html_body = render_email_html(
        "email/verification_code.html",
        **_brand_context(language, subject),
        title=copy["verify_title"],
        intro=copy["verify_intro"],
        code_digits=code_digits,
        expiry_text=copy["verify_expiry"],
        ignore_text=copy["verify_ignore"],
    )

    _send_email(
        to_email,
        subject,
        text_body,
        html_body=html_body
    )


def send_registration_approved_email(
    to_email,
    language="es",
    first_name=None,
    organization_name=None
):
    language = _normalize_language(language)
    copy = _copy(language)
    login_url = f"{get_app_base_url()}/login"
    name = (first_name or "").strip()
    org_name = (organization_name or "").strip()

    subject = copy["approved_subject"]
    greeting = (
        copy["approved_greeting"].format(name=name)
        if name
        else ""
    )

    text_lines = [BRAND_NAME, "", copy["approved_title"], ""]

    if greeting:
        text_lines.append(greeting)
        text_lines.append("")

    text_lines.append(copy["approved_body"])

    if org_name:
        text_lines.append("")
        text_lines.append(
            f"{copy['approved_org_label']} {org_name}"
        )

    text_lines.extend([
        "",
        f"{copy['approved_cta']}: {login_url}",
        "",
        BRAND_NAME,
        copy["footer_tagline"],
        "",
    ])
    text_body = "\n".join(text_lines)

    html_body = render_email_html(
        "email/account_approved.html",
        **_brand_context(language, subject),
        title=copy["approved_title"],
        greeting=greeting,
        body_text=copy["approved_body"],
        organization_name=org_name or None,
        organization_label=copy["approved_org_label"],
        cta_url=login_url,
        cta_label=copy["approved_cta"],
    )

    _send_email(
        to_email,
        subject,
        text_body,
        html_body=html_body
    )


def send_registration_rejected_email(
    to_email,
    language="es",
    first_name=None,
    reason=None
):
    language = _normalize_language(language)
    copy = _copy(language)
    name = (first_name or "").strip()
    reason_text = (reason or "").strip()

    subject = copy["rejected_subject"]
    greeting = (
        copy["rejected_greeting"].format(name=name)
        if name
        else ""
    )

    text_lines = [BRAND_NAME, "", copy["rejected_title"], ""]

    if greeting:
        text_lines.append(greeting)
        text_lines.append("")

    text_lines.append(copy["rejected_body"])

    if reason_text:
        text_lines.append("")
        text_lines.append(
            f"{copy['rejected_reason_label']}: {reason_text}"
        )

    text_lines.extend([
        "",
        BRAND_NAME,
        copy["footer_tagline"],
        "",
    ])
    text_body = "\n".join(text_lines)

    html_body = render_email_html(
        "email/account_rejected.html",
        **_brand_context(language, subject),
        title=copy["rejected_title"],
        greeting=greeting,
        body_text=copy["rejected_body"],
        reason=reason_text or None,
        reason_label=copy["rejected_reason_label"],
    )

    _send_email(
        to_email,
        subject,
        text_body,
        html_body=html_body
    )


# Backward-compatible alias used by older call sites.
def send_verification_email(to_email, verify_url):
    send_verification_code_email(
        to_email,
        code=verify_url,
        language="es"
    )
