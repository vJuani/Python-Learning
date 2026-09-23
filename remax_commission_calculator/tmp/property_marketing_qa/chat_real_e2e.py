"""QA only: real Marketing IA chat (HTTP routes) against a COPY of the production snapshot.

- production_snapshot.db is never opened for writing: it is copied and its sha256 is checked before/after.
- Copy text uses the mock provider (no OpenAI calls). Rendering is the real pipeline.
- Postgres and SMTP are blocked. Outputs go to tmp/property_marketing_qa/chat_e2e/.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SNAPSHOT = HERE / "production_snapshot.db"
OUT = HERE / "chat_e2e"
WORKING_DB = OUT / "chat_working_copy.db"
LEGACY_MARKERS = ("modern_commercial_v2", "pillow_commercial_v2", "modern_commercial_v3", "stamp_branding_overlay")
PROPERTY_TEMPLATES = {"property_clean_grid", "property_lifestyle_dark", "property_premium_hero"}
CASES = (
    ("post_automatic", "Haceme un post de Santamarina 1335", "automatic"),
    ("story_automatic", "Haceme una historia de Santamarina 1335", "automatic"),
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _block(*_args, **_kwargs):
    raise PermissionError("blocked by chat_real_e2e")


def main(property_id=70, organization_id=1):
    OUT.mkdir(parents=True, exist_ok=True)
    before = _sha256(SNAPSHOT)
    shutil.copy2(SNAPSHOT, WORKING_DB)
    os.environ["DATABASE_URL"] = ""
    os.environ["DATABASE_PATH"] = str(WORKING_DB)
    os.environ["JRH_AI_PROVIDER"] = "mock"
    os.environ["MARKETING_AI_PROVIDER"] = "mock"
    os.environ.pop("OPENAI_API_KEY", None)
    import smtplib

    smtplib.SMTP = _block
    smtplib.SMTP_SSL = _block
    try:
        import psycopg

        psycopg.connect = _block
    except ImportError:
        pass

    log_path = OUT / "chat_log.txt"
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    sys.path.insert(0, str(ROOT))
    from modules.auth import ROLE_AGENT
    from modules.config import apply_config, get_private_upload_root
    from modules.database.marketing_conversations_repository import last_generation_id_for_conversation
    from modules.database.marketing_generations_repository import get_marketing_generation
    from modules.database.marketing_repository import get_marketing_asset
    from web_app import app

    apply_config(app)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with sqlite3.connect(f"file:{WORKING_DB}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT u.id, u.agent_id FROM users u JOIN properties p ON p.agent_id = u.agent_id "
            "WHERE p.id = ? AND p.organization_id = ? AND u.organization_id = ? AND u.role = ? "
            "ORDER BY u.id LIMIT 1",
            (property_id, organization_id, organization_id, ROLE_AGENT),
        ).fetchone()
    if not row:
        raise SystemExit("no agent user for the listing agent")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = row[0]
        session["role"] = ROLE_AGENT
        session["organization_id"] = organization_id
        session["agent_id"] = row[1]

    report = {"property_id": property_id, "organization_id": organization_id, "cases": []}
    for name, prompt, design in CASES:
        response = client.post(
            "/marketing/chat",
            data={"prompt": prompt, "layout_template": design},
            follow_redirects=False,
        )
        location = response.headers.get("Location") or ""
        conversation_id = int(location.rstrip("/").split("/")[-1]) if "/marketing/c/" in location else None
        generation = (
            get_marketing_generation(last_generation_id_for_conversation(conversation_id, organization_id), organization_id)
            if conversation_id
            else None
        ) or {}
        data = generation.get("generated_data") or {}
        assets = [get_marketing_asset(asset_id, organization_id) for asset_id in data.get("visual_asset_ids") or []]
        pngs = []
        for asset in assets:
            source = Path(get_private_upload_root()) / asset["storage_key"]
            target = OUT / f"chat_{name}_{asset['format']}_{(asset.get('options') or {}).get('template_used')}.png"
            shutil.copyfile(source, target)
            pngs.append(str(target.relative_to(ROOT)))
        blob = json.dumps({"data": data, "assets": [a.get("options") for a in assets]}, ensure_ascii=False)
        case = {
            "case": name,
            "prompt": prompt,
            "design": design,
            "http_status": response.status_code,
            "conversation_id": conversation_id,
            "generation_id": generation.get("id"),
            "resolved_property_id": generation.get("property_id"),
            "visual_status": data.get("visual_status"),
            "template_used": data.get("template_used"),
            "renderer_used": data.get("renderer_used"),
            "layout_version": data.get("layout_version"),
            "formats": [a["format"] for a in assets],
            "asset_template_used": [(a.get("options") or {}).get("template_used") for a in assets],
            "asset_renderer_used": [(a.get("options") or {}).get("renderer_used") for a in assets],
            "legacy_markers_found": [m for m in LEGACY_MARKERS if m in blob],
            "pngs": pngs,
        }
        case["ok"] = bool(
            case["http_status"] == 302
            and case["resolved_property_id"] == property_id
            and case["visual_status"] == "completed"
            and case["template_used"] in PROPERTY_TEMPLATES
            and case["renderer_used"] == "html_playwright"
            and case["layout_version"] == case["template_used"]
            and all(t in PROPERTY_TEMPLATES for t in case["asset_template_used"])
            and all(r == "html_playwright" for r in case["asset_renderer_used"])
            and not case["legacy_markers_found"]
        )
        report["cases"].append(case)
    after = _sha256(SNAPSHOT)
    report["snapshot_unchanged"] = before == after
    report["snapshot_sha256"] = after
    report["route_log_lines"] = sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if "[MARKETING_RENDER_ROUTE]" in line)
    report["blocked_lines"] = sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if "LEGACY_ROUTE_BLOCKED" in line)
    report["ok"] = report["snapshot_unchanged"] and all(case["ok"] for case in report["cases"])
    (OUT / "chat_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
