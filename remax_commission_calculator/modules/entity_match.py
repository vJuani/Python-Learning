"""Shared entity matching for JRH. Accent/typo tolerant. Org-scoped by callers."""

from __future__ import annotations

import logging
import re

from modules.search import fold_text, token_edit_distance

logger = logging.getLogger(__name__)

UNIQUE_MIN = 70
SUGGEST_MIN = 28
MARGIN = 15
RANK_POOL_LIMIT = 400
RESULT_LIMIT = 8

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MULTI_SPACE_RE = re.compile(r"\s+")

ABBREVIATIONS = {
    "av": "avenida",
    "ave": "avenida",
    "avda": "avenida",
    "pje": "pasaje",
    "dr": "doctor",
    "gral": "general",
    "pte": "presidente",
    "sta": "santa",
    "sto": "santo",
    "cnel": "coronel",
    "bd": "boulevard",
    "blvd": "boulevard",
}

STOPWORDS = frozenset(
    {
        "de",
        "del",
        "la",
        "las",
        "los",
        "el",
        "y",
        "en",
        "a",
        "un",
        "una",
        "the",
        "of",
        "al",
    }
)


def normalize_search_text(value):
    folded = fold_text(value)
    cleaned = _PUNCT_RE.sub(" ", folded)
    tokens = []
    for token in _MULTI_SPACE_RE.sub(" ", cleaned).strip().split():
        tokens.append(ABBREVIATIONS.get(token, token))
    return " ".join(tokens)


def significant_tokens(value):
    tokens = []
    for token in normalize_search_text(value).split():
        if token in STOPWORDS:
            continue
        if token.isdigit() or len(token) >= 2:
            tokens.append(token)
    return tokens


def _token_kind(query_token, candidate_tokens):
    if query_token.isdigit():
        return "number" if query_token in candidate_tokens else "miss"
    if query_token in candidate_tokens:
        return "exact"
    if any(
        (cand.startswith(query_token) or query_token.startswith(cand))
        for cand in candidate_tokens
        if not cand.isdigit() and len(query_token) >= 3 and len(cand) >= 3
    ):
        return "prefix"
    max_dist = 1 if len(query_token) <= 5 else 2
    if len(query_token) >= 4 and any(
        not cand.isdigit()
        and len(cand) >= 4
        and token_edit_distance(query_token, cand, limit=max_dist) <= max_dist
        for cand in candidate_tokens
    ):
        return "fuzzy"
    return "miss"


def score_entity_text(query, candidate_text, *, codes=None):
    query_norm = normalize_search_text(query)
    cand_norm = normalize_search_text(candidate_text)
    if not query_norm or not cand_norm:
        return 0
    if query_norm == cand_norm:
        return 100
    for code in codes or ():
        if code in (None, ""):
            continue
        if normalize_search_text(code) == query_norm:
            return 98
        if query_norm == fold_text(str(code)):
            return 98
    query_tokens = significant_tokens(query)
    cand_tokens = significant_tokens(candidate_text)
    if not query_tokens:
        return 0
    kinds = [_token_kind(token, cand_tokens) for token in query_tokens]
    exact = kinds.count("exact")
    prefix = kinds.count("prefix")
    fuzzy = kinds.count("fuzzy")
    numbers_ok = kinds.count("number")
    miss = kinds.count("miss")
    accounted = exact + prefix + fuzzy + numbers_ok
    score = 0
    if accounted == len(query_tokens) and miss == 0 and fuzzy == 0 and prefix == 0:
        score = 88 if len(query_tokens) > 1 else 82
    elif query_norm in cand_norm:
        score = 76
    elif accounted == len(query_tokens) and miss == 0 and fuzzy == 0:
        score = 80
    elif accounted == len(query_tokens) and miss == 0:
        score = 72
    elif accounted:
        score = min(64, 20 * exact + 16 * numbers_ok + 12 * prefix + 10 * fuzzy)
    if len(query_tokens) == 1 and fuzzy and not exact:
        token = query_tokens[0]
        if len(token) >= 6:
            score = max(score, 70)
        elif len(token) >= 4:
            score = max(score, 48)
    q_nums = [token for token in query_tokens if token.isdigit()]
    c_nums = [token for token in cand_tokens if token.isdigit()]
    if q_nums:
        if all(number in c_nums for number in q_nums):
            score += 18
        elif any(number in c_nums for number in q_nums):
            score += 6
        else:
            score -= 20
    return max(0, min(99, score))


def rank_entity_candidates(
    query,
    candidates,
    *,
    text_fields=("name", "address"),
    code_fields=("external_id", "id"),
    limit=RESULT_LIMIT,
    min_score=SUGGEST_MIN,
):
    ranked = []
    pool = list(candidates or [])[:RANK_POOL_LIMIT]
    for item in pool:
        texts = [item.get(field) for field in text_fields]
        blob = " ".join(str(part) for part in texts if part not in (None, ""))
        codes = [item.get(field) for field in code_fields]
        score = score_entity_text(query, blob, codes=codes)
        if score < min_score:
            continue
        payload = dict(item)
        payload["match_score"] = score
        payload["match_label"] = blob
        ranked.append(payload)
    ranked.sort(key=lambda row: (-row["match_score"], normalize_search_text(row.get("match_label"))))
    return ranked[: max(int(limit or RESULT_LIMIT), 1)]


def decide_entity_matches(
    ranked,
    *,
    unique_min=UNIQUE_MIN,
    margin=MARGIN,
    suggest_min=SUGGEST_MIN,
):
    if not ranked:
        return "empty", [], "none"
    top = ranked[0].get("match_score") or 0
    if top < suggest_min:
        return "empty", [], "low"
    close = [
        row
        for row in ranked
        if (row.get("match_score") or 0) >= max(top - margin, suggest_min)
    ]
    second = ranked[1].get("match_score") if len(ranked) > 1 else 0
    if top >= unique_min and (len(close) == 1 or top - (second or 0) >= margin):
        return "unique", [ranked[0]], "high"
    if top < unique_min:
        return "suggest", close[:3], "low"
    return "ambiguous", close[:5], "medium"


def log_entity_match(query, ranked, status, *, kind="entity"):
    top = [
        f"{item.get('id')}:{item.get('match_score')}"
        for item in (ranked or [])[:3]
    ]
    logger.info(
        "entity_match kind=%s query=%s candidates=%s top=%s status=%s",
        kind,
        normalize_search_text(query),
        len(ranked or []),
        ",".join(top) or "-",
        status,
    )
