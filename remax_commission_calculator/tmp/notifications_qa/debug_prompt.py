"""QA only: why the soft prompt stays hidden in headless Chromium."""

import shots  # noqa: F401  (sets env + sys.path)
from shots import ANDROID_UA, BASE, seed


def main():
    from playwright.sync_api import sync_playwright

    from modules.auth import ROLE_AGENT
    from modules.config import apply_config
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True)
    org, user, agent = seed()
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=user, role=ROLE_AGENT, organization_id=org, agent_id=agent)

    def serve(route):
        from urllib.parse import urlsplit

        parts = urlsplit(route.request.url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        response = client.open(path, method=route.request.method, data=route.request.post_data_buffer,
                               headers={"content-type": route.request.headers.get("content-type", "")})
        route.fulfill(status=response.status_code, body=response.get_data(),
                      headers={"content-type": response.headers.get("Content-Type", "text/html")})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=ANDROID_UA, service_workers="block")
        page = context.new_page()
        page.on("pageerror", lambda e: print("pageerror", e))
        page.on("console", lambda m: print("console", m.type, m.text))
        page.route(f"{BASE}/**", serve)
        page.goto(f"{BASE}/pendings")
        page.wait_for_timeout(3000)
        print(page.evaluate("""() => ({
            permission: ('Notification' in window) ? Notification.permission : 'missing',
            secure: window.isSecureContext,
            sw: 'serviceWorker' in navigator,
            eligible: !!document.querySelector('[data-push-eligible]'),
            prompt: !!document.getElementById('push-soft-prompt'),
            hidden: document.getElementById('push-soft-prompt') && document.getElementById('push-soft-prompt').hidden,
        })"""))
        browser.close()


if __name__ == "__main__":
    main()
