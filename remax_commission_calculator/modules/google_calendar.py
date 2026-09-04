"""
Google Calendar OAuth, push and pull for the agent agenda.

V1 is best-effort: a failed push never blocks creating or updating a
JRH task. Pulled Google events are an overlay (read-only cards), not
new rows in ``agent_tasks``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken

from modules.branding import get_app_base_url
from modules.config import get_secret_key
from modules.database.agent_tasks_repository import set_google_event_id
from modules.database.google_calendar_repository import (
    delete_calendar_connection,
    get_calendar_connection,
    mark_calendar_error,
    touch_calendar_synced,
    update_calendar_cache,
    update_calendar_tokens,
    upsert_calendar_connection,
)
from modules.database.users_repository import get_user_by_agent_id
from modules.organization_time import (
    organization_timezone,
    parse_utc_iso,
    to_utc_iso,
)


logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_EVENTS_URL = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
)

SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
)
OAUTH_STATE_SESSION_KEY = "google_calendar_oauth_state"
CACHE_TTL = timedelta(minutes=5)
HTTP_TIMEOUT_SECONDS = 20
JRH_TASK_PROPERTY = "jrhTaskId"
JRH_ORG_PROPERTY = "jrhOrgId"
DEFAULT_DURATION_MINUTES = 60

STATE_UNCONFIGURED = "unconfigured"
STATE_DISCONNECTED = "disconnected"
STATE_SYNCED = "synced"
STATE_ERROR = "error"
STATE_HIDDEN = "hidden"


class GoogleCalendarError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


class GoogleCalendarHttpError(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail or str(status_code))
        self.status_code = status_code
        self.detail = detail or ""


def is_configured():
    return bool(get_client_id() and get_client_secret())


def get_client_id():
    return os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()


def get_client_secret():
    return os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()


def redirect_uri():
    return f"{get_app_base_url().rstrip('/')}/agenda/calendar/callback"


def _secret_key():
    return get_secret_key()


def _fernet():
    digest = hashlib.sha256(_secret_key().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(value):
    if not value:
        return None

    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_token(value):
    if not value:
        return None

    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def _http_json(
    method,
    url,
    *,
    headers=None,
    json_body=None,
    data=None,
    params=None,
    timeout=HTTP_TIMEOUT_SECONDS,
):
    """
    Single HTTP seam for OAuth and Calendar API calls.

    Tests patch this function instead of ``requests``.
    """
    try:
        response = requests.request(
            method,
            url,
            headers=headers or None,
            json=json_body,
            data=data,
            params=params,
            timeout=timeout,
        )
    except requests.exceptions.Timeout as exc:
        raise GoogleCalendarHttpError(0, "timeout") from exc
    except requests.exceptions.RequestException as exc:
        raise GoogleCalendarHttpError(0, str(exc)[:200]) from exc

    if response.status_code >= 400:
        detail = (response.text or "")[:300]
        raise GoogleCalendarHttpError(response.status_code, detail)

    if not (response.text or "").strip():
        return {}

    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _authorization_url(state):
    params = {
        "client_id": get_client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }

    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def begin_oauth(session):
    if not is_configured():
        raise GoogleCalendarError("agenda_calendar_flash_not_configured")

    state = secrets.token_urlsafe(32)
    session[OAUTH_STATE_SESSION_KEY] = state

    return _authorization_url(state)


def finish_oauth(
    session,
    *,
    organization_id,
    user_id,
    code,
    state,
    error=None,
):
    expected = session.pop(OAUTH_STATE_SESSION_KEY, None)

    if error:
        raise GoogleCalendarError("agenda_calendar_flash_denied")

    if not code or not state or not expected or state != expected:
        raise GoogleCalendarError("agenda_calendar_flash_oauth_error")

    if not organization_id or not user_id:
        raise GoogleCalendarError("agenda_calendar_flash_oauth_error")

    token_payload = _http_json(
        "POST",
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": get_client_id(),
            "client_secret": get_client_secret(),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
    )
    refresh_token = (token_payload.get("refresh_token") or "").strip()
    access_token = (token_payload.get("access_token") or "").strip()

    if not refresh_token or not access_token:
        raise GoogleCalendarError("agenda_calendar_flash_oauth_error")

    userinfo = _http_json(
        "GET",
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    email = (userinfo.get("email") or "").strip()
    expires_at = _expiry_iso(token_payload.get("expires_in"))

    upsert_calendar_connection(
        organization_id,
        user_id,
        google_email=email,
        refresh_token_encrypted=encrypt_token(refresh_token),
        access_token_encrypted=encrypt_token(access_token),
        access_expires_at=expires_at,
    )
    touch_calendar_synced(organization_id, user_id)

    return {"google_email": email}


def disconnect_calendar(organization_id, user_id):
    record = get_calendar_connection(organization_id, user_id)

    if record:
        token = decrypt_token(record.get("refresh_token_encrypted"))
        if token:
            try:
                _http_json(
                    "POST",
                    GOOGLE_REVOKE_URL,
                    data={"token": token},
                )
            except GoogleCalendarHttpError:
                logger.info("google_calendar_revoke_failed user=%s", user_id)

    delete_calendar_connection(organization_id, user_id)


def calendar_chip_for(organization_id, user, *, agent_id=None, can_manage=False):
    if not can_manage and not agent_id:
        return {
            "state": STATE_HIDDEN,
            "configured": is_configured(),
            "connected": False,
            "can_connect": False,
            "can_sync": False,
            "can_disconnect": False,
            "google_email": "",
            "last_synced_at": "",
            "last_synced_label": "",
        }

    if not is_configured():
        return {
            "state": STATE_UNCONFIGURED,
            "configured": False,
            "connected": False,
            "can_connect": False,
            "can_sync": False,
            "can_disconnect": False,
            "google_email": "",
            "last_synced_at": "",
            "last_synced_label": "",
        }

    target_user_id = None

    if can_manage and user:
        target_user_id = user.get("id")
    elif agent_id:
        agent_user = get_user_by_agent_id(agent_id, organization_id)
        target_user_id = (agent_user or {}).get("id")

    if not target_user_id:
        return {
            "state": STATE_DISCONNECTED if can_manage else STATE_HIDDEN,
            "configured": True,
            "connected": False,
            "can_connect": bool(can_manage),
            "can_sync": False,
            "can_disconnect": False,
            "google_email": "",
        }

    record = get_calendar_connection(organization_id, target_user_id)

    if record is None or record["status"] == "revoked":
        return {
            "state": STATE_DISCONNECTED,
            "configured": True,
            "connected": False,
            "can_connect": bool(can_manage),
            "can_sync": False,
            "can_disconnect": False,
            "google_email": "",
            "last_synced_at": "",
            "last_synced_label": "",
        }

    label = _last_synced_label(record, organization_id)

    if record["status"] == "error":
        return {
            "state": STATE_ERROR,
            "configured": True,
            "connected": True,
            "can_connect": bool(can_manage),
            "can_sync": bool(can_manage),
            "can_disconnect": bool(can_manage),
            "google_email": record.get("google_email") or "",
            "last_synced_at": record.get("last_synced_at") or "",
            "last_synced_label": label,
        }

    return {
        "state": STATE_SYNCED,
        "configured": True,
        "connected": True,
        "can_connect": False,
        "can_sync": bool(can_manage),
        "can_disconnect": bool(can_manage),
        "google_email": record.get("google_email") or "",
        "last_synced_at": record.get("last_synced_at") or "",
        "last_synced_label": label,
    }


def _last_synced_label(record, organization_id):
    raw = record.get("last_synced_at") if record else None
    parsed = parse_utc_iso(raw) if raw else None

    if parsed is None:
        return ""

    tz = organization_timezone(organization_id)
    local = parsed.astimezone(tz)
    today = datetime.now(tz).date()
    clock = local.strftime("%H:%M")

    if local.date() == today:
        return f"Hoy {clock}"

    return f"{local.strftime('%d/%m')} {clock}"


def retry_task_sync(task, *, actor_user_id=None):
    return sync_task_event("task_updated", task, actor_user_id=actor_user_id)


def _expiry_iso(expires_in):
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = 3500

    moment = datetime.utcnow().replace(microsecond=0) + timedelta(
        seconds=max(seconds - 60, 30)
    )

    return moment.isoformat()


def _access_token_is_fresh(record):
    expires_at = parse_utc_iso(record.get("access_expires_at"))

    if expires_at is None:
        return False

    return expires_at > datetime.now(timezone.utc)


def _ensure_access_token(organization_id, user_id, record):
    if (
        record.get("access_token_encrypted")
        and _access_token_is_fresh(record)
    ):
        token = decrypt_token(record["access_token_encrypted"])
        if token:
            return token

    refresh_token = decrypt_token(record.get("refresh_token_encrypted"))

    if not refresh_token:
        mark_calendar_error(
            organization_id,
            user_id,
            "missing_refresh_token",
            revoked=True,
        )
        raise GoogleCalendarError("agenda_calendar_flash_oauth_error")

    try:
        payload = _http_json(
            "POST",
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": get_client_id(),
                "client_secret": get_client_secret(),
                "grant_type": "refresh_token",
            },
            headers={"Accept": "application/json"},
        )
    except GoogleCalendarHttpError as exc:
        revoked = exc.status_code in (400, 401)
        mark_calendar_error(
            organization_id,
            user_id,
            exc.detail or "refresh_failed",
            revoked=revoked,
        )
        raise

    access_token = (payload.get("access_token") or "").strip()

    if not access_token:
        mark_calendar_error(organization_id, user_id, "empty_access_token")
        raise GoogleCalendarError("agenda_calendar_flash_oauth_error")

    new_refresh = (payload.get("refresh_token") or "").strip()
    update_calendar_tokens(
        organization_id,
        user_id,
        access_token_encrypted=encrypt_token(access_token),
        access_expires_at=_expiry_iso(payload.get("expires_in")),
        refresh_token_encrypted=(
            encrypt_token(new_refresh) if new_refresh else None
        ),
    )

    return access_token


def _user_id_for_agent(organization_id, agent_id):
    if not agent_id:
        return None

    agent_user = get_user_by_agent_id(agent_id, organization_id)

    return (agent_user or {}).get("id")


def _authorized_headers(organization_id, user_id, record):
    access_token = _ensure_access_token(organization_id, user_id, record)

    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _events_url(calendar_id, event_id=None):
    base = GOOGLE_EVENTS_URL.format(
        calendar_id=quote(calendar_id or "primary", safe="")
    )

    if event_id:
        return f"{base}/{quote(str(event_id), safe='')}"

    return base


def _event_body(task):
    tz = organization_timezone(task["organization_id"])
    start_local = None
    parsed = parse_utc_iso(task.get("due_at"))

    if parsed is not None:
        start_local = parsed.astimezone(tz)

    duration = task.get("duration_minutes") or DEFAULT_DURATION_MINUTES

    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION_MINUTES

    if duration <= 0:
        duration = DEFAULT_DURATION_MINUTES

    if start_local is None:
        start_local = datetime.now(tz)

    end_local = start_local + timedelta(minutes=duration)
    description_parts = []

    if task.get("contact_name"):
        description_parts.append(task["contact_name"])

    if task.get("description"):
        description_parts.append(task["description"])

    if task.get("property_address"):
        description_parts.append(task["property_address"])

    body = {
        "summary": task.get("title") or "JRH",
        "description": "\n".join(description_parts),
        "start": {
            "dateTime": start_local.isoformat(),
            "timeZone": str(tz),
        },
        "end": {
            "dateTime": end_local.isoformat(),
            "timeZone": str(tz),
        },
        "extendedProperties": {
            "private": {
                JRH_TASK_PROPERTY: str(task.get("id") or ""),
                JRH_ORG_PROPERTY: str(task.get("organization_id") or ""),
            }
        },
        "source": {
            "title": "JRH One",
            "url": get_app_base_url(),
        },
    }

    reminder = task.get("reminder_minutes")

    try:
        reminder = int(reminder) if reminder not in (None, "") else None
    except (TypeError, ValueError):
        reminder = None

    if reminder:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": reminder},
            ],
        }

    return body


def _calendar_request(
    method,
    url,
    organization_id,
    user_id,
    record,
    *,
    json_body=None,
    params=None,
):
    headers = _authorized_headers(organization_id, user_id, record)

    try:
        return _http_json(
            method,
            url,
            headers=headers,
            json_body=json_body,
            params=params,
        )
    except GoogleCalendarHttpError as exc:
        if exc.status_code != 401:
            raise

        fresh = dict(record)
        fresh["access_expires_at"] = None
        headers = _authorized_headers(organization_id, user_id, fresh)

        return _http_json(
            method,
            url,
            headers=headers,
            json_body=json_body,
            params=params,
        )


def sync_task_event(event_type, task, *, actor_user_id=None):
    """Push a JRH task to Google Calendar. Never raises to the caller."""
    if not task or not is_configured():
        return None

    organization_id = task.get("organization_id")
    user_id = _user_id_for_agent(organization_id, task.get("agent_id"))

    if user_id is None:
        user_id = actor_user_id

    if not organization_id or not user_id:
        return None

    record = get_calendar_connection(organization_id, user_id)

    if record is None or record["status"] == "revoked":
        return None

    calendar_id = record.get("calendar_id") or "primary"

    try:
        if event_type == "task_cancelled":
            event_id = task.get("google_event_id")
            if not event_id:
                return None

            try:
                _calendar_request(
                    "DELETE",
                    _events_url(calendar_id, event_id),
                    organization_id,
                    user_id,
                    record,
                )
            except GoogleCalendarHttpError as exc:
                if exc.status_code not in (404, 410):
                    raise

            touch_calendar_synced(organization_id, user_id)
            return None

        body = _event_body(task)

        if event_type == "task_completed":
            summary = body.get("summary") or ""
            if not summary.startswith("✓ "):
                body["summary"] = f"✓ {summary}"

        event_id = task.get("google_event_id")

        if event_id:
            try:
                patched = _calendar_request(
                    "PATCH",
                    _events_url(calendar_id, event_id),
                    organization_id,
                    user_id,
                    record,
                    json_body=body,
                )
                touch_calendar_synced(organization_id, user_id)
                return patched
            except GoogleCalendarHttpError as exc:
                if exc.status_code not in (404, 410):
                    raise
                event_id = None

        created = _calendar_request(
            "POST",
            _events_url(calendar_id),
            organization_id,
            user_id,
            record,
            json_body=body,
        )
        new_id = (created or {}).get("id")

        if new_id and task.get("id"):
            set_google_event_id(task["id"], organization_id, new_id)
            task["google_event_id"] = new_id

        touch_calendar_synced(organization_id, user_id)
        return created
    except Exception:
        logger.exception(
            "google_calendar_push_failed type=%s task=%s",
            event_type,
            task.get("id"),
        )
        return None


def google_event_to_task(event, *, organization_id, agent_id):
    """Map a Google Calendar event into a read-only agenda task dict."""
    if not event or event.get("status") == "cancelled":
        return None

    private = (
        (event.get("extendedProperties") or {}).get("private") or {}
    )

    if private.get(JRH_TASK_PROPERTY):
        return None

    start = event.get("start") or {}

    if start.get("date") and not start.get("dateTime"):
        return None

    due_at = _google_datetime_to_utc_iso(start.get("dateTime"))

    if not due_at:
        return None

    event_id = event.get("id") or ""

    return {
        "id": None,
        "organization_id": organization_id,
        "agent_id": agent_id,
        "title": (event.get("summary") or "").strip() or "Google Calendar",
        "description": (event.get("description") or "").strip(),
        "task_type": "other",
        "due_at": due_at,
        "status": "pending",
        "priority": "normal",
        "property_id": None,
        "operation_id": None,
        "related_entity_type": None,
        "related_entity_id": None,
        "contact_name": "",
        "duration_minutes": None,
        "reminder_minutes": None,
        "attendance_status": None,
        "outcome_json": None,
        "created_by_user_id": None,
        "created_at": None,
        "updated_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "agent_name": None,
        "property_address": None,
        "operation_reference": None,
        "google_event_id": event_id,
        "google_html_link": event.get("htmlLink") or "",
        "source": "google",
    }


def _google_datetime_to_utc_iso(value):
    if not value:
        return None

    text = str(value).strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return to_utc_iso(parsed)


def _cache_is_fresh(record):
    synced = parse_utc_iso(record.get("last_synced_at"))

    if synced is None:
        return False

    return datetime.now(timezone.utc) - synced < CACHE_TTL


def _load_cached_events(record, *, organization_id, agent_id):
    raw = record.get("events_cache_json") or ""

    if not raw:
        return []

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []

    if not isinstance(payload, list):
        return []

    tasks = []

    for item in payload:
        if isinstance(item, dict) and item.get("source") == "google":
            item.setdefault("organization_id", organization_id)
            item.setdefault("agent_id", agent_id)
            tasks.append(item)
            continue

        mapped = google_event_to_task(
            item,
            organization_id=organization_id,
            agent_id=agent_id,
        )
        if mapped:
            tasks.append(mapped)

    return tasks


def pull_google_events(
    organization_id,
    *,
    agent_id,
    force=False,
    time_min=None,
    time_max=None,
):
    if not is_configured() or not agent_id:
        return []

    user_id = _user_id_for_agent(organization_id, agent_id)

    if not user_id:
        return []

    record = get_calendar_connection(organization_id, user_id)

    if record is None or record["status"] == "revoked":
        return []

    if not force and _cache_is_fresh(record):
        return _load_cached_events(
            record,
            organization_id=organization_id,
            agent_id=agent_id,
        )

    tz = organization_timezone(organization_id)
    now_local = datetime.now(tz)

    if time_min is None:
        start = (now_local - timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        time_min = start.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    if time_max is None:
        end = (now_local + timedelta(days=30)).replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )
        time_max = end.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    calendar_id = record.get("calendar_id") or "primary"

    try:
        payload = _calendar_request(
            "GET",
            _events_url(calendar_id),
            organization_id,
            user_id,
            record,
            params={
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": 100,
            },
        )
    except Exception:
        logger.exception(
            "google_calendar_pull_failed org=%s agent=%s",
            organization_id,
            agent_id,
        )
        return _load_cached_events(
            record,
            organization_id=organization_id,
            agent_id=agent_id,
        )

    tasks = []

    for event in payload.get("items") or []:
        mapped = google_event_to_task(
            event,
            organization_id=organization_id,
            agent_id=agent_id,
        )
        if mapped:
            tasks.append(mapped)

    update_calendar_cache(
        organization_id,
        user_id,
        events_cache_json=json.dumps(tasks),
    )

    return tasks


def attach_google_overlay(
    agenda,
    organization_id,
    *,
    agent_id,
    language="es",
    force=False,
    now=None,
):
    """Merge read-only Google events into an agenda view."""
    from modules.agent_tasks import (
        FILTER_COMPLETED,
        merge_external_tasks,
    )

    if not agenda or not agent_id:
        return agenda

    if agenda.get("filter") == FILTER_COMPLETED:
        return agenda

    events = pull_google_events(
        organization_id,
        agent_id=agent_id,
        force=force,
    )

    if not events:
        return agenda

    known_ids = {
        task.get("google_event_id")
        for section in agenda.get("sections") or []
        for task in section.get("tasks") or []
        if task.get("google_event_id")
    }
    extras = [
        event
        for event in events
        if event.get("google_event_id") not in known_ids
    ]

    if agenda.get("search"):
        needle = agenda["search"].casefold()
        extras = [
            event
            for event in extras
            if needle in (event.get("title") or "").casefold()
        ]

    if agenda.get("task_type") and agenda["task_type"] != "other":
        return agenda

    tz = organization_timezone(organization_id)

    return merge_external_tasks(
        agenda,
        extras,
        tz=tz,
        now=now,
        language=language,
        due_date=agenda.get("due_date") or None,
    )


def sync_now(organization_id, *, agent_id):
    pull_google_events(
        organization_id,
        agent_id=agent_id,
        force=True,
    )
