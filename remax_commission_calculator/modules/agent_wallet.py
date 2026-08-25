"""
Agent wallet postings: own commission + Team Leader income.

Reuses calculations.py / operation amounts. Does not change
official commission formulas on the operation record.
"""

from __future__ import annotations

from modules.calculations import (
    calculate_agent_payment,
    calculate_martillero,
)
from modules.database.agent_wallet_repository import (
    MOVEMENT_OWN_COMMISSION,
    MOVEMENT_REVERSAL,
    MOVEMENT_TEAM_LEADER_INCOME,
    count_credit_generations,
    get_wallet_movement_by_idempotency_key,
    insert_wallet_movement,
    list_wallet_movements_for_operation,
    list_wallet_movements_for_agent,
    sum_wallet_by_type,
)
from modules.database.agents_repository import (
    get_agent_record,
    list_team_juniors,
)
from modules.database.operations_repository import (
    get_operation_record,
)
from modules.workflow import STATUS_APPROVED


def calculate_team_leader_income(
    commission_after_abao,
    *,
    junior_agent_type="Junior",
):
    """
    TL income = Puro equivalent - Junior yield, same base (post-ABAO).

    Uses existing calculate_martillero / calculate_agent_payment.
    """
    base = float(commission_after_abao or 0)

    junior_payment = calculate_agent_payment(
        junior_agent_type,
        base,
        0,
    )

    martillero_puro = calculate_martillero(base)
    puro_payment = calculate_agent_payment(
        "Puro",
        base,
        martillero_puro,
    )

    return {
        "commission_after_abao": base,
        "junior_payment": junior_payment,
        "martillero_puro": martillero_puro,
        "puro_payment": puro_payment,
        "team_leader_income": puro_payment - junior_payment,
    }


def _credit_key(org_id, op_id, movement_type, agent_id, seq):
    return (
        f"{org_id}:op:{op_id}:{movement_type}:"
        f"{agent_id}:c{seq}"
    )


def _reversal_key(org_id, op_id, movement_type, agent_id, seq):
    return (
        f"{org_id}:op:{op_id}:{movement_type}:"
        f"{agent_id}:r{seq}"
    )


def _active_credits(movements):
    """Credits that have not been reversed yet."""
    reversed_ids = {
        item["related_movement_id"]
        for item in movements
        if item["movement_type"] == MOVEMENT_REVERSAL
        and item.get("related_movement_id") is not None
    }

    return [
        item
        for item in movements
        if item["movement_type"]
        in (
            MOVEMENT_OWN_COMMISSION,
            MOVEMENT_TEAM_LEADER_INCOME,
        )
        and item["id"] not in reversed_ids
    ]


def _insert_credit_idempotent(**kwargs):
    key = kwargs.get("idempotency_key")
    org_id = kwargs["organization_id"]

    existing = get_wallet_movement_by_idempotency_key(
        org_id,
        key,
    )

    if existing is not None:
        return existing, False

    try:
        created = insert_wallet_movement(**kwargs)
        return created, True
    except Exception as error:
        # Unique race / duplicate
        existing = get_wallet_movement_by_idempotency_key(
            org_id,
            key,
        )
        if existing is not None:
            return existing, False
        raise error


def post_wallet_for_approved_operation(
    organization_id,
    operation_id,
):
    operation = get_operation_record(
        operation_id,
        organization_id,
    )

    if operation is None:
        return {"posted": [], "skipped": True}

    if (operation.get("status") or "") != STATUS_APPROVED:
        return {"posted": [], "skipped": True}

    agent_id = operation["agent_db_id"]
    currency = operation.get("currency") or "USD"
    reference = operation.get("id") or f"COM-{operation_id:06d}"
    posted = []

    existing = list_wallet_movements_for_operation(
        organization_id,
        operation_id,
    )
    active = _active_credits(existing)

    # Own commission for the operating agent
    if not any(
        item["movement_type"] == MOVEMENT_OWN_COMMISSION
        and item["agent_id"] == agent_id
        for item in active
    ):
        seq = (
            count_credit_generations(
                organization_id,
                operation_id,
                MOVEMENT_OWN_COMMISSION,
                agent_id,
            )
            + 1
        )
        movement, created = _insert_credit_idempotent(
            organization_id=organization_id,
            agent_id=agent_id,
            movement_type=MOVEMENT_OWN_COMMISSION,
            amount=operation.get("agent_payment") or 0,
            currency=currency,
            operation_id=operation_id,
            description="Own commission",
            reference=reference,
            idempotency_key=_credit_key(
                organization_id,
                operation_id,
                MOVEMENT_OWN_COMMISSION,
                agent_id,
                seq,
            ),
        )
        if created:
            posted.append(movement)

    # Team Leader income when junior has an assigned TL
    agent = get_agent_record(agent_id, organization_id)

    if (
        agent is not None
        and agent.get("team_leader_agent_id")
        and agent.get("type") in ("Junior", "RAPP")
    ):
        tl_id = agent["team_leader_agent_id"]

        if not any(
            item["movement_type"]
            == MOVEMENT_TEAM_LEADER_INCOME
            and item["agent_id"] == tl_id
            for item in active
        ):
            breakdown = calculate_team_leader_income(
                operation.get("commission_after_abao"),
                junior_agent_type=agent["type"],
            )
            income = breakdown["team_leader_income"]

            if income != 0:
                seq = (
                    count_credit_generations(
                        organization_id,
                        operation_id,
                        MOVEMENT_TEAM_LEADER_INCOME,
                        tl_id,
                    )
                    + 1
                )
                movement, created = _insert_credit_idempotent(
                    organization_id=organization_id,
                    agent_id=tl_id,
                    movement_type=MOVEMENT_TEAM_LEADER_INCOME,
                    amount=income,
                    currency=currency,
                    operation_id=operation_id,
                    source_agent_id=agent_id,
                    description=(
                        f"Team Leader income from "
                        f"{agent.get('name')}"
                    ),
                    reference=reference,
                    idempotency_key=_credit_key(
                        organization_id,
                        operation_id,
                        MOVEMENT_TEAM_LEADER_INCOME,
                        tl_id,
                        seq,
                    ),
                )
                if created:
                    posted.append(movement)

    return {"posted": posted, "skipped": False}


def reverse_wallet_for_operation(
    organization_id,
    operation_id,
):
    movements = list_wallet_movements_for_operation(
        organization_id,
        operation_id,
    )
    active = _active_credits(movements)
    reversed_rows = []

    for credit in active:
        seq = count_credit_generations(
            organization_id,
            operation_id,
            credit["movement_type"],
            credit["agent_id"],
        )
        # seq points at the generation of this credit family;
        # use credit id in key for uniqueness of each reverse.
        key = (
            f"{organization_id}:op:{operation_id}:"
            f"reversal:{credit['id']}"
        )

        existing = get_wallet_movement_by_idempotency_key(
            organization_id,
            key,
        )
        if existing is not None:
            continue

        movement, created = _insert_credit_idempotent(
            organization_id=organization_id,
            agent_id=credit["agent_id"],
            movement_type=MOVEMENT_REVERSAL,
            amount=-float(credit["amount"]),
            currency=credit.get("currency") or "USD",
            operation_id=operation_id,
            source_agent_id=credit.get("source_agent_id"),
            related_movement_id=credit["id"],
            description=(
                f"Reversal of {credit['movement_type']} "
                f"#{credit['id']}"
            ),
            reference=credit.get("reference"),
            idempotency_key=key,
        )
        if created:
            reversed_rows.append(movement)

    return reversed_rows


def sync_wallet_for_operation_status(
    organization_id,
    operation_id,
    status,
):
    if status == STATUS_APPROVED:
        return post_wallet_for_approved_operation(
            organization_id,
            operation_id,
        )

    return {
        "reversed": reverse_wallet_for_operation(
            organization_id,
            operation_id,
        )
    }


def build_agent_wallet_view(organization_id, agent_id):
    agent = get_agent_record(agent_id, organization_id)

    if agent is None:
        return None

    juniors = list_team_juniors(agent_id, organization_id)
    totals = sum_wallet_by_type(organization_id, agent_id)
    movements = list_wallet_movements_for_agent(
        organization_id,
        agent_id,
        limit=30,
    )

    return {
        "agent": agent,
        "juniors": juniors,
        "totals": totals,
        "movements": movements,
        "team_leader": (
            {
                "id": agent["team_leader_agent_id"],
                "name": agent.get("team_leader_name"),
                "type": agent.get("team_leader_type"),
            }
            if agent.get("team_leader_agent_id")
            else None
        ),
    }
