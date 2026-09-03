"""
Backend resolution for AI agent payment drafts (Phase 3A.2).

The model only reads text off a receipt. Everything that
identifies a database row — agent, treasury account, pending
charge — is resolved here against the caller's organization.
Nothing is auto-selected unless a single candidate is clearly
better than the rest; otherwise the reviewer must choose.
"""

from __future__ import annotations

import unicodedata

from modules.database.agent_account_repository import (
    list_agents_account_summary,
    list_pending_charges,
)
from modules.database.treasury_accounts_repository import (
    list_treasury_accounts,
    suggest_treasury_account_for_payment,
)


AMOUNT_TOLERANCE = 0.01

AGENT_AUTO_SELECT_SCORE = 0.8
AGENT_MIN_SCORE = 0.5
AGENT_TIE_MARGIN = 0.05

MAX_CANDIDATES = 5

APPLY_MODE_CHARGE = "charge"
APPLY_MODE_ON_ACCOUNT = "on_account"


def normalize_name(raw):
    text = (raw or "").strip().lower()

    if not text:
        return ""

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char)
    )

    return " ".join(stripped.split())


def _name_tokens(normalized):
    return {
        token
        for token in normalized.split()
        if len(token) > 1
    }


def score_name_match(receipt_name, agent_name):
    receipt_normalized = normalize_name(receipt_name)
    agent_normalized = normalize_name(agent_name)

    if not receipt_normalized or not agent_normalized:
        return 0.0

    if receipt_normalized == agent_normalized:
        return 1.0

    receipt_tokens = _name_tokens(receipt_normalized)
    agent_tokens = _name_tokens(agent_normalized)

    if not receipt_tokens or not agent_tokens:
        return 0.0

    if receipt_tokens <= agent_tokens:
        return 0.85

    if agent_tokens <= receipt_tokens:
        return 0.8

    shared = receipt_tokens & agent_tokens
    ratio = len(shared) / float(len(receipt_tokens))

    if ratio < 0.5:
        return 0.0

    return round(0.5 + 0.29 * ratio, 4)


def resolve_agent(
    organization_id,
    *,
    sender_name,
    preselected_agent_id=None,
):
    agents = list_agents_account_summary(organization_id)
    scored = []

    for agent in agents:
        score = score_name_match(
            sender_name,
            agent["agent_name"],
        )
        if score >= AGENT_MIN_SCORE:
            scored.append(
                {
                    "id": agent["agent_id"],
                    "name": agent["agent_name"],
                    "score": score,
                    "balance_ars": agent["balance_ars"],
                    "balance_usd": agent["balance_usd"],
                }
            )

    scored.sort(
        key=lambda item: (-item["score"], item["name"]),
    )
    candidates = scored[:MAX_CANDIDATES]

    if preselected_agent_id is not None:
        known = {agent["agent_id"] for agent in agents}
        if preselected_agent_id in known:
            match = next(
                (
                    item
                    for item in candidates
                    if item["id"] == preselected_agent_id
                ),
                None,
            )
            mismatch = bool(
                normalize_name(sender_name)
                and match is None
            )
            return {
                "selected_id": preselected_agent_id,
                "candidates": candidates,
                "needs_selection": False,
                "source": "preselected",
                "name_mismatch": mismatch,
            }

    selected_id = None
    source = "unresolved"

    if candidates and candidates[0]["score"] >= AGENT_AUTO_SELECT_SCORE:
        runner_up = (
            candidates[1]["score"]
            if len(candidates) > 1
            else 0.0
        )
        if candidates[0]["score"] - runner_up > AGENT_TIE_MARGIN:
            selected_id = candidates[0]["id"]
            source = "name_match"
        else:
            source = "ambiguous"
    elif candidates:
        source = "low_confidence"

    return {
        "selected_id": selected_id,
        "candidates": candidates,
        "needs_selection": selected_id is None,
        "source": source,
        "name_mismatch": False,
    }


def resolve_treasury_account(
    organization_id,
    *,
    currency,
    payment_method=None,
    bank_name=None,
    preselected_account_id=None,
):
    if not currency:
        return {
            "selected_id": None,
            "candidates": [],
            "needs_selection": True,
            "source": "currency_unknown",
        }

    accounts = list_treasury_accounts(
        organization_id,
        currency=currency,
        active_only=True,
    )
    candidates = [
        {
            "id": account["id"],
            "name": account["name"],
            "currency": account["currency"],
            "account_type": account["account_type"],
            "bank_name": account["bank_name"],
            "is_default": account["is_default"],
        }
        for account in accounts
    ]

    if preselected_account_id is not None:
        if any(
            item["id"] == preselected_account_id
            for item in candidates
        ):
            return {
                "selected_id": preselected_account_id,
                "candidates": candidates,
                "needs_selection": False,
                "source": "preselected",
            }

    normalized_bank = normalize_name(bank_name)

    if normalized_bank:
        bank_matches = [
            item
            for item in candidates
            if _bank_matches(normalized_bank, item)
        ]
        if len(bank_matches) == 1:
            return {
                "selected_id": bank_matches[0]["id"],
                "candidates": candidates,
                "needs_selection": False,
                "source": "bank_match",
            }

    suggested = suggest_treasury_account_for_payment(
        organization_id,
        currency,
        payment_method or "",
    )

    if suggested is not None and suggested["currency"] == currency:
        return {
            "selected_id": suggested["id"],
            "candidates": candidates,
            "needs_selection": False,
            "source": "org_default",
        }

    return {
        "selected_id": None,
        "candidates": candidates,
        "needs_selection": True,
        "source": "unresolved",
    }


def _bank_matches(normalized_bank, account):
    haystacks = [
        normalize_name(account.get("bank_name")),
        normalize_name(account.get("name")),
    ]

    for haystack in haystacks:
        if not haystack:
            continue
        if normalized_bank in haystack or haystack in normalized_bank:
            return True

    return False


def resolve_charge(
    organization_id,
    *,
    agent_id,
    currency,
    amount,
    preselected_charge_id=None,
    apply_mode=None,
):
    if not agent_id or not currency:
        return {
            "selected_id": None,
            "candidates": [],
            "needs_selection": True,
            "apply_mode": apply_mode or APPLY_MODE_CHARGE,
            "source": "missing_agent_or_currency",
        }

    charges = list_pending_charges(
        organization_id,
        agent_id,
        currency,
    )
    candidates = [
        {
            "id": charge["id"],
            "description": charge["description"],
            "currency": charge["currency"],
            "pending_amount": charge["pending_amount"],
            "gross_amount": charge["gross_amount"],
            "payment_status": charge["payment_status"],
            "billing_period": charge["billing_period"],
            "movement_date": charge["movement_date"],
            "exact_amount_match": (
                amount is not None
                and abs(
                    float(charge["pending_amount"]) - float(amount)
                )
                <= AMOUNT_TOLERANCE
            ),
        }
        for charge in charges
    ]

    if apply_mode == APPLY_MODE_ON_ACCOUNT:
        return {
            "selected_id": None,
            "candidates": candidates,
            "needs_selection": False,
            "apply_mode": APPLY_MODE_ON_ACCOUNT,
            "source": "on_account",
        }

    if preselected_charge_id is not None:
        if any(
            item["id"] == preselected_charge_id
            for item in candidates
        ):
            return {
                "selected_id": preselected_charge_id,
                "candidates": candidates,
                "needs_selection": False,
                "apply_mode": APPLY_MODE_CHARGE,
                "source": "preselected",
            }

        # An explicit choice that is not pending for this agent
        # and currency is refused rather than silently dropped.
        return {
            "selected_id": None,
            "candidates": candidates,
            "needs_selection": True,
            "apply_mode": APPLY_MODE_CHARGE,
            "source": "invalid_preselection",
            "invalid_preselection": True,
        }

    exact = [
        item
        for item in candidates
        if item["exact_amount_match"]
    ]

    if len(exact) == 1:
        return {
            "selected_id": exact[0]["id"],
            "candidates": candidates,
            "needs_selection": False,
            "apply_mode": APPLY_MODE_CHARGE,
            "source": "exact_amount",
        }

    if not candidates:
        return {
            "selected_id": None,
            "candidates": [],
            "needs_selection": False,
            "apply_mode": APPLY_MODE_ON_ACCOUNT,
            "source": "no_pending_charges",
        }

    return {
        "selected_id": None,
        "candidates": candidates,
        "needs_selection": True,
        "apply_mode": APPLY_MODE_CHARGE,
        "source": "ambiguous" if len(exact) > 1 else "needs_choice",
    }


def build_resolution(
    organization_id,
    payload,
    *,
    preselected_agent_id=None,
    preselected_treasury_account_id=None,
    preselected_charge_id=None,
    apply_mode=None,
):
    currency = payload.get("currency")
    amount = payload.get("amount")

    agent = resolve_agent(
        organization_id,
        sender_name=payload.get("sender_name"),
        preselected_agent_id=preselected_agent_id,
    )
    treasury = resolve_treasury_account(
        organization_id,
        currency=currency,
        payment_method=payload.get("payment_method"),
        bank_name=payload.get("bank_name"),
        preselected_account_id=preselected_treasury_account_id,
    )
    charge = resolve_charge(
        organization_id,
        agent_id=agent["selected_id"],
        currency=currency,
        amount=amount,
        preselected_charge_id=preselected_charge_id,
        apply_mode=apply_mode,
    )

    return {
        "agent": agent,
        "treasury_account": treasury,
        "charge": charge,
    }
