"""
supabase_client.py — Singleton Supabase client and table bootstrap.
"""
import logging
import os

from supabase import Client, create_client

logger = logging.getLogger(__name__)

_client: Client | None = None

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

-- 2. Conversation history with pgvector for semantic search
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
    SELECT
        ch.role,
        ch.content,
        ch.created_at,
        1 - (ch.embedding <=> query_embedding) AS similarity
    FROM conversation_history ch
    ORDER BY ch.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
"""


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_ANON_KEY"],
        )
    return _client


def ensure_table_exists() -> None:
    """
    Verify both required tables are reachable on startup.
    If either is missing, log the full setup SQL and raise.
    """
    sb = get_supabase()
    errors = []

    for table in ("knowledge_nodes", "conversation_history"):
        try:
            sb.table(table).select("id").limit(1).execute()
            logger.info("Table '%s' verified.", table)
        except Exception as e:
            errors.append(f"{table}: {e}")

    if errors:
        logger.error(
            "Missing Supabase tables: %s\nRun the following SQL in the Supabase dashboard:\n%s",
            errors,
            SETUP_SQL,
        )
        raise RuntimeError(f"Supabase setup incomplete: {errors}")
