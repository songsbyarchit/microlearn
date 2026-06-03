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

# Rough chars-per-token estimate for context capping
_CHARS_PER_TOKEN = 4


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _sanitize(name: str) -> str:
    """Convert a string to a safe filename/directory name."""
    return re.sub(r"[^\w\-]", "_", name.lower().strip())


# ---------------------------------------------------------------------------
# Context helpers for brain.py
# ---------------------------------------------------------------------------

def _read_topic_file(topic_path: Path) -> dict[str, Any]:
    """Parse a topic markdown file into a structured dict."""
    if not topic_path.exists():
        return {}

    content = topic_path.read_text(encoding="utf-8")
    data: dict[str, Any] = {"raw": content}

    bloom_match = re.search(r"bloom_level:\s*(\d+)", content)
    if bloom_match:
        data["bloom_level"] = int(bloom_match.group(1))

    summary_match = re.search(r"## Summary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if summary_match:
        data["summary"] = summary_match.group(1).strip()

    edges_match = re.search(r"## Edges\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if edges_match:
        data["edges"] = edges_match.group(1).strip()

    vocab_match = re.search(r"## Vocabulary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if vocab_match:
        data["vocab"] = vocab_match.group(1).strip()

    return data


def get_selective_context(user_message: str, max_tokens: int = 500) -> str:
    """
    Return knowledge-graph context relevant to the user's message.

    Strategy:
    - Keyword-match user message against all topic names in the index.
    - Include the single most relevant topic in full.
    - Include remaining matched topics as one-line summaries.
    - Cap total output at max_tokens (approximate).
    """
    max_chars = max_tokens * _CHARS_PER_TOKEN

    if not INDEX_FILE.exists():
        return "No knowledge graph yet."

    # Collect all known topics from index
    index_content = INDEX_FILE.read_text(encoding="utf-8")
    all_topics: list[dict[str, Any]] = []  # {domain, topic, topic_key, bloom, summary}

    current_domain = None
    current_domain_key = None
    for line in index_content.splitlines():
        domain_match = re.match(r"^## (.+)", line)
        if domain_match:
            current_domain = domain_match.group(1)
            current_domain_key = _sanitize(current_domain)
            continue
        topic_match = re.match(r"^- \*\*(.+?)\*\*.*bloom:(\d+).*?—\s*(.*)", line)
        if topic_match and current_domain:
            all_topics.append({
                "domain": current_domain,
                "domain_key": current_domain_key,
                "topic": topic_match.group(1),
                "topic_key": _sanitize(topic_match.group(1)),
                "bloom": int(topic_match.group(2)),
                "summary": topic_match.group(3).strip(),
            })

    if not all_topics:
        return "No knowledge graph yet."

    # Keyword match: score each topic by how many message words appear in the topic name
    message_words = set(w.lower() for w in re.split(r"\W+", user_message) if len(w) > 3)
    scored: list[tuple[int, dict]] = []
    for t in all_topics:
        topic_words = set(re.split(r"\W+", t["topic"].lower()))
        score = len(message_words & topic_words)
        scored.append((score, t))

    scored.sort(key=lambda x: x[0], reverse=True)
    matched = [(s, t) for s, t in scored if s > 0]
    unmatched = [(s, t) for s, t in scored if s == 0]

    # Build output
    sections: list[str] = []
    chars_used = 0

    # Most relevant topic: include in full
    primary_list = matched if matched else scored
    if primary_list:
        _, primary = primary_list[0]
        topic_path = KNOWLEDGE_DIR / primary["domain_key"] / f"{primary['topic_key']}.md"
        full_data = _read_topic_file(topic_path)
        full_text = full_data.get("raw", f"Topic: {primary['topic']} (bloom {primary['bloom']})\n{primary['summary']}")
        if chars_used + len(full_text) <= max_chars:
            sections.append(f"### {primary['topic']} (full)\n{full_text}")
            chars_used += len(full_text)

    # Remaining matched topics: one-line summaries
    remaining = (matched[1:] if matched else []) + unmatched
    for _, t in remaining:
        line = f"- {t['topic']} (bloom {t['bloom']}): {t['summary']}"
        if chars_used + len(line) > max_chars:
            break
        sections.append(line)
        chars_used += len(line)

    return "\n".join(sections) if sections else "No relevant knowledge nodes found."


def get_knowledge_summary() -> str:
    """Return the raw index for injection when selective context isn't needed."""
    if not INDEX_FILE.exists():
        return "No knowledge graph yet."
    return INDEX_FILE.read_text(encoding="utf-8")[:2000]


# ---------------------------------------------------------------------------
# Knowledge viewer helper
# ---------------------------------------------------------------------------

def get_all_topics() -> list[dict[str, Any]]:
    """Return all topic dicts with full file data, sorted by last_updated desc."""
    results = []
    if not KNOWLEDGE_DIR.exists():
        return results

    for md_file in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        if md_file.name == "_index.md":
            continue
        content = md_file.read_text(encoding="utf-8")
        data: dict[str, Any] = {"file": str(md_file), "raw": content}

        title_match = re.match(r"^# (.+)", content)
        data["title"] = title_match.group(1) if title_match else md_file.stem

        domain_match = re.search(r"^domain:\s*(.+)", content, re.MULTILINE)
        data["domain"] = domain_match.group(1).strip() if domain_match else md_file.parent.name

        bloom_match = re.search(r"^bloom_level:\s*(\d+)", content, re.MULTILINE)
        data["bloom_level"] = int(bloom_match.group(1)) if bloom_match else 0

        updated_match = re.search(r"^last_updated:\s*(.+)", content, re.MULTILINE)
        data["last_updated"] = updated_match.group(1).strip() if updated_match else ""

        summary_match = re.search(r"## Summary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
        data["summary"] = summary_match.group(1).strip() if summary_match else ""

        results.append(data)

    results.sort(key=lambda x: x["last_updated"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Graph updates
# ---------------------------------------------------------------------------

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
                "edges": [{"type": "prerequisite", "target": "classical_mechanics"}],
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

        edges_md = "\n".join(
            f"- {e.get('type', 'related')}: {e.get('target', '')}" for e in edges
        )
        vocab_md = "\n".join(f"- {v}" for v in vocab)

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

        domain_key = _sanitize(domain)
        if domain_key not in updated_index_entries:
            updated_index_entries[domain_key] = {"topics": {}, "domain": domain}
        updated_index_entries[domain_key]["topics"][_sanitize(topic)] = {
            "bloom_level": bloom_level,
            "summary": summary,
            "topic": topic,
        }

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
                f"- **{td['topic'].title()}** (bloom:{td['bloom_level']}) -- {td.get('summary', '')}"
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
        match = re.search(r"```json\s*(\{.*?\"topics\".*?)```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse KG JSON: %s", e)
        return None
