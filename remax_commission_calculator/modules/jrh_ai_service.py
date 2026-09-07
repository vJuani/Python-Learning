"""Transversal JRH assistant. Interprets, resolves, never writes money."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from modules.agent_account import build_agent_detail_view
from modules.auth import is_admin, is_agent
from modules.database.tenant import require_organization_id
from modules.i18n import translate
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
    START_AGENT_PAYMENT,
    START_INVOICE,
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
from modules.organization_time import (
    local_date_bounds_utc,
    now_utc,
    organization_timezone,
)
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
    text = (prompt or "").strip()
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
    if entities.get("self") and agent_id:
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
    view = build_agent_detail_view(
        organization_id,
        chosen["id"],
        language=language,
    )
    balances = view.get("display_balances") or {}
    charges = resolve_pending_charges(organization_id, chosen["id"])
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
    href_name = "my_agent_account" if is_agent(user) else "agent_account_detail"
    href_args = {} if is_agent(user) else {"agent_id": chosen["id"]}
    return _result(
        QUERY_AGENT_ACCOUNT,
        "ready",
        language=language,
        summary=chosen["name"],
        cards=cards,
        actions=[
            {"label_key": "jrh_ai_view_account", "href_name": href_name, "href_args": href_args},
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


def _handle_agenda(
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
    from modules.database.agent_tasks_repository import (
        STATUS_PENDING,
        list_agent_tasks,
    )

    if not agent_id and is_agent(user):
        return _fallback(language, confidence)
    scoped = agent_id if is_agent(user) else None
    tz = organization_timezone(organization_id)
    current = (now or now_utc()).astimezone(tz)
    day = current.date()
    if entities.get("when") == "tomorrow":
        day = day + timedelta(days=1)
    start, end = local_date_bounds_utc(day, tz)
    tasks = list_agent_tasks(
        organization_id,
        agent_id=scoped,
        statuses=(STATUS_PENDING,),
        due_from=start,
        due_to=end,
        limit=20,
    )
    cards = [
        {
            "title": item.get("title") or item.get("task_type") or "",
            "subtitle": item.get("due_label") or item.get("contact_name") or "",
        }
        for item in tasks[:8]
    ]
    return _result(
        QUERY_AGENDA,
        "ready",
        language=language,
        summary=_t("jrh_ai_agenda_count", language, count=len(tasks)),
        cards=cards,
        actions=[{"label_key": "jrh_cta_view", "href_name": "agenda_index"}],
        confidence=confidence,
    )


def _handle_properties(
    *,
    organization_id,
    user,
    agent_id,
    language,
    entities,
    confidence,
    **_kwargs,
):
    matches = resolve_properties(
        organization_id,
        user=user,
        agent_id=agent_id,
        address=entities.get("address") or "",
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
        price = ""
        if item.get("listing_price") not in (None, ""):
            amount = item.get("listing_price")
            currency = item.get("listing_currency") or ""
            price = f"{currency} {amount}".strip()
        rooms_label = (
            f"{item.get('rooms')} amb" if item.get("rooms") else ""
        )
        zone = item.get("neighborhood") or item.get("jurisdiction") or ""
        subtitle = " · ".join(
            part for part in (zone, type_label, price, rooms_label) if part
        )
        cards.append(
            {
                "title": item.get("name") or "",
                "subtitle": subtitle,
                "href_name": "properties_detail",
                "href_args": {"property_id": item.get("id")},
            }
        )
    actions = [
        {"label_key": "jrh_cta_view_all", "href_name": "properties_list"},
    ]
    if not matches:
        actions.extend(
            [
                {"label_key": "jrh_ai_suggest_widen", "href_name": "properties_list"},
                {"label_key": "jrh_ai_suggest_budget", "href_name": "properties_list"},
            ]
        )
    return _result(
        QUERY_PROPERTIES,
        "ready" if matches else "needs_attention",
        language=language,
        message_key=(
            "jrh_ai_properties_count"
            if matches
            else "jrh_ai_properties_empty"
        ),
        data={
            "count": len(matches),
            "empty_hint": "" if matches else _t(
                "jrh_ai_properties_empty_hint",
                language,
            ),
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
    query = entities.get("address") or entities.get("agent_name") or ""
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
    if entities.get("refers_to_previous") and entities.get("previous_id"):
        previous_kind = entities.get("previous_kind")
        if previous_kind == "charge":
            return _invoice_charge_preview(
                organization_id,
                entities.get("previous_id"),
                language=language,
                confidence=confidence,
                session=session,
                prompt=prompt,
            )
        if previous_kind == "agent" and not entities.get("agent_name"):
            from modules.database.agents_repository import get_agent_record

            previous_agent = get_agent_record(
                entities.get("previous_id"),
                organization_id,
            )
            if previous_agent:
                entities = dict(entities)
                entities["agent_name"] = previous_agent.get("name") or ""

    query = entities.get("agent_name") or ""
    matches = resolve_agents(
        organization_id,
        query,
        user=user,
        agent_id=agent_id,
    )
    if query and matches:
        status, chosen, matches = pick_unique(matches, confidence=confidence)
        if status == "ambiguous":
            return _result(
                START_INVOICE,
                "needs_attention",
                language=language,
                message_key="jrh_ai_agent_ambiguous",
                candidates=matches,
                confidence=confidence,
            )
        if status == "unique":
            charges = resolve_pending_charges(
                organization_id,
                chosen["id"],
                currency=entities.get("currency"),
                hint=entities.get("charge_hint") or entities.get("period") or "",
            )
            if len(charges) > 1:
                return _result(
                    START_INVOICE,
                    "needs_attention",
                    language=language,
                    message_key="jrh_ai_charge_ambiguous",
                    candidates=charges,
                    confidence=confidence,
                    entity=chosen,
                )
            if len(charges) == 1:
                return _invoice_charge_preview(
                    organization_id,
                    charges[0]["id"],
                    language=language,
                    confidence=confidence,
                    session=session,
                    prompt=prompt,
                    agent=chosen,
                    charge=charges[0],
                )

    from modules.auth import ROLE_AGENT, ROLE_ADMIN
    from modules.invoice_ai_service import (
        DisambiguationResult,
        ResolvedInvoiceIntent,
        parse_invoice_intent,
        resolve_invoice_intent,
    )

    parsed = parse_invoice_intent(prompt)
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
    if isinstance(resolved, DisambiguationResult):
        options = [
            {
                "id": item.get("operation_id"),
                "name": item.get("label"),
                "kind": "operation",
            }
            for item in (resolved.options or [])
        ]
        return _result(
            START_INVOICE,
            "needs_attention",
            language=language,
            message_key=resolved.message_key or "jrh_ai_invoice_ambiguous",
            candidates=options,
            confidence=confidence,
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
        subtitle = f"{charge.get('name')} · {charge.get('currency')} {charge.get('amount')}"
    return _result(
        START_INVOICE,
        "ready",
        language=language,
        summary=title or prompt,
        cards=[{"title": title, "subtitle": subtitle}],
        actions=[
            {
                "label_key": "jrh_ai_generate_invoice",
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
