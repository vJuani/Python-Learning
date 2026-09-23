"""End-to-end QA of Property Marketing Renderer V1 against a REAL synced property.

Safety guarantees (enforced at runtime, not only by convention):
  * DB read-only: every Postgres connection is switched to
    SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY before the app gets it
    (server rejects any write) + every SQL statement must be SELECT / WITH / SHOW.
  * No INSERT / UPDATE / DELETE / DDL / migrations: blocked before reaching the DB.
  * Filesystem: Python-level writes only allowed under tmp/property_marketing_qa/e2e/.
  * No external actions: SMTP blocked, HTTP only GET/HEAD (photo/logo downloads),
    provider keys (OpenAI, Resend, SMTP) removed from this process.
  * DATABASE_URL and credentials are never printed; logs are redacted.
  * SQLite only with an explicit --database-path: opened as
    file:...?mode=ro&immutable=1 + PRAGMA query_only=ON; sha256 checked before/after.
  * Files stored under /data/uploads or /data/static-uploads that are missing locally
    block the run (no fixture, no fallback) and are listed with their Railway path.

Usage (PowerShell, from the app folder):
    # Postgres (DATABASE_URL only in the shell session)
    $env:DATABASE_URL = "postgresql://..."
    ..\\.venv\\Scripts\\python.exe tmp/property_marketing_qa/e2e_real_property.py --organization-id 1

    # Production SQLite snapshot
    ..\\.venv\\Scripts\\python.exe tmp/property_marketing_qa/e2e_real_property.py `
        --database-path tmp/property_marketing_qa/production_snapshot.db --organization-id 1 `
        [--uploads-root <local copy of /data/uploads>] [--static-uploads-root <copy of /data/static-uploads>]
"""

from __future__ import annotations

import argparse
import base64
import builtins
import hashlib
import http.client
import io
import json
import logging
import os
import re
import smtplib
import sqlite3
import sys
from pathlib import Path

sys.dont_write_bytecode = True

APP_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = (Path(__file__).resolve().parent / "e2e").resolve()
sys.path.insert(0, str(APP_DIR))

RAILWAY_PRIVATE_ROOT = "/data/uploads"
RAILWAY_STATIC_ROOT = "/data/static-uploads"


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _early_exit(message):
    print(f"E2E_BLOCKED {message}")
    sys.exit(2)


_early_parser = argparse.ArgumentParser(add_help=False)
_early_parser.add_argument("--database-path")
_early_parser.add_argument("--uploads-root")
_early_parser.add_argument("--static-uploads-root")
EARLY_ARGS, _ = _early_parser.parse_known_args()

SNAPSHOT = None
SNAPSHOT_STATE = {}
if EARLY_ARGS.database_path:
    if os.environ.get("DATABASE_URL", "").strip():
        _early_exit("both DATABASE_URL and --database-path are set; unset DATABASE_URL to use the snapshot")
    SNAPSHOT = Path(EARLY_ARGS.database_path).expanduser().resolve()
    if not SNAPSHOT.is_file():
        _early_exit(f"--database-path not found: {SNAPSHOT}")
    SNAPSHOT_STATE = {
        "path": str(SNAPSHOT),
        "size_before": SNAPSHOT.stat().st_size,
        "sha256_before": _file_sha256(SNAPSHOT),
        "mtime_before": SNAPSHOT.stat().st_mtime,
    }
    os.environ["DATABASE_PATH"] = str(SNAPSHOT)
for _flag, _env in (("uploads_root", "PRIVATE_UPLOAD_ROOT"), ("static_uploads_root", "UPLOAD_DIR")):
    _value = getattr(EARLY_ARGS, _flag)
    if _value:
        _root = Path(_value).expanduser().resolve()
        if not _root.is_dir():
            _early_exit(f"--{_flag.replace('_', '-')} is not a directory: {_root}")
        os.environ[_env] = str(_root)

_DSN = os.environ.get("DATABASE_URL", "").strip()
os.environ["MARKETING_HTML_DEBUG"] = "0"
os.environ["MARKETING_AI_DEBUG"] = "0"
for _secret_key in ("OPENAI_API_KEY", "RESEND_API_KEY", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"):
    os.environ.pop(_secret_key, None)
os.environ["EMAIL_BACKEND"] = "console"

SAFETY = {
    "sql_statements": {},
    "sql_blocked": [],
    "network_requests": [],
    "network_blocked": [],
    "files_written": [],
    "files_blocked": [],
}


# --------------------------------------------------------------------------- secrets
_DSN_RE = re.compile(r"\b(postgres(?:ql)?(?:\+\w+)?)://[^\s'\"]+", re.IGNORECASE)
_SECRETS = [value for value in (_DSN,) if value]
try:
    from urllib.parse import urlparse as _urlparse

    _password = _urlparse(_DSN).password if _DSN else None
    if _password:
        _SECRETS.append(_password)
except Exception:
    pass


def redact(text):
    text = str(text)
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    return _DSN_RE.sub(r"\1://***", text)


class _RedactFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


# --------------------------------------------------------------------------- database session
SAFETY_DB = {"connections": 0, "read_only_sessions": 0}
try:
    import psycopg

    _real_pg_connect = psycopg.connect

    def _read_only_pg_connect(*args, **kwargs):
        conn = _real_pg_connect(*args, **kwargs)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            conn.commit()
            with conn.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                state = str(cursor.fetchone()[0]).lower()
            conn.rollback()
        except Exception:
            conn.close()
            raise
        SAFETY_DB["connections"] += 1
        if state != "on":
            conn.close()
            raise PermissionError("E2E read-only guard: session could not be set READ ONLY")
        SAFETY_DB["read_only_sessions"] += 1
        return conn

    psycopg.connect = _read_only_pg_connect
except ImportError:
    psycopg = None

_real_sqlite_connect = sqlite3.connect


def _read_only_sqlite_connect(database, *args, **kwargs):
    if str(database) == ":memory:":
        return _real_sqlite_connect(database, *args, **kwargs)
    target = None if str(database).startswith("file:") else Path(os.fspath(database)).resolve()
    if SNAPSHOT is None or target != SNAPSHOT:
        SAFETY["sql_blocked"].append(f"sqlite_connect:{database}")
        raise PermissionError("E2E read-only guard: SQLite is only allowed for the explicit --database-path snapshot")
    kwargs.pop("uri", None)
    conn = _real_sqlite_connect(f"{SNAPSHOT.as_uri()}?mode=ro&immutable=1", *args, uri=True, **kwargs)
    conn.execute("PRAGMA query_only = ON")
    SAFETY_DB["connections"] += 1
    if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
        conn.close()
        raise PermissionError("E2E read-only guard: SQLite query_only could not be enabled")
    SAFETY_DB["read_only_sessions"] += 1
    return conn


sqlite3.connect = _read_only_sqlite_connect


# --------------------------------------------------------------------------- filesystem
def _inside_out_dir(path):
    try:
        Path(os.path.abspath(os.fspath(path))).relative_to(OUT_DIR)
        return True
    except (TypeError, ValueError):
        return False


def _check_write(path, action):
    if isinstance(path, int):
        return
    if _inside_out_dir(path):
        relative = str(Path(os.path.abspath(os.fspath(path))).relative_to(OUT_DIR))
        if relative != "." and relative not in SAFETY["files_written"]:
            SAFETY["files_written"].append(relative)
        return
    SAFETY["files_blocked"].append(f"{action}:{path}")
    raise PermissionError(f"E2E read-only guard: {action} outside {OUT_DIR} blocked: {path}")


_real_open = builtins.open


def _guarded_open(file, mode="r", *args, **kwargs):
    if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
        _check_write(file, "open")
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _guarded_open
io.open = _guarded_open

_real_mkdir = Path.mkdir
_real_unlink = Path.unlink
_real_touch = Path.touch


def _guarded_mkdir(self, *args, **kwargs):
    _check_write(self, "mkdir")
    return _real_mkdir(self, *args, **kwargs)


def _guarded_unlink(self, *args, **kwargs):
    _check_write(self, "unlink")
    return _real_unlink(self, *args, **kwargs)


def _guarded_touch(self, *args, **kwargs):
    _check_write(self, "touch")
    return _real_touch(self, *args, **kwargs)


Path.mkdir = _guarded_mkdir
Path.unlink = _guarded_unlink
Path.touch = _guarded_touch
for _name in ("remove", "unlink", "rmdir", "makedirs", "mkdir"):
    _original = getattr(os, _name)

    def _make(original, name):
        def guarded(path, *args, **kwargs):
            _check_write(path, name)
            return original(path, *args, **kwargs)

        return guarded

    setattr(os, _name, _make(_original, _name))
for _name in ("rename", "replace"):
    _original = getattr(os, _name)

    def _make_pair(original, name):
        def guarded(src, dst, *args, **kwargs):
            _check_write(src, name)
            _check_write(dst, name)
            return original(src, dst, *args, **kwargs)

        return guarded

    setattr(os, _name, _make_pair(_original, _name))


# --------------------------------------------------------------------------- network / external actions
_READ_METHODS = {"GET", "HEAD"}
_real_putrequest = http.client.HTTPConnection.putrequest


def _guarded_putrequest(self, method, url, *args, **kwargs):
    entry = {"method": str(method).upper(), "host": getattr(self, "host", "")}
    if entry["method"] not in _READ_METHODS:
        SAFETY["network_blocked"].append(entry)
        raise PermissionError(f"E2E guard: HTTP {method} to {entry['host']} blocked")
    SAFETY["network_requests"].append(entry)
    return _real_putrequest(self, method, url, *args, **kwargs)


http.client.HTTPConnection.putrequest = _guarded_putrequest


def _blocked_smtp(*_args, **_kwargs):
    SAFETY["network_blocked"].append({"method": "SMTP", "host": ""})
    raise PermissionError("E2E guard: SMTP blocked")


smtplib.SMTP = _blocked_smtp
smtplib.SMTP_SSL = _blocked_smtp
try:
    import httpx

    _real_httpx_send = httpx.Client.send

    def _guarded_httpx_send(self, request, *args, **kwargs):
        entry = {"method": request.method.upper(), "host": request.url.host}
        if entry["method"] not in _READ_METHODS:
            SAFETY["network_blocked"].append(entry)
            raise PermissionError(f"E2E guard: HTTP {request.method} to {request.url.host} blocked")
        SAFETY["network_requests"].append(entry)
        return _real_httpx_send(self, request, *args, **kwargs)

    httpx.Client.send = _guarded_httpx_send

    async def _blocked_async_send(self, request, *args, **kwargs):
        SAFETY["network_blocked"].append({"method": request.method.upper(), "host": request.url.host})
        raise PermissionError("E2E guard: async HTTP blocked")

    httpx.AsyncClient.send = _blocked_async_send
except ImportError:
    pass


# --------------------------------------------------------------------------- app imports (after guards)
from modules.database import connection as db_connection  # noqa: E402
from modules.config import get_private_upload_root, get_upload_root  # noqa: E402
from modules.database.connection import get_connection, get_database_backend, get_database_path  # noqa: E402

_ALLOWED_SQL = re.compile(r"^\s*(SELECT|WITH|SHOW)\b", re.IGNORECASE)
_WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|CALL|LOCK|COMMENT)\b"
    r"|\bFOR\s+(UPDATE|SHARE|NO\s+KEY\s+UPDATE)\b|\bINTO\b",
    re.IGNORECASE,
)
_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_PRAGMA_READ = re.compile(r"^\s*PRAGMA\s+[\w.]+\s*(\(\s*[\w'.]*\s*\))?\s*;?\s*$", re.IGNORECASE)


def _check_sql(sql):
    text = str(sql or "")
    bare = _LITERAL.sub("''", text)
    verb = (bare.strip().split(None, 1) or ["?"])[0].upper()
    if _PRAGMA_READ.match(bare):
        SAFETY["sql_statements"]["PRAGMA"] = SAFETY["sql_statements"].get("PRAGMA", 0) + 1
        return
    if not _ALLOWED_SQL.match(bare) or _WRITE_SQL.search(bare):
        SAFETY["sql_blocked"].append(verb)
        raise PermissionError(f"E2E read-only guard: SQL '{verb}' blocked")
    SAFETY["sql_statements"][verb] = SAFETY["sql_statements"].get(verb, 0) + 1


_real_cursor_execute = db_connection.AdaptingCursor.execute
_real_cursor_executemany = db_connection.AdaptingCursor.executemany
_real_connection_execute = db_connection.AdaptingConnection.execute


def _guarded_cursor_execute(self, sql, parameters=None):
    _check_sql(sql)
    return _real_cursor_execute(self, sql, parameters)


def _guarded_executemany(self, sql, parameters_seq):
    _check_sql("EXECUTEMANY " + str(sql))


def _guarded_connection_execute(self, sql, parameters=None):
    _check_sql(sql)
    return _real_connection_execute(self, sql, parameters)


def _blocked_execute_insert(*_args, **_kwargs):
    SAFETY["sql_blocked"].append("execute_insert")
    raise PermissionError("E2E read-only guard: execute_insert blocked")


db_connection.AdaptingCursor.execute = _guarded_cursor_execute
db_connection.AdaptingCursor.executemany = _guarded_executemany
db_connection.AdaptingConnection.execute = _guarded_connection_execute
db_connection.execute_insert = _blocked_execute_insert

from modules.database.organization_settings_repository import get_organization_settings  # noqa: E402
from modules.database.properties_repository import get_property_record  # noqa: E402
from modules.marketing_context import build_property_marketing_context, context_to_snapshot  # noqa: E402
from modules.marketing_service import _context_from_asset, _options_from_request, _render_local_visual  # noqa: E402
from modules.property_marketing import (  # noqa: E402
    TEMPLATE_CLEAN_GRID,
    TEMPLATE_LIFESTYLE_DARK,
    TEMPLATE_PREMIUM_HERO,
)
import modules.organization_marketing_logo as org_logo  # noqa: E402
import modules.property_sync.media as property_media  # noqa: E402

import modules.property_sync.remote_media as remote_media  # noqa: E402

# Photo byte cache in %TEMP% disabled; bytes stay in memory.
property_media.original_media_temp_cache_dir = lambda: None

REMOTE_FETCHES = []
_real_fetch = remote_media.fetch_allowed_image_bytes


def _tracked_fetch(url, *args, **kwargs):
    payload = _real_fetch(url, *args, **kwargs)
    REMOTE_FETCHES.append(
        {
            "url": str(url),
            "ok": bool(payload),
            "bytes": len(payload or b""),
            "sha256": hashlib.sha256(payload).hexdigest() if payload else "",
        }
    )
    return payload


remote_media.fetch_allowed_image_bytes = _tracked_fetch

# URL logos are materialized under e2e/_logo_cache instead of the uploads volume.
_real_materialize = org_logo._materialize_url_logo


def _materialize_into_out_dir(organization_id, url):
    real_dir = org_logo.marketing_branding_dir
    org_logo.marketing_branding_dir = lambda org_id: OUT_DIR / "_logo_cache" / str(org_id)
    try:
        return _real_materialize(organization_id, url)
    finally:
        org_logo.marketing_branding_dir = real_dir


org_logo._materialize_url_logo = _materialize_into_out_dir

TEMPLATES = (TEMPLATE_CLEAN_GRID, TEMPLATE_LIFESTYLE_DARK, TEMPLATE_PREMIUM_HERO)


# --------------------------------------------------------------------------- helpers
def _rows(sql, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


class Blocked(Exception):
    pass


def _fail(message):
    raise Blocked(redact(message))


def _preflight():
    backend = get_database_backend()
    if SNAPSHOT is not None:
        if backend != "sqlite" or Path(get_database_path()).resolve() != SNAPSHOT:
            _fail(f"backend={backend} does not point to --database-path snapshot")
        try:
            read_only = "on" if _rows("PRAGMA query_only")[0].get("query_only") == 1 else "off"
        except PermissionError as exc:
            _fail(str(exc))
    else:
        if backend != "postgres":
            _fail(
                f"database backend is '{backend}': set DATABASE_URL (Postgres) or pass --database-path explicitly"
            )
        if psycopg is None:
            _fail("psycopg is not installed in this interpreter; use the project .venv")
        try:
            read_only = _rows("SHOW transaction_read_only")[0]
            read_only = str(next(iter(read_only.values()))).lower()
        except PermissionError as exc:
            _fail(str(exc))
        except Exception as exc:
            _fail(f"cannot connect to DATABASE_URL: {type(exc).__name__}")
    if read_only != "on":
        _fail(f"session is not read-only (read_only={read_only})")
    try:
        count = _rows("SELECT COUNT(*) AS n FROM properties")[0]["n"]
    except Exception as exc:
        _fail(f"properties table not readable: {type(exc).__name__}")
    if not count:
        _fail("properties table is empty")
    return read_only == "on"


def _organization(args):
    if args.organization_id is not None:
        rows = _rows("SELECT id, name FROM organizations WHERE id = ?", (args.organization_id,))
    elif args.property_id is not None and not args.organization:
        rows = _rows(
            "SELECT o.id, o.name FROM properties p JOIN organizations o ON o.id = p.organization_id WHERE p.id = ?",
            (args.property_id,),
        )
    else:
        name = args.organization or "JRH One"
        rows = _rows(
            "SELECT id, name FROM organizations WHERE LOWER(name) LIKE ? ORDER BY id",
            (f"%{name.lower()}%",),
        )
    if len(rows) != 1:
        _fail(
            f"organization matched {len(rows)} rows {[(row['id'], row['name']) for row in rows]}; "
            "use --organization-id"
        )
    return rows[0]


def _candidate_property(organization_id):
    rows = _rows(
        """
        SELECT p.id, COUNT(m.id) AS photos
        FROM properties p
        JOIN property_media m
          ON m.property_id = p.id AND m.organization_id = p.organization_id
         AND m.media_type = 'photo' AND m.status = 'active'
         AND COALESCE(m.source, '') <> 'mock_network'
         AND COALESCE(m.url_kind, '') <> 'local_fixture'
         AND LOWER(COALESCE(m.original_url, '')) NOT LIKE '%example.com%'
        WHERE p.organization_id = ? AND p.agent_id IS NOT NULL AND p.listing_price IS NOT NULL
        GROUP BY p.id
        HAVING COUNT(m.id) >= 4
        ORDER BY COUNT(m.id) DESC, p.id DESC
        LIMIT 1
        """,
        (organization_id,),
    )
    if not rows:
        _fail("no synced property with agent, price and >= 4 real (non-mock) active photos; pass --property-id")
    return rows[0]["id"]


def _railway_path(key, *, static=False):
    root = RAILWAY_STATIC_ROOT if static else RAILWAY_PRIVATE_ROOT
    text = str(key or "").replace("\\", "/")
    if text.startswith(("/data/uploads/", "/data/static-uploads/")):
        return text
    return f"{root}/{text.lstrip('/')}"


def _local_candidate(key, *, static=False):
    text = str(key or "").replace("\\", "/")
    for prefix, root in (("/data/uploads/", get_private_upload_root()), ("/data/static-uploads/", get_upload_root())):
        if text.startswith(prefix):
            return Path(root) / text[len(prefix):]
    return Path(get_upload_root() if static else get_private_upload_root()) / text.lstrip("/")


def _is_http(value):
    return str(value or "").lower().startswith(("http://", "https://"))


def _audit_stored_files(media_rows, agent_row, settings):
    """Every file the render needs that lives on the Railway volume, and whether it exists here."""
    required = []
    missing = []

    def need(kind, ref, key, *, static=False, present=None):
        local = _local_candidate(key, static=static)
        exists = bool(present) if present is not None else local.is_file()
        item = {
            "kind": kind,
            "ref": ref,
            "stored_value": key,
            "railway_path": _railway_path(key, static=static),
            "local_path_expected": str(local),
            "exists_locally": exists,
        }
        required.append(item)
        if not exists:
            missing.append(item)

    for row in media_rows:
        key = row.get("storage_key")
        remote = _is_http(row.get("original_url")) and row.get("storage_strategy") == "remote_reference"
        if remote and not key:
            required.append({"kind": "property_photo", "ref": row["id"], "remote_url": row.get("original_url")})
            continue
        if key:
            found = property_media.resolve_media_filesystem_path(row)
            if found or not _is_http(row.get("original_url")):
                need("property_photo", row["id"], key, present=bool(found))
            else:
                required.append(
                    {"kind": "property_photo", "ref": row["id"], "stored_value": key,
                     "exists_locally": False, "remote_url": row.get("original_url")}
                )
    for column in ("profile_photo_key", "profile_photo_original_key"):
        if agent_row.get(column):
            need(f"agent_{column}", agent_row.get("id"), agent_row[column])
    source = str(settings.get("marketing_logo_source") or "").lower()
    logo_path = settings.get("marketing_logo_path")
    if logo_path and source in ("", "upload"):
        need("organization_marketing_logo", settings.get("organization_id"), logo_path,
             present=bool(org_logo.resolve_stored_logo_file(logo_path)))
    elif _is_http(settings.get("marketing_logo_url")) and source == "url":
        required.append({"kind": "organization_marketing_logo", "remote_url": settings.get("marketing_logo_url")})
    if settings.get("logo_path") and not _is_http(settings.get("logo_path")):
        need("organization_logo", settings.get("organization_id"), settings["logo_path"],
             present=bool(org_logo.resolve_stored_logo_file(settings["logo_path"])))
    return required, missing


def _demo_values():
    from modules.marketing_branding import DEMO_MARKETING_BRANDING

    try:
        from modules.property_sync.mock import get_mock_catalog

        mock_names = {
            str(item.get("agent_name") or "").strip().casefold()
            for item in get_mock_catalog()
            if item.get("agent_name")
        }
    except Exception:
        mock_names = set()
    return {
        "mock_agent_names": mock_names,
        "fixture_instagram": {"@josebarreiro", "josebarreiro"},
        "fixture_brokers": {"maria eugenia conti", "c.i. mat. nº 1234"},
        "demo_broker": {
            str(DEMO_MARKETING_BRANDING.get("legal_broker_name") or "").casefold(),
            str(DEMO_MARKETING_BRANDING.get("legal_broker_license") or "").casefold(),
        },
        "fixture_phone_digits": {"5491125131361"},
    }


_FAKE_PHONE = re.compile(r"(\d)\1{6,}|1234567|5555555|0{6,}")
_DEMO_HOST = re.compile(r"example\.(com|org|net)|\.test\b|\.invalid\b|\bmock\b", re.IGNORECASE)


def _detect_demo(info, entry, settings, facts):
    """Fail markers (demo_data) vs coincidences with demo constants stored as real data (warnings)."""
    values = _demo_values()
    demo = list(info.get("demo_data") or [])
    warnings = []
    for label, value in (
        ("agent_email", info.get("email")),
        ("agent_instagram", info.get("instagram")),
        ("agent_whatsapp", info.get("whatsapp")),
        ("broker", info.get("broker")),
        ("logo_url", settings.get("marketing_logo_url")),
    ):
        if value and _DEMO_HOST.search(str(value)):
            demo.append(f"{label}_demo_domain:{value}")
    for photo in entry["photos"]:
        location = f"{photo.get('source_url') or ''} {photo.get('source_path') or ''}"
        if _DEMO_HOST.search(location) or photo.get("url_kind") == "local_fixture":
            demo.append(f"photo_mock:{photo.get('property_media_id')}")
        if "property_marketing_qa" in location.replace("\\", "/"):
            demo.append(f"photo_qa_fixture:{photo.get('property_media_id')}")
    name = str(info.get("agent") or "").strip().casefold()
    if name in values["mock_agent_names"]:
        demo.append(f"agent_mock_catalog:{info.get('agent')}")
    handle = str(info.get("instagram") or "").strip().casefold()
    if handle in values["fixture_instagram"]:
        demo.append(f"agent_instagram_fixture:{info.get('instagram')}")
    digits = re.sub(r"\D", "", str(info.get("whatsapp") or ""))
    if digits and _FAKE_PHONE.search(digits):
        demo.append(f"agent_whatsapp_fake_pattern:{info.get('whatsapp')}")
    broker = str(info.get("broker") or "").casefold()
    if any(value and value in broker for value in values["fixture_brokers"]):
        demo.append(f"broker_fixture:{info.get('broker')}")
    if facts.get("used_demo_fallback"):
        demo.append("broker_demo_fallback_not_from_organization_settings")
    stored_broker = str(settings.get("legal_broker_name") or "").casefold()
    if stored_broker and stored_broker in values["demo_broker"]:
        warnings.append(
            "broker stored in organization_settings equals DEMO_MARKETING_BRANDING constant "
            f"({settings.get('legal_broker_name')} {settings.get('legal_broker_license')}); treated as real data"
        )
    if digits in values["fixture_phone_digits"]:
        warnings.append(
            f"agent whatsapp {info.get('whatsapp')} equals the QA fixture phone; value comes from the agents row"
        )
    return sorted(set(demo)), warnings


def _data_uri_sha256(uri):
    text = str(uri or "")
    if not text.startswith("data:") or "," not in text:
        return ""
    return hashlib.sha256(base64.b64decode(text.split(",", 1)[1])).hexdigest()


def _check(entry, key, ok, detail):
    entry["checks"][key] = {"ok": bool(ok), "detail": detail}


def _photo_provenance(photo, media_by_id):
    audit = photo.get("source_audit") or {}
    row = media_by_id.get(photo.get("id")) or {}
    return {
        "property_media_id": photo.get("id"),
        "in_property_media": bool(row),
        "url_kind": row.get("url_kind"),
        "storage_strategy": row.get("storage_strategy"),
        "source_variant": photo.get("source_variant"),
        "is_original": bool(photo.get("is_original")),
        "source_url": audit.get("source_url"),
        "source_path": audit.get("source_path"),
        "original_dimensions": f"{audit.get('original_width')}x{audit.get('original_height')}",
        "loaded_dimensions": f"{audit.get('loaded_width')}x{audit.get('loaded_height')}",
        "rendered_dimensions": f"{photo.get('display_width')}x{photo.get('display_height')}",
        "scale_factor": photo.get("scale_factor"),
        "crop": photo.get("crop"),
        "upscale": photo.get("upscale"),
        "sha256_source": audit.get("sha256"),
        "sha256_loaded": audit.get("loaded_sha256"),
        "sha256_data_uri": audit.get("data_uri_sha256"),
        "bytes_match": bool(audit.get("match")),
    }


# --------------------------------------------------------------------------- main
def run(args):
    read_only = _preflight()
    organization = _organization(args)
    organization_id = organization["id"]
    property_id = args.property_id or _candidate_property(organization_id)
    property_data = get_property_record(property_id, organization_id)
    if not property_data:
        _fail(f"property {property_id} not found in organization {organization_id}")

    media_rows = _rows(
        "SELECT id, source, url_kind, storage_strategy, original_url, storage_key, width, height "
        "FROM property_media "
        "WHERE property_id = ? AND organization_id = ? AND status = 'active' AND media_type = 'photo'",
        (property_id, organization_id),
    )
    media_by_id = {row["id"]: row for row in media_rows}
    agent_rows = _rows(
        "SELECT id, name, profile_photo_key, profile_photo_original_key FROM agents "
        "WHERE id = ? AND organization_id = ?",
        (property_data.get("agent_id"), organization_id),
    )
    agent_row = agent_rows[0] if agent_rows else {}
    settings = dict(get_organization_settings(organization_id) or {})
    settings.setdefault("organization_id", organization_id)

    report = {
        "organization": organization,
        "property_id": property_id,
        "property_address": property_data.get("address"),
        "database_backend": get_database_backend(),
        "database_source": "sqlite_snapshot" if SNAPSHOT is not None else "postgres_database_url",
        "database_session_read_only": read_only,
        "uploads_roots": {
            "private_upload_root_local": str(get_private_upload_root()),
            "static_upload_root_local": str(get_upload_root()),
            "railway_private_root": RAILWAY_PRIVATE_ROOT,
            "railway_static_root": RAILWAY_STATIC_ROOT,
        },
        "templates": {},
    }
    required, missing = _audit_stored_files(media_rows, agent_row, settings)
    report["required_files"] = required
    report["missing_railway_files"] = missing
    for item in missing:
        logging.getLogger("e2e").error(
            "[E2E_MISSING_RAILWAY_FILE] kind=%s ref=%s railway_path=%s local_expected=%s",
            item["kind"],
            item["ref"],
            item["railway_path"],
            item["local_path_expected"],
        )
    if missing:
        report["blocked"] = (
            f"{len(missing)} file(s) stored on the Railway volume are missing locally; "
            "no fixture or fallback is used. Copy them and pass --uploads-root / --static-uploads-root."
        )
        report["passed"] = False
        return report

    context = build_property_marketing_context(property_data, language="es", include_agent=True)
    options = _options_from_request({}, context)
    asset = {
        "property_snapshot": context_to_snapshot(context),
        "agent_branding_snapshot": context.get("agent") or {},
        "options": dict(options),
    }

    report["db_record"] = {
        key: property_data.get(key)
        for key in (
            "address", "listing_price", "listing_currency", "rooms", "bedrooms",
            "bathrooms", "covered_m2", "total_m2", "agent_id",
        )
    }
    report["organization_settings"] = {
        key: settings.get(key)
        for key in (
            "legal_broker_name", "legal_broker_license", "legal_broker_college",
            "marketing_logo_source", "marketing_logo_path", "marketing_logo_url", "logo_path",
        )
    }
    for template_id in TEMPLATES:
        render_context = _context_from_asset(asset)
        item_options = dict(options, layout_template=template_id, template=template_id)
        result = _render_local_visual(render_context, "post", options=item_options, art={}, language="es")
        png_path = OUT_DIR / f"{template_id}.png"
        png_path.write_bytes(result["png_bytes"])
        view = result["render_context"]
        info = result["provenance"]
        photos = [view["hero"]] + list(view.get("secondary") or [])
        agent_view = view.get("agent") or {}
        entry = {
            "png": str(png_path.relative_to(APP_DIR)),
            "canvas": list(view["canvas_size"]),
            "photos": [_photo_provenance(photo, media_by_id) for photo in photos],
            "agent": {
                "name": info["agent"],
                "agents_row": agent_row,
                "photo_rendered": info["agent_photo"],
                "photo_path": info["agent_photo_path"],
                "whatsapp": info["whatsapp"],
                "instagram": info["instagram"],
                "email": info["email"],
            },
            "broker": info["broker"],
            "logo": {
                "source": info["logo_source"],
                "sha256": _data_uri_sha256((view.get("branding") or {}).get("logo")),
            },
            "fallbacks": info["fallbacks"],
            "checks": {},
        }
        demo, warnings = _detect_demo(info, entry, settings, render_context.get("facts") or {})
        entry["demo_data"] = demo
        entry["demo_data_detected"] = bool(demo)
        entry["warnings"] = warnings
        _check(entry, "01_property_id", info["property_id"] == property_id, info["property_id"])
        _check(entry, "02_organization_id", info["organization_id"] == organization_id, info["organization_id"])
        _check(
            entry,
            "03_photos_from_property_media",
            all(item["in_property_media"] for item in entry["photos"]),
            [item["property_media_id"] for item in entry["photos"]],
        )
        _check(
            entry,
            "04_original_variant",
            all(item["is_original"] and item["bytes_match"] for item in entry["photos"]),
            [(item["property_media_id"], item["source_variant"], item["original_dimensions"]) for item in entry["photos"]],
        )
        _check(entry, "05_address", bool(view.get("address")), view.get("address"))
        _check(entry, "06_price", bool((view.get("price") or {}).get("amount")), view.get("price"))
        _check(entry, "07_rooms_bedrooms_bathrooms_m2", bool(view.get("facts")), view.get("facts"))
        _check(
            entry,
            "08_real_agent",
            bool(info["agent"]) and info["agent"] == str(agent_row.get("name") or "").strip(),
            {"rendered": info["agent"], "agents_row": agent_row.get("name")},
        )
        _check(entry, "09_agent_photo", info["agent_photo"] and bool(agent_view.get("photo")), info["agent_photo_path"])
        _check(entry, "10_whatsapp", bool(info["whatsapp"]), info["whatsapp"])
        _check(entry, "11_instagram", bool(info["instagram"]), info["instagram"])
        _check(entry, "12_email", bool(info["email"]), info["email"])
        settings_broker = str(settings.get("legal_broker_name") or "").strip()
        _check(
            entry,
            "13_broker_from_organization_settings",
            bool(settings_broker)
            and settings_broker in info["broker"]
            and not any(item.startswith("broker") for item in demo),
            {"rendered": info["broker"], "settings.legal_broker_name": settings_broker},
        )
        _check(entry, "14_organization_logo", info["logo_source"] == "organization", info["logo_source"])
        _check(entry, "15_no_demo_data", not demo, demo)
        entry["passed"] = all(item["ok"] for item in entry["checks"].values())
        report["templates"][template_id] = entry

    entries = list(report["templates"].values())
    report["same_property_all_templates"] = len({json.dumps(e["checks"]["01_property_id"]["detail"]) for e in entries}) == 1
    report["fallbacks"] = sorted({item for entry in entries for item in entry["fallbacks"]})
    report["demo_data"] = sorted({item for entry in entries for item in entry["demo_data"]})
    report["demo_data_detected"] = bool(report["demo_data"])
    report["warnings"] = sorted({item for entry in entries for item in entry["warnings"]})
    report["remote_fetches"] = REMOTE_FETCHES
    report["passed"] = all(entry["passed"] for entry in entries) and report["same_property_all_templates"]
    return report


def main():
    parser = argparse.ArgumentParser(description="Read-only E2E QA for Property Marketing Renderer V1")
    parser.add_argument("--property-id", type=int)
    parser.add_argument("--organization-id", type=int)
    parser.add_argument("--organization", help="organization name (substring); default 'JRH One'")
    parser.add_argument("--database-path", help="SQLite snapshot, opened strictly read-only (mode=ro&immutable=1)")
    parser.add_argument("--uploads-root", help="local copy of Railway /data/uploads (PRIVATE_UPLOAD_ROOT)")
    parser.add_argument("--static-uploads-root", help="local copy of Railway /data/static-uploads (UPLOAD_DIR)")
    args = parser.parse_args()

    _real_mkdir(OUT_DIR, parents=True, exist_ok=True)
    for template_id in TEMPLATES:
        stale = OUT_DIR / f"{template_id}.png"
        if stale.is_file():
            stale.unlink()
    log_path = OUT_DIR / "e2e_log.txt"
    handlers = [logging.FileHandler(log_path, mode="w", encoding="utf-8"), logging.StreamHandler()]
    formatter = _RedactFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    log = logging.getLogger("e2e")
    report = {"passed": False}
    exit_code = 1
    try:
        report = run(args)
        exit_code = 0 if report["passed"] else (2 if report.get("blocked") else 1)
        if report.get("blocked"):
            log.error("E2E_BLOCKED %s", report["blocked"])
    except Blocked as exc:
        report = {"passed": False, "blocked": str(exc)}
        log.error("E2E_BLOCKED %s", exc)
        exit_code = 2
    except Exception as exc:
        report = {"passed": False, "error": f"{type(exc).__name__}: {redact(exc)}"}
        log.error("E2E_ERROR %s", report["error"])
        exit_code = 3
    report["safety"] = dict(SAFETY, **SAFETY_DB)
    report["safety"]["read_only_ok"] = (
        not SAFETY["sql_blocked"]
        and not SAFETY["files_blocked"]
        and not SAFETY["network_blocked"]
        and SAFETY_DB["connections"] == SAFETY_DB["read_only_sessions"]
    )
    if SNAPSHOT is not None:
        SNAPSHOT_STATE["size_after"] = SNAPSHOT.stat().st_size
        SNAPSHOT_STATE["sha256_after"] = _file_sha256(SNAPSHOT)
        SNAPSHOT_STATE["mtime_after"] = SNAPSHOT.stat().st_mtime
        SNAPSHOT_STATE["unchanged"] = (
            SNAPSHOT_STATE["sha256_after"] == SNAPSHOT_STATE["sha256_before"]
            and SNAPSHOT_STATE["mtime_after"] == SNAPSHOT_STATE["mtime_before"]
        )
        SNAPSHOT_STATE["sidecar_files"] = [p.name for p in SNAPSHOT.parent.glob(SNAPSHOT.name + "-*")]
        report["snapshot"] = SNAPSHOT_STATE
        report["safety"]["read_only_ok"] = report["safety"]["read_only_ok"] and SNAPSHOT_STATE["unchanged"]
        log.info(
            "E2E_SNAPSHOT unchanged=%s sha256=%s sidecars=%s",
            str(SNAPSHOT_STATE["unchanged"]).lower(),
            SNAPSHOT_STATE["sha256_after"],
            SNAPSHOT_STATE["sidecar_files"] or "none",
        )
    report_path = OUT_DIR / "e2e_report.json"
    report_path.write_text(redact(json.dumps(report, indent=2, ensure_ascii=False, default=str)), encoding="utf-8")

    for template_id, entry in (report.get("templates") or {}).items():
        failed = [key for key, item in entry["checks"].items() if not item["ok"]]
        log.info("%s: %s failed=%s png=%s", template_id, "PASS" if entry["passed"] else "FAIL", failed or "none", entry["png"])
    log.info(
        "E2E_DB connections=%s read_only_sessions=%s",
        SAFETY_DB["connections"],
        SAFETY_DB["read_only_sessions"],
    )
    log.info(
        "E2E_SAFETY sql=%s sql_blocked=%s network=%s network_blocked=%s files_written=%s files_blocked=%s",
        SAFETY["sql_statements"],
        len(SAFETY["sql_blocked"]),
        len(SAFETY["network_requests"]),
        len(SAFETY["network_blocked"]),
        SAFETY["files_written"],
        len(SAFETY["files_blocked"]),
    )
    log.info(
        "E2E_RESULT %s fallbacks=%s demo_data_detected=%s report=%s",
        "PASS" if report.get("passed") else ("BLOCKED" if exit_code == 2 else "FAIL"),
        report.get("fallbacks", "n/a"),
        str(report["demo_data_detected"]).lower() if "demo_data_detected" in report else "n/a",
        report_path.relative_to(APP_DIR),
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
