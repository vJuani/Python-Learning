"""Idempotent Marketing IA tables. SQLite + PostgreSQL."""

from __future__ import annotations

from .connection import get_connection, get_database_backend
from modules.config import BACKEND_POSTGRES


MARKETING_ASSETS_SQL = """
CREATE TABLE IF NOT EXISTS marketing_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER,
    created_by_user_id INTEGER,
    property_id INTEGER NOT NULL,
    generation_id TEXT NOT NULL,
    format TEXT NOT NULL,
    style TEXT NOT NULL,
    tone TEXT NOT NULL,
    template TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'generated',
    copy_snapshot_json TEXT,
    property_snapshot_json TEXT,
    agent_branding_snapshot_json TEXT,
    options_json TEXT,
    storage_key TEXT,
    pdf_storage_key TEXT,
    temporary INTEGER NOT NULL DEFAULT 1,
    saved INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE SET NULL,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,

    CHECK (status IN ('generated', 'selected', 'archived'))
)
"""

INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_org_agent
    ON marketing_assets (organization_id, agent_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_generation
    ON marketing_assets (organization_id, generation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_property
    ON marketing_assets (organization_id, property_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_expires
    ON marketing_assets (organization_id, temporary, expires_at)
    """,
)

ASSET_COLUMNS = (
    ("temporary", "INTEGER NOT NULL DEFAULT 1"),
    ("saved", "INTEGER NOT NULL DEFAULT 0"),
    ("expires_at", "TEXT"),
)

GENERATIONS_SQL = """
CREATE TABLE IF NOT EXISTS marketing_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    created_by_user_id INTEGER,
    agent_id INTEGER,
    property_id INTEGER,
    parent_generation_id INTEGER,
    content_type TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    objective TEXT,
    style TEXT,
    tone TEXT,
    format TEXT,
    prompt_input TEXT,
    generated_copy TEXT,
    generated_data TEXT,
    provider TEXT,
    model_name TEXT,
    error_message TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE SET NULL,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE SET NULL,

    CHECK (content_type IN ('post', 'story', 'carousel', 'copy', 'whatsapp')),
    CHECK (origin IN ('property', 'personal_brand', 'office', 'free')),
    CHECK (status IN ('draft', 'processing', 'completed', 'failed', 'discarded'))
)
"""

GENERATION_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_gen_org_created
    ON marketing_generations (organization_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_gen_org_author
    ON marketing_generations (organization_id, created_by_user_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_gen_org_type
    ON marketing_generations (organization_id, content_type, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_gen_org_agent
    ON marketing_generations (organization_id, agent_id, created_at)
    """,
)


CONVERSATIONS_SQL = """
CREATE TABLE IF NOT EXISTS marketing_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    title TEXT,
    property_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE SET NULL
)
"""

MESSAGES_SQL = """
CREATE TABLE IF NOT EXISTS marketing_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'text',
    content TEXT,
    generation_id INTEGER,
    metadata_json TEXT,
    created_at TEXT NOT NULL,

    FOREIGN KEY (conversation_id)
        REFERENCES marketing_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (generation_id)
        REFERENCES marketing_generations(id) ON DELETE SET NULL,

    CHECK (role IN ('user', 'assistant')),
    CHECK (message_type IN ('text', 'generation', 'status'))
)
"""

CONVERSATION_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_conv_org_user
    ON marketing_conversations (organization_id, user_id, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_conv_org_updated
    ON marketing_conversations (organization_id, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_msg_conversation
    ON marketing_messages (conversation_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkt_msg_generation
    ON marketing_messages (generation_id)
    """,
)


def _ensure_generations(cursor, *, postgres):
    sql = GENERATIONS_SQL
    if postgres:
        sql = (
            sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
            .replace("created_by_user_id INTEGER,", "created_by_user_id BIGINT,")
            .replace("agent_id INTEGER,", "agent_id BIGINT,")
            .replace("property_id INTEGER,", "property_id BIGINT,")
            .replace("parent_generation_id INTEGER,", "parent_generation_id BIGINT,")
        )
    cursor.execute(sql)
    _ensure_whatsapp_content_type(cursor, postgres=postgres)
    for statement in GENERATION_INDEXES:
        cursor.execute(statement)
    _ensure_conversations(cursor, postgres=postgres)


def _pg_int_sql(sql):
    return (
        sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("user_id INTEGER NOT NULL", "user_id BIGINT NOT NULL")
        .replace("property_id INTEGER,", "property_id BIGINT,")
        .replace("conversation_id INTEGER NOT NULL", "conversation_id BIGINT NOT NULL")
        .replace("generation_id INTEGER,", "generation_id BIGINT,")
    )


def _ensure_conversations(cursor, *, postgres):
    conversations_sql = CONVERSATIONS_SQL
    messages_sql = MESSAGES_SQL
    if postgres:
        conversations_sql = _pg_int_sql(conversations_sql)
        messages_sql = _pg_int_sql(messages_sql)
    cursor.execute(conversations_sql)
    cursor.execute(messages_sql)
    for statement in CONVERSATION_INDEXES:
        cursor.execute(statement)


def _ensure_whatsapp_content_type(cursor, *, postgres):
    if postgres:
        cursor.execute(
            """
            ALTER TABLE marketing_generations
            DROP CONSTRAINT IF EXISTS marketing_generations_content_type_check
            """
        )
        cursor.execute(
            """
            ALTER TABLE marketing_generations
            ADD CONSTRAINT marketing_generations_content_type_check
            CHECK (content_type IN ('post', 'story', 'carousel', 'copy', 'whatsapp'))
            """
        )
        return
    row = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='marketing_generations'"
    ).fetchone()
    sql = (row[0] or "") if row else ""
    if "whatsapp" in sql:
        return
    for name in (
        "idx_mkt_gen_org_created",
        "idx_mkt_gen_org_author",
        "idx_mkt_gen_org_type",
        "idx_mkt_gen_org_agent",
    ):
        cursor.execute(f"DROP INDEX IF EXISTS {name}")
    cursor.execute("ALTER TABLE marketing_generations RENAME TO marketing_generations_old")
    cursor.execute(GENERATIONS_SQL)
    cursor.execute(
        """
        INSERT INTO marketing_generations (
            id, organization_id, created_by_user_id, agent_id, property_id,
            parent_generation_id, content_type, origin, status, objective,
            style, tone, format, prompt_input, generated_copy, generated_data,
            provider, model_name, error_message, input_tokens, output_tokens,
            estimated_cost, created_at, updated_at, completed_at
        )
        SELECT
            id, organization_id, created_by_user_id, agent_id, property_id,
            parent_generation_id, content_type, origin, status, objective,
            style, tone, format, prompt_input, generated_copy, generated_data,
            provider, model_name, error_message, input_tokens, output_tokens,
            estimated_cost, created_at, updated_at, completed_at
        FROM marketing_generations_old
        """
    )
    cursor.execute("DROP TABLE marketing_generations_old")


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


BATCHES_SQL = """
CREATE TABLE IF NOT EXISTS marketing_batches (
    id TEXT PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    agent_id INTEGER,
    created_by_user_id INTEGER,
    prompt TEXT,
    request_json TEXT,
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def migrate_marketing_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(MARKETING_ASSETS_SQL)
        cursor.execute(BATCHES_SQL)
        for name, definition in ASSET_COLUMNS:
            if not _column_exists(cursor, "marketing_assets", name):
                cursor.execute(f"ALTER TABLE marketing_assets ADD COLUMN {name} {definition}")
        for statement in INDEXES:
            cursor.execute(statement)
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_marketing_batches_org
            ON marketing_batches (organization_id, created_at)
            """
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_marketing_batches_idem
            ON marketing_batches (organization_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND TRIM(idempotency_key) != ''
            """
        )
        _ensure_generations(cursor, postgres=False)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_marketing_postgres(cursor):
    sql = (
        MARKETING_ASSETS_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("agent_id INTEGER,", "agent_id BIGINT,")
        .replace("created_by_user_id INTEGER,", "created_by_user_id BIGINT,")
        .replace("property_id INTEGER NOT NULL", "property_id BIGINT NOT NULL")
    )
    cursor.execute(sql)
    for name, definition in ASSET_COLUMNS:
        mapped = definition.replace("INTEGER", "SMALLINT")
        cursor.execute(
            f"ALTER TABLE marketing_assets ADD COLUMN IF NOT EXISTS {name} {mapped}"
        )
    pg_batches = (
        BATCHES_SQL.replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("property_id INTEGER NOT NULL", "property_id BIGINT NOT NULL")
        .replace("agent_id INTEGER,", "agent_id BIGINT,")
        .replace("created_by_user_id INTEGER,", "created_by_user_id BIGINT,")
    )
    cursor.execute(pg_batches)
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_marketing_batches_org
        ON marketing_batches (organization_id, created_at)
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_marketing_batches_idem
        ON marketing_batches (organization_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL AND BTRIM(idempotency_key) != ''
        """
    )
    for statement in INDEXES:
        cursor.execute(statement.replace("TRIM(", "BTRIM("))
    _ensure_generations(cursor, postgres=True)


def migrate_marketing():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        cursor = connection.cursor()
        try:
            migrate_marketing_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_marketing_sqlite()
