"""
Render JRH One transactional email templates to HTML files for local preview.

Usage (from remax_commission_calculator/):
    python scripts/preview_emails.py
    # Open tmp/email-previews/*.html in a browser
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.branding import get_brand_name  # noqa: E402
from modules.email_delivery import render_email_html  # noqa: E402

OUT_DIR = ROOT / "tmp" / "email-previews"

SAMPLES = [
    (
        "verification_code.html",
        "email/verification_code.html",
        {
            "title": "Verificá tu correo",
            "intro": "Usá este código para completar tu registro:",
            "code_digits": list("482916"),
            "expiry_text": "Este código vence en 10 minutos.",
            "ignore_text": "Si vos no solicitaste este código, podés ignorar este correo.",
        },
    ),
    (
        "password_reset.html",
        "email/password_reset.html",
        {
            "title": "Restablecé tu contraseña",
            "intro": "Usá este enlace para elegir una nueva contraseña:",
            "cta_url": "https://app.jrhone.com/reset-password/example-token",
            "cta_label": "Restablecer contraseña",
            "expiry_text": "Este enlace vence en 60 minutos.",
            "ignore_text": "Si vos no solicitaste restablecer la contraseña, podés ignorar este correo.",
        },
    ),
    (
        "account_approved.html",
        "email/account_approved.html",
        {
            "title": "¡Tu cuenta fue aprobada!",
            "greeting": "Hola Juan,",
            "body_text": f"Ya podés empezar a usar {get_brand_name()}.",
            "organization_label": "Inmobiliaria:",
            "organization_name": "Demo Brokerage",
            "cta_url": "https://app.jrhone.com/login",
            "cta_label": "Iniciar sesión",
        },
    ),
    (
        "account_rejected.html",
        "email/account_rejected.html",
        {
            "title": "Actualización sobre tu solicitud",
            "greeting": "Hola Juan,",
            "body_text": f"Tu solicitud para acceder a {get_brand_name()} fue rechazada.",
            "reason_label": "Motivo",
            "reason": "Código de inmobiliaria incorrecto.",
        },
    ),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    brand_ctx = {
        "language": "es",
        "subject": "Preview",
        "brand_name": get_brand_name(),
        "brand_domain": "jrhone.com",
        "footer_tagline": "Gestión. Control. Resultados.",
        "logo_url": None,
        "email_footer_image_url": None,
    }

    for filename, template_name, extra in SAMPLES:
        html = render_email_html(template_name, **brand_ctx, **extra)
        path = OUT_DIR / filename
        path.write_text(html, encoding="utf-8")
        print(path)

    print(f"\nPreview files written to {OUT_DIR}")


if __name__ == "__main__":
    main()
