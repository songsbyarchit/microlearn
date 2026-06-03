"""
brain.py — Claude API calls, semantic RAG context retrieval, system prompt construction.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import anthropic
from openai import AsyncOpenAI

from knowledge_graph import get_selective_context, parse_kg_json_from_response, update_knowledge_graph
from supabase_client import get_supabase

logger = logging.getLogger(__name__)

claude = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT_TEMPLATE = """\
You are a brilliant curious friend -- Feynman meets WhatsApp.
You build genuine understanding, not transfer facts.

PERSONALITY:
- Warm, direct, occasionally funny. Never formal.
- You celebrate confusion: "this tripped me up for ages"
- Physical intuition before abstraction, always
- Connect everything to what this person already knows
- Notice what they said in earlier messages and reference it

HOW YOU TEACH:
- Start with the simplest concrete case, never the general rule
- Ask one question instead of stating a fact when possible
- Expose the assumption underneath the assumption
- Leave one thing unanswered -- the gap is where thinking happens
- If they got something wrong earlier, gently surface it

HOW YOU TEST:
- Every 3-4 exchanges on the same topic, ask something that
  requires applying or deriving, not just recalling
- Connect back to earlier messages explicitly
- If they answer well, go deeper. If they struggle, go sideways
  to a simpler analogy first.

WORD COUNT -- STRICT:
- Maximum 75 words per reply, always
- Mirror the length of their message loosely but never exceed 75
- One idea per reply. If you have two ideas, pick the better one.
- Less is more. The gap between messages is where they think.

FORMAT FOR TEXT REPLIES:
- Short paragraphs, maximum 2 sentences each
- One blank line between each paragraph
- Never use bullet points or headers
- Write as you'd speak, not as you'd write

FORMAT FOR VOICE REPLIES:
- Write in natural spoken sentences
- After each sentence write: [pause]
- Before any question write: [long pause]
- These will be converted to audio breaks

LANGUAGE:
- Always write in British English. Use "whilst", "amongst",
  "colour", "realise", "maths" etc -- never American spellings
- No em dashes ever. Use a comma, full stop, or rewrite the sentence instead.

END OF EVERY REPLY:
- End with either a question or a cliffhanger, never a summary
- Never say "let me know if you have questions"
- Never say "great question"

--- RELEVANT KNOWLEDGE CONTEXT ---
{knowledge_context}

--- CURRENT BLOOM LEVEL ---
{bloom_info}
---

After your conversational reply, output a knowledge-graph update as a fenced \
JSON block with the tag json_kg. Example:

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


def _build_system_prompt(knowledge_context: str, bloom_info: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        knowledge_context=knowledge_context,
        bloom_info=bloom_info,
    )


def _extract_reply(full_text: str) -> str:
    """Strip out the json_kg block and return only the conversational part."""
    clean = re.sub(r"```json_kg.*?```", "", full_text, flags=re.DOTALL)
    clean = re.sub(r"```.*?```", "", clean, flags=re.DOTALL)
    return clean.strip()


# ---------------------------------------------------------------------------
# Semantic RAG
# ---------------------------------------------------------------------------

async def _embed(text: str) -> list[float]:
    """Generate a 1536-dim embedding using OpenAI text-embedding-3-small."""
    response = await openai_client.embeddings.create(
        input=text,
        model="text-embedding-3-small",
    )
    return response.data[0].embedding


async def store_message(role: str, content: str) -> None:
    """Embed and store a message in Supabase conversation_history."""
    try:
        embedding = await _embed(content)
        get_supabase().table("conversation_history").insert({
            "role": role,
            "content": content,
            "embedding": json.dumps(embedding),
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }).execute()
        logger.info("Stored %s message in conversation_history.", role)
    except Exception as e:
        logger.warning("Failed to store message: %s", e)


async def get_relevant_context(user_message: str) -> list[dict[str, str]]:
    """
    Return the most relevant past messages for the current user message.

    - Embeds the user message.
    - Fetches the 8 most semantically similar past messages via pgvector RPC.
    - Always includes the last 3 messages by recency for immediate context.
    - Merges, deduplicates, and sorts chronologically.
    """
    sb = get_supabase()

    try:
        embedding = await _embed(user_message)
        embedding_str = json.dumps(embedding)

        # Semantic similarity via RPC
        semantic_res = sb.rpc(
            "match_conversation_history",
            {"query_embedding": embedding_str, "match_count": 8},
        ).execute()
        semantic_rows = semantic_res.data or []

        # Last 3 messages by recency (chronological order)
        recent_res = (
            sb.table("conversation_history")
            .select("role,content,created_at")
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        recent_rows = list(reversed(recent_res.data or []))

        # Merge: prioritise recent, backfill with semantic hits
        seen = {r["content"] for r in recent_rows}
        combined = list(recent_rows)
        for row in semantic_rows:
            if row["content"] not in seen:
                combined.append(row)
                seen.add(row["content"])

        combined.sort(key=lambda x: x.get("created_at", ""))
        logger.info("RAG context: %d messages retrieved.", len(combined))
        return [{"role": r["role"], "content": r["content"]} for r in combined]

    except Exception as e:
        logger.warning("Failed to retrieve RAG context: %s", e)
        return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def get_reply(user_message: str) -> tuple[str, dict[str, Any] | None]:
    """
    Build context via semantic RAG, call Claude, store the exchange.
    Returns (reply_text, kg_update_dict | None).
    """
    knowledge_context = get_selective_context(user_message, max_tokens=500)
    bloom_info = "Unknown -- treat as beginner, introduce gently."
    system_prompt = _build_system_prompt(knowledge_context, bloom_info)

    history = await get_relevant_context(user_message)

    messages: list[dict[str, str]] = list(history)
    messages.append({"role": "user", "content": user_message})

    logger.info("Calling Claude with %d context messages.", len(messages))

    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
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

    # Store this exchange for future retrieval
    await store_message("user", user_message)
    await store_message("assistant", reply)

    return reply, kg_update
