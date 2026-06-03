"""
supabase_client.py — Singleton Supabase client and table bootstrap.
"""
import logging
import os

from supabase import Client, create_client

logger = logging.getLogger(__name__)

_client: Client | None = None

CREATE_TABLE_SQL = """
-- Run this once in the Supabase dashboard SQL editor:

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
    Verify the knowledge_nodes table is reachable.
    If not, log the SQL needed to create it and raise so startup fails loudly.
    """
    try:
        get_supabase().table("knowledge_nodes").select("id").limit(1).execute()
        logger.info("knowledge_nodes table verified.")
    except Exception as e:
        logger.error(
            "Cannot reach knowledge_nodes table: %s\n"
            "Create it in the Supabase dashboard SQL editor:\n%s",
            e,
            CREATE_TABLE_SQL,
        )
        raise
