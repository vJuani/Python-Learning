"""
Manual walkthrough for Phase 4B (section 32 of the spec).

Runs against a throwaway database:

    python scripts/manual_check_agenda_4b.py
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "agenda_manual.db")

from modules.agent_tasks import (  # noqa: E402
    build_agenda_summary,
    build_agenda_view,
    complete_task,
    create_task,
    list_tasks_for_property,
)
from modules.auth import ROLE_AGENT, hash_password  # noqa: E402
from modules.database import (  # noqa: E402
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.organization_time import (  # noqa: E402
    now_utc,
    organization_timezone,
)
from modules.pending_actions import (  # noqa: E402
    build_agent_pending_actions,
)


def _local_parts(organization_id, offset):
    tz = organization_timezone(organization_id)
    moment = (now_utc() + offset).astimezone(tz)

    return moment.date().isoformat(), moment.strftime("%H:%M")


def main():
    create_tables()

    organization_id = add_organization("JRH One Manual 4B")
    agent_id = add_agent("José Luis Barreiro", "Alto", organization_id)
    user_id = add_user(
        "jose_manual",
        hash_password("Password1"),
        ROLE_AGENT,
        organization_id,
        agent_id=agent_id,
    )
    property_id = add_property(
        "Av. Libertador 4200",
        "CABA",
        organization_id,
        agent_id=agent_id,
        status="approved",
    )

    due_date, due_time = _local_parts(
        organization_id,
        timedelta(hours=1),
    )
    task = create_task(
        organization_id,
        agent_id,
        {
            "title": "Segunda visita con cliente",
            "task_type": "visit",
            "priority": "normal",
            "due_date": due_date,
            "due_time": due_time,
            "property_id": property_id,
            "description": "Quiere coordinar segunda visita.",
        },
        created_by_user_id=user_id,
    )

    print("== Tarea creada ==")
    print(f"  id={task['id']} due_at(UTC)={task['due_at']}")
    print(f"  local={due_date} {due_time}")

    summary = build_agenda_summary(organization_id, agent_id)
    print("\n== Dashboard · Tu agenda ==")
    print(f"  hoy: {summary['today_count']} tarea(s)")
    for item in summary["tasks"]:
        print(f"  {item['due_time_label']} {item['title']}")

    agenda = build_agenda_view(organization_id, agent_id=agent_id)
    print("\n== Agenda ==")
    for section in agenda["sections"]:
        print(f"  [{section['label']}]")
        for item in section["tasks"]:
            print(
                f"    {item['due_time_label']} "
                f"{item['type_label']} · {item['title']} "
                f"· {item['relation_label']}"
            )

    related = list_tasks_for_property(
        organization_id,
        property_id,
        agent_id=agent_id,
    )
    print("\n== Propiedad · Seguimientos ==")
    for item in related:
        print(f"  {item['due_datetime_label']} {item['title']}")

    overdue_task = create_task(
        organization_id,
        agent_id,
        {
            "title": "Llamar a Martín López",
            "task_type": "call",
            "priority": "high",
            **dict(
                zip(
                    ("due_date", "due_time"),
                    _local_parts(
                        organization_id,
                        timedelta(hours=-2),
                    ),
                )
            ),
        },
        created_by_user_id=user_id,
    )
    pendings = build_agent_pending_actions(
        organization_id,
        agent_id,
        user_id=user_id,
    )
    print("\n== Centro de Pendientes (Agent) ==")
    for action in pendings:
        print(f"  [{action['priority']}] {action['title']}")
        print(f"      {action['subtitle']}")

    complete_task(
        organization_id,
        task["id"],
        agent_id=agent_id,
        actor_user_id=user_id,
    )
    complete_task(
        organization_id,
        overdue_task["id"],
        agent_id=agent_id,
        actor_user_id=user_id,
    )

    after_agenda = build_agenda_view(
        organization_id,
        agent_id=agent_id,
    )
    after_history = build_agenda_view(
        organization_id,
        agent_id=agent_id,
        agenda_filter="completed",
    )
    after_pendings = build_agent_pending_actions(
        organization_id,
        agent_id,
        user_id=user_id,
    )

    print("\n== Después de completar ==")
    print(f"  agenda activa: {after_agenda['total']} tarea(s)")
    print(f"  historial: {after_history['total']} tarea(s)")
    print(f"  pendientes: {len(after_pendings)}")
    for section in after_history["sections"]:
        for item in section["tasks"]:
            print(
                f"    {item['status_label']} · {item['title']} "
                f"· completada {item['completed_at']}"
            )


if __name__ == "__main__":
    main()
