"""QA screenshots for Property Matcher V1. Throwaway database only."""

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
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "property_match_qa.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, str(ROOT))

BASE = "http://localhost:5999"
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
    from modules.database.contacts_repository import record_property_interaction
    from modules.property_match import decorate_match, rank_contact_properties

    create_tables()
    org = add_organization("Matcher QA")
    agent = add_agent("QA Agent", "Alto", org)
    user = add_user("match_qa", hash_password("Password1"), ROLE_AGENT, org, agent_id=agent)
    contact = create_agent_contact(
        org,
        agent,
        {
            "name": "Martín Pérez",
            "phone": "5491155550000",
            "preferences": {
                "areas": ["Martínez"],
                "budget": {"max": 200000, "currency": "USD"},
                "property_types": ["departamento"],
                "rooms": 3,
                "bedrooms": 2,
                "features": ["cochera", "pileta"],
                "purpose": "sale",
            },
        },
    )
    first = add_property(
        "Av. Santa Fe 2100",
        "Buenos Aires",
        org,
        agent_id=agent,
        neighborhood="Martínez",
        property_type="apartment",
        listing_price=205000,
        listing_currency="USD",
        listing_purpose="sale",
        rooms=3,
        bedrooms=2,
        parking_spaces=1,
        features={"pool": False},
    )
    second = add_property(
        "Alvear 450",
        "Buenos Aires",
        org,
        agent_id=agent,
        neighborhood="Martinez",
        property_type="apartment",
        listing_price=190000,
        listing_currency="USD",
        listing_purpose="sale",
        rooms=4,
        bedrooms=2,
        features={"balcony": True},
    )
    third = add_property(
        "Libertador 800",
        "CABA",
        org,
        agent_id=agent,
        neighborhood="Palermo",
        property_type="apartment",
        listing_price=180000,
        listing_currency="USD",
        listing_purpose="sale",
        rooms=3,
        bedrooms=2,
        parking_spaces=1,
        features={"pool": True},
    )
    record_property_interaction(
        org,
        agent,
        contact_id=contact["id"],
        property_id=second,
        interaction_type="visited",
    )
    ranked = rank_contact_properties(org, contact, agent_id=agent)
    examples = []
    for row in ranked[:3]:
        card = decorate_match(row, language="es")
        examples.append(
            {
                "address": card["listing"].get("address"),
                "score": card["score"],
                "history": card["history_label"],
                "reasons": card["reasons"],
            }
        )
    return org, user, agent, contact, examples


def main():
    from playwright.sync_api import sync_playwright

    from modules.auth import ROLE_AGENT
    from modules.config import apply_config
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    org, user, agent, contact, examples = seed()
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

    report = {"errors": [], "examples": examples}

    def open_page(browser, viewport, *, ua, mobile=False):
        context = browser.new_context(
            viewport=viewport,
            device_scale_factor=2,
            user_agent=ua,
            is_mobile=mobile,
            has_touch=mobile,
            service_workers="block",
        )
        page = context.new_page()
        page.route("**/*", serve)
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        return context, page

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        context, page = open_page(browser, {"width": 1280, "height": 1100}, ua=WINDOWS_UA)
        page.goto(f"{BASE}/contacts/{contact['id']}")
        page.wait_for_selector(".recommend-card")
        dismiss = page.get_by_role("button", name="Ahora no")
        if dismiss.count():
            dismiss.click()
        page.locator(".recommend-panel").scroll_into_view_if_needed()
        page.screenshot(path=str(HERE / "detail_desktop.png"), full_page=True)
        context.close()

        context, page = open_page(
            browser, {"width": 390, "height": 844}, ua=IPHONE_UA, mobile=True
        )
        page.goto(f"{BASE}/contacts/{contact['id']}")
        page.wait_for_selector(".recommend-card")
        page.locator(".recommend-panel").screenshot(path=str(HERE / "detail_mobile.png"))
        report["mobile_no_hscroll"] = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        page.goto(f"{BASE}/contacts/{contact['id']}/property-matches")
        page.wait_for_selector("text=Santa Fe")
        page.screenshot(path=str(HERE / "matches_mobile.png"), full_page=True)
        report["matches_no_hscroll"] = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        context.close()
        browser.close()

    print(report)


if __name__ == "__main__":
    main()
