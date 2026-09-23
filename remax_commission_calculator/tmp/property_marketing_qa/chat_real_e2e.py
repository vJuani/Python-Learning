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
LEGACY_MARKERS = (
    "modern_commercial_v1",
    "modern_commercial_v2",
    "modern_commercial_v3",
    "pillow_commercial_v2",
    "stamp_branding_overlay",
)
VARIANTS = ["property_clean_grid", "property_lifestyle_dark", "property_premium_hero"]
PROMPT = "Haceme un post de Santamarina 1335"


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
    import modules.marketing_chat_service as chat_service
    import modules.property_marketing as property_marketing
    from modules.auth import ROLE_AGENT
    from modules.config import apply_config, get_private_upload_root
    from modules.database.marketing_conversations_repository import last_generation_id_for_conversation
    from modules.database.marketing_generations_repository import get_marketing_generation
    from modules.database.marketing_repository import get_marketing_asset, list_generation_assets
    from web_app import app

    copy_calls = []
    photo_loads = []
    real_copy = chat_service.create_and_run_generation
    real_load = property_marketing.load_original_media_bytes

    def _count_copy(*args, **kwargs):
        copy_calls.append(kwargs.get("content_type"))
        return real_copy(*args, **kwargs)

    def _count_load(item, **kwargs):
        photo_loads.append((item or {}).get("storage_key") or (item or {}).get("original_url"))
        return real_load(item, **kwargs)

    chat_service.create_and_run_generation = _count_copy
    property_marketing.load_original_media_bytes = _count_load

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

    def _last(conversation_id):
        return get_marketing_generation(
            last_generation_id_for_conversation(conversation_id, organization_id), organization_id
        ) or {}

    def _step(name, response, generation, expected):
        data = generation.get("generated_data") or {}
        rows = data.get("visual_variants") or []
        assets = list_generation_assets(organization_id, data.get("generation_group_id")) if data.get("generation_group_id") else []
        pngs = []
        for row in rows:
            if row.get("status") != "ready":
                continue
            asset = get_marketing_asset(row["asset_id"], organization_id)
            target = OUT / f"chat_{name}_{row['template_used']}.png"
            shutil.copyfile(Path(get_private_upload_root()) / asset["storage_key"], target)
            pngs.append(str(target.relative_to(ROOT)))
        blob = json.dumps({"data": data, "assets": [a.get("options") for a in assets]}, ensure_ascii=False)
        step = {
            "step": name,
            "http_status": response.status_code,
            "generation_id": generation.get("id"),
            "generation_group_id": data.get("generation_group_id"),
            "resolved_property_id": generation.get("property_id"),
            "selected_template": data.get("selected_template"),
            "variant_count": len(rows),
            "group_asset_count": len(assets),
            "variants": [
                {
                    key: row.get(key)
                    for key in ("variant_id", "template_used", "renderer_used", "layout_version", "property_id", "generation_group_id", "status")
                }
                for row in rows
            ],
            "same_copy": len({json.dumps(a.get("copy_snapshot"), sort_keys=True) for a in assets}) == 1,
            "same_photos": len({tuple((a.get("options") or {}).get("photo_ids") or []) for a in assets}) == 1,
            "legacy_markers_found": [m for m in LEGACY_MARKERS if m in blob],
            "pngs": pngs,
        }
        step["ok"] = bool(
            step["http_status"] == 302
            and step["resolved_property_id"] == property_id
            and [row["template_used"] for row in step["variants"]] == expected
            and step["group_asset_count"] == len(expected)
            and all(row["renderer_used"] == "html_playwright" for row in step["variants"])
            and all(row["layout_version"] == row["template_used"] for row in step["variants"])
            and all(row["property_id"] == property_id for row in step["variants"])
            and step["same_copy"]
            and step["same_photos"]
            and not step["legacy_markers_found"]
        )
        return step

    report = {"property_id": property_id, "organization_id": organization_id, "steps": []}
    response = client.post("/marketing/chat", data={"prompt": PROMPT}, follow_redirects=False)
    location = response.headers.get("Location") or ""
    conversation_id = int(location.rstrip("/").split("/")[-1])
    report["conversation_id"] = conversation_id
    first = _last(conversation_id)
    report["copy_calls_first_post"] = len(copy_calls)
    report["photo_reads_first_post"] = len(photo_loads)
    report["photo_reads_unique_first_post"] = len(set(photo_loads))
    report["steps"].append(_step("post", response, first, VARIANTS))
    page = client.get(location).get_data(as_text=True)
    (OUT / "chat_page.html").write_text(page, encoding="utf-8")
    report["page_choose_buttons"] = page.count("Elegir este diseño")

    premium = next(
        row for row in (first.get("generated_data") or {}).get("visual_variants") or []
        if row["template_used"] == "property_premium_hero"
    )
    response = client.post(
        f"/marketing/c/{conversation_id}/select-variant",
        data={"generation_id": first["id"], "asset_id": premium["asset_id"]},
        follow_redirects=False,
    )
    chosen = get_marketing_generation(first["id"], organization_id)
    report["selected_template"] = (chosen.get("generated_data") or {}).get("selected_template")

    response = client.post(
        f"/marketing/c/{conversation_id}/regenerate",
        data={"generation_id": first["id"]},
        follow_redirects=False,
    )
    report["steps"].append(_step("regenerate_selected", response, _last(conversation_id), ["property_premium_hero"]))

    response = client.post(
        f"/marketing/c/{conversation_id}", data={"prompt": "Mostrame las tres de nuevo"}, follow_redirects=False
    )
    report["steps"].append(_step("show_all_again", response, _last(conversation_id), VARIANTS))

    from PIL import Image

    sheet_sources = [OUT / f"chat_post_{name}.png" for name in VARIANTS]
    if all(path.is_file() for path in sheet_sources):
        images = [Image.open(path).convert("RGB") for path in sheet_sources]
        height = 900
        scaled = [image.resize((round(image.width * height / image.height), height)) for image in images]
        sheet = Image.new("RGB", (sum(image.width for image in scaled) + 40 * 4, height + 80), (238, 242, 239))
        left = 40
        for image in scaled:
            sheet.paste(image, (left, 40))
            left += image.width + 40
        sheet.save(OUT / "chat_post_three_variants.png")
        report["contact_sheet"] = str((OUT / "chat_post_three_variants.png").relative_to(ROOT))
    after = _sha256(SNAPSHOT)
    report["snapshot_unchanged"] = before == after
    report["snapshot_sha256"] = after
    report["route_log_lines"] = sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if "[MARKETING_RENDER_ROUTE]" in line)
    report["blocked_lines"] = sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if "LEGACY_ROUTE_BLOCKED" in line)
    report["ok"] = bool(
        report["snapshot_unchanged"]
        and all(step["ok"] for step in report["steps"])
        and report["copy_calls_first_post"] == 1
        and report["photo_reads_first_post"] == report["photo_reads_unique_first_post"]
        and report["page_choose_buttons"] == 3
        and report["selected_template"] == "property_premium_hero"
        and not report["blocked_lines"]
    )
    (OUT / "chat_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
