"""QA screenshots for the visit-close sheet. Throwaway database only."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "visit_close_qa.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, str(ROOT))

BASE = "http://localhost:5998"
WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)


def seed():
    from modules.agent_tasks import create_task
    from modules.auth import ROLE_AGENT, hash_password
    from modules.contacts import create_agent_contact
    from modules.database import add_agent, add_organization, add_property, add_user, create_tables
    from modules.organization_time import organization_timezone

    create_tables()
    org = add_organization("Visit Close QA")
    agent = add_agent("QA Agent", "Alto", org)
    user = add_user("visit_qa", hash_password("Password1"), ROLE_AGENT, org, agent_id=agent)
    contact = create_agent_contact(
        org,
        agent,
        {"name": "Martín Pérez", "phone": "1155550000", "status": "lead", "source": "manual"},
    )
    property_id = add_property("Santamarina 1335", "CABA", org, agent_id=agent)
    local = datetime.now(organization_timezone(org))
    task = create_task(
        org,
        agent,
        {
            "title": "Visita Santamarina",
            "task_type": "visit",
            "due_date": local.date().isoformat(),
            "due_time": local.strftime("%H:%M"),
            "contact_id": contact["id"],
            "contact_name": contact["name"],
            "property_id": property_id,
        },
    )
    return org, user, agent, contact, task


def main():
    from playwright.sync_api import sync_playwright

    from modules.auth import ROLE_AGENT
    from modules.config import apply_config
    from modules.database.contacts_repository import get_contact
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    org, user, agent, contact, task = seed()
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
            print("REDIRECT", response.status_code, "->", path)
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

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        context, page = open_page(browser, {"width": 1280, "height": 900}, ua=WINDOWS_UA)
        page.goto(f"{BASE}/agenda")
        page.get_by_role("button", name="Cerrar visita").click()
        page.wait_for_selector("#visit-close-sheet[open]")
        page.locator("#visit-close-sheet input[value=liked]").check()
        page.screenshot(path=str(HERE / "close_desktop.png"))
        report["desktop_title"] = page.locator("#visit-close-sheet h2").inner_text()
        context.close()

        context, page = open_page(
            browser, {"width": 390, "height": 844}, ua=IPHONE_UA, mobile=True
        )
        page.goto(f"{BASE}/agenda")
        page.get_by_role("button", name="Cerrar visita").click()
        page.wait_for_selector("#visit-close-sheet[open]")
        page.locator("#visit-close-sheet input[value=liked]").check()
        page.locator("#visit-close-sheet textarea[name=note]").fill(
            "Le gustó pero quiere cochera. El presupuesto máximo es USD 220.000."
        )
        page.locator("#visit-close-sheet select[name=next_step]").select_option("call")
        page.screenshot(path=str(HERE / "close_mobile.png"))
        report["mobile_no_hscroll"] = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        page.locator("#visit-close-sheet button[type=submit]").click()
        page.wait_for_timeout(1500)
        page.wait_for_selector(".visit-close__changes")
        page.screenshot(path=str(HERE / "need_mobile.png"))
        report["need_text"] = page.locator(".visit-close__changes").inner_text()
        stored = get_contact(contact["id"], org)
        report["prefs_before_choice"] = stored.get("preferences_json") or ""
        context.close()

        context, page = open_page(browser, {"width": 1280, "height": 900}, ua=WINDOWS_UA)
        page.goto(f"{BASE}/agenda/{task['id']}/visit-need")
        page.wait_for_selector("text=Detecté cambios en la búsqueda")
        page.screenshot(path=str(HERE / "need_desktop.png"))
        page.get_by_role("button", name="No actualizar").click()
        page.wait_for_selector("text=Buenas")
        stored = get_contact(contact["id"], org)
        report["prefs_after_skip"] = stored.get("preferences_json") or ""
        page.goto(f"{BASE}/agenda/{task['id']}/visit-need")
        page.get_by_role("button", name="Guardar cambios").click()
        page.wait_for_selector("text=Buenas")
        stored = get_contact(contact["id"], org)
        report["prefs_after_save"] = stored.get("preferences_json") or ""
        context.close()
        browser.close()

    print(report)


if __name__ == "__main__":
    main()
