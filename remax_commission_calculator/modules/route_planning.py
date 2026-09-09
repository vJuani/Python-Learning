"""Daily visit route planner. Read-only over agent_tasks. No GPS. No Directions API.

Visits come from JRH ``agent_tasks`` (task_type=visit). Google Calendar
overlay is never mixed in, so a synced event cannot appear twice.

Distance is Haversine via geo_service. That is NOT driving time.
"""

from __future__ import annotations

from datetime import timedelta

from modules.database.agent_tasks_repository import STATUS_PENDING, list_agent_tasks
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.maps.geo import distance_between_coordinates, format_distance
from modules.maps.links import build_directions_url, build_route_directions_url
from modules.maps.location import has_coordinates
from modules.organization_time import (
    local_date_bounds_utc,
    now_utc,
    organization_timezone,
    parse_utc_iso,
    to_local,
)

VISIT_TASK_TYPE = "visit"
MAX_MAPS_WAYPOINTS = 9
MAX_MAPS_STOPS = MAX_MAPS_WAYPOINTS + 2
DEFAULT_DURATION_MINUTES = 0


class RoutePlanningError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def require_route_agent(user, organization_id=None):
    from modules.auth import ROLE_AGENT

    if not user or user.get("role") != ROLE_AGENT or not user.get("agent_id"):
        raise RoutePlanningError("route_err_agent_only", 403)
    user_org = user.get("organization_id")
    if organization_id is not None and user_org not in (None, ""):
        if int(user_org) != int(organization_id):
            raise RoutePlanningError("route_err_agent_only", 403)
    return user


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def is_visit_task(task):
    """A visit is an explicit visit task, never a meeting or Google overlay."""
    if not task:
        return False
    if (task.get("source") or "jrh") == "google":
        return False
    return task.get("task_type") == VISIT_TASK_TYPE


def _is_flexible(stop):
    if stop.get("time_window_start") or stop.get("time_window_end"):
        return True
    return not stop.get("due_at")


def _due_dt(stop):
    return parse_utc_iso(stop.get("due_at")) if stop.get("due_at") else None


def _duration_delta(stop):
    try:
        minutes = int(stop.get("duration_minutes") or DEFAULT_DURATION_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_DURATION_MINUTES
    return timedelta(minutes=max(0, minutes))


def _anchor_key(stop):
    lng = stop.get("longitude")
    lat = stop.get("latitude")
    return (
        lng if lng is not None else 0.0,
        lat if lat is not None else 0.0,
        stop.get("id") or 0,
    )


def nearest_neighbor_order(stops):
    """Deterministic NN: start west/south, then closest unused neighbor."""
    remaining = [dict(stop) for stop in (stops or []) if has_coordinates(stop)]
    if not remaining:
        return []
    remaining.sort(key=_anchor_key)
    ordered = [remaining.pop(0)]
    while remaining:
        last = ordered[-1]
        remaining.sort(
            key=lambda item: (
                distance_between_coordinates(
                    last.get("latitude"),
                    last.get("longitude"),
                    item.get("latitude"),
                    item.get("longitude"),
                )
                or 10**12,
                item.get("id") or 0,
            )
        )
        ordered.append(remaining.pop(0))
    return ordered


def detect_conflicts(stops, language="es"):
    """Same-minute starts or overlapping durations. Does not reschedule."""
    timed = []
    for stop in stops or []:
        due = _due_dt(stop)
        if due is None:
            continue
        timed.append((due, due + _duration_delta(stop), stop))
    timed.sort(key=lambda item: (item[0], item[2].get("id") or 0))
    conflicts = []
    seen_pairs = set()
    by_minute = {}
    for due, _end, stop in timed:
        key = due.replace(second=0, microsecond=0)
        by_minute.setdefault(key, []).append(stop)
    for due, group in by_minute.items():
        if len(group) < 2:
            continue
        pair_key = tuple(sorted(item.get("id") or 0 for item in group))
        seen_pairs.add(pair_key)
        conflicts.append(
            {
                "kind": "same_time",
                "task_ids": [item.get("id") for item in group],
                "time_label": due.strftime("%H:%M"),
                "label": _t(
                    "route_conflict_same_time",
                    language,
                    time=due.strftime("%H:%M"),
                ),
            }
        )
    for index, (due_a, end_a, stop_a) in enumerate(timed):
        for due_b, _end_b, stop_b in timed[index + 1 :]:
            pair_key = tuple(sorted((stop_a.get("id") or 0, stop_b.get("id") or 0)))
            if pair_key in seen_pairs:
                continue
            if due_b < end_a:
                seen_pairs.add(pair_key)
                conflicts.append(
                    {
                        "kind": "overlap",
                        "task_ids": [stop_a.get("id"), stop_b.get("id")],
                        "label": _t("route_conflict_overlap", language),
                    }
                )
    return conflicts


def order_visits(stops, *, manual_ids=None):
    """Order located visits. Fixed times stay chronological. No time changes."""
    located = [dict(stop) for stop in (stops or []) if has_coordinates(stop)]
    unlocated = [dict(stop) for stop in (stops or []) if not has_coordinates(stop)]
    if manual_ids:
        wanted = []
        seen = set()
        by_id = {item.get("id"): item for item in located}
        for raw in manual_ids:
            try:
                key = int(raw)
            except (TypeError, ValueError):
                continue
            if key in seen or key not in by_id:
                continue
            seen.add(key)
            wanted.append(by_id[key])
        wanted.extend(item for item in located if item.get("id") not in seen)
        return wanted, unlocated, {"schedule_locked": False, "manual": True}

    fixed = [item for item in located if not _is_flexible(item)]
    flexible = [item for item in located if _is_flexible(item)]
    fixed.sort(key=lambda item: (item.get("due_at") or "", item.get("id") or 0))
    if not flexible:
        return fixed, unlocated, {"schedule_locked": bool(fixed), "manual": False}
    if not fixed:
        return nearest_neighbor_order(flexible), unlocated, {
            "schedule_locked": False,
            "manual": False,
        }

    ordered = list(fixed)
    for candidate in nearest_neighbor_order(flexible):
        best_index = len(ordered)
        best_extra = None
        for index in range(len(ordered) + 1):
            prev_stop = ordered[index - 1] if index else None
            next_stop = ordered[index] if index < len(ordered) else None
            extra = 0.0
            if prev_stop and next_stop:
                direct = distance_between_coordinates(
                    prev_stop.get("latitude"),
                    prev_stop.get("longitude"),
                    next_stop.get("latitude"),
                    next_stop.get("longitude"),
                ) or 0.0
                via = (
                    distance_between_coordinates(
                        prev_stop.get("latitude"),
                        prev_stop.get("longitude"),
                        candidate.get("latitude"),
                        candidate.get("longitude"),
                    )
                    or 0.0
                ) + (
                    distance_between_coordinates(
                        candidate.get("latitude"),
                        candidate.get("longitude"),
                        next_stop.get("latitude"),
                        next_stop.get("longitude"),
                    )
                    or 0.0
                )
                extra = via - direct
            elif prev_stop:
                extra = distance_between_coordinates(
                    prev_stop.get("latitude"),
                    prev_stop.get("longitude"),
                    candidate.get("latitude"),
                    candidate.get("longitude"),
                ) or 0.0
            elif next_stop:
                extra = distance_between_coordinates(
                    candidate.get("latitude"),
                    candidate.get("longitude"),
                    next_stop.get("latitude"),
                    next_stop.get("longitude"),
                ) or 0.0
            if best_extra is None or extra < best_extra:
                best_extra = extra
                best_index = index
        ordered.insert(best_index, candidate)
    return ordered, unlocated, {"schedule_locked": False, "manual": False}


def _decorate_stop(task, tz, language):
    from modules.agent_tasks import decorate_task

    now = now_utc()
    item = decorate_task(task, tz=tz, now=now, language=language)
    local = to_local(item.get("due_at"), tz)
    return {
        **item,
        "time_window_start": item.get("time_window_start"),
        "time_window_end": item.get("time_window_end"),
        "local_due": local,
        "place_label": (
            item.get("property_address")
            or item.get("formatted_address")
            or item.get("display_title")
            or item.get("title")
            or ""
        ),
    }


def list_visits_for_day(
    organization_id,
    agent_id,
    *,
    local_date=None,
    language="es",
    now=None,
):
    """Pending visit tasks for one local day. JRH tasks only. Org + agent scoped."""
    organization_id = require_organization_id(organization_id)
    if agent_id is None:
        return []
    tz = organization_timezone(organization_id)
    instant = now or now_utc()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=now_utc().tzinfo)
    day = local_date or instant.astimezone(tz).date()
    due_from, due_to = local_date_bounds_utc(day, tz)
    rows = list_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=(STATUS_PENDING,),
        task_type=VISIT_TASK_TYPE,
        due_from=due_from,
        due_to=due_to,
        order="asc",
        limit=80,
    )
    seen = set()
    visits = []
    for row in rows:
        if not is_visit_task(row):
            continue
        task_id = row.get("id")
        if task_id in seen:
            continue
        seen.add(task_id)
        visits.append(_decorate_stop(row, tz, language))
    return visits


def get_next_visit(
    organization_id,
    agent_id,
    *,
    language="es",
    now=None,
):
    """Next pending visit at or after now, including one in progress."""
    organization_id = require_organization_id(organization_id)
    if agent_id is None:
        return None
    tz = organization_timezone(organization_id)
    instant = now or now_utc()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=now_utc().tzinfo)
    today = instant.astimezone(tz).date()
    due_from, _due_to = local_date_bounds_utc(today, tz)
    rows = list_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=(STATUS_PENDING,),
        task_type=VISIT_TASK_TYPE,
        due_from=due_from,
        order="asc",
        limit=40,
    )
    upcoming = []
    in_progress = None
    for row in rows:
        if not is_visit_task(row):
            continue
        due = parse_utc_iso(row.get("due_at"))
        if due is None:
            continue
        end = due + _duration_delta(row)
        if due <= instant < end:
            in_progress = _decorate_stop(row, tz, language)
            break
        if due >= instant:
            upcoming.append(_decorate_stop(row, tz, language))
            break
    return in_progress or (upcoming[0] if upcoming else None)


def _attach_legs(stops, language):
    previous = None
    total = 0.0
    out = []
    for index, stop in enumerate(stops, start=1):
        item = dict(stop)
        item["sequence"] = index
        meters = None
        if previous is not None:
            meters = distance_between_coordinates(
                previous.get("latitude"),
                previous.get("longitude"),
                item.get("latitude"),
                item.get("longitude"),
            )
            if meters is not None:
                total += meters
        item["distance_from_previous_m"] = meters
        item["distance_from_previous_label"] = (
            format_distance(meters, language) if meters is not None else None
        )
        item["travel_duration_label"] = None
        item["directions_url"] = build_directions_url(item) or item.get("directions_url")
        out.append(item)
        previous = item
    return out, total


def _path_label(stops):
    parts = []
    for stop in stops:
        label = (stop.get("place_label") or stop.get("property_address") or "").strip()
        if label:
            parts.append(label)
    return " → ".join(parts)


def parse_order_ids(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = str(raw).split(",")
    ids = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        try:
            ids.append(int(text))
        except (TypeError, ValueError):
            continue
    return ids or None


def build_daily_route(
    organization_id,
    agent_id,
    *,
    local_date=None,
    order_ids=None,
    language="es",
    now=None,
):
    """On-demand route for one day. Never writes agenda or Google Calendar."""
    organization_id = require_organization_id(organization_id)
    tz = organization_timezone(organization_id)
    instant = now or now_utc()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=now_utc().tzinfo)
    day = local_date or instant.astimezone(tz).date()
    visits = list_visits_for_day(
        organization_id,
        agent_id,
        local_date=day,
        language=language,
        now=instant,
    )
    meetings_ignored = 0
    ordered, unlocated, meta = order_visits(visits, manual_ids=order_ids)
    stops, total_m = _attach_legs(ordered, language)
    conflicts = detect_conflicts(stops, language=language)
    ids = [item.get("id") for item in stops]
    for index, item in enumerate(stops):
        if index:
            swapped = list(ids)
            swapped[index], swapped[index - 1] = swapped[index - 1], swapped[index]
            item["order_up"] = ",".join(str(value) for value in swapped)
        else:
            item["order_up"] = ""
        if index < len(ids) - 1:
            swapped = list(ids)
            swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
            item["order_down"] = ",".join(str(value) for value in swapped)
        else:
            item["order_down"] = ""
    maps_truncated = len(stops) > MAX_MAPS_STOPS
    maps_stops = stops[:MAX_MAPS_STOPS]
    maps_url = build_route_directions_url(maps_stops)
    locked = bool(meta.get("schedule_locked")) and not meta.get("manual")
    facts = {
        "schedule_locked": locked,
        "manual": bool(meta.get("manual")),
        "path": _path_label(stops),
        "linear_distance": format_distance(total_m, language) if stops else "",
        "travel_duration_estimated": False,
    }
    return {
        "date": day.isoformat(),
        "date_label": day.strftime("%d/%m/%Y"),
        "visit_count": len(visits),
        "stop_count": len(stops),
        "stops": stops,
        "unlocated": unlocated,
        "conflicts": conflicts,
        "total_distance_m": total_m if stops else None,
        "total_distance_label": format_distance(total_m, language) if stops else None,
        "maps_url": maps_url,
        "maps_truncated": maps_truncated,
        "schedule_locked": locked,
        "manual": bool(meta.get("manual")),
        "travel_duration_estimated": False,
        "meetings_ignored": meetings_ignored,
        "facts": facts,
        "map": _map_payload(stops),
        "original_due_at": {item.get("id"): item.get("due_at") for item in visits},
    }


def _map_payload(stops):
    markers = []
    for item in stops:
        if not has_coordinates(item):
            continue
        markers.append(
            {
                "lat": item["latitude"],
                "lng": item["longitude"],
                "title": f"{item.get('sequence')}. {item.get('place_label') or ''}",
                "label": str(item.get("sequence") or ""),
                "time": item.get("due_time_label") or "",
                "distance": item.get("distance_from_previous_label") or "",
                "href": (
                    f"/properties/{item['property_id']}"
                    if item.get("property_id")
                    else ""
                ),
            }
        )
    if not markers:
        return {"available": False, "markers": [], "path": []}
    return {
        "available": True,
        "target": {"lat": markers[0]["lat"], "lng": markers[0]["lng"], "title": markers[0]["title"]},
        "markers": markers,
        "path": [{"lat": item["lat"], "lng": item["lng"]} for item in markers],
    }


def find_visit_on_route(route, *, time_text="", place_text=""):
    """Return a stop that matches a spoken time/place, or None."""
    folded_place = (place_text or "").strip().lower()
    folded_time = (time_text or "").strip()
    for stop in route.get("stops") or []:
        time_ok = True
        if folded_time:
            label = (stop.get("due_time_label") or stop.get("due_time_value") or "")
            time_ok = folded_time in label or label.startswith(folded_time)
        place_ok = True
        if folded_place:
            blob = " ".join(
                str(part or "")
                for part in (
                    stop.get("place_label"),
                    stop.get("property_address"),
                    stop.get("formatted_address"),
                    stop.get("title"),
                )
            ).lower()
            place_ok = folded_place in blob
        if time_ok and place_ok:
            return stop
    return None
