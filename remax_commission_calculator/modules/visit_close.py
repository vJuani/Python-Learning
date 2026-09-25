"""Close a completed visit: timeline, next task, stage, and property link.

Need changes are detected here and saved only when the caller confirms.
"""

from __future__ import annotations

import re
from datetime import timedelta

from modules.contact_follow_up import (
    STAGE_FOLLOWING,
    STAGE_INTERESTED,
    STAGE_NEGOTIATING,
    _raise_stage,
    record_timeline_note,
)
from modules.contacts import diff_preference_update, normalize_preferences
from modules.database.agent_tasks_repository import STATUS_PENDING, list_agent_tasks
from modules.database.contacts_repository import (
    list_property_interactions,
    record_property_interaction,
    update_contact,
)
from modules.organization_time import local_datetime_to_utc_iso, parse_utc_iso, to_local, to_utc_iso
from modules.visit_outcome import NEXT_STEPS, VISIT_RESULTS, normalize_visit_outcome


_STEP_TASK = {
    "call": "call",
    "whatsapp": "follow_up",
    "send_properties": "follow_up",
    "second_visit": "visit",
    "negotiate": "follow_up",
    "custom": "follow_up",
}
_RELATION_FOR_RESULT = {
    "liked": "interested",
    "interested": "interested",
    "second_visit": "interested",
    "negotiate": "negotiation",
    "disliked": "discarded",
    "other": "visited",
}


def learn_need_from_text(text):
    """Pull search facts out of a close note. Does not write them."""
    raw = (text or "").strip()
    folded = _fold(raw)
    features = []
    if re.search(r"\bcochera\b", folded) and not re.search(
        r"\b(sin|no quiere)\s+cochera\b", folded
    ):
        features.append("cochera")
    observations = []
    for match in re.finditer(
        r"no quiere(?:\s+m[aá]s)?\s+([^.,\n]+)",
        raw,
        flags=re.IGNORECASE,
    ):
        observations.append(match.group(0).strip())
    budget = {}
    amount = re.search(
        r"(?:presupuesto|hasta|m[aá]ximo)[^\d]{0,24}(?:usd|u\$s|ars)?\s*"
        r"(\d{1,3}(?:[.\s]\d{3})+|\d+)",
        folded,
    )
    if amount:
        digits = re.sub(r"\D", "", amount.group(1))
        if digits:
            budget["max"] = int(digits)
            budget["currency"] = "ARS" if "ars" in folded or "peso" in folded else "USD"
    payload = {}
    if features:
        payload["features"] = features
    if observations:
        payload["observations"] = " ".join(observations)
    if budget:
        payload["budget"] = budget
    return normalize_preferences(payload)


def need_changes_for_outcome(contact, outcome):
    data = normalize_visit_outcome(outcome)
    pieces = [data.get("note") or "", data.get("next_step_note") or ""]
    pieces.extend(data.get("objections") or [])
    incoming = learn_need_from_text(" ".join(pieces))
    if data.get("areas") or data.get("budget") or data.get("preferences"):
        from modules.contacts import preferences_from_outcome

        incoming = _merge_detected(
            incoming,
            preferences_from_outcome(data),
        )
    return diff_preference_update(
        (contact or {}).get("preferences_json"),
        incoming,
    )


def describe_need_changes(preview, language="es"):
    """Short lines for the confirm sheet. Does not write."""
    from modules.i18n import translate
    from modules.visit_outcome import format_budget_label

    preview = preview or {}
    budget = ((preview.get("incoming") or {}).get("budget") or {})
    lines = []
    for item in preview.get("additions") or []:
        value = str(item.get("value") or "")
        if item.get("field") == "features" and value:
            shown = value[:1].upper() + value[1:]
            lines.append(f"+ {shown}: {translate('yes', language)}")
        elif value:
            lines.append(f"+ {value}")
    for item in preview.get("fills") or []:
        key = item.get("key") or ""
        if key == "budget_max":
            amount = format_budget_label(
                {"max": item.get("value"), "currency": budget.get("currency") or "USD"}
            )
            lines.append(
                f"+ {translate('contacts_merge_budget_max', language)}: {amount}"
            )
        elif key == "budget_min":
            amount = format_budget_label(
                {"max": item.get("value"), "currency": budget.get("currency") or "USD"}
            )
            lines.append(f"+ {amount}")
        elif key == "budget_currency":
            continue
        elif key == "observations":
            lines.append(
                f"+ {translate('contacts_field_observations', language)}: {item.get('value')}"
            )
        elif item.get("value") not in (None, ""):
            lines.append(f"+ {item.get('value')}")
    for item in preview.get("conflicts") or []:
        label = item.get("incoming_label") or item.get("incoming") or ""
        if label:
            lines.append(f"+ {label}")
    return lines


def apply_visit_close(
    task,
    outcome,
    *,
    organization_id,
    agent_id,
    actor_user_id=None,
    now=None,
    tz=None,
    language="es",
    save_need=False,
    accepted_conflicts=None,
):
    """Persist a close. Creates the next task unless the step is none or a duplicate visit."""
    from modules.agent_tasks import create_task, save_visit_outcome
    from modules.contacts import load_contact, save_contact_preference_update
    from modules.i18n import translate
    from modules.organization_time import now_utc, organization_timezone

    instant = now or now_utc()
    tz = tz or organization_timezone(organization_id)
    data = _with_default_when(normalize_visit_outcome(outcome), now=instant, tz=tz)
    updated = save_visit_outcome(
        organization_id,
        task["id"],
        data,
        agent_id=agent_id,
    )
    contact = None
    if updated.get("contact_id"):
        contact = load_contact(
            organization_id,
            updated["contact_id"],
            agent_id=agent_id,
        )
    place = (
        updated.get("property_address")
        or updated.get("title")
        or ""
    ).strip()
    result_label = ""
    if data.get("result"):
        result_label = translate(f"visit_result_{data['result']}", language)
    line = f"Visitó {place}".strip()
    if result_label:
        line = f"{line} — {result_label}"
    if contact:
        contact = record_timeline_note(
            contact,
            line,
            kind="visit_outcome",
            now=instant,
            tz=tz,
        )
        contact = _apply_stage(contact, data)
        if data.get("next_step") not in ("", "none") and data.get("next_step_at"):
            contact = update_contact(
                contact["id"],
                organization_id,
                next_follow_up_at=data["next_step_at"],
            )
    created = _create_next_task(
        updated,
        data,
        organization_id=organization_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        language=language,
        tz=tz,
    )
    relations = _record_property_relations(updated, data)
    preview = need_changes_for_outcome(contact, data) if contact else {
        "has_changes": False,
        "additions": [],
        "fills": [],
        "conflicts": [],
    }
    if save_need and contact and preview.get("has_changes"):
        keys = [item["key"] for item in preview.get("conflicts") or []]
        contact = save_contact_preference_update(
            organization_id,
            contact["id"],
            preview.get("incoming") or {},
            accepted_conflicts=accepted_conflicts if accepted_conflicts is not None else keys,
            agent_id=agent_id,
        )
    return {
        "task": updated,
        "contact": contact,
        "created_task": created.get("task"),
        "duplicate_visit": created.get("duplicate"),
        "relations": relations,
        "need_preview": preview,
        "outcome": data,
    }


def _with_default_when(outcome, *, now, tz):
    data = dict(outcome or {})
    step = data.get("next_step") or ""
    if step in ("", "none") or data.get("next_step_at"):
        return data
    if step not in _STEP_TASK:
        return data
    local = to_local(to_utc_iso(now), tz)
    if local is None:
        return data
    target = (local + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    data["next_step_at"] = to_utc_iso(target)
    return data


def _apply_stage(contact, outcome):
    result = outcome.get("result") or ""
    step = outcome.get("next_step") or ""
    current = contact.get("commercial_stage") or ""
    proposed = ""
    if result == "negotiate" or step == "negotiate":
        proposed = STAGE_NEGOTIATING
    elif result == "interested":
        from modules.contact_follow_up import STAGE_VISIT_SCHEDULED, _STAGE_RANK

        if _STAGE_RANK.get(current, 0) >= _STAGE_RANK[STAGE_VISIT_SCHEDULED]:
            proposed = STAGE_FOLLOWING
        else:
            proposed = STAGE_INTERESTED
    if not proposed:
        return contact
    stage = _raise_stage(current, proposed)
    if stage == current:
        return contact
    return update_contact(
        contact["id"],
        contact["organization_id"],
        commercial_stage=stage,
    )


def _create_next_task(
    task,
    outcome,
    *,
    organization_id,
    agent_id,
    actor_user_id,
    language,
    tz,
):
    from modules.agent_tasks import create_task
    from modules.i18n import translate

    step = outcome.get("next_step") or ""
    if step in ("", "none") or step not in _STEP_TASK:
        return {"task": None, "duplicate": False}
    when = parse_utc_iso(outcome.get("next_step_at"))
    local = to_local(outcome.get("next_step_at"), tz) if when else None
    if local is None:
        return {"task": None, "duplicate": False}
    task_type = _STEP_TASK[step]
    if task_type == "visit" and _pending_visit_exists(task):
        return {"task": None, "duplicate": True}
    title = translate(f"visit_step_{step}", language)
    name = task.get("contact_name") or ""
    if name:
        title = f"{title} · {name}"
    created = create_task(
        organization_id,
        task.get("agent_id") or agent_id,
        {
            "title": title,
            "task_type": task_type,
            "due_date": local.date().isoformat(),
            "due_time": local.strftime("%H:%M"),
            "contact_id": task.get("contact_id"),
            "contact_name": name,
            "property_id": task.get("property_id"),
            "description": outcome.get("next_step_note") or outcome.get("note") or "",
        },
        created_by_user_id=actor_user_id,
    )
    return {"task": created, "duplicate": False}


def _pending_visit_exists(task):
    if not task.get("contact_id"):
        return False
    pending = list_agent_tasks(
        task["organization_id"],
        agent_id=task.get("agent_id"),
        statuses=(STATUS_PENDING,),
        task_type="visit",
        contact_id=task.get("contact_id"),
        limit=20,
    )
    property_id = task.get("property_id")
    for item in pending:
        if item.get("id") == task.get("id"):
            continue
        if (
            property_id
            and item.get("property_id")
            and item.get("property_id") != property_id
        ):
            continue
        return True
    return False


def _record_property_relations(task, outcome):
    result = outcome.get("result") or ""
    if result == "no_show" or not task.get("contact_id"):
        return []
    property_id = task.get("property_id")
    label = task.get("property_address") or task.get("title") or ""
    wanted = ["visited"]
    specific = _RELATION_FOR_RESULT.get(result)
    if specific and specific != "visited":
        wanted.append(specific)
    existing = {
        (row.get("property_id"), row.get("interaction_type"))
        for row in list_property_interactions(
            task["organization_id"],
            task["contact_id"],
            limit=80,
        )
    }
    recorded = []
    for kind in wanted:
        key = (property_id, kind)
        if key in existing:
            continue
        record_property_interaction(
            task["organization_id"],
            task.get("agent_id"),
            contact_id=task["contact_id"],
            property_id=property_id,
            interaction_type=kind,
            activity_id=task.get("id"),
            label=label,
        )
        recorded.append(kind)
    return recorded


def _merge_detected(left, right):
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if key in ("features", "areas", "property_types"):
            current = list(merged.get(key) or [])
            seen = {item.casefold() for item in current}
            for item in value or []:
                if item.casefold() not in seen:
                    current.append(item)
                    seen.add(item.casefold())
            if current:
                merged[key] = current
        elif key == "budget":
            budget = dict(merged.get("budget") or {})
            for part, amount in (value or {}).items():
                if budget.get(part) in (None, ""):
                    budget[part] = amount
            if budget:
                merged["budget"] = budget
        elif merged.get(key) in (None, ""):
            merged[key] = value
    return normalize_preferences(merged)


def _fold(text):
    import unicodedata

    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def parse_visit_close_prompt(text, *, today, now_local):
    """Structured read of a spoken visit close. Returns None when it is not one."""
    from modules.agenda_nlp import parse_agenda_prompt, parse_person

    raw = (text or "").strip()
    folded = _fold(raw)
    if not folded or not _looks_like_visit_close(folded):
        return None
    agenda = parse_agenda_prompt(raw, today=today, now_local=now_local)
    result = _result_from_text(folded)
    step = _step_from_text(folded)
    when = ""
    if agenda.get("date_found"):
        when = local_datetime_to_utc_iso(
            agenda.get("due_date"),
            agenda.get("due_time") or "10:00",
            now_local.tzinfo,
        )
    note = raw
    return {
        "contact_name": parse_person(raw) or "",
        "result": result or "other",
        "note": note,
        "next_step": step,
        "next_step_at": when,
        "next_step_note": "",
    }


def _looks_like_visit_close(folded):
    if re.search(r"\b(pasame|move|reprograma|reprogramar)\b", folded):
        return False
    if "visita" not in folded and "no se presento" not in folded:
        return False
    return any(
        phrase in folded
        for phrase in (
            "la visita con",
            "la visita de",
            "salio bien",
            "salio mal",
            "le gusto",
            "no le gusto",
            "no se presento",
            "como salio la visita",
        )
    )


def _result_from_text(folded):
    if "no se presento" in folded:
        return "no_show"
    if "no le gusto" in folded:
        return "disliked"
    if "segunda visita" in folded and "quiere" in folded:
        return "second_visit"
    if "negoci" in folded:
        return "negotiate"
    if "interesad" in folded:
        return "interested"
    if "le gusto" in folded or "salio bien" in folded:
        return "liked"
    return ""


def _step_from_text(folded):
    if "sin seguimiento" in folded:
        return "none"
    if re.search(r"\b(llamalo|llamarlo|llamarla|llamala|llamar)\b", folded):
        return "call"
    if "whatsapp" in folded:
        return "whatsapp"
    if "segunda visita" in folded:
        return "second_visit"
    if "negoci" in folded:
        return "negotiate"
    if "enviar" in folded and "propiedad" in folded:
        return "send_properties"
    return ""
