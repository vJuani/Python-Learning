"""QA only: screenshots of the push prompt, Mis dispositivos and the notification center.

Uses a throwaway SQLite database in this folder; never touches real data.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "notifications_qa.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["NOTIFICATION_DISPATCHER"] = "off"
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, str(ROOT))

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)
BASE = "http://localhost:5999"


def _iso(delta):
    return (datetime.utcnow() - delta).replace(microsecond=0).isoformat()


def seed():
    from modules.auth import ROLE_AGENT, hash_password
    from modules.database import add_agent, add_organization, add_user, create_tables
    from modules.database.connection import get_connection
    from modules.database.notifications_repository import create_notification
    from modules.database.push_subscriptions_repository import upsert_push_subscription
    from modules.pwa_routes import _device_label

    create_tables()
    org = add_organization("Oficina Demo")
    agent = add_agent("Lucía Demo", "Alto", org)
    user = add_user("lucia_demo", hash_password("Password1"), ROLE_AGENT, org, agent_id=agent)

    items = [
        ("visit_reminder", "Visita en 30 minutos", "Av. Libertador 4200, 3°B — con Martín Gómez", "important", timedelta(minutes=12), False),
        ("operation_approved", "Operación aprobada", "Venta Palermo Soho · #1042 fue aprobada por la oficina", "info", timedelta(hours=1, minutes=5), False),
        ("task_overdue", "Tarea vencida: llamar a propietario", "Seguimiento de tasación en Belgrano R con un título bastante largo para probar el recorte", "urgent", timedelta(hours=3), False),
        ("property_match", "3 nuevas coincidencias", "Clientes interesados en 2 ambientes en Núñez", "info", timedelta(hours=5), True),
        ("invoice_ready", "Lista para facturar", "Operación #1038 lista para emitir factura", "important", timedelta(days=1, hours=2), False),
        ("office_announcement", "Reunión de oficina", "Mañana 9:30 en sala principal", "info", timedelta(days=1, hours=6), True),
        ("agenda_changed", "Visita reprogramada", "Coghlan 3100 pasó al jueves 16:00", "info", timedelta(days=3), True),
        ("contact_assigned", "Nuevo contacto asignado", "Carolina Pérez te fue asignada", "info", timedelta(days=4), True),
    ]
    for index in range(22):
        items.append(("office_announcement", f"Aviso de oficina #{index + 1}", "Novedad semanal", "info", timedelta(days=6 + index), True))

    updates = []
    for kind, title, body, priority, age, read in reversed(items):
        notification_id = create_notification(
            org, user, kind, "notification", 0,
            payload={"title": title, "body": body, "priority": priority},
            priority=priority,
        )
        updates.append((_iso(age), 1 if read else 0, notification_id))

    devices = [
        ("https://push.example.com/qa-windows", WINDOWS_UA, True, timedelta(minutes=3)),
        ("https://push.example.com/qa-iphone", IPHONE_UA, True, timedelta(hours=20)),
        ("https://push.example.com/qa-android", ANDROID_UA, False, timedelta(days=9)),
    ]
    device_updates = []
    for endpoint, ua, active, age in devices:
        row = upsert_push_subscription(
            org, user, endpoint=endpoint,
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345", user_agent=ua, device_label=_device_label(ua),
        )
        device_updates.append((1 if active else 0, _iso(age), row["id"]))

    connection = get_connection()
    try:
        for params in updates:
            connection.execute(
                "UPDATE notifications SET created_at = ?, is_read = ? WHERE id = ?", params
            )
        for params in device_updates:
            connection.execute(
                "UPDATE push_subscriptions SET is_active = ?, updated_at = ? WHERE id = ?", params
            )
        connection.commit()
    finally:
        connection.close()
    return org, user, agent


def main():
    from playwright.sync_api import sync_playwright

    from modules.auth import ROLE_AGENT
    from modules.config import apply_config
    from modules.pwa_routes import PUSH_DEVICE_SESSION_KEY
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    org, user, agent = seed()
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=user, role=ROLE_AGENT, organization_id=org, agent_id=agent)
        session[PUSH_DEVICE_SESSION_KEY] = "https://push.example.com/qa-windows"

    def serve(route):
        request = route.request
        parts = urlsplit(request.url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        response = client.open(
            path,
            method=request.method,
            data=request.post_data_buffer,
            headers={k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept")},
        )
        headers = {"content-type": response.headers.get("Content-Type", "text/html")}
        if response.headers.get("Location"):
            headers["location"] = response.headers["Location"]
        route.fulfill(status=response.status_code, body=response.get_data(), headers=headers)

    report = {"errors": [], "checks": {}}

    def open_page(browser, viewport, *, ua=None, dismiss_prompt=True, mobile=False):
        context = browser.new_context(
            viewport=viewport, device_scale_factor=2, user_agent=ua,
            is_mobile=mobile, has_touch=mobile, service_workers="block",
        )
        if dismiss_prompt:
            context.add_init_script(
                "try{localStorage.setItem('jrh_push_prompt_dismissed_at', String(Date.now()))}catch(e){}"
            )
        else:
            # Headless Chromium reports "denied"; a first-time real user sees "default".
            context.add_init_script(
                "if(window.Notification){Object.defineProperty(Notification,'permission',{get:function(){return 'default'}})}"
            )
        page = context.new_page()
        page.route(f"{BASE}/**", serve)
        page.on("pageerror", lambda error: report["errors"].append(f"pageerror: {error}"))
        page.on(
            "console",
            lambda msg: report["errors"].append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error" else None,
        )
        return context, page

    def no_hscroll(page):
        return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        # 1. Soft prompt, Android/desktop flavour.
        context, page = open_page(browser, {"width": 412, "height": 860}, ua=ANDROID_UA, dismiss_prompt=False, mobile=True)
        page.goto(f"{BASE}/pendings")
        page.wait_for_selector("#push-soft-prompt:not([hidden])", timeout=8000)
        page.screenshot(path=str(HERE / "prompt_android.png"))
        report["checks"]["prompt_android_text"] = page.inner_text("#push-soft-prompt-text")
        page.click("#push-soft-prompt-later")
        report["checks"]["prompt_hidden_after_later"] = page.is_hidden("#push-soft-prompt")
        page.reload()
        page.wait_for_timeout(1500)
        report["checks"]["prompt_snoozed_after_reload"] = page.is_hidden("#push-soft-prompt")
        context.close()

        # 2. Soft prompt on iPhone Safari (not installed).
        context, page = open_page(browser, {"width": 390, "height": 844}, ua=IPHONE_UA, dismiss_prompt=False, mobile=True)
        page.goto(f"{BASE}/pendings")
        page.wait_for_selector("#push-soft-prompt:not([hidden])", timeout=8000)
        page.screenshot(path=str(HERE / "prompt_iphone.png"))
        report["checks"]["prompt_iphone_text"] = page.inner_text("#push-soft-prompt-text")
        report["checks"]["prompt_iphone_accept_hidden"] = page.is_hidden("#push-soft-prompt-accept")
        context.close()

        # 3. Mis dispositivos (desktop + mobile).
        context, page = open_page(browser, {"width": 1366, "height": 900}, ua=WINDOWS_UA)
        page.goto(f"{BASE}/settings/notifications")
        card = page.locator(".push-devices-card")
        card.scroll_into_view_if_needed()
        card.screenshot(path=str(HERE / "devices_desktop.png"))
        context.close()
        context, page = open_page(browser, {"width": 390, "height": 844}, ua=IPHONE_UA, mobile=True)
        page.goto(f"{BASE}/settings/notifications")
        card = page.locator(".push-devices-card")
        card.scroll_into_view_if_needed()
        card.screenshot(path=str(HERE / "devices_mobile.png"))
        report["checks"]["devices_mobile_no_hscroll"] = no_hscroll(page)
        context.close()

        # 4. Center desktop.
        context, page = open_page(browser, {"width": 1366, "height": 900}, ua=WINDOWS_UA)
        page.goto(f"{BASE}/notifications")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(HERE / "center_desktop.png"), full_page=False)
        report["checks"]["desktop_cards_initial"] = page.locator("[data-notification-id]").count()
        page.click("#notif-load-more")
        page.wait_for_function("document.querySelectorAll('[data-notification-id]').length > 20")
        report["checks"]["desktop_cards_after_load_more"] = page.locator("[data-notification-id]").count()
        report["checks"]["load_more_hidden_at_end"] = page.is_hidden("#notif-load-more")
        before = page.inner_text(".nav-bell-link [data-unread-badge]") if page.locator(".nav-bell-link [data-unread-badge]").count() else None
        page.locator(".notif-card.is-unread [data-mark-read] button").first.click()
        page.wait_for_timeout(600)
        report["checks"]["bell_before_mark_one"] = before
        report["checks"]["unread_badges_after_mark_one"] = page.eval_on_selector_all(
            "[data-unread-badge]", "nodes => nodes.map(n => n.hidden ? 'hidden' : n.textContent)"
        )
        context.close()

        # 5. Center mobile.
        context, page = open_page(browser, {"width": 390, "height": 844}, ua=IPHONE_UA, mobile=True)
        page.goto(f"{BASE}/notifications")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(HERE / "center_mobile.png"), full_page=False)
        page.screenshot(path=str(HERE / "center_mobile_full.png"), full_page=True)
        report["checks"]["mobile_no_hscroll"] = no_hscroll(page)
        page.goto(f"{BASE}/notifications?filter=unread")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(HERE / "center_mobile_unread.png"), full_page=False)
        page.click("[data-mark-all] button")
        page.wait_for_timeout(700)
        report["checks"]["after_mark_all_badges"] = page.eval_on_selector_all(
            "[data-unread-badge]", "nodes => nodes.map(n => n.hidden ? 'hidden' : n.textContent)"
        )
        report["checks"]["after_mark_all_empty_visible"] = page.is_visible("#notif-empty")
        page.screenshot(path=str(HERE / "center_mobile_all_read.png"), full_page=False)
        context.close()

        browser.close()

    (HERE / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
