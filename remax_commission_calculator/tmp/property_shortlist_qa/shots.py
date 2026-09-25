"""QA screenshots for the property shortlist. Throwaway database only."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "shortlist_qa.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, str(ROOT))

BASE = "http://localhost:5997"
WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)


def seed():
    from modules.auth import ROLE_AGENT, hash_password
    from modules.contacts import create_agent_contact
    from modules.database import add_agent, add_organization, add_property, add_user, create_tables
    from modules.database.connection import get_connection

    create_tables()
    org = add_organization("Shortlist QA")
    agent = add_agent("QA Agent", "Alto", org)
    user = add_user("short_qa", hash_password("Password1"), ROLE_AGENT, org, agent_id=agent)
    contact = create_agent_contact(
        org,
        agent,
        {
            "name": "Martín Pérez",
            "phone": "5491155550000",
            "preferences": {
                "areas": ["Martínez"],
                "budget": {"max": 220000, "currency": "USD"},
                "property_types": ["departamento"],
                "rooms": 3,
                "bedrooms": 2,
                "features": ["cochera"],
                "purpose": "sale",
            },
        },
    )
    ids = []
    for address, hood, price, rooms, beds, parking in (
        ("Alvear 450", "Martínez", 190000, 4, 2, None),
        ("Av. Santa Fe 2100", "Martínez", 205000, 3, 2, 1),
        ("Libertador 800", "Palermo", 180000, 3, 2, 1),
    ):
        property_id = add_property(
            address,
            "Buenos Aires",
            org,
            agent_id=agent,
            neighborhood=hood,
            property_type="apartment",
            listing_price=price,
            listing_currency="USD",
            listing_purpose="sale",
            rooms=rooms,
            bedrooms=beds,
            parking_spaces=parking,
        )
        ids.append(property_id)
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE properties SET external_url = ? WHERE id = ?",
            ("https://www.remax.com.ar/listings/alvear-450", ids[0]),
        )
        connection.execute(
            "UPDATE properties SET external_url = ? WHERE id = ?",
            ("https://www.remax.com.ar/listings/santa-fe-2100", ids[1]),
        )
        connection.commit()
    finally:
        connection.close()
    return org, user, agent, contact


def main():
    from playwright.sync_api import sync_playwright

    from modules.auth import ROLE_AGENT
    from modules.config import apply_config
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    org, user, agent, contact = seed()
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=user, role=ROLE_AGENT, organization_id=org, agent_id=agent)

    def serve(route):
        request = route.request
        parts = urlsplit(request.url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        response = client.open(
            path,
            method=request.method,
            data=request.post_data_buffer,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() in ("content-type", "accept")
            },
        )
        hops = 0
        while response.status_code in (301, 302, 303, 307, 308) and hops < 5:
            hops += 1
            parsed = urlsplit(response.headers.get("Location") or "/")
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            response = client.get(path)
        headers = {"content-type": response.headers.get("Content-Type", "text/html")}
        route.fulfill(status=response.status_code, body=response.get_data(), headers=headers)

    report = {"errors": []}

    def open_page(browser, viewport, *, ua, mobile=False):
        context = browser.new_context(
            viewport=viewport,
            device_scale_factor=2,
            user_agent=ua,
            is_mobile=mobile,
            has_touch=mobile,
            service_workers="block",
        )
        context.add_init_script(
            "try{localStorage.setItem('jrh_push_prompt_dismissed_at', String(Date.now()))}catch(e){}"
        )
        page = context.new_page()
        page.route("**/*", serve)
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        return context, page

    def dismiss_prompt(page):
        dismiss = page.get_by_role("button", name="Ahora no")
        if dismiss.count():
            dismiss.click()

    def select_three(page):
        page.goto(f"{BASE}/contacts/{contact['id']}")
        page.wait_for_selector(".recommend-card")
        dismiss_prompt(page)
        boxes = page.locator(".recommend-card input[type=checkbox]")
        boxes.nth(0).check()
        boxes.nth(1).check()
        boxes.nth(2).check()
        page.locator("[data-share-submit]").wait_for(state="visible")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        context, page = open_page(browser, {"width": 1280, "height": 1100}, ua=WINDOWS_UA)
        select_three(page)
        page.locator(".recommend-panel").scroll_into_view_if_needed()
        page.screenshot(path=str(HERE / "select_desktop.png"), full_page=True)
        context.close()

        context, page = open_page(
            browser, {"width": 390, "height": 844}, ua=IPHONE_UA, mobile=True
        )
        select_three(page)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.screenshot(path=str(HERE / "select_mobile.png"))
        report["select_bar"] = page.locator("[data-share-submit]").inner_text()
        context.close()

        context, page = open_page(browser, {"width": 1280, "height": 1100}, ua=WINDOWS_UA)
        select_three(page)
        page.locator("[data-share-submit]").click()
        page.wait_for_selector("#shortlist-message")
        page.screenshot(path=str(HERE / "shortlist_desktop.png"), full_page=True)
        report["message"] = page.locator("#shortlist-message").input_value()
        report["desktop_no_hscroll"] = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        page.locator("button", has_text="Abrir WhatsApp").click()
        page.wait_for_selector(".shortlist-follow")
        page.screenshot(path=str(HERE / "sent_desktop.png"), full_page=True)
        context.close()

        context, page = open_page(
            browser, {"width": 390, "height": 844}, ua=IPHONE_UA, mobile=True
        )
        page.goto(f"{BASE}/contacts/{contact['id']}/shortlist/sent")
        page.wait_for_selector(".shortlist-follow")
        page.screenshot(path=str(HERE / "sent_mobile.png"))
        page.goto(f"{BASE}/contacts/{contact['id']}/shortlist")
        page.wait_for_selector("#shortlist-message")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.screenshot(path=str(HERE / "shortlist_mobile.png"))
        report["mobile_no_hscroll"] = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        report["open_visible"] = page.locator(".shortlist-bar .btn").is_visible()
        context.close()
        browser.close()

    print({key: value for key, value in report.items() if key != "message"})
    print("---MESSAGE---")
    print(report["message"])


if __name__ == "__main__":
    main()
