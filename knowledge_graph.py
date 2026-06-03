"""
knowledge_graph.py — Knowledge graph backed by Supabase via direct REST API calls.

Table schema (create once in Supabase dashboard — see supabase_client.SETUP_SQL):
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid()
    domain      text NOT NULL
    topic       text NOT NULL
    content     text          -- full markdown
    bloom_score integer DEFAULT 1
    edges       jsonb DEFAULT '[]'::jsonb
    updated_at  timestamptz DEFAULT now()
    UNIQUE (domain, topic)
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from supabase_client import sb_headers, sb_url

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4


def _sanitize(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.lower().strip())


def _extract_summary(content: str) -> str:
    m = re.search(r"## Summary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    return m.group(1).strip() if m else ""


def _get(params: dict) -> list[dict]:
    """GET /rest/v1/knowledge_nodes with query params. Returns list of rows."""
    resp = httpx.get(
        sb_url("/rest/v1/knowledge_nodes"),
        headers=sb_headers(),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def update_knowledge_graph(kg_update: dict[str, Any]) -> None:
    """
    Upsert topics from a Claude KG update dict into Supabase via REST API.
    """
    topics = kg_update.get("topics", [])
    if not topics:
        logger.debug("No topics in KG update, skipping.")
        return

    now = datetime.now(tz=timezone.utc).isoformat()

    for t in topics:
        domain = t.get("domain", "general")
        topic = t.get("topic", "unknown")
        bloom_level = t.get("bloom_level", 1)
        summary = t.get("summary", "")
        edges = t.get("edges", [])
        vocab = t.get("vocab", [])

        # Fetch existing content to preserve history
        existing_history = ""
        try:
            rows = _get({
                "select": "content",
                "domain": f"eq.{domain}",
                "topic": f"eq.{topic}",
                "limit": "1",
            })
            if rows:
                old_content = rows[0].get("content", "")
                m = re.search(r"## History\n(.*)", old_content, re.DOTALL)
                if m:
                    existing_history = m.group(1).strip()
        except Exception as e:
            logger.warning("Could not fetch existing node for history: %s", e)

        edges_md = "\n".join(f"- {e.get('type', 'related')}: {e.get('target', '')}" for e in edges)
        vocab_md = "\n".join(f"- {v}" for v in vocab)
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        history_entry = f"- {now_str}: bloom_score updated to {bloom_level}"
        history_md = f"{existing_history}\n{history_entry}".strip()

        content = f"""# {topic.title()}

domain: {domain}
bloom_level: {bloom_level}
last_updated: {now_str}

## Summary
{summary}

## Edges
{edges_md or "(none)"}

## Vocabulary
{vocab_md or "(none)"}

## History
{history_md}
"""

        row = {
            "domain": domain,
            "topic": topic,
            "content": content,
            "bloom_score": bloom_level,
            "edges": edges,
            "updated_at": now,
        }

        try:
            resp = httpx.post(
                sb_url("/rest/v1/knowledge_nodes"),
                headers=sb_headers(prefer="resolution=merge-duplicates"),
                params={"on_conflict": "domain,topic"},
                content=json.dumps(row),
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Upserted knowledge node: %s / %s (bloom %d)", domain, topic, bloom_level)
        except Exception as e:
            logger.error("Failed to upsert knowledge node %s/%s: %s", domain, topic, e)


# ---------------------------------------------------------------------------
# Reads for brain.py
# ---------------------------------------------------------------------------

def get_selective_context(user_message: str, max_tokens: int = 500) -> str:
    """
    Return knowledge-graph context relevant to the user's message.
    Keyword-matches against topic names; primary topic in full, rest as one-liners.
    """
    max_chars = max_tokens * _CHARS_PER_TOKEN

    try:
        rows = _get({"select": "domain,topic,bloom_score,content"})
    except Exception as e:
        logger.warning("Failed to fetch knowledge nodes: %s", e)
        return "No knowledge graph yet."

    if not rows:
        return "No knowledge graph yet."

    message_words = set(w.lower() for w in re.split(r"\W+", user_message) if len(w) > 3)
    scored: list[tuple[int, dict]] = []
    for row in rows:
        topic_words = set(re.split(r"\W+", row["topic"].lower()))
        score = len(message_words & topic_words)
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    matched = [(s, r) for s, r in scored if s > 0]

    sections: list[str] = []
    chars_used = 0

    primary_list = matched if matched else scored
    if primary_list:
        _, primary = primary_list[0]
        full_text = primary.get("content") or f"Topic: {primary['topic']} (bloom {primary['bloom_score']})"
        if chars_used + len(full_text) <= max_chars:
            sections.append(f"### {primary['topic']} (full)\n{full_text}")
            chars_used += len(full_text)

    remaining = (matched[1:] if matched else []) + [(s, r) for s, r in scored if s == 0]
    for _, row in remaining:
        summary = _extract_summary(row.get("content", ""))
        line = f"- {row['topic']} ({row['domain']}, bloom {row['bloom_score']}): {summary}"
        if chars_used + len(line) > max_chars:
            break
        sections.append(line)
        chars_used += len(line)

    return "\n".join(sections) if sections else "No relevant knowledge nodes found."


# ---------------------------------------------------------------------------
# Reads for /knowledge viewer
# ---------------------------------------------------------------------------

def get_all_topics() -> list[dict[str, Any]]:
    """Return all knowledge nodes sorted by updated_at desc."""
    try:
        rows = _get({"select": "*", "order": "updated_at.desc"})
    except Exception as e:
        logger.error("Failed to fetch all topics: %s", e)
        return []

    results = []
    for row in rows:
        content = row.get("content", "")
        results.append({
            "id": f"{row['domain']}/{row['topic']}",
            "title": row["topic"].title(),
            "topic": row["topic"],
            "domain": row["domain"],
            "bloom_level": row.get("bloom_score", 0),
            "last_updated": row.get("updated_at", ""),
            "summary": _extract_summary(content),
            "raw": content,
            "edges": row.get("edges") or [],
        })
    return results


def get_knowledge_index_markdown() -> str:
    """Build an index markdown string from all nodes."""
    try:
        rows = _get({"select": "domain,topic,bloom_score,content", "order": "domain"})
    except Exception as e:
        logger.error("Failed to fetch knowledge index: %s", e)
        return "(error fetching knowledge graph)"

    if not rows:
        return "No knowledge graph yet."

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Knowledge Graph Index\n\n_Last updated: {now}_\n"]
    current_domain = None
    for row in rows:
        if row["domain"] != current_domain:
            current_domain = row["domain"]
            lines.append(f"\n## {current_domain.title()}\n")
        summary = _extract_summary(row.get("content", ""))
        lines.append(f"- **{row['topic'].title()}** (bloom:{row['bloom_score']}) -- {summary}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON parsing (unchanged)
# ---------------------------------------------------------------------------

def parse_kg_json_from_response(text: str) -> dict[str, Any] | None:
    """Extract the json_kg block from Claude's response."""
    match = re.search(r"```json_kg\s*(.*?)```", text, re.DOTALL)
    if not match:
        match = re.search(r"```json\s*(\{.*?\"topics\".*?)```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse KG JSON: %s", e)
        return None
