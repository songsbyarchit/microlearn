"""
brain.py — Claude API calls, semantic RAG context retrieval, system prompt construction.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import anthropic
from openai import AsyncOpenAI

from knowledge_graph import get_selective_context, parse_kg_json_from_response, update_knowledge_graph
from settings_manager import DEFAULT_SETTINGS, get_settings
from supabase_client import sb_headers, sb_url

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
- Connect to what they already know only when it genuinely helps
- Never reference past topics unless the user brings them up first
- If they switch topic, go with it immediately -- curiosity is the point

HOW YOU TEACH:
- Start with the simplest concrete case, never the general rule
- Ask one question instead of stating a fact when possible
- Expose the assumption underneath the assumption
- Leave one thing unanswered -- the gap is where thinking happens
- If they got something wrong earlier, gently surface it

MICRO-TEST STRUCTURE — follow this in every reply that isn't the very first message:

EARLY IN SESSION (first 2-3 exchanges): Weight heavily toward retrieval.
  - Open with TWO quick recall questions about specific things just said.
    Keep them casual and fast: "What was the name again?" / "Which country was that?"
  - Then one short teaching sentence.
  - End with one forward question.

AFTER THE FIRST FEW EXCHANGES: Shift toward building, but still close with two questions.
  1. RETRIEVAL HOOK (1 sentence): One casual question about something specific
     from two messages ago. Skip if they already answered it correctly.
  2. TEACH (1-2 sentences): One new idea only.
  3. CLOSE WITH TWO QUESTIONS: Always end with exactly two questions —
     first a recall question ("remind me, what was X?"),
     then a build-forward question ("so what do you think happens when Y?").
     Separate them with a [long pause].

EXPLAIN-BACK PROMPTS — trigger one of these every 7-10 exchanges, randomised:
  - Instead of your normal reply, ask them to summarise what's been covered.
    Pick one of these naturally, don't repeat the same phrasing twice in a row:
    "Before we go further, can you walk me through what we've covered so far?"
    "In your own words, what's the story so far?"
    "Recap the last few things we talked about — just the headlines."
  - Then WAIT for their response. Do not teach anything in the same message.
  - When they respond, assess accuracy silently, then:
    * Mostly correct → briefly confirm and build one step further.
    * Partial → fill the gap explicitly: "You got X right, but Y was actually..."
    * Mostly wrong → slow down and re-explain from the simplest case before moving on.
  - After assessing, close with your two standard questions as normal.
  - Then append [OFFER_CHOICE] on a new line at the very end of your reply
    (after json_kg). This signals the app to offer the three session options.

That's the whole reply. Natural spoken gaps between each part.
Never merge into a wall of text. Never ask more than two questions at the end.

WORD COUNT -- STRICT:
- Maximum {max_words} words per reply, always
- Mirror the length of their message loosely but never exceed {max_words}
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

{recap_section}VARIETY — rotate styles, never repeat the same one twice in a row:
- Pure question (no statement at all)
- One bold claim followed by a question
- An analogy first, then ask if it lands
- A surprising fact that reframes what they thought they knew
- A gentle challenge to something they said

END OF EVERY REPLY:
- End with either a question or a cliffhanger, never a summary
- Never say "let me know if you have questions"
- Never say "great question"

MINI SESSIONS:
- You are having a mini learning session, not a single isolated exchange.
- If the user signals they are leaving — says "bye", "leaving", "talk later",
  "I'll reply later", "going now", reacts with an emoji only, or sends a very
  short acknowledgement like "ok", "got it", "thanks", "cool" after a substantive
  exchange — append [END_SESSION] at the very end of your reply (after the json_kg block).
- Do NOT append [END_SESSION] mid-conversation when there is more to explore.

--- BACKGROUND KNOWLEDGE (silent reference only) ---
Use this only to calibrate depth and avoid repeating things they already know.
Do NOT mention, recap, or reference these topics unless the user brings them up.
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


def _build_system_prompt(
    knowledge_context: str,
    bloom_info: str,
    max_words: int = 75,
    recap_enabled: bool = True,
) -> str:
    recap_section = (
        "RECAP RULE:\n"
        "- Only recap if the last exchange was more than 3 hours ago AND the user\n"
        "  is clearly continuing the same topic they left on.\n"
        "- Never recap when the user opens with a new topic or question.\n"
        "- Never say things like 'we were just talking about' or 'getting back to'.\n"
        "  One natural sentence maximum, then move on.\n\n"
    ) if recap_enabled else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        knowledge_context=knowledge_context,
        bloom_info=bloom_info,
        max_words=max_words,
        recap_section=recap_section,
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


def clear_conversation_history() -> None:
    """Delete all rows from conversation_history to start a fresh session."""
    try:
        resp = httpx.delete(
            sb_url("/rest/v1/conversation_history"),
            headers=sb_headers(),
            params={"id": "neq.00000000-0000-0000-0000-000000000000"},  # match all rows
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Conversation history cleared.")
    except Exception as e:
        logger.error("Failed to clear conversation history: %s", e)


async def store_message(role: str, content: str) -> None:
    """Embed and store a message in Supabase conversation_history."""
    try:
        embedding = await _embed(content)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                sb_url("/rest/v1/conversation_history"),
                headers=sb_headers(),
                content=json.dumps({
                    "role": role,
                    "content": content,
                    "embedding": json.dumps(embedding),
                    "created_at": datetime.now(tz=timezone.utc).isoformat(),
                }),
                timeout=10,
            )
            resp.raise_for_status()
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
    try:
        embedding = await _embed(user_message)
        embedding_str = json.dumps(embedding)

        async with httpx.AsyncClient() as client:
            # Semantic similarity via RPC
            rpc_resp = await client.post(
                sb_url("/rest/v1/rpc/match_conversation_history"),
                headers=sb_headers(),
                content=json.dumps({"query_embedding": embedding_str, "match_count": 8}),
                timeout=10,
            )
            rpc_resp.raise_for_status()
            semantic_rows = rpc_resp.json() or []

            # Last 3 messages by recency
            recent_resp = await client.get(
                sb_url("/rest/v1/conversation_history"),
                headers=sb_headers(),
                params={"select": "role,content,created_at", "order": "created_at.desc", "limit": "3"},
                timeout=10,
            )
            recent_resp.raise_for_status()
            recent_rows = list(reversed(recent_resp.json() or []))

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

async def get_recall_question(node: dict, sender: str = "") -> str:
    """Generate a spoken recall question for a knowledge node."""
    prompt = (
        f"Generate one recall question testing understanding of '{node['topic']}' "
        f"(domain: {node['domain']}, bloom level: {node.get('bloom_score', 1)}). "
        f"Test genuine application, not just definition recall. "
        f"Write conversationally, as if asking a friend. "
        f"Add [pause] markers where natural for spoken audio. "
        f"Write just the question — nothing else."
    )
    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


async def maybe_generate_diagram(reply_text: str) -> str:
    """
    Ask Claude if a diagram would help, generate one via OpenAI if so.
    Returns a public R2 URL, or "" on any failure or when not needed.
    Non-blocking: all errors are caught and logged.
    """
    try:
        # Step 1: YES/NO check
        check = await claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=5,
            messages=[{"role": "user", "content": (
                f"Explanation:\n{reply_text}\n\n"
                "Would a diagram significantly aid understanding of this explanation? "
                "Reply only YES or NO."
            )}],
        )
        if not check.content[0].text.strip().upper().startswith("YES"):
            return ""

        # Step 2: describe the ideal diagram
        desc_resp = await claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            messages=[{"role": "user", "content": (
                f"Explanation:\n{reply_text}\n\n"
                "Describe the ideal educational diagram to accompany this explanation. "
                "Specify the diagram type, all elements, labels, layout, and visual style. "
                "Be precise — this description is used directly as an image generation prompt."
            )}],
        )
        diagram_desc = desc_resp.content[0].text.strip()
        logger.info("Diagram description: %s", diagram_desc[:120])

        # Step 3: generate image via OpenAI
        img_resp = await openai_client.images.generate(
            model="gpt-4o",
            prompt=diagram_desc,
            size="1024x1024",
            n=1,
        )
        img_data = img_resp.data[0]
        img_url = getattr(img_data, "url", None)

        # Step 4: download image bytes
        if img_url:
            async with httpx.AsyncClient() as client:
                dl = await client.get(img_url, timeout=30)
                dl.raise_for_status()
                img_bytes = dl.content
        else:
            import base64
            img_bytes = base64.b64decode(img_data.b64_json)

        # Step 5: upload to R2
        from voice import _r2_upload_sync
        filename = f"diagram-{uuid.uuid4()}.png"
        public_url = await asyncio.to_thread(_r2_upload_sync, img_bytes, filename, "image/png")
        logger.info("Diagram uploaded: %s", public_url)
        return public_url

    except Exception as e:
        logger.warning("Diagram generation failed (non-fatal): %s", e)
        return ""


async def get_reply(user_message: str, sender: str = "") -> tuple[str, dict[str, Any] | None, str]:
    """
    Build context via semantic RAG, call Claude, store the exchange.
    Returns (reply_text, kg_update_dict | None, diagram_url).
    """
    settings = get_settings(sender) if sender else dict(DEFAULT_SETTINGS)
    max_words = settings.get("max_words", 75)
    bloom_target = settings.get("bloom_target", 3)
    recap_enabled = settings.get("recap_enabled", True)

    knowledge_context = get_selective_context(user_message, max_tokens=500)
    bloom_info = (
        f"Target Bloom level: {bloom_target}/8 "
        f"(1=remember, 8=create). Push gently toward this level."
    )
    system_prompt = _build_system_prompt(knowledge_context, bloom_info, max_words, recap_enabled)

    history = await get_relevant_context(user_message)

    messages: list[dict[str, str]] = list(history)
    messages.append({"role": "user", "content": user_message})

    logger.info("Calling Claude with %d context messages.", len(messages))

    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_words * 6,  # generous headroom for json_kg block
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

    diagram_url = await maybe_generate_diagram(reply)

    return reply, kg_update, diagram_url
