"""
knowledge_graph.py — Read and write the /knowledge markdown knowledge graph.
"""
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")
INDEX_FILE = KNOWLEDGE_DIR / "_index.md"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _sanitize(name: str) -> str:
    """Convert a string to a safe filename/directory name."""
    return re.sub(r"[^\w\-]", "_", name.lower().strip())


def get_knowledge_summary() -> str:
    """Return a short summary of all top-level topics for injection into the system prompt."""
    if not INDEX_FILE.exists():
        return "No knowledge graph yet."

    content = INDEX_FILE.read_text(encoding="utf-8")
    # Return first 2000 chars to avoid context bloat
    return content[:2000]


def _read_topic_file(domain: str, topic: str) -> dict[str, Any]:
    """Parse a topic markdown file into a structured dict."""
    topic_path = KNOWLEDGE_DIR / _sanitize(domain) / f"{_sanitize(topic)}.md"
    if not topic_path.exists():
        return {}

    content = topic_path.read_text(encoding="utf-8")

    data: dict[str, Any] = {"raw": content}

    # Extract Bloom level
    bloom_match = re.search(r"bloom_level:\s*(\d+)", content)
    if bloom_match:
        data["bloom_level"] = int(bloom_match.group(1))

    # Extract edges
    edges_match = re.search(r"## Edges\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if edges_match:
        data["edges"] = edges_match.group(1).strip()

    # Extract vocab
    vocab_match = re.search(r"## Vocabulary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if vocab_match:
        data["vocab"] = vocab_match.group(1).strip()

    return data


def update_knowledge_graph(kg_update: dict[str, Any]) -> None:
    """
    Apply a knowledge graph update dict (produced by Claude) to the markdown files.

    Expected shape:
    {
        "topics": [
            {
                "domain": "physics",
                "topic": "thermodynamics",
                "bloom_level": 3,
                "summary": "brief one-liner",
                "edges": [
                    {"type": "prerequisite", "target": "classical_mechanics"}
                ],
                "vocab": ["entropy", "enthalpy"]
            }
        ]
    }
    """
    _ensure_dir(KNOWLEDGE_DIR)

    topics = kg_update.get("topics", [])
    if not topics:
        logger.debug("No topics in KG update, skipping.")
        return

    updated_index_entries: dict[str, dict[str, Any]] = {}

    # Load existing index entries
    if INDEX_FILE.exists():
        _parse_index_into(updated_index_entries)

    for t in topics:
        domain = t.get("domain", "general")
        topic = t.get("topic", "unknown")
        bloom_level = t.get("bloom_level", 1)
        summary = t.get("summary", "")
        edges = t.get("edges", [])
        vocab = t.get("vocab", [])

        domain_dir = KNOWLEDGE_DIR / _sanitize(domain)
        _ensure_dir(domain_dir)

        topic_path = domain_dir / f"{_sanitize(topic)}.md"

        # Build / update topic file
        edges_md = "\n".join(
            f"- {e.get('type', 'related')}: {e.get('target', '')}" for e in edges
        )
        vocab_md = "\n".join(f"- {v}" for v in vocab)

        # Append new exchange to history if file exists
        existing_history = ""
        if topic_path.exists():
            old = topic_path.read_text(encoding="utf-8")
            history_match = re.search(r"## History\n(.*)", old, re.DOTALL)
            if history_match:
                existing_history = history_match.group(1).strip()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        new_history_entry = f"- {now}: bloom_level updated to {bloom_level}"
        history_md = f"{existing_history}\n{new_history_entry}".strip()

        topic_content = f"""# {topic.title()}

domain: {domain}
bloom_level: {bloom_level}
last_updated: {now}

## Summary
{summary}

## Edges
{edges_md or "(none)"}

## Vocabulary
{vocab_md or "(none)"}

## History
{history_md}
"""
        topic_path.write_text(topic_content, encoding="utf-8")
        logger.info("Updated knowledge file: %s", topic_path)

        # Track for index
        domain_key = _sanitize(domain)
        if domain_key not in updated_index_entries:
            updated_index_entries[domain_key] = {"topics": {}, "domain": domain}
        updated_index_entries[domain_key]["topics"][_sanitize(topic)] = {
            "bloom_level": bloom_level,
            "summary": summary,
            "topic": topic,
        }

    # Rewrite index
    _write_index(updated_index_entries)


def _parse_index_into(entries: dict[str, Any]) -> None:
    """Parse existing _index.md into the entries dict (best-effort)."""
    content = INDEX_FILE.read_text(encoding="utf-8")
    current_domain = None
    for line in content.splitlines():
        domain_match = re.match(r"^## (.+)", line)
        if domain_match:
            current_domain = _sanitize(domain_match.group(1))
            if current_domain not in entries:
                entries[current_domain] = {"topics": {}, "domain": domain_match.group(1)}
            continue
        topic_match = re.match(r"^- \*\*(.+?)\*\*.*bloom:(\d+).*?—\s*(.*)", line)
        if topic_match and current_domain:
            t_key = _sanitize(topic_match.group(1))
            entries[current_domain]["topics"][t_key] = {
                "bloom_level": int(topic_match.group(2)),
                "summary": topic_match.group(3).strip(),
                "topic": topic_match.group(1),
            }


def _write_index(entries: dict[str, Any]) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Knowledge Graph Index\n\n_Last updated: {now}_\n"]
    for domain_key, domain_data in sorted(entries.items()):
        lines.append(f"\n## {domain_data.get('domain', domain_key).title()}\n")
        for topic_key, td in sorted(domain_data["topics"].items()):
            lines.append(
                f"- **{td['topic'].title()}** (bloom:{td['bloom_level']}) — {td.get('summary', '')}"
            )
    INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Rewrote knowledge index.")


def parse_kg_json_from_response(text: str) -> dict[str, Any] | None:
    """
    Extract the JSON knowledge-graph update block from Claude's response.
    Claude is instructed to wrap it in ```json_kg ... ``` fences.
    """
    match = re.search(r"```json_kg\s*(.*?)```", text, re.DOTALL)
    if not match:
        # Fallback: try plain ```json
        match = re.search(r"```json\s*(\{.*?\"topics\".*?)```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse KG JSON: %s", e)
        return None
