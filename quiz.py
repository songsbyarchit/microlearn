"""
quiz.py — Topic selection, question generation, and answer grading for quiz mode.
"""
import logging
import os
from datetime import datetime, timezone

import anthropic

from knowledge_graph import _get

logger = logging.getLogger(__name__)

claude = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def get_quiz_topics(n: int = 4) -> list[dict]:
    """
    Fetch all knowledge nodes and return the top n weighted toward:
    - lower bloom scores (more room to grow)
    - more recent updated_at (active topics)

    Returns list of dicts with keys: topic, domain, bloom_score, content.
    """
    try:
        rows = _get({"select": "domain,topic,bloom_score,content,updated_at"})
    except Exception as e:
        logger.warning("Failed to fetch knowledge nodes for quiz: %s", e)
        return []

    if not rows:
        return []

    now = datetime.now(tz=timezone.utc)

    def _score(row: dict) -> float:
        bloom = row.get("bloom_score") or 1
        # Lower bloom → higher priority (invert, scale 1-8 → 7-0)
        bloom_weight = (9 - bloom) / 8.0

        # Recency: days since last update, capped at 30
        updated_str = row.get("updated_at", "")
        try:
            updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            days_old = (now - updated).total_seconds() / 86400
        except Exception:
            days_old = 30
        recency_weight = max(0.0, 1.0 - days_old / 30.0)

        return bloom_weight * 0.6 + recency_weight * 0.4

    rows.sort(key=_score, reverse=True)

    return [
        {
            "topic": r["topic"],
            "domain": r["domain"],
            "bloom_score": r.get("bloom_score") or 1,
            "content": r.get("content", ""),
        }
        for r in rows[:n]
    ]


async def generate_question(topic: str, domain: str, content: str) -> dict:
    """
    Ask Claude to generate one multiple-choice question about the topic.

    Returns:
        {
            "question": str,
            "options": [str, str, str, str],   # exactly 4
            "correct_index": int,               # 0-3
            "explanation": str,
        }
    """
    prompt = f"""You are a quiz question writer for a personal learning app.

Topic: {topic}
Domain: {domain}
Context from learner's knowledge graph:
{content[:1200]}

Write ONE multiple-choice question that tests genuine understanding of this topic
(not just definition recall). The question should require the learner to apply,
analyse, or explain — not just recognise a term.

Respond with ONLY valid JSON in exactly this format, no other text:
{{
  "question": "...",
  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "correct_index": 0,
  "explanation": "..."
}}

Rules:
- correct_index is 0-based (0 = A, 1 = B, 2 = C, 3 = D)
- All four options must be plausible
- explanation should be 1-2 sentences saying why the correct answer is right
- Write in British English
- Keep the question under 40 words"""

    import json

    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    data = json.loads(raw)

    # Validate shape
    assert isinstance(data.get("question"), str)
    assert isinstance(data.get("options"), list) and len(data["options"]) == 4
    assert isinstance(data.get("correct_index"), int) and 0 <= data["correct_index"] <= 3
    assert isinstance(data.get("explanation"), str)

    return data


def grade_answer(user_reply: str, question_dict: dict) -> tuple[bool, str]:
    """
    Check whether user_reply matches the correct answer.

    Accepts: "1"/"2"/"3"/"4" (1-based) or "a"/"b"/"c"/"d" (case-insensitive).

    Returns (is_correct, explanation).
    """
    correct_index: int = question_dict["correct_index"]
    explanation: str = question_dict.get("explanation", "")

    reply = user_reply.strip().lower()

    # Map reply to 0-based index
    letter_map = {"a": 0, "b": 1, "c": 2, "d": 3}
    number_map = {"1": 0, "2": 1, "3": 2, "4": 3}

    if reply in letter_map:
        chosen = letter_map[reply]
    elif reply in number_map:
        chosen = number_map[reply]
    else:
        # Unrecognised reply — treat as wrong
        logger.debug("Unrecognised quiz reply: %r", user_reply)
        return False, explanation

    is_correct = chosen == correct_index
    return is_correct, explanation
