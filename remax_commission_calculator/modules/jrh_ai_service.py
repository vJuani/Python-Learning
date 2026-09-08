"""Transversal JRH assistant. Interprets, resolves, never writes money."""

from __future__ import annotations

import logging
import time

from modules.agent_account import build_agent_detail_view
from modules.auth import is_admin, is_agent
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.jrh_ai_classify import (
    CABA_ALIASES,
    fold_text,
    format_agenda_day_label,
    normalize_user_text,
    resolve_agenda_date,
)
from modules.jrh_ai_context import load_context, store_context
from modules.jrh_ai_intents import (
    CREATE_TASK,
    FALLBACK,
    FINANCIAL_ACTIONS,
    LOW_CONFIDENCE,
    QUERY_AGENDA,
    QUERY_AGENT_ACCOUNT,
    QUERY_INVOICES,
    QUERY_OPERATIONS,
    QUERY_PENDINGS,
    QUERY_PROPERTIES,
    QUERY_PROPERTY_NEEDS,
    START_ACM,
    START_AGENT_PAYMENT,
    START_INVOICE,
    QUERY_ACM,
    ACM_EXPLAIN,
    ACM_REMOVE_COMPARABLE,
    ACM_FILTER_COMPARABLES,
    ACM_PRICE_SCENARIO,
    DOWNLOAD_ACM,
    QUERY_PRODUCTIVITY,
    WRITE_ACTIONS,
)
from modules.jrh_ai_provider import interpret_prompt
from modules.jrh_ai_resolver import (
    pick_unique,
    resolve_agents,
    resolve_contacts,
    resolve_operations,
    resolve_pending_charges,
    resolve_properties,
)
from modules.organization_time import now_utc, organization_timezone
from modules.pending_actions import (
    build_agent_pending_actions,
    build_staff_pending_actions,
    summarize_pending_actions,
)

logger = logging.getLogger(__name__)

SESSION_DRAFT_KEY = "jrh_ai_draft"


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _result(
    intent,
    status,
    *,
    language,
    summary="",
    message_key="",
    cards=None,
    actions=None,
    candidates=None,
    confirm_required=False,
    confidence=1.0,
    entity=None,
    data=None,
    wrote=False,
    pending_invoice=None,
):
    return {
        "intent": intent,
        "status": status,
        "summary": summary,
        "message_key": message_key,
        "message": _t(message_key, language, **(data or {})) if message_key else summary,
        "cards": cards or [],
        "actions": actions or [],
        "candidates": candidates or [],
        "confirm_required": confirm_required,
        "confidence": confidence,
        "entity": entity or {},
        "data": data or {},
        "wrote": wrote,
        "pending_invoice": pending_invoice or {},
    }


def _fallback(language, confidence=0.2):
    return _result(
        FALLBACK,
        "fallback",
        language=language,
        message_key="jrh_ai_fallback",
        confidence=confidence,
        actions=[
            {"label_key": "jrh_ai_suggest_pendings", "href_name": "pendings_center"},
            {"label_key": "jrh_ai_suggest_agenda", "href_name": "agenda_index"},
            {"label_key": "jrh_ai_suggest_properties", "href_name": "properties_list"},
            {"label_key": "jrh_ai_suggest_billing", "href_name": "billing_list"},
            {"label_key": "jrh_ai_suggest_account", "href_name": "my_agent_account"},
        ],
    )


def ask_jrh(
    prompt,
    *,
    organization_id,
    user,
    agent_id=None,
    language="es",
    session=None,
    provider=None,
    now=None,
):
    organization_id = require_organization_id(organization_id)
    text = normalize_user_text(prompt)
    if not text:
        return _result(
            FALLBACK,
            "fallback",
            language=language,
            message_key="jrh_err_empty",
        )
    started = time.perf_counter()
    context = load_context(session)
    parsed = interpret_prompt(
        text,
        context=context,
        language=language,
        provider=provider,
    )
    intent = parsed.get("intent") or FALLBACK
    entities = parsed.get("entities") or {}
    confidence = float(parsed.get("confidence") or 0)
    if entities.get("cancel_pending"):
        store_context(session, intent=FALLBACK, prompt=text, pending_invoice={})
        return _fallback(language, confidence=0.9)
    pending = context.get("pending_invoice") or {}
    if (
        (confidence < LOW_CONFIDENCE or intent == FALLBACK)
        and pending.get("expected_entity")
        and not entities.get("abandon_invoice_slot")
    ):
        intent = START_INVOICE
        confidence = max(confidence, 0.86)
        from modules.jrh_ai_classify import apply_expected_entity

        entities = apply_expected_entity(entities, text, pending)
    if confidence < LOW_CONFIDENCE or intent == FALLBACK:
        result = _fallback(language, confidence=confidence)
        if entities.get("agent_name") and confidence >= 0.35:
            result = _result(
                intent if intent != FALLBACK else QUERY_AGENT_ACCOUNT,
                "needs_attention",
                language=language,
                message_key="jrh_ai_low_confidence",
                data={"name": entities.get("agent_name")},
                confidence=confidence,
            )
        logger.info(
            "jrh_ai stage=handled intent=%s status=%s duration_ms=%s "
            "provider=%s model=%s",
            result["intent"],
            result["status"],
            int((time.perf_counter() - started) * 1000),
            parsed.get("provider"),
            parsed.get("model") or "",
        )
        return result

    handlers = {
        QUERY_PENDINGS: _handle_pendings,
        QUERY_AGENT_ACCOUNT: _handle_account,
        QUERY_AGENDA: _handle_agenda,
        QUERY_PROPERTIES: _handle_properties,
        QUERY_PROPERTY_NEEDS: _handle_needs,
        QUERY_OPERATIONS: _handle_operations,
        QUERY_INVOICES: _handle_invoices,
        CREATE_TASK: _handle_create_task,
        START_INVOICE: _handle_start_invoice,
        START_AGENT_PAYMENT: _handle_start_payment,
        START_ACM: _handle_start_acm,
        QUERY_ACM: _handle_query_acm,
        ACM_EXPLAIN: _handle_acm_explain,
        ACM_REMOVE_COMPARABLE: _handle_acm_remove,
        ACM_FILTER_COMPARABLES: _handle_acm_filter,
        ACM_PRICE_SCENARIO: _handle_acm_scenario,
        DOWNLOAD_ACM: _handle_download_acm,
        QUERY_PRODUCTIVITY: _handle_productivity,
    }
    handler = handlers.get(intent, lambda **_kwargs: _fallback(language, confidence))
    result = handler(
        organization_id=organization_id,
        user=user,
        agent_id=agent_id,
        language=language,
        entities=entities,
        prompt=text,
        confidence=confidence,
        session=session,
        now=now,
    )
    store_context(
        session,
        intent=result.get("intent"),
        entity=result.get("entity"),
        prompt=text,
        pending_invoice=result.get("pending_invoice"),
    )
    logger.info(
        "jrh_ai stage=handled intent=%s status=%s duration_ms=%s "
        "provider=%s model=%s",
        result.get("intent"),
        result.get("status"),
        int((time.perf_counter() - started) * 1000),
        parsed.get("provider"),
        parsed.get("model") or "",
    )
    return result


def _handle_pendings(
    *,
    organization_id,
    user,
    agent_id,
    language,
    prompt,
    confidence,
    **_kwargs,
):
    if is_agent(user) and agent_id:
        actions = build_agent_pending_actions(
            organization_id,
            agent_id,
            user_id=(user or {}).get("id"),
            language=language,
        )
    elif is_admin(user):
        actions = build_staff_pending_actions(
            organization_id,
            language=language,
        )
    else:
        return _fallback(language, confidence)
    summary = summarize_pending_actions(actions, language=language)
    cards = [
        {
            "title": item.get("label") or item.get("category") or "",
            "subtitle": str(item.get("count") or ""),
        }
        for item in (summary.get("groups") or [])[:6]
    ]
    return _result(
        QUERY_PENDINGS,
        "ready",
        language=language,
        summary=_t("jrh_msg_pending_count", language, count=summary.get("total") or 0),
        cards=cards,
        actions=[{"label_key": "jrh_cta_view", "href_name": "pendings_center"}],
        confidence=confidence,
        data={"total": summary.get("total") or 0, "source_prompt": prompt},
    )


def _handle_account(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    **_kwargs,
):
    query = ""
    use_self = bool(entities.get("self") and agent_id) or (
        is_agent(user)
        and agent_id
        and not entities.get("agent_name")
    )
    if use_self:
        from modules.database.agents_repository import get_agent_record

        own = get_agent_record(agent_id, organization_id)
        matches = (
            [{"id": own["id"], "name": own.get("name") or "", "kind": "agent"}]
            if own
            else []
        )
    else:
        query = entities.get("agent_name") or ""
        matches = resolve_agents(
            organization_id,
            query,
            user=user,
            agent_id=agent_id,
        )
    status, chosen, matches = pick_unique(matches, confidence=confidence)
    if status == "empty":
        return _result(
            QUERY_AGENT_ACCOUNT,
            "needs_attention",
            language=language,
            message_key="jrh_ai_agent_missing",
            data={"name": query},
            confidence=confidence,
        )
    if status == "ambiguous":
        return _result(
            QUERY_AGENT_ACCOUNT,
            "needs_attention",
            language=language,
            message_key="jrh_ai_agent_ambiguous",
            candidates=matches,
            confidence=confidence,
        )
    hint = entities.get("charge_hint") or ""
    charges = resolve_pending_charges(
        organization_id,
        chosen["id"],
        hint=hint,
    )
    href_name = "my_agent_account" if is_agent(user) else "agent_account_detail"
    href_args = {} if is_agent(user) else {"agent_id": chosen["id"]}
    account_action = {
        "label_key": "jrh_ai_view_current_account",
        "href_name": href_name,
        "href_args": href_args,
    }
    if fold_text(hint) == "fee":
        if not charges:
            return _result(
                QUERY_AGENT_ACCOUNT,
                "ready",
                language=language,
                message_key="jrh_ai_fee_clear",
                actions=[account_action],
                confidence=confidence,
                entity=chosen,
                data={"source_prompt": prompt},
            )
        cards = [
            {
                "title": " · ".join(
                    part
                    for part in (
                        "Fee",
                        charge.get("period_label") or charge.get("name") or "",
                    )
                    if part
                ),
                "subtitle": f"{charge.get('currency') or ''} {charge.get('amount') or ''}".strip(),
            }
            for charge in charges[:5]
        ]
        return _result(
            QUERY_AGENT_ACCOUNT,
            "ready",
            language=language,
            message_key="jrh_ai_fee_pending",
            cards=cards,
            actions=[account_action],
            confidence=confidence,
            entity=charges[0] if len(charges) == 1 else chosen,
            data={"source_prompt": prompt},
        )
    view = build_agent_detail_view(
        organization_id,
        chosen["id"],
        language=language,
    )
    balances = view.get("display_balances") or {}
    cards = [
        {
            "title": chosen["name"],
            "subtitle": " · ".join(
                f"{code} {balances.get(code) or ''}"
                for code in ("USD", "ARS")
            ),
        }
    ]
    for charge in charges[:5]:
        cards.append(
            {
                "title": charge.get("name") or "",
                "subtitle": f"{charge.get('currency')} {charge.get('amount')}",
            }
        )
    return _result(
        QUERY_AGENT_ACCOUNT,
        "ready",
        language=language,
        summary=chosen["name"],
        cards=cards,
        actions=[
            account_action,
            {
                "label_key": "jrh_ai_register_payment",
                "href_name": "agent_payment_ai_new",
                "href_args": {"agent_id": chosen["id"]} if not is_agent(user) else {},
                "confirm_required": True,
            },
        ],
        confidence=confidence,
        entity=(
            charges[0]
            if len(charges) == 1
            else chosen
        ),
        data={"source_prompt": prompt},
    )


def _agenda_when_phrase(resolved, language, *, empty=False):
    when = resolved.get("when")
    start = resolved["start"]
    if when == "today":
        return _t("jrh_ai_when_today_empty" if empty else "jrh_ai_when_today", language)
    if when == "tomorrow":
        return _t("jrh_ai_when_tomorrow_empty" if empty else "jrh_ai_when_tomorrow", language)
    if when == "day_after_tomorrow":
        return _t("jrh_ai_when_day_after_empty" if empty else "jrh_ai_when_day_after", language)
    if when == "this_week":
        return _t("jrh_ai_when_this_week", language)
    label = format_agenda_day_label(start, language)
    if language == "es":
        return f"el {label}" if empty else f"El {label}"
    return label


def _collect_agenda_items(agenda, start, end):
    items = []
    start_iso = start.isoformat()
    end_iso = end.isoformat()
    for section in agenda.get("sections") or []:
        for task in section.get("tasks") or []:
            due = task.get("due_date_value") or ""
            if due and not (start_iso <= due <= end_iso):
                continue
            items.append(task)
    return items


def _handle_agenda(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    now=None,
    **_kwargs,
):
    from modules.agent_tasks import build_agenda_view
    from modules.google_calendar import attach_google_overlay

    if not agent_id and is_agent(user):
        return _fallback(language, confidence)
    scoped = agent_id if is_agent(user) else None
    tz = organization_timezone(organization_id)
    current = (now or now_utc()).astimezone(tz)
    resolved = resolve_agenda_date(entities, current)
    start = resolved["start"]
    end = resolved["end"]
    when_phrase = _agenda_when_phrase(resolved, language)
    when_empty = _agenda_when_phrase(resolved, language, empty=True)
    due_date = start.isoformat() if start == end else None
    agenda = build_agenda_view(
        organization_id,
        agent_id=scoped,
        due_date=due_date,
        language=language,
        now=now or now_utc(),
    )
    if agent_id:
        try:
            agenda = attach_google_overlay(
                agenda,
                organization_id,
                agent_id=agent_id,
                language=language,
                now=now or now_utc(),
            )
        except Exception:
            logger.info("jrh_ai agenda google overlay skipped")
    tasks = _collect_agenda_items(agenda, start, end)
    cards = []
    for item in tasks[:8]:
        time_label = item.get("due_time_label") or item.get("due_time_value") or ""
        title = item.get("title") or item.get("type_label") or ""
        relation = item.get("relation_label") or item.get("contact_name") or ""
        cards.append(
            {
                "title": " — ".join(part for part in (time_label, title) if part),
                "subtitle": relation,
            }
        )
    if (
        resolved.get("when") == "today"
        and is_agent(user)
        and agent_id
    ):
        pending = summarize_pending_actions(
            build_agent_pending_actions(
                organization_id,
                agent_id,
                user_id=(user or {}).get("id"),
                language=language,
            ),
            language=language,
        )
        pending_total = pending.get("total") or 0
        if pending_total:
            cards.append(
                {
                    "title": _t("jrh_ai_pendings_group", language),
                    "subtitle": _t(
                        "jrh_ai_pendings_count",
                        language,
                        count=pending_total,
                    ),
                    "href_name": "pendings_center",
                }
            )
    agenda_href = {"date": start.isoformat()} if start == end else {}
    if not tasks:
        return _result(
            QUERY_AGENDA,
            "ready",
            language=language,
            message_key="jrh_ai_agenda_empty",
            cards=cards,
            actions=[
                {
                    "label_key": "jrh_ai_schedule_something",
                    "href_name": "agenda_compose",
                },
                {
                    "label": _t(
                        "jrh_ai_view_agenda_day",
                        language,
                        when=when_phrase,
                    ),
                    "href_name": "agenda_index",
                    "href_args": agenda_href,
                },
            ],
            confidence=confidence,
            data={"when": when_empty, "count": 0, "source_prompt": prompt},
        )
    return _result(
        QUERY_AGENDA,
        "ready",
        language=language,
        message_key="jrh_ai_agenda_found",
        cards=cards,
        actions=[
            {
                "label": _t(
                    "jrh_ai_view_agenda_day",
                    language,
                    when=when_phrase,
                ),
                "href_name": "agenda_index",
                "href_args": agenda_href,
            }
        ],
        confidence=confidence,
        data={
            "when": when_phrase,
            "count": len(tasks),
            "source_prompt": prompt,
        },
    )


def _property_place_label(entities):
    neighborhood = entities.get("neighborhood") or ""
    jurisdiction = entities.get("jurisdiction") or ""
    location = entities.get("location") or ""
    if neighborhood:
        return neighborhood
    if jurisdiction:
        return jurisdiction
    folded = fold_text(location)
    if folded in CABA_ALIASES:
        return "CABA"
    return location


def _property_list_href_args(entities):
    args = {}
    if entities.get("jurisdiction"):
        args["jurisdiction"] = entities["jurisdiction"]
    if entities.get("neighborhood"):
        args["neighborhood"] = entities["neighborhood"]
    if entities.get("availability"):
        args["commercial_status"] = entities["availability"]
    purpose = entities.get("listing_purpose") or entities.get("operation_type")
    if purpose:
        args["listing_purpose"] = purpose
    if entities.get("max_price") not in (None, ""):
        args["max_price"] = entities["max_price"]
    if entities.get("min_price") not in (None, ""):
        args["min_price"] = entities["min_price"]
    if entities.get("currency"):
        args["listing_currency"] = entities["currency"]
    if entities.get("property_type"):
        args["type"] = entities["property_type"]
    return args


def _format_listing_price(item):
    if item.get("listing_price") in (None, ""):
        return ""
    currency = item.get("listing_currency") or ""
    return f"{currency} {item.get('listing_price')}".strip()


def _property_detail_line(item):
    parts = []
    if item.get("rooms"):
        parts.append(f"{item['rooms']} amb")
    if item.get("bedrooms"):
        parts.append(f"{item['bedrooms']} dorm")
    if item.get("covered_m2"):
        parts.append(f"{item['covered_m2']} m²")
    return " · ".join(parts)


def _handle_properties(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    **_kwargs,
):
    matches, total = resolve_properties(
        organization_id,
        user=user,
        agent_id=agent_id,
        address=entities.get("address") or entities.get("property_text") or "",
        neighborhood=entities.get("neighborhood") or "",
        jurisdiction=entities.get("jurisdiction") or "",
        location_group=entities.get("location_group") or "",
        property_type=entities.get("property_type") or "",
        listing_purpose=entities.get("listing_purpose")
        or entities.get("operation_type")
        or "",
        availability=entities.get("availability") or "",
        max_price=entities.get("max_price"),
        min_price=entities.get("min_price"),
        currency=entities.get("currency") or "",
        rooms=entities.get("rooms"),
        bedrooms=entities.get("bedrooms"),
        bathrooms=entities.get("bathrooms"),
        min_area=entities.get("min_area"),
        parking=entities.get("parking"),
        balcony=entities.get("balcony"),
        terrace=entities.get("terrace"),
        garden=entities.get("garden"),
        limit=5,
    )
    cards = []
    for item in matches[:5]:
        type_label = ""
        if item.get("property_type"):
            type_label = _t(
                f"property_type_{item['property_type']}",
                language,
            )
        zone = item.get("neighborhood") or item.get("jurisdiction") or ""
        cards.append(
            {
                "title": item.get("name") or "",
                "subtitle": " · ".join(part for part in (zone, type_label) if part),
                "meta": _format_listing_price(item),
                "detail": _property_detail_line(item),
                "cta_key": "jrh_ai_view_property",
                "href_name": "properties_detail",
                "href_args": {"property_id": item.get("id")},
            }
        )
    list_args = _property_list_href_args(entities)
    actions = []
    if total:
        actions.append(
            {
                "label": _t("jrh_ai_view_all_count", language, count=total),
                "href_name": "properties_list",
                "href_args": list_args,
            }
        )
    else:
        actions.extend(
            [
                {
                    "label_key": "jrh_ai_suggest_widen",
                    "href_name": "properties_list",
                    "href_args": list_args,
                },
                {
                    "label_key": "jrh_ai_suggest_budget",
                    "href_name": "properties_list",
                    "href_args": list_args,
                },
            ]
        )
    place = _property_place_label(entities)
    availability_label = (
        _t("jrh_ai_properties_available_label", language)
        if entities.get("availability")
        else ""
    )
    place_label = f" en {place}" if place else ""
    return _result(
        QUERY_PROPERTIES,
        "ready" if matches else "needs_attention",
        language=language,
        message_key=(
            "jrh_ai_properties_suggest"
            if matches
            and all(
                item.get("match_score") is not None and item.get("match_score") < 70
                for item in matches
            )
            else "jrh_ai_properties_found"
            if matches
            else "jrh_ai_properties_empty"
        ),
        data={
            "count": total,
            "availability_label": availability_label,
            "place_label": place_label,
            "empty_hint": "" if matches else _t(
                "jrh_ai_properties_empty_hint",
                language,
            ),
            "source_prompt": prompt,
            "filters": list_args,
        },
        cards=cards,
        actions=actions,
        candidates=matches if len(matches) > 1 else [],
        confidence=confidence,
        entity=matches[0] if len(matches) == 1 else {},
    )


def _handle_needs(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    confidence,
    **_kwargs,
):
    from modules.property_match import rank_contact_properties

    query = entities.get("contact_name") or entities.get("agent_name") or ""
    matches = resolve_contacts(
        organization_id,
        query,
        user=user,
        agent_id=agent_id,
    )
    status, chosen, matches = pick_unique(matches, confidence=confidence)
    if status != "unique":
        return _result(
            QUERY_PROPERTY_NEEDS,
            "needs_attention",
            language=language,
            message_key=(
                "jrh_ai_contact_ambiguous"
                if status == "ambiguous"
                else "jrh_ai_contact_missing"
            ),
            candidates=matches,
            data={"name": query},
            confidence=confidence,
        )
    ranked = rank_contact_properties(
        organization_id,
        {"id": chosen["id"], "name": chosen["name"]},
        agent_id=agent_id if is_agent(user) else None,
    )
    items = ranked if isinstance(ranked, list) else []
    cards = []
    for item in list(items)[:5]:
        listing = item.get("listing") or item
        cards.append(
            {
                "title": listing.get("address") or listing.get("title") or chosen["name"],
                "subtitle": listing.get("neighborhood") or "",
            }
        )
    return _result(
        QUERY_PROPERTY_NEEDS,
        "ready",
        language=language,
        summary=chosen["name"],
        cards=cards,
        actions=[
            {
                "label_key": "jrh_cta_open_results",
                "href_name": "contacts_property_matches",
                "href_args": {"contact_id": chosen["id"]},
            }
        ],
        confidence=confidence,
        entity=chosen,
    )


def _handle_operations(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    confidence,
    **_kwargs,
):
    query = (
        entities.get("address")
        or entities.get("property_text")
        or entities.get("operation_reference")
        or entities.get("agent_name")
        or ""
    )
    matches = resolve_operations(
        organization_id,
        query,
        user=user,
        agent_id=agent_id,
    )
    status, chosen, matches = pick_unique(matches, confidence=confidence)
    if status == "empty":
        return _result(
            QUERY_OPERATIONS,
            "needs_attention",
            language=language,
            message_key="jrh_msg_operation_missing",
            data={"name": query},
            confidence=confidence,
        )
    if status == "ambiguous":
        return _result(
            QUERY_OPERATIONS,
            "needs_attention",
            language=language,
            message_key="jrh_msg_operation_ambiguous",
            candidates=matches,
            confidence=confidence,
        )
    return _result(
        QUERY_OPERATIONS,
        "ready",
        language=language,
        summary=chosen.get("name") or "",
        actions=[
            {
                "label_key": "jrh_cta_open",
                "href_name": "operations_detail",
                "href_args": {"operation_id": chosen["id"]},
            }
        ],
        confidence=confidence,
        entity=chosen,
    )


def _handle_invoices(
    *,
    organization_id,
    user,
    agent_id,
    language,
    confidence,
    **_kwargs,
):
    from modules.invoicing import list_pending_operations

    pending = list_pending_operations(
        organization_id,
        agent_id=agent_id if is_agent(user) else None,
    )
    cards = [
        {
            "title": item.get("label") or item.get("property_address") or "",
            "subtitle": item.get("side") or "",
        }
        for item in (pending or [])[:8]
    ]
    return _result(
        QUERY_INVOICES,
        "ready",
        language=language,
        summary=_t("jrh_ai_invoices_count", language, count=len(pending or [])),
        cards=cards,
        actions=[{"label_key": "jrh_cta_view", "href_name": "billing_list"}],
        confidence=confidence,
    )


def _handle_create_task(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    session,
    now=None,
    **_kwargs,
):
    from modules.agenda_ai import interpret_agenda_input

    if not agent_id:
        return _result(
            CREATE_TASK,
            "needs_attention",
            language=language,
            message_key="jrh_ai_task_needs_agent",
            confidence=confidence,
        )
    parsed = interpret_agenda_input(
        prompt,
        organization_id,
        agent_id,
        now=now or now_utc(),
    )
    item = (parsed.get("items") or [{}])[0]
    draft = {
        "intent": CREATE_TASK,
        "title": item.get("title") or entities.get("title") or prompt,
        "task_type": item.get("task_type") or "visit",
        "due_date": item.get("due_date"),
        "due_time": item.get("due_time"),
        "contact_name": item.get("contact_name") or entities.get("contact_name") or "",
        "property_id": item.get("property_id"),
    }
    if session is not None:
        session[SESSION_DRAFT_KEY] = draft
    return _result(
        CREATE_TASK,
        "ready",
        language=language,
        summary=draft["title"],
        cards=[
            {
                "title": draft["title"],
                "subtitle": draft.get("contact_name") or draft.get("task_type") or "",
            }
        ],
        actions=[
            {"label_key": "jrh_ai_confirm_task", "href_name": "jrh_ask_confirm"},
            {
                "label_key": "jrh_cta_review",
                "href_name": "agenda_compose",
                "href_args": {"prompt": prompt},
            },
        ],
        confirm_required=True,
        confidence=confidence,
        data={"draft": draft, "source_prompt": prompt},
    )


def _handle_start_invoice(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    session,
    **_kwargs,
):
    from modules.auth import ROLE_AGENT, ROLE_ADMIN
    from modules.invoice_ai_service import (
        DisambiguationResult,
        ExistingInvoiceResult,
        MissingSideResult,
        ResolvedChargeIntent,
        ResolvedInvoiceIntent,
        build_pending_invoice_context,
        parse_invoice_intent,
        resolve_invoice_intent,
    )

    if entities.get("previous_kind") == "agent" and not entities.get("agent_name"):
        from modules.database.agents_repository import get_agent_record

        previous_agent = get_agent_record(
            entities.get("previous_id"),
            organization_id,
        )
        if previous_agent:
            entities = dict(entities)
            entities["agent_name"] = previous_agent.get("name") or ""

    invoice_context = load_context(session)
    if entities.get("refers_to_previous"):
        invoice_context = dict(invoice_context)
        invoice_context["last_entity"] = {
            "kind": entities.get("previous_kind"),
            "id": entities.get("previous_id"),
        }
    parsed = parse_invoice_intent(
        prompt,
        context=invoice_context,
    )
    merged = dict(parsed.entities or {})
    merged.update({key: value for key, value in entities.items() if value not in (None, "")})
    parsed.entities = merged
    if merged.get("origin_type"):
        parsed.origin_type = merged["origin_type"]
    parsed.side = parsed.side or merged.get("side")
    resolved = resolve_invoice_intent(
        parsed,
        organization_id,
        {
            "id": (user or {}).get("id"),
            "role": ROLE_ADMIN if is_admin(user) else ROLE_AGENT,
            "agent_id": agent_id,
        },
        agent_scope=agent_id if is_agent(user) else None,
    )
    if isinstance(resolved, ExistingInvoiceResult):
        return _result(
            START_INVOICE,
            "needs_attention",
            language=language,
            message_key="billing_ai_invoice_exists",
            actions=[
                {
                    "label_key": "jrh_cta_open",
                    "href_name": "billing_detail",
                    "href_args": {"invoice_id": resolved.invoice_id},
                }
            ]
            if resolved.invoice_id
            else [],
            confidence=confidence,
            data={"source_prompt": prompt},
        )
    if isinstance(resolved, ResolvedChargeIntent):
        return _invoice_charge_preview(
            organization_id,
            resolved.charge_id,
            language=language,
            confidence=confidence,
            session=session,
            prompt=prompt,
            agent=resolved.agent,
            charge=resolved.charge,
        )
    if isinstance(resolved, MissingSideResult):
        return _result(
            START_INVOICE,
            "needs_attention",
            language=language,
            message_key="billing_ai_ask_side",
            confidence=confidence,
            entity={
                "kind": "operation",
                "id": resolved.operation_id,
                "label": resolved.operation_label,
            },
            data={"source_prompt": prompt},
            pending_invoice=build_pending_invoice_context(parsed, resolved),
            actions=[
                {
                    "label_key": "billing_invoice_buyer",
                    "href_name": "billing_prepare",
                    "href_args": {
                        "operation_id": resolved.operation_id,
                        "side": "buyer",
                    },
                },
                {
                    "label_key": "billing_invoice_seller",
                    "href_name": "billing_prepare",
                    "href_args": {
                        "operation_id": resolved.operation_id,
                        "side": "seller",
                    },
                },
            ],
        )
    if isinstance(resolved, DisambiguationResult):
        options = [
            {
                "id": item.get("id") or item.get("charge_id") or item.get("operation_id"),
                "name": item.get("name") or item.get("label"),
                "kind": item.get("kind") or "operation",
                "amount": item.get("amount"),
                "currency": item.get("currency"),
            }
            for item in (resolved.options or [])
        ]
        message_data = dict(resolved.message_data or {})
        message_data.setdefault("count", len(options))
        message_data.setdefault("source_prompt", prompt)
        return _result(
            START_INVOICE,
            "needs_attention",
            language=language,
            message_key=resolved.message_key or "jrh_ai_invoice_ambiguous",
            candidates=options,
            cards=[
                {
                    "title": item.get("name") or "",
                    "subtitle": " · ".join(
                        part
                        for part in (
                            f"{item.get('currency') or ''} {item.get('amount') or ''}".strip(),
                        )
                        if part
                    ),
                }
                for item in options[:5]
            ],
            confidence=confidence,
            data=message_data,
            pending_invoice=build_pending_invoice_context(parsed, resolved),
        )
    if isinstance(resolved, ResolvedInvoiceIntent):
        draft = {
            "intent": START_INVOICE,
            "operation_id": resolved.operation_id,
            "side": resolved.side,
        }
        if session is not None:
            session[SESSION_DRAFT_KEY] = draft
        return _result(
            START_INVOICE,
            "ready",
            language=language,
            message_key="jrh_msg_invoice_preview",
            confirm_required=True,
            confidence=confidence,
            entity={"kind": "operation", "id": resolved.operation_id, "label": ""},
            data=draft,
            actions=[
                {
                    "label_key": "jrh_ai_generate_invoice",
                    "href_name": "billing_prepare",
                    "href_args": {
                        "operation_id": resolved.operation_id,
                        "side": resolved.side,
                    },
                }
            ],
        )
    return _result(
        START_INVOICE,
        "needs_attention",
        language=language,
        message_key="jrh_msg_invoice_redirect",
        actions=[{"label_key": "jrh_cta_review", "href_name": "billing_ai_prepare", "href_args": {"prompt": prompt}}],
        confidence=confidence,
        data={"source_prompt": prompt},
    )


def _invoice_charge_preview(
    organization_id,
    charge_id,
    *,
    language,
    confidence,
    session,
    prompt,
    agent=None,
    charge=None,
):
    draft = {
        "intent": START_INVOICE,
        "charge_id": charge_id,
        "wrote": False,
    }
    if session is not None:
        session[SESSION_DRAFT_KEY] = draft
    title = (agent or {}).get("name") or ""
    subtitle = ""
    if charge:
        title = charge.get("name") or title
        subtitle = f"{charge.get('currency')} {charge.get('amount')}".strip()
    draft["source_prompt"] = prompt
    return _result(
        START_INVOICE,
        "ready",
        language=language,
        summary=title or prompt,
        cards=[
            {
                "title": title,
                "subtitle": subtitle,
                "detail": _t("billing_origin_agent_account", language),
            }
        ],
        message_key="jrh_ai_invoice_one",
        actions=[
            {
                "label_key": "jrh_ai_prepare_invoice",
                "href_name": "billing_prepare_charge",
                "href_args": {"charge_id": charge_id},
            }
        ],
        confirm_required=True,
        confidence=confidence,
        entity={
            "kind": "charge",
            "id": charge_id,
            "label": (charge or {}).get("name") or "",
        },
        data=draft,
    )


def _operation_db_id(value):
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    import re

    match = re.search(r"(?:com[- ]?)?(\d+)$", str(value).strip(), flags=re.I)
    return int(match.group(1)) if match else None


def _acm_query_from_prompt(prompt, entities):
    import re

    query = (
        entities.get("operation_reference")
        or entities.get("address")
        or entities.get("property_text")
        or entities.get("location_text")
        or ""
    )
    if query:
        return " ".join(str(query).split())
    cleaned = re.sub(
        r"(haceme un acm de la operacion de|haceme un acm de la operación de|"
        r"haceme el acm de la operacion de|haceme el acm de la operación de|"
        r"haceme un acm de|haceme el acm de|armame un acm de|"
        r"haceme un acm|haceme el acm|armame un acm|armame un comparativo|"
        r"quiero tasar|tasame|cuanto puede valer|cuánto puede valer|"
        r"esta propiedad|el depto de|el departamento de|de la operacion de|"
        r"de la operación de|la operacion de|la operación de)",
        " ",
        prompt or "",
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split())


def _owned_property_match(organization_id, property_id, agent_id):
    from modules.database.properties_repository import get_property_record

    row = get_property_record(property_id, organization_id)
    if not row:
        return None
    if int(row.get("agent_id") or 0) != int(agent_id):
        return None
    return {
        "id": row["id"],
        "name": row.get("address") or "",
        "kind": "property",
        "address": row.get("address"),
        "neighborhood": row.get("neighborhood"),
        "covered_m2": row.get("covered_m2"),
        "total_m2": row.get("total_m2"),
    }


def _hydrate_operation_match(organization_id, raw, agent_id):
    from modules.database.operations_repository import get_operation_record

    db_id = raw.get("db_id") if isinstance(raw, dict) else raw
    if db_id in (None, "") and isinstance(raw, dict):
        db_id = raw.get("id")
    db_id = _operation_db_id(db_id)
    if db_id is None:
        return None
    row = raw if isinstance(raw, dict) and raw.get("property_db_id") else get_operation_record(
        db_id, organization_id
    )
    if not row:
        return None
    if int(row.get("agent_db_id") or row.get("agent_id") or 0) != int(agent_id):
        return None
    property_id = row.get("property_db_id")
    if not property_id:
        return None
    return {
        "operation_id": row.get("db_id") or db_id,
        "operation_ref": row.get("id") if str(row.get("id", "")).startswith("COM-") else f"COM-{int(db_id):06d}",
        "property_id": property_id,
        "id": property_id,
        "name": row.get("property") or row.get("property_address") or "",
        "address": row.get("property") or row.get("property_address") or "",
        "kind": "operation",
        "match_score": raw.get("match_score") if isinstance(raw, dict) else row.get("match_score"),
    }


def _resolve_acm_operations(organization_id, query, *, user, agent_id):
    import re

    from modules.database.operations_repository import (
        get_operation_record,
        search_operations_by_id,
    )

    matches = []
    ref = _operation_db_id(query)
    if ref is not None and re.search(r"com", fold_text(query or ""), flags=re.I):
        row = get_operation_record(ref, organization_id)
        item = _hydrate_operation_match(organization_id, row, agent_id) if row else None
        if item:
            return [item]
        by_id = search_operations_by_id(query, organization_id)
        for row in by_id:
            item = _hydrate_operation_match(organization_id, row, agent_id)
            if item:
                matches.append(item)
        if matches:
            return matches
    if not query:
        return []
    resolved = resolve_operations(
        organization_id,
        query,
        user=user,
        agent_id=agent_id,
        limit=8,
    )
    for raw in resolved or []:
        item = _hydrate_operation_match(organization_id, raw, agent_id)
        if item:
            matches.append(item)
    return matches


def _resolve_acm_properties(organization_id, query, *, user, agent_id, entities):
    if not query:
        return []
    resolved = resolve_properties(
        organization_id,
        user=user,
        agent_id=agent_id,
        address=query,
        neighborhood=entities.get("neighborhood") or "",
        jurisdiction=entities.get("jurisdiction") or "",
        property_type=entities.get("property_type") or "",
        listing_purpose=entities.get("listing_purpose") or "",
    )
    matches = resolved[0] if isinstance(resolved, tuple) else resolved
    if isinstance(matches, dict):
        matches = matches.get("items") or matches.get("matches") or []
    return matches or []


def _acm_ready_result(view, *, language, confidence, prompt=""):
    from modules.i18n import translate

    facts = view.get("facts") or {}
    acm = view["acm"]
    used = facts.get("used") or 0
    confidence_label = translate(
        f"acm_confidence_{facts.get('confidence') or 'low'}",
        language=language,
    )
    return _result(
        START_ACM,
        "ready",
        language=language,
        summary=facts.get("estimated_label") or "",
        message_key="acm_jrh_ready",
        cards=[
            {
                "title": facts.get("address") or "",
                "subtitle": facts.get("estimated_label") or "",
                "href_name": "acm_detail",
                "href_args": {"acm_id": acm["id"]},
            }
        ],
        actions=[
            {
                "label_key": "acm_see_results",
                "href_name": "acm_detail",
                "href_args": {"acm_id": acm["id"]},
            },
            {
                "label_key": "acm_download_report",
                "href_name": "acm_detail",
                "href_args": {"acm_id": acm["id"]},
            },
            {
                "label_key": "acm_download_with_me",
                "href_name": "acm_pdf",
                "href_args": {"acm_id": acm["id"], "include_agent": 1},
            },
            {
                "label_key": "acm_download_without_me",
                "href_name": "acm_pdf",
                "href_args": {"acm_id": acm["id"], "include_agent": 0},
            },
        ],
        confirm_required=False,
        wrote=True,
        confidence=confidence,
        entity={
            "kind": "acm",
            "id": acm["id"],
            "label": facts.get("address") or "",
        },
        data={
            "source_prompt": prompt,
            "wrote": True,
            "used": used,
            "address": facts.get("address") or "",
            "estimated": facts.get("estimated_label") or "",
            "range_min": facts.get("range_min_label") or "",
            "range_max": facts.get("range_max_label") or "",
            "confidence": confidence_label,
        },
    )


def _handle_start_acm(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    session=None,
    **_kwargs,
):
    import re

    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(START_ACM, language, confidence)
    from modules.acm_engine import display_area
    from modules.acm_service import AcmError, create_acm_for_property, get_acm_view
    from modules.database.properties_repository import get_property_record
    from modules.entity_match import UNIQUE_MIN

    query = _acm_query_from_prompt(prompt, entities)
    wants_operation = bool(
        entities.get("operation_reference")
        or re.search(r"operacion|operación|com[- ]?\d+", fold_text(prompt or ""))
        or entities.get("previous_kind") == "operation"
    )
    matches = []
    if (
        not query
        and entities.get("previous_kind") == "acm"
        and entities.get("previous_id")
    ):
        try:
            existing = get_acm_view(
                entities["previous_id"],
                organization_id,
                user=user,
                language=language,
            )
        except AcmError:
            existing = None
        if existing:
            ready = _acm_ready_result(
                existing, language=language, confidence=confidence, prompt=prompt
            )
            ready["wrote"] = False
            ready["data"]["wrote"] = False
            return ready
    if (
        not query
        and entities.get("previous_kind") == "operation"
        and entities.get("previous_id")
    ):
        item = _hydrate_operation_match(
            organization_id, entities.get("previous_id"), agent_id
        )
        if item:
            matches = [item]
    if not matches and entities.get("operation_reference"):
        matches = _resolve_acm_operations(
            organization_id,
            entities.get("operation_reference"),
            user=user,
            agent_id=agent_id,
        )
    if not matches and wants_operation and query:
        matches = _resolve_acm_operations(
            organization_id, query, user=user, agent_id=agent_id
        )
    if (
        not matches
        and not query
        and entities.get("previous_kind") == "property"
        and entities.get("previous_id")
    ):
        owned = _owned_property_match(
            organization_id, entities.get("previous_id"), agent_id
        )
        if owned:
            matches = [owned]
    if not matches and query:
        prop_hits = _resolve_acm_properties(
            organization_id,
            query,
            user=user,
            agent_id=agent_id,
            entities=entities,
        )
        op_hits = _resolve_acm_operations(
            organization_id, query, user=user, agent_id=agent_id
        )
        merged = list(op_hits or []) + list(prop_hits or [])
        seen = set()
        matches = []
        merged.sort(key=lambda item: item.get("match_score") or 0, reverse=True)
        for item in merged:
            key = item.get("property_id") or item.get("id")
            if key in seen:
                continue
            seen.add(key)
            matches.append(item)
    if not matches:
        return _result(
            START_ACM,
            "needs_attention",
            language=language,
            message_key=(
                "acm_err_operation_missing" if wants_operation else "acm_err_property_missing"
            ),
            confidence=confidence,
        )
    top_score = matches[0].get("match_score")
    if len(matches) > 1 or (top_score is not None and top_score < UNIQUE_MIN):
        from_ops = all(item.get("operation_ref") or item.get("kind") == "operation" for item in matches)
        near = top_score is not None and top_score < UNIQUE_MIN
        return _result(
            START_ACM,
            "needs_attention",
            language=language,
            message_key=(
                "acm_err_property_suggest"
                if near
                else (
                    "acm_err_operation_ambiguous"
                    if from_ops
                    else "acm_err_property_ambiguous"
                )
            ),
            candidates=[
                {
                    "id": item.get("property_id") or item.get("id"),
                    "name": item.get("address") or item.get("name") or "",
                    "kind": "operation" if item.get("operation_ref") else "property",
                    "label": item.get("operation_ref") or "",
                }
                for item in matches[:8]
            ],
            cards=[
                {
                    "title": item.get("operation_ref") or item.get("address") or item.get("name") or "",
                    "subtitle": item.get("address") or item.get("name") or "",
                    "href_name": "acm_new",
                    "href_args": {"property_id": item.get("property_id") or item.get("id")},
                }
                for item in matches[:5]
            ],
            confidence=confidence,
            data={"count": len(matches)},
        )
    chosen = matches[0]
    property_id = chosen.get("property_id") or chosen.get("id")
    chosen_row = get_property_record(property_id, organization_id)
    if chosen_row is None:
        return _result(
            START_ACM,
            "needs_attention",
            language=language,
            message_key="acm_err_property_missing",
            confidence=confidence,
        )
    if not display_area(chosen_row):
        return _result(
            START_ACM,
            "needs_attention",
            language=language,
            message_key="acm_jrh_missing_area",
            cards=[
                {
                    "title": chosen_row.get("address") or "",
                    "subtitle": chosen_row.get("neighborhood") or "",
                }
            ],
            actions=[
                {
                    "label_key": "acm_continue",
                    "href_name": "acm_new",
                    "href_args": {"property_id": property_id},
                }
            ],
            confidence=confidence,
            entity={
                "kind": "property",
                "id": property_id,
                "label": chosen_row.get("address") or "",
            },
        )
    view = create_acm_for_property(
        organization_id,
        user=user,
        property_id=property_id,
        language=language,
    )
    return _acm_ready_result(view, language=language, confidence=confidence, prompt=prompt)


def _handle_download_acm(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    confidence,
    session=None,
    **_kwargs,
):
    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(DOWNLOAD_ACM, language, confidence)
    view = _latest_acm_view(organization_id, user, language, session=session)
    if view is None:
        return _result(
            DOWNLOAD_ACM,
            "needs_attention",
            language=language,
            message_key="acm_jrh_no_acm",
            confidence=confidence,
        )
    acm_id = view["acm"]["id"]
    include_agent = entities.get("include_agent")
    actions = []
    if include_agent is False:
        actions = [
            {
                "label_key": "acm_download_without_me",
                "href_name": "acm_pdf",
                "href_args": {"acm_id": acm_id, "include_agent": 0},
            }
        ]
        message_key = "acm_jrh_download"
    elif include_agent is True:
        actions = [
            {
                "label_key": "acm_download_with_me",
                "href_name": "acm_pdf",
                "href_args": {"acm_id": acm_id, "include_agent": 1},
            }
        ]
        message_key = "acm_jrh_download"
    else:
        actions = [
            {
                "label_key": "acm_download_with_me",
                "href_name": "acm_pdf",
                "href_args": {"acm_id": acm_id, "include_agent": 1},
            },
            {
                "label_key": "acm_download_without_me",
                "href_name": "acm_pdf",
                "href_args": {"acm_id": acm_id, "include_agent": 0},
            },
        ]
        message_key = "acm_jrh_download_choice"
    return _result(
        DOWNLOAD_ACM,
        "ready",
        language=language,
        message_key=message_key,
        actions=actions,
        confidence=confidence,
        entity={"kind": "acm", "id": acm_id, "label": (view.get("facts") or {}).get("address") or ""},
    )


def _handle_productivity(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    confidence,
    now=None,
    **_kwargs,
):
    from modules.agent_productivity import (
        ProductivityError,
        jrh_productivity_answer,
    )

    if not is_agent(user) or not agent_id:
        return _result(
            QUERY_PRODUCTIVITY,
            "needs_attention",
            language=language,
            message_key="prod_err_agent_only",
            confidence=confidence,
        )
    period = entities.get("period") or "daily"
    try:
        answer = jrh_productivity_answer(
            organization_id,
            user=user,
            language=language,
            period=period,
            now=now,
        )
    except ProductivityError as error:
        return _result(
            QUERY_PRODUCTIVITY,
            "needs_attention",
            language=language,
            message_key=error.message_key,
            confidence=confidence,
        )
    return _result(
        QUERY_PRODUCTIVITY,
        "ready",
        language=language,
        summary=answer["message"],
        actions=[
            {
                "label_key": "prod_title",
                "href_name": "productivity_home",
                "href_args": {"period": period},
            }
        ],
        confidence=confidence,
        data={"period": period, "activity": answer["view"]["activity"]},
        entity={"kind": "productivity", "label": period},
    )


def _require_acm_agent_result(intent, language, confidence):
    return _result(
        intent,
        "needs_attention",
        language=language,
        message_key="acm_err_agent_only",
        confidence=confidence,
    )


_ACM_REMOVE_STOPWORDS = frozenset(
    {
        "saca",
        "exclui",
        "excluir",
        "este",
        "esta",
        "esto",
        "comparable",
        "del",
        "los",
        "las",
        "por",
        "favor",
        "solo",
    }
)


def _latest_acm_view(organization_id, user, language="es", session=None):
    from modules.acm_service import AcmError, get_acm_view, list_agent_acms

    context = load_context(session)
    last = context.get("last_entity") or {}
    if last.get("kind") == "acm" and last.get("id"):
        try:
            return get_acm_view(
                last["id"],
                organization_id,
                user=user,
                language=language,
            )
        except AcmError:
            pass
    items = list_agent_acms(organization_id, user=user)
    if not items:
        return None
    return get_acm_view(
        items[0]["id"],
        organization_id,
        user=user,
        language=language,
    )


def _match_acm_comparable(prompt, rows):
    tokens = [
        part
        for part in fold_text(prompt or "").split()
        if len(part) > 2 and part not in _ACM_REMOVE_STOPWORDS
    ]
    if not tokens:
        return None
    best = None
    best_score = 0
    for row in rows or []:
        label = fold_text(
            f"{row.get('external_reference') or ''} {row.get('snapshot_location') or ''}"
        )
        if not label:
            continue
        hits = sum(1 for token in tokens if token in label)
        score = hits
        if hits == len(tokens):
            score += 2
        if score > best_score:
            best = row
            best_score = score
    return best if best_score else None


def _handle_query_acm(
    *,
    organization_id,
    user,
    agent_id,
    language,
    confidence,
    session=None,
    **_kwargs,
):
    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(QUERY_ACM, language, confidence)
    view = _latest_acm_view(organization_id, user, language, session=session)
    if view is None:
        return _result(
            QUERY_ACM,
            "needs_attention",
            language=language,
            message_key="acm_jrh_no_acm",
            confidence=confidence,
        )
    acm = view["acm"]
    facts = view.get("facts") or {}
    return _result(
        QUERY_ACM,
        "ready",
        language=language,
        message_key="acm_jrh_query",
        summary=facts.get("estimated_label") or "",
        cards=[
            {
                "title": facts.get("address") or "",
                "subtitle": facts.get("estimated_label") or "",
                "href_name": "acm_detail",
                "href_args": {"acm_id": acm["id"]},
            }
        ],
        confidence=confidence,
        entity={"kind": "acm", "id": acm["id"], "label": facts.get("address") or ""},
        data={
            "estimated": facts.get("estimated"),
            "range_min": facts.get("range_min"),
            "range_max": facts.get("range_max"),
        },
    )


def _handle_acm_explain(
    *,
    organization_id,
    user,
    agent_id,
    language,
    confidence,
    session=None,
    **_kwargs,
):
    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(ACM_EXPLAIN, language, confidence)
    view = _latest_acm_view(organization_id, user, language, session=session)
    if view is None:
        return _result(
            ACM_EXPLAIN,
            "needs_attention",
            language=language,
            message_key="acm_jrh_no_acm",
            confidence=confidence,
        )
    explained = view.get("ai_explanation") or {}
    facts = view.get("facts") or {}
    return _result(
        ACM_EXPLAIN,
        "ready",
        language=language,
        message_key="acm_jrh_explain",
        summary=explained.get("text") or "",
        cards=[
            {
                "title": facts.get("address") or "",
                "subtitle": facts.get("estimated_label") or "",
                "href_name": "acm_detail",
                "href_args": {"acm_id": view["acm"]["id"]},
            }
        ],
        confidence=confidence,
        entity={"kind": "acm", "id": view["acm"]["id"]},
        data={
            "estimated": facts.get("estimated"),
            "source": explained.get("source"),
        },
    )


def _handle_acm_remove(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    session=None,
    **_kwargs,
):
    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(ACM_REMOVE_COMPARABLE, language, confidence)
    from modules.acm_service import set_comparable_selected

    view = _latest_acm_view(organization_id, user, language, session=session)
    if view is None:
        return _result(
            ACM_REMOVE_COMPARABLE,
            "needs_attention",
            language=language,
            message_key="acm_jrh_no_acm",
            confidence=confidence,
        )
    match = _match_acm_comparable(prompt, view.get("comparables") or [])
    if match is None and view.get("comparables"):
        match = view.get("best_comparable") or view["comparables"][0]
    if match is None:
        return _result(
            ACM_REMOVE_COMPARABLE,
            "needs_attention",
            language=language,
            message_key="acm_err_comparable_missing",
            confidence=confidence,
        )
    name = match.get("external_reference") or match.get("snapshot_location") or ""
    if not entities.get("confirmed"):
        return _result(
            ACM_REMOVE_COMPARABLE,
            "needs_attention",
            language=language,
            message_key="acm_jrh_exclude_preview",
            confirm_required=True,
            data={"name": name, "comparable_id": match.get("id")},
            entity={"kind": "acm", "id": view["acm"]["id"]},
            confidence=confidence,
            actions=[
                {
                    "label_key": "acm_exclude_confirm_yes",
                    "href_name": "acm_comparables",
                    "href_args": {
                        "acm_id": view["acm"]["id"],
                        "confirm_exclude": match.get("id"),
                    },
                }
            ],
        )
    set_comparable_selected(
        view["acm"]["id"],
        organization_id,
        user=user,
        comparable_id=match["id"],
        selected=False,
        language=language,
    )
    return _result(
        ACM_REMOVE_COMPARABLE,
        "ready",
        language=language,
        message_key="acm_jrh_excluded",
        wrote=True,
        confidence=confidence,
        entity={"kind": "acm", "id": view["acm"]["id"]},
    )


def _handle_acm_filter(
    *,
    organization_id,
    user,
    agent_id,
    language,
    confidence,
    session=None,
    **_kwargs,
):
    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(ACM_FILTER_COMPARABLES, language, confidence)
    from modules.acm_service import refresh_draft

    view = _latest_acm_view(organization_id, user, language, session=session)
    if view is None:
        return _result(
            ACM_FILTER_COMPARABLES,
            "needs_attention",
            language=language,
            message_key="acm_jrh_no_acm",
            confidence=confidence,
        )
    refresh_draft(
        view["acm"]["id"],
        organization_id,
        user=user,
        language=language,
        filters={"include_closing": True, "include_listing": False},
    )
    return _result(
        ACM_FILTER_COMPARABLES,
        "ready",
        language=language,
        message_key="acm_jrh_filter_closings",
        wrote=True,
        confidence=confidence,
        entity={"kind": "acm", "id": view["acm"]["id"]},
        actions=[
            {
                "label_key": "acm_see_results",
                "href_name": "acm_detail",
                "href_args": {"acm_id": view["acm"]["id"]},
            }
        ],
    )


def _handle_acm_scenario(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    prompt,
    confidence,
    session=None,
    **_kwargs,
):
    if not is_agent(user) or not agent_id:
        return _require_acm_agent_result(ACM_PRICE_SCENARIO, language, confidence)
    from modules.acm_engine import compute_price_scenario
    from modules.formatting import format_money

    view = _latest_acm_view(organization_id, user, language, session=session)
    if view is None:
        return _result(
            ACM_PRICE_SCENARIO,
            "needs_attention",
            language=language,
            message_key="acm_jrh_no_acm",
            confidence=confidence,
        )
    proposed = entities.get("proposed_price") or ""
    if not proposed:
        import re

        found = re.search(r"(\d[\d.\s]{2,})", prompt or "")
        proposed = found.group(1) if found else ""
    cleaned = proposed.replace(" ", "").replace(".", "")
    if "mil" in fold_text(prompt or "") and cleaned.isdigit() and int(cleaned) < 10000:
        cleaned = str(int(cleaned) * 1000)
    acm = view["acm"]
    scenario = compute_price_scenario(
        cleaned,
        acm.get("estimated_value"),
        acm.get("suggested_min_value"),
        acm.get("suggested_max_value"),
    )
    if scenario is None:
        return _result(
            ACM_PRICE_SCENARIO,
            "needs_attention",
            language=language,
            message_key="acm_err_manual_price",
            confidence=confidence,
        )
    band_key = (
        "acm_scenario_in"
        if scenario["in_range"]
        else (
            "acm_scenario_out_below"
            if scenario["band"] == "below"
            else "acm_scenario_out_above"
        )
    )
    currency = acm.get("currency") or "USD"
    return _result(
        ACM_PRICE_SCENARIO,
        "ready",
        language=language,
        message_key="acm_jrh_scenario",
        data={
            "price": format_money(scenario["proposed"], currency=currency, language=language),
            "band": translate(band_key, language=language),
            "delta": str(scenario["delta_vs_market_pct"]),
            "estimated": acm.get("estimated_value"),
        },
        confidence=confidence,
        entity={"kind": "acm", "id": acm["id"]},
    )


def _handle_start_payment(
    *,
    language,
    confidence,
    prompt,
    **_kwargs,
):
    return _result(
        START_AGENT_PAYMENT,
        "ready",
        language=language,
        message_key="jrh_ai_payment_preview",
        confirm_required=True,
        confidence=confidence,
        actions=[
            {
                "label_key": "jrh_ai_register_payment",
                "href_name": "agent_payment_ai_new",
            }
        ],
        data={"source_prompt": prompt, "wrote": False},
    )


def confirm_jrh_action(
    *,
    organization_id,
    user,
    agent_id,
    session,
    language="es",
):
    """Execute a previously previewed write using existing services."""
    draft = (session or {}).get(SESSION_DRAFT_KEY) or {}
    intent = draft.get("intent")
    if intent not in WRITE_ACTIONS:
        return _result(
            FALLBACK,
            "fallback",
            language=language,
            message_key="jrh_ai_nothing_to_confirm",
        )
    if intent == CREATE_TASK:
        from modules.agent_tasks import create_task

        if not agent_id:
            return _result(
                CREATE_TASK,
                "needs_attention",
                language=language,
                message_key="jrh_ai_task_needs_agent",
            )
        task = create_task(
            organization_id,
            agent_id,
            {
                "title": draft.get("title") or "JRH",
                "task_type": draft.get("task_type") or "visit",
                "due_date": draft.get("due_date"),
                "due_time": draft.get("due_time"),
                "contact_name": draft.get("contact_name") or "",
                "property_id": draft.get("property_id"),
            },
            created_by_user_id=(user or {}).get("id"),
        )
        if session is not None:
            session.pop(SESSION_DRAFT_KEY, None)
        return _result(
            CREATE_TASK,
            "ready",
            language=language,
            message_key="jrh_ai_task_created",
            wrote=True,
            data={"task_id": task.get("id")},
            actions=[{"label_key": "jrh_cta_view", "href_name": "agenda_index"}],
        )
    return _result(
        intent,
        "ready",
        language=language,
        message_key="jrh_ai_open_existing_flow",
        wrote=False,
        confirm_required=True,
        data=draft,
    )
