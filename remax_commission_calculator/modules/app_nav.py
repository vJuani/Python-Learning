"""Central app navigation. Roles filter items; backend remains the authority."""

from __future__ import annotations

import os

from flask import url_for

from modules.auth import is_admin, is_agent, is_guest_session, is_team_leader


ROLE_ADMIN = "admin"
ROLE_AGENT = "agent"
ROLE_GUEST = "guest"

ICONS = {
    "home": '<path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1v-9.5Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>',
    "contacts": '<circle cx="9" cy="8" r="3" stroke="currentColor" stroke-width="1.75"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><path d="M16 8h5M18.5 5.5v5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "properties": '<path d="M4 20V9.5L12 4l8 5.5V20H4Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M10 20v-6h4v6" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>',
    "acm": '<path d="M5 19V9M12 19V5M19 19v-7" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><path d="M4 19h16" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "agenda": '<rect x="4" y="5" width="16" height="15" rx="2" stroke="currentColor" stroke-width="1.75"/><path d="M4 10h16M9 3v4M15 3v4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "operations": '<path d="M8 7h12M8 12h12M8 17h8" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><circle cx="4.5" cy="7" r="1" fill="currentColor"/><circle cx="4.5" cy="12" r="1" fill="currentColor"/><circle cx="4.5" cy="17" r="1" fill="currentColor"/>',
    "wallet": '<path d="M4 7h16v12H4z" stroke="currentColor" stroke-width="1.75"/><path d="M8 11h8M8 15h5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><path d="M8 4h8" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "cash": '<rect x="3" y="7" width="18" height="12" rx="2" stroke="currentColor" stroke-width="1.75"/><path d="M3 10h18" stroke="currentColor" stroke-width="1.75"/><path d="M12 13v3M10.5 14.5h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    "billing": '<path d="M7 3h10v18H7z" stroke="currentColor" stroke-width="1.75"/><path d="M9 8h6M9 12h6M9 16h4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "marketing": '<path d="M5 10v4l12-6v12L5 14" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M5 14c0 2 1.5 4 4 4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "jrh": '<path d="M12 4v3M12 17v3M4 12h3M17 12h3" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><circle cx="12" cy="12" r="4.5" stroke="currentColor" stroke-width="1.75"/>',
    "reports": '<path d="M5 19V9M12 19V5M19 19v-7" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "productivity": '<path d="M12 3a9 9 0 1 0 9 9" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><path d="M12 7v5l3 2" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "team": '<circle cx="8" cy="9" r="2.5" stroke="currentColor" stroke-width="1.75"/><circle cx="16" cy="9" r="2.5" stroke="currentColor" stroke-width="1.75"/><path d="M3 19c0-2.8 2.2-5 5-5s5 2.2 5 5M13 19c.4-2.2 2-4 4-4 1.2 0 2.3.5 3 1.2" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "settings": '<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.75"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "users": '<circle cx="12" cy="8" r="3" stroke="currentColor" stroke-width="1.75"/><path d="M5 20c0-3.3 2.7-6 7-6s7 2.7 7 6" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "agents": '<circle cx="9" cy="8" r="3" stroke="currentColor" stroke-width="1.75"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/><path d="M19 8v6M16 11h6" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "plug": '<rect x="4" y="5" width="16" height="14" rx="2" stroke="currentColor" stroke-width="1.75"/><path d="M8 12h8M8 16h5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "bell": '<path d="M12 4a5 5 0 0 0-5 5v3.2L5.4 15h13.2L17 12.2V9a5 5 0 0 0-5-5Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M10 18a2 2 0 0 0 4 0" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "profile": '<circle cx="12" cy="8" r="3.5" stroke="currentColor" stroke-width="1.75"/><path d="M5 20c0-3.3 2.7-6 7-6s7 2.7 7 6" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    "office": '<path d="M4 20V6l8-3 8 3v14" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M9 20v-6h6v6" stroke="currentColor" stroke-width="1.75"/>',
}

NAV_GROUPS = (
    # Home lives on the JRH logo in the rail brand, not as a duplicate house icon.
    {
        "key": "crm",
        "label_key": "nav_group_crm",
        "icon": "contacts",
        "roles": (ROLE_AGENT,),
        "children": (
            {
                "key": "contacts",
                "label_key": "contacts_title",
                "icon": "contacts",
                "endpoint": "contacts_index",
                "active_prefixes": ("contacts_",),
                "roles": (ROLE_AGENT,),
                "require_agent_id": True,
            },
        ),
    },
    {
        "key": "properties",
        "label_key": "nav_group_properties",
        "icon": "properties",
        "roles": (ROLE_ADMIN, ROLE_AGENT, ROLE_GUEST),
        "children": (
            {
                "key": "properties_list",
                "label_key": "nav_my_properties",
                "icon": "properties",
                "endpoint": "properties_list",
                "active_prefixes": ("properties_",),
                "roles": (ROLE_ADMIN, ROLE_AGENT, ROLE_GUEST),
            },
            {
                "key": "acm",
                "label_key": "nav_acm",
                "icon": "acm",
                "endpoint": "acm_list",
                "active_prefixes": ("acm_",),
                "roles": (ROLE_AGENT,),
            },
        ),
    },
    {
        "key": "agenda",
        "label_key": "agenda_title",
        "icon": "agenda",
        "roles": (ROLE_AGENT,),
        "item": {
            "key": "agenda",
            "endpoint": "agenda_index",
            "active_prefixes": ("agenda_",),
            "require_agent_id": True,
        },
    },
    {
        "key": "operations",
        "label_key": "nav_group_operations",
        "icon": "operations",
        "roles": (ROLE_ADMIN, ROLE_AGENT, ROLE_GUEST),
        "children": (
            {
                "key": "operations_list",
                "label_key": "nav_my_operations",
                "icon": "operations",
                "endpoint": "operations_list",
                "active_prefixes": ("operations_",),
                "roles": (ROLE_ADMIN, ROLE_AGENT, ROLE_GUEST),
            },
            {
                "key": "agent_accounts",
                "label_key": "nav_agent_account",
                "icon": "wallet",
                "endpoint": "agent_account_index",
                "active_prefixes": ("agent_account",),
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "my_account",
                "label_key": "nav_my_agent_account",
                "icon": "wallet",
                "endpoint": "my_agent_account",
                "active_prefixes": (),
                "roles": (ROLE_AGENT,),
                "require_agent_id": True,
            },
        ),
    },
    {
        "key": "treasury",
        "label_key": "nav_group_treasury",
        "icon": "cash",
        "roles": (ROLE_ADMIN,),
        "children": (
            {
                "key": "cash",
                "label_key": "nav_cash",
                "icon": "cash",
                "endpoint": "cash_list",
                "active_prefixes": ("cash_",),
                "exclude_prefixes": ("cash_ai",),
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "cash_ai",
                "label_key": "nav_cash_ai",
                "icon": "cash",
                "endpoint": "cash_ai_new",
                "active_prefixes": ("cash_ai",),
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "billing_admin",
                "label_key": "nav_billing",
                "icon": "billing",
                "endpoint": "billing_list",
                "active_prefixes": ("billing_",),
                "roles": (ROLE_ADMIN,),
            },
        ),
    },
    {
        "key": "billing",
        "label_key": "nav_billing",
        "icon": "billing",
        "roles": (ROLE_AGENT,),
        "item": {
            "key": "billing",
            "endpoint": "billing_list",
            "active_prefixes": ("billing_",),
        },
    },
    {
        "key": "marketing",
        "label_key": "marketing_ia_title",
        "icon": "marketing",
        "roles": (ROLE_AGENT,),
        "item": {
            "key": "marketing",
            "endpoint": "marketing_home",
            "active_prefixes": ("marketing_",),
            "require_agent_id": True,
        },
    },
    {
        "key": "jrh",
        "label_key": "nav_jrh_ia",
        "icon": "jrh",
        "roles": (ROLE_ADMIN, ROLE_AGENT),
        "featured": True,
        "chip": "IA",
        "item": {
            "key": "jrh",
            "endpoint": "jrh_ask",
            "active_prefixes": ("jrh_",),
            "chip": "IA",
        },
    },
    {
        "key": "reports",
        "label_key": "nav_group_reports",
        "icon": "reports",
        "roles": (ROLE_ADMIN, ROLE_AGENT, ROLE_GUEST),
        "children": (
            {
                "key": "reports",
                "label_key": "nav_reports",
                "icon": "reports",
                "endpoint": "reports_index",
                "active_prefixes": ("reports_",),
                "roles": (ROLE_ADMIN, ROLE_AGENT, ROLE_GUEST),
            },
            {
                "key": "team_report_agent",
                "label_key": "nav_team_report",
                "icon": "team",
                "endpoint": "team_report",
                "endpoint_args": "agent_team",
                "active_prefixes": ("team_report",),
                "roles": (ROLE_AGENT,),
                "require_agent_id": True,
                "require_team_leader": True,
            },
            {
                "key": "productivity",
                "label_key": "nav_productivity",
                "icon": "productivity",
                "endpoint": "productivity_home",
                "active_prefixes": ("productivity_",),
                "roles": (ROLE_AGENT,),
            },
        ),
    },
    {
        "key": "settings",
        "label_key": "nav_group_settings",
        "icon": "settings",
        "roles": (ROLE_ADMIN, ROLE_AGENT),
        "children": (
            {
                "key": "organization",
                "label_key": "nav_organization",
                "icon": "office",
                "endpoint": "organization_settings",
                "active_prefixes": (),
                "exact": True,
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "users",
                "label_key": "nav_users",
                "icon": "users",
                "endpoint": "users_list",
                "active_prefixes": ("users_",),
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "agents",
                "label_key": "nav_agents",
                "icon": "agents",
                "endpoint": "agents_list",
                "active_prefixes": ("agents_",),
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "integrations",
                "label_key": "settings_integrations_title",
                "icon": "plug",
                "endpoint": "settings_integrations",
                "active_prefixes": ("settings_integrations", "settings_property"),
                "roles": (ROLE_ADMIN, ROLE_AGENT),
            },
            {
                "key": "notifications",
                "label_key": "nav_notifications",
                "icon": "bell",
                "endpoint": "settings_notifications",
                "active_prefixes": ("settings_notifications", "notifications_"),
                "roles": (ROLE_ADMIN, ROLE_AGENT),
                "badge": "unread_notifications",
            },
            {
                "key": "office_notifications",
                "label_key": "office_announcement_title",
                "icon": "office",
                "endpoint": "settings_office_notifications",
                "active_prefixes": ("settings_office",),
                "roles": (ROLE_ADMIN,),
            },
            {
                "key": "profile",
                "label_key": "nav_profile",
                "icon": "profile",
                "endpoint": "my_wallet",
                "active_prefixes": (),
                "extra_endpoints": ("my_wallet", "agents_detail"),
                "roles": (ROLE_AGENT,),
                "require_agent_id": True,
            },
        ),
    },
)

OMITTED_ITEMS = (
    {
        "label": "Necesidades",
        "reason": "No list route; lives at /contacts/<id>/need",
    },
    {
        "label": "Matches",
        "reason": "No list route; lives at /contacts/<id>/property-matches",
    },
    {
        "label": "Captaciones / Inventario",
        "reason": "Same page as properties_list filters",
    },
    {
        "label": "Visitas / Tareas",
        "reason": "Same agenda_index UI; no independent routes",
    },
    {
        "label": "Comisiones",
        "reason": "No standalone page; shown on operation detail",
    },
    {
        "label": "Movimientos",
        "reason": "Same as cash_list",
    },
    {
        "label": "Métricas",
        "reason": "No /metrics route; KPIs live on dashboard/reports",
    },
    {
        "label": "Historia / Post / Flyer",
        "reason": "Marketing chat lives at /marketing; composer remains /marketing/new",
    },
)


def use_legacy_nav():
    raw = (os.environ.get("APP_NAV_LEGACY") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def viewer_role(user):
    try:
        guest = is_guest_session()
    except RuntimeError:
        guest = False
    if guest or user is None:
        return ROLE_GUEST
    if is_admin(user):
        return ROLE_ADMIN
    if is_agent(user):
        return ROLE_AGENT
    return ROLE_GUEST


def _role_allowed(roles, role):
    return role in (roles or ())


def _has_agent_id(user):
    return bool(user and user.get("agent_id"))


def _endpoint_args(spec, user):
    kind = spec.get("endpoint_args")
    if kind == "agent_team" and user and user.get("agent_id"):
        return {"team_leader_id": user["agent_id"]}
    return {}


def item_is_active(spec, endpoint):
    if not endpoint:
        return False
    extras = spec.get("extra_endpoints") or ()
    if endpoint in extras:
        return True
    if spec.get("exact"):
        return endpoint == spec.get("endpoint")
    if endpoint == spec.get("endpoint"):
        return True
    for prefix in spec.get("exclude_prefixes") or ():
        if endpoint.startswith(prefix):
            return False
    for prefix in spec.get("active_prefixes") or ():
        if endpoint.startswith(prefix):
            return True
    return False


LIVE_BADGE_KEYS = frozenset({"unread_notifications"})


def _resolve_item(spec, *, user, role, endpoint, badges, language, translate):
    if not _role_allowed(spec.get("roles") or (role,), role):
        return None
    if spec.get("require_agent_id") and not _has_agent_id(user):
        return None
    if spec.get("require_team_leader") and not is_team_leader(user):
        return None
    try:
        href = url_for(spec["endpoint"], **_endpoint_args(spec, user))
    except Exception:
        return None
    badge_key = spec.get("badge")
    badge = badges.get(badge_key) if badge_key else None
    if not badge:
        badge = None
    return {
        "key": spec["key"],
        "label": translate(spec.get("label_key") or spec["key"], language),
        "icon": spec.get("icon") or "home",
        "icon_svg": ICONS.get(spec.get("icon") or "home", ICONS["home"]),
        "href": href,
        "endpoint": spec["endpoint"],
        "active": item_is_active(spec, endpoint),
        "badge": badge,
        "live_badge": badge_key if badge_key in LIVE_BADGE_KEYS else None,
        "chip": spec.get("chip"),
        "featured": bool(spec.get("featured")),
    }


def build_app_nav(
    user,
    *,
    endpoint=None,
    unread_notifications=0,
    language="es",
    translate=None,
):
    from modules.i18n import translate as default_translate

    translate = translate or default_translate
    role = viewer_role(user)
    badges = {"unread_notifications": int(unread_notifications or 0)}
    groups = []
    for group in NAV_GROUPS:
        if not _role_allowed(group["roles"], role):
            continue
        children = []
        for child in group.get("children") or ():
            resolved = _resolve_item(
                {**child, "icon": child.get("icon") or group["icon"]},
                user=user,
                role=role,
                endpoint=endpoint,
                badges=badges,
                language=language,
                translate=translate,
            )
            if resolved:
                children.append(resolved)
        leaf = None
        if group.get("item"):
            leaf = _resolve_item(
                {
                    **group["item"],
                    "roles": group["roles"],
                    "label_key": group["label_key"],
                    "icon": group["icon"],
                    "featured": group.get("featured"),
                    "chip": group.get("chip"),
                },
                user=user,
                role=role,
                endpoint=endpoint,
                badges=badges,
                language=language,
                translate=translate,
            )
        if not children and leaf is None:
            continue
        child_active = any(item["active"] for item in children)
        child_badges = [item["badge"] for item in children if item.get("badge")]
        live_keys = {item["live_badge"] for item in children if item.get("live_badge")}
        groups.append(
            {
                "key": group["key"],
                "label": translate(group["label_key"], language),
                "icon": group["icon"],
                "icon_svg": ICONS.get(group["icon"], ICONS["home"]),
                "featured": bool(group.get("featured")),
                "item": leaf,
                "children": children,
                "active": bool((leaf and leaf["active"]) or child_active),
                "expanded": child_active,
                "badge": sum(child_badges) if child_badges else None,
                "live_badge": live_keys.pop() if len(live_keys) == 1 else None,
                "href": None if children else (leaf or {}).get("href"),
            }
        )
    return {"role": role, "groups": groups}


def visible_endpoints(user):
    nav = build_app_nav(user, endpoint=None, unread_notifications=0)
    names = []
    for group in nav["groups"]:
        if group.get("item"):
            names.append(group["item"]["endpoint"])
        for child in group["children"]:
            names.append(child["endpoint"])
    return names


def all_configured_endpoints():
    names = []
    for group in NAV_GROUPS:
        if group.get("item"):
            names.append(group["item"]["endpoint"])
        for child in group.get("children") or ():
            names.append(child["endpoint"])
    return names
