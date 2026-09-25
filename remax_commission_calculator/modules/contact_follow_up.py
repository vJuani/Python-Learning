"""Commercial stage, follow-up cadence, and the daily contact list.

``last_interaction_at`` is stored in the existing ``last_interacted_at``
column. A future ``next_follow_up_at`` keeps the contact out of the
daily list until that moment.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import timedelta

from modules.database.contacts_repository import update_contact
from modules.organization_time import parse_utc_iso, to_utc_iso


STAGE_NEW = "new"
STAGE_CONTACTED = "contacted"
STAGE_INTERESTED = "interested"
STAGE_VISIT_SCHEDULED = "visit_scheduled"
STAGE_FOLLOWING = "following"
STAGE_NEGOTIATING = "negotiating"
STAGE_CONVERTED = "converted"
STAGE_LOST = "lost"

COMMERCIAL_STAGES = (
    STAGE_NEW,
    STAGE_CONTACTED,
    STAGE_INTERESTED,
    STAGE_VISIT_SCHEDULED,
    STAGE_FOLLOWING,
    STAGE_NEGOTIATING,
    STAGE_CONVERTED,
    STAGE_LOST,
)

PRIORITY_POTENTIAL = "potential"
PRIORITY_ACTIVE_CLIENT = "active_client"
PRIORITY_FOLLOW_UP = "follow_up"
PRIORITY_LONG_TERM = "long_term"
PRIORITY_NO_OPPORTUNITY = "no_opportunity"

FOLLOW_UP_PRIORITIES = (
    PRIORITY_POTENTIAL,
    PRIORITY_ACTIVE_CLIENT,
    PRIORITY_FOLLOW_UP,
    PRIORITY_LONG_TERM,
    PRIORITY_NO_OPPORTUNITY,
)

CADENCE_HIGH = "high"
CADENCE_MEDIUM = "medium"
CADENCE_LOW = "low"
CADENCE_MANUAL = "manual"
CADENCE_NONE = "none"

FOLLOW_UP_CADENCES = (
    CADENCE_HIGH,
    CADENCE_MEDIUM,
    CADENCE_LOW,
    CADENCE_MANUAL,
    CADENCE_NONE,
)

CADENCE_CHOICES = (
    ("high_2", "followup_cadence_high_2"),
    ("high", "followup_cadence_high"),
    ("medium", "followup_cadence_medium"),
    ("low", "followup_cadence_low"),
    ("low_30", "followup_cadence_low_30"),
    ("manual", "followup_cadence_manual"),
    ("none", "followup_cadence_none"),
)

AUTOMATIC_CADENCES = frozenset({CADENCE_HIGH, CADENCE_MEDIUM, CADENCE_LOW})
TERMINAL_STAGES = frozenset({STAGE_CONVERTED, STAGE_LOST})
IMPORT_SOURCE_TYPES = frozenset({"phone_import", "vcard"})
POSTPONE_DAYS = (1, 3, 7, 15, 30)
VERY_OVERDUE_DAYS = 7
VISIT_LOOKBACK_DAYS = 14
ACTIVE_STALE_FALLBACK_DAYS = 7
OWNER_STALE_FALLBACK_DAYS = 15
NOTE_LIMIT = 2000

REASON_OVERDUE = "overdue"
REASON_VERY_OVERDUE = "very_overdue"
REASON_VISIT = "visit_without_follow_up"
REASON_NEW = "new_unattended"
REASON_ACTIVE = "active_client_stale"
REASON_TASK = "pending_task"
REASON_OWNER = "owner_stale"
REASON_LONG_TERM = "long_term_due"
REASON_OUTCOME_MISSING = "visit_outcome_missing"
REASON_NEXT_STEP = "visit_next_step_missing"
REASON_SHARED = "properties_shared_pending"

REASON_KEYS = {
    REASON_OVERDUE: "followup_reason_overdue",
    REASON_VERY_OVERDUE: "followup_reason_very_overdue",
    REASON_VISIT: "followup_reason_visit",
    REASON_NEW: "followup_reason_new",
    REASON_ACTIVE: "followup_reason_active",
    REASON_TASK: "followup_reason_task",
    REASON_OWNER: "followup_reason_owner",
    REASON_LONG_TERM: "followup_reason_long_term",
    REASON_OUTCOME_MISSING: "followup_reason_outcome_missing",
    REASON_NEXT_STEP: "followup_reason_next_step",
    REASON_SHARED: "followup_reason_properties_shared",
}

_STAGE_RANK = {
    STAGE_NEW: 0,
    STAGE_CONTACTED: 1,
    STAGE_INTERESTED: 2,
    STAGE_VISIT_SCHEDULED: 3,
    STAGE_FOLLOWING: 4,
    STAGE_NEGOTIATING: 5,
    STAGE_CONVERTED: 6,
    STAGE_LOST: 6,
}

_NUM_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "quince": 15,
    "treinta": 30,
}

_NAME_STOP = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "por",
        "ahora",
        "ya",
        "no",
        "con",
    }
)

_NUMBER = r"(\d+|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|quince|treinta)"


class FollowUpInputError(ValueError):
    """Form values that are not a known stage, priority, or cadence."""


def fold_follow_up_text(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def interval_days(contact):
    """Days between automatic touches. Manual and none have no interval."""
    cadence = (contact or {}).get("follow_up_cadence") or ""
    raw = (contact or {}).get("follow_up_interval_days")
    try:
        days = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    if cadence == CADENCE_HIGH:
        return days if days in (2, 3) else 3
    if cadence == CADENCE_MEDIUM:
        return 7
    if cadence == CADENCE_LOW:
        return days if days in (15, 30) else 15
    return None


def cadence_form_value(contact):
    cadence = (contact or {}).get("follow_up_cadence") or ""
    days = interval_days(contact) if cadence in AUTOMATIC_CADENCES else None
    if cadence == CADENCE_HIGH and days == 2:
        return "high_2"
    if cadence == CADENCE_LOW and days == 30:
        return "low_30"
    return cadence


def parse_cadence_choice(choice):
    raw = (choice or "").strip()
    if raw in ("", "unset"):
        return "", None
    if raw == "high_2":
        return CADENCE_HIGH, 2
    if raw == "high":
        return CADENCE_HIGH, 3
    if raw == "medium":
        return CADENCE_MEDIUM, 7
    if raw == "low":
        return CADENCE_LOW, 15
    if raw == "low_30":
        return CADENCE_LOW, 30
    if raw == CADENCE_MANUAL:
        return CADENCE_MANUAL, None
    if raw == CADENCE_NONE:
        return CADENCE_NONE, None
    raise FollowUpInputError(raw)


def follow_up_columns_from_payload(payload, *, organization_id, creating=False):
    """Columns for create/update. Empty dict means leave stored values alone."""
    payload = payload or {}
    explicit = any(
        key in payload
        for key in (
            "commercial_stage",
            "follow_up_priority",
            "follow_up_cadence",
            "follow_up_cadence_choice",
            "follow_up_reason",
            "next_follow_up_date",
            "next_follow_up_at",
        )
    )
    source_type = (
        payload.get("source_type") or payload.get("source") or "manual"
    ).strip()
    if creating and not explicit:
        if source_type in IMPORT_SOURCE_TYPES:
            return {}
        return {
            "commercial_stage": STAGE_NEW,
            "follow_up_priority": PRIORITY_POTENTIAL,
            "follow_up_cadence": CADENCE_MEDIUM,
            "follow_up_interval_days": 7,
        }
    if not explicit:
        return {}

    stage = (payload.get("commercial_stage") or "").strip()
    if stage and stage not in COMMERCIAL_STAGES:
        raise FollowUpInputError(stage)
    priority = (payload.get("follow_up_priority") or "").strip()
    if priority and priority not in FOLLOW_UP_PRIORITIES:
        raise FollowUpInputError(priority)

    if "follow_up_cadence_choice" in payload or "follow_up_cadence" in payload:
        choice = payload.get("follow_up_cadence_choice")
        if choice is None:
            choice = payload.get("follow_up_cadence") or ""
        cadence, days = parse_cadence_choice(choice)
    else:
        cadence, days = "", None

    columns = {
        "commercial_stage": stage,
        "follow_up_priority": priority,
        "follow_up_cadence": cadence,
        "follow_up_reason": (payload.get("follow_up_reason") or "").strip()[:300],
    }
    if days is None:
        columns["follow_up_interval_days"] = None
    else:
        columns["follow_up_interval_days"] = days

    if payload.get("next_follow_up_at"):
        columns["next_follow_up_at"] = payload.get("next_follow_up_at")
    elif "next_follow_up_date" in payload:
        date_text = (payload.get("next_follow_up_date") or "").strip()
        if not date_text or cadence == CADENCE_NONE:
            columns["next_follow_up_at"] = ""
        else:
            from modules.organization_time import (
                local_datetime_to_utc_iso,
                organization_timezone,
            )

            try:
                columns["next_follow_up_at"] = local_datetime_to_utc_iso(
                    date_text,
                    "09:00",
                    organization_timezone(organization_id),
                )
            except ValueError as error:
                raise FollowUpInputError(date_text) from error
    elif cadence == CADENCE_NONE:
        columns["next_follow_up_at"] = ""

    if columns["follow_up_interval_days"] is None and cadence in AUTOMATIC_CADENCES:
        columns["follow_up_interval_days"] = interval_days(
            {"follow_up_cadence": cadence}
        )
    return columns


def _aware(value):
    if value is None:
        return None
    if hasattr(value, "tzinfo"):
        return value if value.tzinfo else None
    return parse_utc_iso(value)


def _interaction_at(contact):
    return _aware(
        (contact or {}).get("last_interaction_at")
        or (contact or {}).get("last_interacted_at")
    )


def _next_at(contact):
    return _aware((contact or {}).get("next_follow_up_at"))


def _is_snoozed(contact, now):
    moment = _next_at(contact)
    return moment is not None and moment > now


def _days_since(moment, now):
    if moment is None:
        return None
    return (now - moment).total_seconds() / 86400


def _raise_stage(current, proposed):
    current = current or ""
    if not proposed:
        return current
    if current in TERMINAL_STAGES:
        return current
    if not current:
        return proposed
    if _STAGE_RANK.get(proposed, 0) >= _STAGE_RANK.get(current, 0):
        return proposed
    return current


def append_contact_note(existing, text, now, tz, *, kind=""):
    body = (text or "").strip()
    if not body:
        return existing or ""
    from modules.organization_time import to_local

    local = to_local(now, tz)
    stamp = local.strftime("%Y-%m-%d") if local else ""
    marker = f"{stamp}|{kind}" if stamp and kind else stamp
    line = f"[{marker}] {body}" if marker else body
    base = (existing or "").strip()
    combined = f"{base}\n{line}".strip() if base else line
    if len(combined) > NOTE_LIMIT:
        combined = combined[-NOTE_LIMIT:]
    return combined


def mark_contacted(contact, *, now):
    """Record a touch and advance the automatic cadence."""
    stage = _raise_stage(contact.get("commercial_stage"), STAGE_CONTACTED)
    cadence = contact.get("follow_up_cadence") or ""
    fields = {
        "commercial_stage": stage,
        "last_interacted_at": to_utc_iso(now),
    }
    if cadence in AUTOMATIC_CADENCES:
        days = interval_days(contact) or 7
        fields["next_follow_up_at"] = to_utc_iso(now + timedelta(days=days))
    elif cadence == CADENCE_NONE:
        fields["next_follow_up_at"] = ""
    else:
        moment = _next_at(contact)
        if moment is not None and moment <= now:
            fields["next_follow_up_at"] = ""
    return update_contact(contact["id"], contact["organization_id"], **fields)


def postpone_follow_up(contact, *, now, days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if days not in POSTPONE_DAYS:
        raise FollowUpInputError(str(days))
    return update_contact(
        contact["id"],
        contact["organization_id"],
        next_follow_up_at=to_utc_iso(now + timedelta(days=days)),
    )


def add_follow_up_note(contact, text, *, now, tz):
    body = (text or "").strip()
    if not body:
        raise FollowUpInputError("note")
    return update_contact(
        contact["id"],
        contact["organization_id"],
        notes=append_contact_note(contact.get("notes"), body, now, tz),
    )


def _item(contact, reason, score, *, now, individual=False):
    overdue_days = 0
    moment = _next_at(contact)
    if moment is not None and moment <= now:
        overdue_days = int(_days_since(moment, now) or 0)
    return {
        "contact_id": contact["id"],
        "name": contact.get("name") or "",
        "phone": contact.get("phone") or "",
        "reason": reason,
        "reason_key": REASON_KEYS[reason],
        "score": score,
        "overdue_days": overdue_days,
        "individual": individual,
        "commercial_stage": contact.get("commercial_stage") or "",
        "follow_up_priority": contact.get("follow_up_priority") or "",
        "follow_up_cadence": contact.get("follow_up_cadence") or "",
        "follow_up_reason": contact.get("follow_up_reason") or "",
        "next_follow_up_at": contact.get("next_follow_up_at") or "",
    }


def _consider(bucket, item):
    current = bucket.get(item["contact_id"])
    if current is None or item["score"] > current["score"]:
        if current and current.get("individual"):
            item["individual"] = True
        bucket[item["contact_id"]] = item
    elif item.get("individual"):
        current["individual"] = True


def _automatic_item(contact, now):
    """One follow-up reason, or None when the contact should stay quiet."""
    if contact.get("archived_at"):
        return None
    stage = contact.get("commercial_stage") or ""
    cadence = contact.get("follow_up_cadence") or ""
    priority = contact.get("follow_up_priority") or ""
    if stage in TERMINAL_STAGES or cadence == CADENCE_NONE:
        return None
    if _is_snoozed(contact, now):
        return None

    moment = _next_at(contact)
    due = moment is not None and moment <= now
    very = due and _days_since(moment, now) >= VERY_OVERDUE_DAYS
    high = cadence == CADENCE_HIGH and due

    if priority == PRIORITY_LONG_TERM and due:
        return _item(
            contact,
            REASON_VERY_OVERDUE if very else REASON_LONG_TERM,
            100 if very else 80,
            now=now,
            individual=very or high,
        )
    if due:
        return _item(
            contact,
            REASON_VERY_OVERDUE if very else REASON_OVERDUE,
            100 if very else (90 if high else 80),
            now=now,
            individual=very or high,
        )

    last = _interaction_at(contact)
    age = _days_since(last, now)
    gap = interval_days(contact)

    if stage == STAGE_NEW and last is None:
        return _item(contact, REASON_NEW, 75, now=now, individual=high)

    if priority == PRIORITY_ACTIVE_CLIENT:
        limit = gap or ACTIVE_STALE_FALLBACK_DAYS
        if age is None or age >= limit:
            return _item(
                contact,
                REASON_ACTIVE,
                70,
                now=now,
                individual=cadence == CADENCE_HIGH,
            )

    if contact.get("contact_type") == "owner" and (
        priority == PRIORITY_POTENTIAL
        or stage in (STAGE_NEW, STAGE_CONTACTED, STAGE_INTERESTED)
    ):
        limit = gap or OWNER_STALE_FALLBACK_DAYS
        if age is None or age >= limit:
            return _item(contact, REASON_OWNER, 55, now=now, individual=high)

    return None


def _visit_without_follow_up(contact, visits, now):
    if contact.get("archived_at"):
        return False
    if (contact.get("commercial_stage") or "") in TERMINAL_STAGES:
        return False
    if (contact.get("follow_up_cadence") or "") == CADENCE_NONE:
        return False
    if _is_snoozed(contact, now):
        return False
    last = _interaction_at(contact)
    window_start = now - timedelta(days=VISIT_LOOKBACK_DAYS)
    for visit in visits:
        if visit.get("contact_id") != contact.get("id"):
            continue
        if visit.get("task_type") != "visit":
            continue
        completed = _aware(
            visit.get("completed_at") or visit.get("due_at") or visit.get("updated_at")
        )
        if completed is None or completed < window_start or completed > now:
            continue
        if last is None or last <= completed + timedelta(minutes=2):
            return True
    return False


def _pending_task_for(contact, tasks, now, day_end=None):
    if contact.get("archived_at"):
        return False
    if (contact.get("commercial_stage") or "") in TERMINAL_STAGES:
        return False
    if (contact.get("follow_up_cadence") or "") == CADENCE_NONE:
        return False
    if _is_snoozed(contact, now):
        return False
    for task in tasks:
        if task.get("contact_id") != contact.get("id"):
            continue
        if task.get("status") not in (None, "", "pending"):
            continue
        due = _aware(task.get("due_at"))
        limit = day_end or now
        if due is not None and due <= limit:
            return True
    return False


def _post_visit_daily_item(contact, visits, now):
    """Completed visit still missing a result, or interest without a next step."""
    if contact.get("archived_at"):
        return None
    if (contact.get("commercial_stage") or "") in TERMINAL_STAGES:
        return None
    if (contact.get("follow_up_cadence") or "") == CADENCE_NONE:
        return None
    if _is_snoozed(contact, now):
        return None
    from modules.visit_outcome import normalize_visit_outcome

    window_start = now - timedelta(days=VISIT_LOOKBACK_DAYS)
    for visit in visits or []:
        if visit.get("contact_id") != contact.get("id"):
            continue
        if visit.get("task_type") != "visit":
            continue
        if visit.get("status") not in (None, "", "completed"):
            continue
        completed = _aware(
            visit.get("completed_at") or visit.get("due_at") or visit.get("updated_at")
        )
        if completed is None or completed < window_start or completed > now:
            continue
        outcome = normalize_visit_outcome(visit.get("outcome_json"))
        step_at = _aware(outcome.get("next_step_at"))
        if step_at is not None and step_at > now:
            continue
        if not outcome.get("result"):
            return _item(contact, REASON_OUTCOME_MISSING, 88, now=now)
        if outcome.get("result") == "interested" and not outcome.get("next_step"):
            return _item(contact, REASON_NEXT_STEP, 86, now=now)
    return None


def _shared_properties_due(contact, now):
    """Shared properties whose scheduled follow-up is due, with no later touch."""
    if contact.get("archived_at"):
        return None
    if (contact.get("commercial_stage") or "") in TERMINAL_STAGES:
        return None
    if (contact.get("follow_up_cadence") or "") == CADENCE_NONE:
        return None
    if _is_snoozed(contact, now):
        return None
    moment = _next_at(contact)
    if moment is None or moment > now:
        return None
    share_day = ""
    for line in reversed((contact.get("notes") or "").splitlines()):
        match = re.match(
            r"^\[(\d{4}-\d{2}-\d{2})\|property_shortlist_shared\]",
            line.strip(),
        )
        if match:
            share_day = match.group(1)
            break
    if not share_day:
        return None
    last = _interaction_at(contact)
    if last is not None and last.date().isoformat() > share_day:
        return None
    return _item(contact, REASON_SHARED, 84, now=now)


def build_daily_follow_up_list(
    contacts, *, now, tasks=None, visits=None, day_end=None
):
    """Prioritized contacts to move. A future next date excludes the row."""
    tasks = tasks or []
    visits = visits or []
    bucket = {}
    for contact in contacts or []:
        item = _automatic_item(contact, now)
        if item is not None:
            _consider(bucket, item)
        if _visit_without_follow_up(contact, visits, now):
            _consider(
                bucket,
                _item(
                    contact,
                    REASON_VISIT,
                    85,
                    now=now,
                    individual=(contact.get("follow_up_cadence") or "") == CADENCE_HIGH,
                ),
            )
        if _pending_task_for(contact, tasks, now, day_end):
            _consider(
                bucket,
                _item(contact, REASON_TASK, 65, now=now),
            )
        post_visit = _post_visit_daily_item(contact, visits, now)
        if post_visit is not None:
            _consider(bucket, post_visit)
        shared = _shared_properties_due(contact, now)
        if shared is not None:
            _consider(bucket, shared)
    rows = list(bucket.values())
    rows.sort(key=lambda row: (-row["score"], row["name"].lower(), row["contact_id"]))
    return rows


def _number(token):
    if token is None:
        return None
    if str(token).isdigit():
        return int(token)
    return _NUM_WORDS.get(token)


def _clean_name(value):
    parts = []
    for part in (value or "").strip(" ,.;:").split():
        folded = fold_follow_up_text(part)
        if folded in _NAME_STOP or len(folded) < 2:
            break
        parts.append(part.strip(" ,.;:"))
        if len(parts) == 2:
            break
    return " ".join(parts)


def _capture_name(text):
    raw = text or ""
    patterns = (
        r"habl[eé]\s+con\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)?)",
        r"(?:llamalo|llamarlo|hablale|hablarle)\s+a\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)",
        r"^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)\s+sigue\s+buscando",
        r"^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)\s+ya\s+(?:compr|vend|cerr)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            name = _clean_name(match.group(1))
            if name:
                return name
    return ""


def _delay_from_text(folded):
    if "semana que viene" in folded or "proxima semana" in folded:
        return {"days": 7}
    if "manana" in folded:
        return {"days": 1}
    match = re.search(rf"\b(?:en\s+)?{_NUMBER}\s+mes(?:es)?\b", folded)
    if match:
        months = _number(match.group(1))
        if months:
            return {"months": months}
    match = re.search(rf"\b(?:en\s+)?{_NUMBER}\s+semanas?\b", folded)
    if match:
        weeks = _number(match.group(1))
        if weeks:
            return {"days": weeks * 7}
    match = re.search(rf"\ben\s+{_NUMBER}\s+dias?\b", folded)
    if match:
        days = _number(match.group(1))
        if days:
            return {"days": days}
    return None


def detect_follow_up_command(text):
    """Parse a spoken follow-up. Returns None when it is not one."""
    raw = (text or "").strip()
    folded = fold_follow_up_text(raw)
    if not folded:
        return None
    bought = bool(re.search(r"\bya\s+(compro|vendio|cerro)\b", folded))
    searching = "sigue buscando" in folded
    no_sale = any(
        phrase in folded
        for phrase in ("no vende", "no compra", "por ahora no")
    )
    spoke = bool(re.search(r"\b(hable con|llame a|llame con|vi a)\b", folded))
    call_later = bool(
        re.search(r"\b(llamalo|llamarlo|hablale|hablarle)\b", folded)
    )
    lost = any(
        phrase in folded
        for phrase in ("no le interesa", "lo perdi", "la perdi", "esta perdido")
    )
    if not any((bought, searching, no_sale, spoke, call_later, lost)):
        return None
    name = _capture_name(raw)
    if not name:
        return None
    plan = {
        "contact_name": name,
        "note": raw,
        "spoke": spoke or bought,
        "bought": bought,
        "searching": searching,
        "no_sale": no_sale,
        "lost": lost,
        "stop_automatic": bought or lost,
        "delay": _delay_from_text(folded),
        "follow_up_reason": "",
    }
    if bought:
        plan["follow_up_reason"] = "Ya compró"
        plan["commercial_stage"] = STAGE_CONVERTED
    elif lost:
        plan["follow_up_reason"] = "Sin oportunidad actual"
        plan["commercial_stage"] = STAGE_LOST
        plan["follow_up_priority"] = PRIORITY_NO_OPPORTUNITY
    elif no_sale:
        plan["follow_up_reason"] = "Por ahora no vende"
        plan["follow_up_priority"] = PRIORITY_LONG_TERM
        plan["follow_up_cadence"] = CADENCE_MANUAL
    elif searching:
        plan["follow_up_reason"] = "Sigue buscando"
        plan["follow_up_priority"] = PRIORITY_FOLLOW_UP
        plan["commercial_stage"] = STAGE_INTERESTED
        if plan["delay"]:
            plan["follow_up_cadence"] = CADENCE_MANUAL
    elif spoke:
        plan["commercial_stage"] = STAGE_CONTACTED
    if plan["delay"] and not plan.get("follow_up_cadence") and not plan["stop_automatic"]:
        plan["follow_up_cadence"] = CADENCE_MANUAL
    return plan


def resolve_follow_up_when(plan, *, now, tz):
    """Turn a relative delay into ``next_follow_up_at`` (UTC ISO)."""
    resolved = dict(plan or {})
    delay = resolved.get("delay") or {}
    if resolved.get("stop_automatic"):
        resolved["next_follow_up_at"] = ""
        return resolved
    if not delay:
        return resolved
    from modules.organization_time import to_local

    local = to_local(now, tz)
    if local is None:
        return resolved
    if delay.get("months"):
        months = int(delay["months"])
        month_index = local.month - 1 + months
        year = local.year + month_index // 12
        month = month_index % 12 + 1
        day = min(local.day, calendar.monthrange(year, month)[1])
        target = local.replace(year=year, month=month, day=day)
    else:
        target = local + timedelta(days=int(delay.get("days") or 0))
    resolved["next_follow_up_at"] = to_utc_iso(target)
    return resolved


def follow_up_updates(contact, plan, *, now, tz):
    """Fields a follow-up would write. Does not touch the database."""
    plan = resolve_follow_up_when(plan, now=now, tz=tz)
    fields = {}
    note = append_contact_note(
        contact.get("notes"),
        plan.get("note"),
        now,
        tz,
        kind="follow_up",
    )
    if note and note != (contact.get("notes") or ""):
        fields["notes"] = note
    if plan.get("follow_up_reason"):
        fields["follow_up_reason"] = plan["follow_up_reason"]
    if plan.get("spoke"):
        fields["last_interacted_at"] = to_utc_iso(now)

    if plan.get("stop_automatic"):
        fields["commercial_stage"] = plan.get("commercial_stage") or STAGE_CONVERTED
        fields["follow_up_cadence"] = CADENCE_NONE
        fields["next_follow_up_at"] = ""
        if plan.get("follow_up_priority"):
            fields["follow_up_priority"] = plan["follow_up_priority"]
    else:
        if plan.get("spoke") or plan.get("commercial_stage"):
            fields["commercial_stage"] = _raise_stage(
                contact.get("commercial_stage"),
                plan.get("commercial_stage") or STAGE_CONTACTED,
            )
        if plan.get("follow_up_priority"):
            current_priority = contact.get("follow_up_priority") or ""
            proposed = plan["follow_up_priority"]
            if proposed == PRIORITY_FOLLOW_UP and current_priority == PRIORITY_ACTIVE_CLIENT:
                proposed = current_priority
            fields["follow_up_priority"] = proposed
        if plan.get("follow_up_cadence"):
            fields["follow_up_cadence"] = plan["follow_up_cadence"]
        if "next_follow_up_at" in plan:
            fields["next_follow_up_at"] = plan["next_follow_up_at"]

    return plan, fields


def apply_follow_up_plan(contact, plan, *, now, tz):
    """Persist a parsed follow-up: note, stage, priority, and next date."""
    _plan, fields = follow_up_updates(contact, plan, now=now, tz=tz)
    if not fields:
        return contact
    return update_contact(contact["id"], contact["organization_id"], **fields)


def record_timeline_note(contact, text, *, kind, now, tz):
    notes = append_contact_note(
        contact.get("notes"),
        text,
        now,
        tz,
        kind=kind,
    )
    if notes == (contact.get("notes") or ""):
        return contact
    return update_contact(
        contact["id"],
        contact["organization_id"],
        notes=notes,
    )
