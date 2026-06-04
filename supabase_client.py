"""
supabase_client.py — Thin helpers for direct Supabase REST API access via httpx.
No supabase-py library required.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

SETUP_SQL = """
-- Run this once in the Supabase dashboard SQL editor:

-- 1. Knowledge graph nodes
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain      text NOT NULL,
    topic       text NOT NULL,
    content     text,
    bloom_score integer DEFAULT 1,
    edges       jsonb DEFAULT '[]'::jsonb,
    updated_at  timestamptz DEFAULT now(),
    UNIQUE (domain, topic)
);

-- 2. Transcript log
CREATE TABLE IF NOT EXISTS transcripts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content     text,
    created_at  timestamptz DEFAULT now(),
    word_count  integer,
    is_voice_note boolean DEFAULT false
);

-- 3. Conversation history with pgvector for semantic search
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS conversation_history (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role       text,
    content    text,
    embedding  vector(1536),
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversation_history_embedding_idx
    ON conversation_history USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- 3. RPC function for semantic similarity search
CREATE OR REPLACE FUNCTION match_conversation_history(
    query_embedding vector(1536),
    match_count     int DEFAULT 8
)
RETURNS TABLE(role text, content text, created_at timestamptz, similarity float)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT ch.role, ch.content, ch.created_at,
           1 - (ch.embedding <=> query_embedding) AS similarity
    FROM conversation_history ch
    ORDER BY ch.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
"""


def sb_url(path: str) -> str:
    """Build a full Supabase REST URL. path should start with /rest/v1/..."""
    base = os.environ["SUPABASE_URL"].rstrip("/")
    return f"{base}{path}"


def sb_headers(prefer: str | None = None) -> dict[str, str]:
    """Return standard Supabase REST headers."""
    key = os.environ["SUPABASE_ANON_KEY"]
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def ensure_table_exists() -> None:
    """
    Verify both required tables are reachable on startup via the REST API.
    Logs the full setup SQL and raises if either table is missing.
    """
    errors = []
    for table in ("knowledge_nodes", "conversation_history", "transcripts"):
        url = sb_url(f"/rest/v1/{table}?select=id&limit=1")
        try:
            resp = httpx.get(url, headers=sb_headers(), timeout=10)
            if resp.status_code >= 400:
                errors.append(f"{table}: HTTP {resp.status_code} — {resp.text[:120]}")
            else:
                logger.info("Table '%s' verified.", table)
        except Exception as e:
            errors.append(f"{table}: {e}")

    if errors:
        logger.error(
            "Missing or unreachable Supabase tables: %s\n"
            "Run the following SQL in the Supabase dashboard:\n%s",
            errors, SETUP_SQL,
        )
        raise RuntimeError(f"Supabase setup incomplete: {errors}")
