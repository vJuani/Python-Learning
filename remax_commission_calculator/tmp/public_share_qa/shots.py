"""Screenshots for public property and shortlist pages. Throwaway database."""

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
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "public_share_qa.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, str(ROOT))

BASE = "http://localhost:5996"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
    b"\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def seed():
    from modules.auth import ROLE_AGENT, hash_password
    from modules.contacts import create_agent_contact
    from modules.database import add_agent, add_organization, add_property, add_user, create_tables
    from modules.database.connection import get_connection
    from modules.database.organization_settings_repository import ensure_organization_settings
    from modules.database.property_media_repository import MEDIA_PHOTO, STRATEGY_COPY, upsert_property_media
    from modules.config import get_private_upload_root
    from modules.property_shortlist import draft_whatsapp_message
    from modules.public_share import ensure_property_link, ensure_public_shortlist, prepare_client_links

    create_tables()
    org = add_organization("Norte")
    agent = add_agent("Ana López", "Alto", org)
    user_id = add_user("ana", hash_password("Password1"), ROLE_AGENT, org, agent_id=agent)
    ensure_organization_settings(org, "Inmobiliaria Norte")
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE organization_settings
            SET legal_footer_line = ?, display_name = ?
            WHERE organization_id = ?
            """,
            ("Mat. 1234 CUCICBA", "Inmobiliaria Norte", org),
        )
        connection.execute(
            """
            UPDATE users
            SET phone = ?, first_name = ?, last_name = ?
            WHERE id = ?
            """,
            ("5491112345678", "Ana", "López", user_id),
        )
        connection.commit()
    finally:
        connection.close()
    contact = create_agent_contact(
        org,
        agent,
        {"name": "Martín Pérez", "phone": "5491155550000"},
    )
    folder = get_private_upload_root() / "qa"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cover.png").write_bytes(PNG)
    ids = []
    for address, hood, price, status in (
        ("Alvear 450", "Martínez", 190000, "available"),
        ("Av. Santa Fe 2100", "Martínez", 205000, "available"),
        ("Libertador 800", "Palermo", 180000, "sold"),
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
            rooms=3,
            bedrooms=2,
            bathrooms=2,
            parking_spaces=1,
            description="Departamento luminoso con balcón.",
        )
        ids.append(property_id)
        upsert_property_media(
            org,
            property_id,
            source="manual",
            media_type=MEDIA_PHOTO,
            storage_key="qa/cover.png",
            storage_strategy=STRATEGY_COPY,
            position=0,
            is_cover=True,
            content_type="image/png",
            content_hash=f"qa-{property_id}",
        )
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE properties SET commercial_status = ? WHERE id = ?",
            ("sold", ids[2]),
        )
        connection.commit()
    finally:
        connection.close()
    link = ensure_property_link(org, ids[0], agent_id=agent)
    collection = ensure_public_shortlist(
        org,
        ids,
        contact_id=contact["id"],
        agent_id=agent,
        days=30,
    )
    items = [
        {"property_id": ids[0], "address": "Alvear 450", "neighborhood": "Martínez", "price_label": "USD 190.000", "rooms": 4, "bedrooms": 2, "parking": False},
        {"property_id": ids[1], "address": "Av. Santa Fe 2100", "neighborhood": "Martínez", "price_label": "USD 205.000", "rooms": 3, "bedrooms": 2, "parking": True},
    ]
    collection_url, _token = prepare_client_links(
        org,
        items,
        agent_id=agent,
        base_url="https://app.example",
        mode="collection",
        contact_id=contact["id"],
        collection_token=collection["token"],
    )
    message = draft_whatsapp_message(contact, items, collection_url=collection_url)
    return link["token"], collection["token"], message


def main():
    from playwright.sync_api import sync_playwright

    from modules.config import apply_config
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    property_token, shortlist_token, message = seed()
    client = app.test_client()

    def serve(route):
        request = route.request
        parts = urlsplit(request.url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        response = client.get(path)
        route.fulfill(
            status=response.status_code,
            body=response.get_data(),
            headers={"content-type": response.headers.get("Content-Type", "text/html")},
        )

    report = {"message": message}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        shots = (
            (f"/p/{property_token}", "property_desktop.png", {"width": 1280, "height": 900}, False),
            (f"/p/{property_token}", "property_mobile.png", {"width": 390, "height": 844}, True),
            (f"/s/{shortlist_token}", "shortlist_desktop.png", {"width": 1280, "height": 900}, False),
            (f"/s/{shortlist_token}", "shortlist_mobile.png", {"width": 390, "height": 844}, True),
        )
        for path, name, viewport, mobile in shots:
            context = browser.new_context(
                viewport=viewport,
                device_scale_factor=2,
                is_mobile=mobile,
                has_touch=mobile,
                service_workers="block",
            )
            page = context.new_page()
            page.route("**/*", serve)
            page.goto(BASE + path)
            page.wait_for_selector("h1")
            page.screenshot(path=str(HERE / name), full_page=True)
            report[name] = page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth + 1"
            )
            context.close()
        browser.close()
    print({key: value for key, value in report.items() if key != "message"})
    print("---MESSAGE---")
    print(message)


if __name__ == "__main__":
    main()
