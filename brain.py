"""
brain.py — Claude API calls and system prompt construction.
"""
import json
import logging
import os
from typing import Any

import anthropic

from knowledge_graph import get_knowledge_summary, parse_kg_json_from_response, update_knowledge_graph

logger = logging.getLogger(__name__)

claude = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT_TEMPLATE = """\
You are a brilliant, curious friend who knows everything but explains it simply — \
Feynman-style. You never overwhelm. You always build on what the person already knows. \
You ask one follow-up question at the end if it feels natural.

Keep replies SHORT. Max 3-4 sentences. This is a friend texting, not a lecture. \
Never use bullet points or headers in your reply.

--- KNOWLEDGE GRAPH SUMMARY ---
{knowledge_summary}

--- CURRENT BLOOM LEVEL FOR THIS TOPIC ---
{bloom_info}
---

After your conversational reply, output a knowledge-graph update as a fenced JSON block \
with the tag json_kg. Example:

```json_kg
{{
  "topics": [
    {{
      "domain": "physics",
      "topic": "thermodynamics",
      "bloom_level": 3,
      "summary": "Laws of heat, energy, and entropy",
      "edges": [
        {{"type": "prerequisite", "target": "classical mechanics"}}
      ],
      "vocab": ["entropy", "enthalpy", "Carnot cycle"]
    }}
  ]
}}
```

The conversational reply comes FIRST, then the json_kg block.
"""


def _build_system_prompt(knowledge_summary: str, bloom_info: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        knowledge_summary=knowledge_summary,
        bloom_info=bloom_info,
    )


def _extract_reply(full_text: str) -> str:
    """Strip out the json_kg block and return only the conversational part."""
    import re
    clean = re.sub(r"```json_kg.*?```", "", full_text, flags=re.DOTALL)
    # Also strip stray ``` blocks
    clean = re.sub(r"```.*?```", "", clean, flags=re.DOTALL)
    return clean.strip()


async def get_reply(
    user_message: str,
    history: list[dict[str, str]],
) -> tuple[str, dict[str, Any] | None]:
    """
    Call Claude with the conversation history and return:
    - (reply_text, kg_update_dict | None)
    """
    knowledge_summary = get_knowledge_summary()

    # Infer a rough bloom level from knowledge graph if available
    bloom_info = "Unknown — treat as beginner, introduce gently."

    system_prompt = _build_system_prompt(knowledge_summary, bloom_info)

    # Build message list: history + current user message
    messages: list[dict[str, str]] = []
    for h in history[-10:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    logger.info("Calling Claude with %d messages", len(messages))

    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=system_prompt,
        messages=messages,
    )

    full_text = response.content[0].text
    logger.debug("Claude raw response: %s", full_text[:300])

    reply = _extract_reply(full_text)
    kg_update = parse_kg_json_from_response(full_text)

    if kg_update:
        try:
            update_knowledge_graph(kg_update)
        except Exception as e:
            logger.warning("Failed to update knowledge graph: %s", e)

    return reply, kg_update
