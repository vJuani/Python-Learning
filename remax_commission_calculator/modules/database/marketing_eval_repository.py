"""Internal evaluation runs. Isolated from marketing_assets / user production."""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


SCORES = ("excellent", "good", "fair", "poor")


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _parse_json(raw, default=None):
    if raw in (None, ""):
        return {} if default is None else default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {} if default is None else default
    return data


def _dump(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _row_to_run(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "created_by_user_id": row[2],
        "created_at": row[3],
        "property_id": row[4],
        "property_label": row[5],
        "case_slug": row[6],
        "origin": row[7],
        "format": row[8],
        "style": row[9],
        "tone": row[10],
        "notes": row[11],
        "variant_label": row[12],
        "group_id": row[13],
        "prompt_version": row[14],
        "model": row[15],
        "provider": row[16],
        "input_summary": _parse_json(row[17], {}),
        "creative_brief": row[18],
        "output": _parse_json(row[19], {}),
        "rendered_text": row[20],
        "tokens_input": row[21],
        "tokens_output": row[22],
        "duration_ms": row[23],
        "error": row[24],
        "score": row[25],
        "issues": _parse_json(row[26], []),
    }


def create_eval_run(
    organization_id,
    *,
    created_by_user_id=None,
    property_id=None,
    property_label=None,
    case_slug=None,
    origin="listing",
    fmt="post",
    style="modern",
    tone="close",
    notes=None,
    variant_label=None,
    group_id=None,
    prompt_version=None,
    model=None,
    provider=None,
    input_summary=None,
    creative_brief=None,
    output=None,
    rendered_text=None,
    tokens_input=None,
    tokens_output=None,
    duration_ms=None,
    error=None,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        run_id = execute_insert(
            cursor,
            """
            INSERT INTO marketing_eval_runs (
                organization_id, created_by_user_id, created_at, property_id,
                property_label, case_slug, origin, format, style, tone, notes,
                variant_label, group_id, prompt_version, model, provider,
                input_summary_json, creative_brief, output_json, rendered_text,
                tokens_input, tokens_output, duration_ms, error, score, issues_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, NULL, NULL
            )
            """,
            (
                organization_id,
                created_by_user_id,
                _now_iso(),
                property_id,
                property_label,
                case_slug,
                origin,
                fmt,
                style,
                tone,
                notes,
                variant_label,
                group_id,
                prompt_version,
                model,
                provider,
                _dump(input_summary),
                creative_brief,
                _dump(output),
                rendered_text,
                tokens_input,
                tokens_output,
                duration_ms,
                error,
            ),
        )
        connection.commit()
        return get_eval_run(run_id, organization_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_eval_run(run_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT id, organization_id, created_by_user_id, created_at, property_id,
               property_label, case_slug, origin, format, style, tone, notes,
               variant_label, group_id, prompt_version, model, provider,
               input_summary_json, creative_brief, output_json, rendered_text,
               tokens_input, tokens_output, duration_ms, error, score, issues_json
        FROM marketing_eval_runs
        WHERE id = ? AND organization_id = ?
        """,
        (run_id, organization_id),
    )
    row = cursor.fetchone()
    connection.close()
    return _row_to_run(row)


def list_eval_runs(organization_id, *, limit=40, group_id=None):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    query = """
        SELECT id, organization_id, created_by_user_id, created_at, property_id,
               property_label, case_slug, origin, format, style, tone, notes,
               variant_label, group_id, prompt_version, model, provider,
               input_summary_json, creative_brief, output_json, rendered_text,
               tokens_input, tokens_output, duration_ms, error, score, issues_json
        FROM marketing_eval_runs
        WHERE organization_id = ?
    """
    params = [organization_id]
    if group_id:
        query += " AND group_id = ?"
        params.append(group_id)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()
    return [_row_to_run(row) for row in rows]


def score_eval_run(run_id, organization_id, *, score, issues=None):
    organization_id = require_organization_id(organization_id)
    if score not in SCORES:
        raise ValueError("invalid_score")
    clean_issues = []
    for item in issues or []:
        token = str(item or "").strip()
        if token and token not in clean_issues:
            clean_issues.append(token)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE marketing_eval_runs
            SET score = ?, issues_json = ?
            WHERE id = ? AND organization_id = ?
            """,
            (score, _dump(clean_issues), run_id, organization_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_eval_run(run_id, organization_id)
