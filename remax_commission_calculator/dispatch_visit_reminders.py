"""One-shot visit reminder job for Railway scheduled execution."""

from __future__ import annotations

import argparse

from modules.config import load_dotenv_file
from modules.database import create_tables
from modules.visit_reminders import dispatch_due_visit_reminders_all


def build_parser():
    parser = argparse.ArgumentParser(
        description="Send push reminders for visits due in about 30 minutes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching visits without sending notifications.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_dotenv_file()
    create_tables()
    if args.dry_run:
        from datetime import timedelta

        from modules.database.agent_tasks_repository import (
            STATUS_PENDING,
            list_agent_tasks,
        )
        from modules.database.organizations_repository import get_organizations
        from modules.organization_time import now_utc, to_utc_iso
        from modules.visit_reminders import (
            WINDOW_END_MINUTES,
            WINDOW_START_MINUTES,
            VISIT_TYPE,
        )

        instant = now_utc()
        due_from = to_utc_iso(instant + timedelta(minutes=WINDOW_START_MINUTES))
        due_to = to_utc_iso(
            instant + timedelta(minutes=WINDOW_END_MINUTES, seconds=1)
        )
        total = 0
        for organization in get_organizations():
            if not organization.get("is_active", True):
                continue
            visits = list_agent_tasks(
                organization["id"],
                statuses=(STATUS_PENDING,),
                task_type=VISIT_TYPE,
                due_from=due_from,
                due_to=due_to,
                limit=200,
            )
            if not visits:
                continue
            print(
                f"Organization {organization['id']} "
                f"({organization['name']}): {len(visits)} visit(s)"
            )
            for task in visits:
                print(
                    f"  - task={task.get('id')} agent={task.get('agent_id')} "
                    f"due_at={task.get('due_at')}"
                )
            total += len(visits)
        print(f"{total} visit(s) in the 30-minute window.")

        overdue_total = 0
        now_iso = to_utc_iso(instant)
        for organization in get_organizations():
            if not organization.get("is_active", True):
                continue
            overdue = [
                task
                for task in list_agent_tasks(
                    organization["id"],
                    statuses=(STATUS_PENDING,),
                    due_to=now_iso,
                    limit=200,
                )
                if (task.get("task_type") or "") != "visit"
            ]
            if not overdue:
                continue
            print(
                f"Organization {organization['id']} "
                f"({organization['name']}): {len(overdue)} overdue task(s)"
            )
            for task in overdue:
                print(
                    f"  - overdue task={task.get('id')} "
                    f"agent={task.get('agent_id')} due_at={task.get('due_at')}"
                )
            overdue_total += len(overdue)
        print(f"{overdue_total} overdue pending task(s).")
        return 0

    from modules.notifications.jobs import run_notification_jobs

    summary = run_notification_jobs(source="cron")
    print(
        f"{summary['agenda_created']} reminder(s) dispatched "
        f"from {summary['agenda_candidates']} agenda event(s) in window."
    )
    print(
        f"{summary['overdue_created']} overdue task reminder(s) dispatched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
