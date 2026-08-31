import logging
import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from modules.branding import (
    get_app_base_url,
    get_app_domain,
    get_brand_email_footer_rel,
    get_brand_logo_dark_rel,
    get_brand_name,
)
from modules.config import BASE_DIR
from modules.email_providers import (
    EmailDeliveryError,
    OutboundEmail,
    get_email_provider,
    is_console_email_backend,
)
from modules.email_providers._log import mask_email_for_log
from modules.email_providers.factory import get_email_backend


logger = logging.getLogger(__name__)

# Re-export for existing imports.
__all__ = [
    "EmailDeliveryError",
    "get_brand_name",
    "get_email_backend",
    "is_console_email_backend",
    "get_app_base_url",
    "get_email_logo_url",
    "render_email_html",
    "send_verification_code_email",
    "send_registration_approved_email",
    "send_registration_rejected_email",
    "send_transactional_email",
    "send_password_reset_email",
    "send_verification_email",
]

EMAIL_COPY = {
    "es": {
        "footer_tagline": "Gestión. Control. Resultados.",
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
            "Ya podés empezar a usar {brand_name}."
        ),
        "approved_org_label": "Inmobiliaria:",
        "approved_cta": "Iniciar sesión",
        "rejected_subject": "Actualización sobre tu solicitud",
        "rejected_title": "Actualización sobre tu solicitud",
        "rejected_greeting": "Hola {name},",
        "rejected_body": (
            "Tu solicitud para acceder a {brand_name} "
            "fue rechazada por tu inmobiliaria."
        ),
        "rejected_reason_label": "Motivo",
        "reset_subject": "Restablecé tu contraseña",
        "reset_title": "Restablecé tu contraseña",
        "reset_intro": (
            "Usá este enlace para elegir una nueva contraseña:"
        ),
        "reset_expiry": "Este enlace vence en 60 minutos.",
        "reset_ignore": (
            "Si vos no solicitaste restablecer la contraseña, "
            "podés ignorar este correo."
        ),
        "reset_cta": "Restablecer contraseña",
    },
    "en": {
        "footer_tagline": "Manage. Control. Deliver.",
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
            "You can start using {brand_name} now."
        ),
        "approved_org_label": "Brokerage:",
        "approved_cta": "Sign in",
        "rejected_subject": "Update on your request",
        "rejected_title": "Update on your request",
        "rejected_greeting": "Hi {name},",
        "rejected_body": (
            "Your request to access {brand_name} "
            "was rejected by your brokerage."
        ),
        "rejected_reason_label": "Reason",
        "reset_subject": "Reset your password",
        "reset_title": "Reset your password",
        "reset_intro": (
            "Use this link to choose a new password:"
        ),
        "reset_expiry": "This link expires in 60 minutes.",
        "reset_ignore": (
            "If you did not request a password reset, "
            "you can ignore this email."
        ),
        "reset_cta": "Reset password",
    },
}


def get_email_logo_url():
    """
    Public URL for the product logo in emails.
    Localhost URLs are ignored because mail clients cannot load them.
    """
    raw = os.environ.get("EMAIL_LOGO_URL", "").strip()

    if raw:
        lower = raw.lower()

        if "127.0.0.1" in lower or "localhost" in lower:
            logger.warning(
                "EMAIL_LOGO_URL ignored: mail clients cannot load "
                "localhost images. Use a public URL."
            )
            return None

        return raw

    base = get_app_base_url().rstrip("/")
    lower_base = base.lower()

    if "127.0.0.1" in lower_base or "localhost" in lower_base:
        return None

    return f"{base}/static/{get_brand_logo_dark_rel()}"


def get_email_footer_image_url():
    """Public URL for the branded email footer image."""
    raw = os.environ.get("EMAIL_FOOTER_IMAGE_URL", "").strip()

    if raw:
        lower = raw.lower()

        if "127.0.0.1" in lower or "localhost" in lower:
            return None

        return raw

    base = get_app_base_url().rstrip("/")
    lower_base = base.lower()

    if "127.0.0.1" in lower_base or "localhost" in lower_base:
        return None

    return f"{base}/static/{get_brand_email_footer_rel()}"


def _brand_context(language, subject):
    copy = _copy(language)

    return {
        "language": language,
        "subject": subject,
        "brand_name": get_brand_name(),
        "brand_domain": get_app_domain(),
        "footer_tagline": copy["footer_tagline"],
        "logo_url": get_email_logo_url(),
        "email_footer_image_url": get_email_footer_image_url(),
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


def _normalize_recipient(email):
    return (email or "").strip().lower()


def send_transactional_email(
    to_email,
    subject,
    text_body,
    *,
    html_body=None,
):
    """
    Generic transactional send used by verification, approvals,
    and future flows (e.g. password reset).
    """
    to_email = _normalize_recipient(to_email)
    provider = get_email_provider()

    logger.info(
        "email_send_start backend=%s to=%s subject=%s html=%s",
        provider.backend_name,
        mask_email_for_log(to_email),
        subject,
        bool(html_body),
    )

    try:
        provider.send(
            OutboundEmail(
                to=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
            )
        )
    except EmailDeliveryError:
        logger.error(
            "email_send_failed backend=%s to=%s subject=%s",
            provider.backend_name,
            mask_email_for_log(to_email),
            subject,
        )
        raise
    except Exception as exc:
        detail = str(exc)[:200]
        logger.error(
            "email_send_failed backend=%s to=%s detail=%s",
            provider.backend_name,
            mask_email_for_log(to_email),
            detail,
        )
        raise EmailDeliveryError(
            "err_verify_email_send_failed",
            detail=detail,
        ) from exc

    logger.info(
        "email_send_success backend=%s to=%s subject=%s",
        provider.backend_name,
        mask_email_for_log(to_email),
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
        f"{get_brand_name()}\n\n"
        f"{copy['verify_title']}\n\n"
        f"{copy['verify_intro']}\n\n"
        f"{code_text}\n\n"
        f"{copy['verify_expiry']}\n"
        f"{copy['verify_ignore']}\n\n"
        f"{get_brand_name()}\n"
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

    logger.info(
        "verification_email_send_start to=%s",
        mask_email_for_log(to_email),
    )
    try:
        send_transactional_email(
            to_email,
            subject,
            text_body,
            html_body=html_body,
        )
    except EmailDeliveryError:
        logger.error(
            "verification_email_send_failed to=%s",
            mask_email_for_log(to_email),
        )
        raise

    logger.info(
        "verification_email_send_success to=%s",
        mask_email_for_log(to_email),
    )


def send_registration_approved_email(
    to_email,
    language="es",
    first_name=None,
    organization_name=None,
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

    brand = get_brand_name()
    approved_body = copy["approved_body"].format(brand_name=brand)

    text_lines = [brand, "", copy["approved_title"], ""]

    if greeting:
        text_lines.append(greeting)
        text_lines.append("")

    text_lines.append(approved_body)

    if org_name:
        text_lines.append("")
        text_lines.append(
            f"{copy['approved_org_label']} {org_name}"
        )

    text_lines.extend([
        "",
        f"{copy['approved_cta']}: {login_url}",
        "",
        get_brand_name(),
        copy["footer_tagline"],
        "",
    ])
    text_body = "\n".join(text_lines)

    html_body = render_email_html(
        "email/account_approved.html",
        **_brand_context(language, subject),
        title=copy["approved_title"],
        greeting=greeting,
        body_text=approved_body,
        organization_name=org_name or None,
        organization_label=copy["approved_org_label"],
        cta_url=login_url,
        cta_label=copy["approved_cta"],
    )

    send_transactional_email(
        to_email,
        subject,
        text_body,
        html_body=html_body,
    )


def send_registration_rejected_email(
    to_email,
    language="es",
    first_name=None,
    reason=None,
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

    brand = get_brand_name()
    rejected_body = copy["rejected_body"].format(brand_name=brand)

    text_lines = [brand, "", copy["rejected_title"], ""]

    if greeting:
        text_lines.append(greeting)
        text_lines.append("")

    text_lines.append(rejected_body)

    if reason_text:
        text_lines.append("")
        text_lines.append(
            f"{copy['rejected_reason_label']}: {reason_text}"
        )

    text_lines.extend([
        "",
        get_brand_name(),
        copy["footer_tagline"],
        "",
    ])
    text_body = "\n".join(text_lines)

    html_body = render_email_html(
        "email/account_rejected.html",
        **_brand_context(language, subject),
        title=copy["rejected_title"],
        greeting=greeting,
        body_text=rejected_body,
        reason=reason_text or None,
        reason_label=copy["rejected_reason_label"],
    )

    send_transactional_email(
        to_email,
        subject,
        text_body,
        html_body=html_body,
    )


def send_password_reset_email(
    to_email,
    reset_url,
    language="es",
):
    language = _normalize_language(language)
    copy = _copy(language)
    to_email = _normalize_recipient(to_email)
    reset_url = (reset_url or "").strip()

    subject = copy["reset_subject"]
    text_body = (
        f"{get_brand_name()}\n\n"
        f"{copy['reset_title']}\n\n"
        f"{copy['reset_intro']}\n\n"
        f"{reset_url}\n\n"
        f"{copy['reset_expiry']}\n"
        f"{copy['reset_ignore']}\n\n"
        f"{get_brand_name()}\n"
        f"{copy['footer_tagline']}\n"
    )

    html_body = render_email_html(
        "email/password_reset.html",
        **_brand_context(language, subject),
        title=copy["reset_title"],
        intro=copy["reset_intro"],
        cta_url=reset_url,
        cta_label=copy["reset_cta"],
        expiry_text=copy["reset_expiry"],
        ignore_text=copy["reset_ignore"],
    )

    logger.info(
        "password_reset_email_send_start to=%s",
        mask_email_for_log(to_email),
    )
    try:
        send_transactional_email(
            to_email,
            subject,
            text_body,
            html_body=html_body,
        )
    except EmailDeliveryError:
        logger.error(
            "password_reset_email_send_failed to=%s",
            mask_email_for_log(to_email),
        )
        raise

    logger.info(
        "password_reset_email_send_success to=%s",
        mask_email_for_log(to_email),
    )


def send_verification_email(to_email, verify_url):
    send_verification_code_email(
        to_email,
        code=verify_url,
        language="es",
    )
