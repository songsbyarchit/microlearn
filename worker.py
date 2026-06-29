"""
worker.py — Cron worker that checks Redis for due replies and sends them.
Also sends a daily PDF report at 21:00 UK time.
Run every 60 seconds via Railway cron.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx
import pytz
from dotenv import load_dotenv
from upstash_redis import Redis

load_dotenv()

from delay_queue import pop_due_replies
from supabase_client import sb_headers, sb_url
from voice import clean_for_text, generate_and_upload_audio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def send_text(to: str, text: str, twilio_sid: str, twilio_token: str, from_number: str) -> None:
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            messages_url,
            data={"From": from_number, "To": to, "Body": clean_for_text(text)},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.error("Twilio text send failed %d: %s", resp.status_code, resp.text)
        else:
            logger.info("Sent text reply to %s", to)


async def send_voice(to: str, text: str, twilio_sid: str, twilio_token: str, from_number: str) -> None:
    """Generate TTS, upload to R2, send public URL via Twilio MediaUrl."""
    try:
        media_url = await generate_and_upload_audio(text, sender=to)
        logger.info("Sending voice note via R2 URL: %s", media_url)

        messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                messages_url,
                data={"From": from_number, "To": to, "MediaUrl": media_url},
                auth=(twilio_sid, twilio_token),
                timeout=15,
            )
            if resp.status_code >= 400:
                logger.error("Twilio voice send failed %d: %s", resp.status_code, resp.text)
            else:
                logger.info("Sent voice reply to %s", to)
    except Exception as e:
        logger.error("Voice send failed, falling back to text: %s", e)
        await send_text(to, text, twilio_sid, twilio_token, from_number)


async def check_and_send_daily_report() -> None:
    """Send a daily PDF report at 21:00 UK time. No-op at all other times."""
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(tz=uk_tz)

    if now_uk.hour != 21:
        return

    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    today = now_uk.strftime("%Y-%m-%d")
    last_date = redis.get("microlearn:last_report_date")
    if last_date and last_date.strip('"') == today:
        logger.info("Daily report already sent for %s.", today)
        return

    # Check if any transcripts exist today
    today_start = now_uk.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    try:
        resp = httpx.get(
            sb_url("/rest/v1/transcripts"),
            headers=sb_headers(),
            params={"select": "id", "created_at": f"gte.{today_start}", "limit": "1"},
            timeout=10,
        )
        rows = resp.json() or []
    except Exception as e:
        logger.error("Failed to check today's transcripts: %s", e)
        return

    if not rows:
        logger.info("No transcripts today, skipping daily report.")
        return

    from report import generate_report_image
    url, stats = await generate_report_image(1)

    if not url:
        logger.info("Report generation returned empty, skipping send.")
        return

    my_number = os.environ.get("MY_WHATSAPP_NUMBER", "")
    if not my_number:
        logger.warning("MY_WHATSAPP_NUMBER not set, cannot send daily report.")
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    async with httpx.AsyncClient() as client:
        await client.post(
            messages_url,
            data={"From": from_number, "To": my_number, "MediaUrl": url, "Body": "Your MicroLearn daily recap 📚"},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        stats_text = (
            f"Today: {stats['total_messages']} messages, "
            f"{stats['total_words']:,} words spoken, "
            f"{stats['topic_count']} topics updated."
        )
        await client.post(
            messages_url,
            data={"From": from_number, "To": my_number, "Body": stats_text},
            auth=(twilio_sid, twilio_token),
            timeout=10,
        )

    redis.set("microlearn:last_report_date", json.dumps(today))
    logger.info("Daily report sent for %s.", today)


async def check_and_send_morning_recall() -> None:
    """Send 3 morning recall questions at 08:00 UK time. No-op at all other times."""
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(tz=uk_tz)

    if now_uk.hour != 8:
        return

    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    today = now_uk.strftime("%Y-%m-%d")
    last_date = redis.get("microlearn:last_recall_date")
    if last_date and last_date.strip('"') == today:
        logger.info("Morning recall already sent for %s.", today)
        return

    my_number = os.environ.get("MY_WHATSAPP_NUMBER", "")
    if not my_number:
        logger.warning("MY_WHATSAPP_NUMBER not set, cannot send morning recall.")
        return

    now_utc = datetime.now(tz=timezone.utc)

    def _fetch_due_nodes(limit: int = 5) -> list[dict]:
        """Fetch nodes whose next_review_at is due, ordered by bloom_score asc."""
        try:
            resp = httpx.get(
                sb_url("/rest/v1/knowledge_nodes"),
                headers=sb_headers(),
                params=[
                    ("select", "domain,topic,bloom_score,content,next_review_at"),
                    ("next_review_at", f"lte.{now_utc.isoformat()}"),
                    ("order", "bloom_score.asc"),
                    ("limit", str(limit)),
                ],
                timeout=10,
            )
            return resp.json() or []
        except Exception as e:
            logger.warning("Failed to fetch due nodes: %s", e)
            return []

    # Fall back to recency-based fetch if next_review_at column doesn't exist yet
    # or no nodes are due
    nodes = _fetch_due_nodes(3)
    if not nodes:
        # Fallback: pick 3 oldest-updated nodes
        try:
            resp = httpx.get(
                sb_url("/rest/v1/knowledge_nodes"),
                headers=sb_headers(),
                params=[
                    ("select", "domain,topic,bloom_score,content"),
                    ("order", "updated_at.asc"),
                    ("limit", "3"),
                ],
                timeout=10,
            )
            nodes = resp.json() or []
        except Exception as e:
            logger.warning("Fallback node fetch failed: %s", e)
            nodes = []

    if not nodes:
        logger.info("No nodes found for morning recall windows.")
        return

    from quiz import generate_question

    questions = []
    for node in nodes:
        try:
            q = await generate_question(node["topic"], node["domain"], node.get("content", ""))
            q["topic"] = node["topic"]
            q["domain"] = node["domain"]
            questions.append(q)
        except Exception as e:
            logger.error("generate_question failed for %s: %s", node["topic"], e)

    if not questions:
        logger.info("No questions generated for morning recall.")
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    # Send all questions as separate WhatsApp messages
    async with httpx.AsyncClient() as client:
        for q in questions:
            opts = q["options"]
            body = (
                f"Morning recall \U0001f9e0\n\n"
                f"{q['question']}\n\n"
                f"1. {opts[0]}\n"
                f"2. {opts[1]}\n"
                f"3. {opts[2]}\n"
                f"4. {opts[3]}"
            )
            resp = await client.post(
                messages_url,
                data={"From": from_number, "To": my_number, "Body": body},
                auth=(twilio_sid, twilio_token),
                timeout=15,
            )
            if resp.status_code >= 400:
                logger.error("Failed to send morning recall message: %s", resp.text)

    # Store first question as active quiz state; remainder go in pending queue
    state = {
        "state": "awaiting_answer",
        "question": questions[0],
        "pending": questions[1:],
    }
    redis.set(f"microlearn:quiz:{my_number}", json.dumps(state), ex=43200)  # 12h TTL
    redis.set("microlearn:last_recall_date", json.dumps(today))
    logger.info("Morning recall sent: %d questions for %s.", len(questions), today)


async def check_and_send_afternoon_story() -> None:
    """
    At 15:00 UK time, send a ~2-minute narrative voice note about something
    the user has been learning — a story, not just a fact.
    """
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(tz=uk_tz)

    if now_uk.hour != 15:
        return

    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    today = now_uk.strftime("%Y-%m-%d")
    last_date = redis.get("microlearn:last_story_date")
    if last_date and last_date.strip('"') == today:
        logger.info("Afternoon story already sent for %s.", today)
        return

    my_number = os.environ.get("MY_WHATSAPP_NUMBER", "")
    if not my_number:
        logger.warning("MY_WHATSAPP_NUMBER not set, cannot send afternoon story.")
        return

    # Pick a topic with some depth — bloom >= 3 preferred
    try:
        resp = httpx.get(
            sb_url("/rest/v1/knowledge_nodes"),
            headers=sb_headers(),
            params=[
                ("select", "domain,topic,bloom_score,content"),
                ("bloom_score", "gte.3"),
                ("order", "updated_at.desc"),
                ("limit", "10"),
            ],
            timeout=10,
        )
        nodes = resp.json() or []
    except Exception as e:
        logger.warning("Afternoon story node fetch failed: %s", e)
        return

    if not nodes:
        logger.info("No nodes with bloom >= 3 for afternoon story, skipping.")
        return

    import random as _random
    node = _random.choice(nodes[:5])
    topic = node["topic"]
    domain = node.get("domain", "general")
    content_snippet = (node.get("content") or "")[:800]

    import anthropic as _anthropic
    _claude = _anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = f"""You are MicroLearn, a brilliant curious friend. The user has been learning about {topic} (domain: {domain}).

Here is what you know about their understanding so far:
{content_snippet}

Write a compelling 90-180 second spoken narrative voice note for them — not a lesson, but a *story*.
Pick one surprising angle, historical moment, counterintuitive consequence, or real-world application related to {topic}.
Tell it like you're sharing something that genuinely excites you.

Structure: hook them in the first sentence, build through a narrative, land on one idea that reframes something they thought they knew.
End with one open question that leaves a gap — something they'll want to think about or ask you about later.

Write in spoken British English. After each sentence write [pause]. Before the final question write [long pause].
No bullet points, no headers. Pure narrative. Around 220-260 words."""

    try:
        resp = await _claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        script = resp.content[0].text.strip()
        logger.info("Afternoon story generated for topic '%s' (%d chars)", topic, len(script))
    except Exception as e:
        logger.error("Afternoon story Claude call failed: %s", e)
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]

    await send_voice(my_number, script, twilio_sid, twilio_token, from_number)
    redis.set("microlearn:last_story_date", json.dumps(today))
    logger.info("Afternoon story sent for %s (topic: %s).", today, topic)


async def check_and_send_curiosity_hook() -> None:
    """
    Once per day, send a surprising fact/connection as a plain WhatsApp text.
    The send time is randomised to a minute within 11:00–17:00 UK, chosen fresh
    each day and cached in Redis under microlearn:hook_time:{date}.
    """
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(tz=uk_tz)
    today = now_uk.strftime("%Y-%m-%d")

    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )

    # Only fire once per day
    last_date = redis.get("microlearn:last_hook_date")
    if last_date and last_date.strip('"') == today:
        logger.info("Curiosity hook already sent for %s.", today)
        return

    # Determine (or generate) today's send time
    hook_time_key = f"microlearn:hook_time:{today}"
    raw_hook_time = redis.get(hook_time_key)
    if raw_hook_time:
        try:
            hook_hour, hook_minute = map(int, raw_hook_time.strip('"').split(":"))
        except Exception:
            hook_hour, hook_minute = None, None
    else:
        hook_hour = None

    if hook_hour is None:
        import random as _random
        hook_hour = _random.randint(11, 16)      # 11–16 inclusive → fires 11:xx–16:xx
        hook_minute = _random.randint(0, 59)
        redis.set(hook_time_key, f"{hook_hour}:{hook_minute}", ex=90000)  # 25-hour TTL
        logger.info("Curiosity hook scheduled for %s at %02d:%02d UK", today, hook_hour, hook_minute)

    if not (now_uk.hour == hook_hour and now_uk.minute == hook_minute):
        return  # Not time yet

    my_number = os.environ.get("MY_WHATSAPP_NUMBER", "")
    if not my_number:
        logger.warning("MY_WHATSAPP_NUMBER not set, cannot send curiosity hook.")
        return

    # Fetch topics from the knowledge graph
    try:
        resp = httpx.get(
            sb_url("/rest/v1/knowledge_nodes"),
            headers=sb_headers(),
            params=[
                ("select", "domain,topic,bloom_score"),
                ("order", "updated_at.desc"),
                ("limit", "20"),
            ],
            timeout=10,
        )
        nodes = resp.json() or []
    except Exception as e:
        logger.warning("Curiosity hook node fetch failed: %s", e)
        return

    if not nodes:
        logger.info("No topics for curiosity hook, skipping.")
        return

    topics_list = ", ".join(
        f"{n['topic']} ({n.get('domain', 'general')}, bloom {n.get('bloom_score') or 1})"
        for n in nodes
    )

    import anthropic as _anthropic
    _claude = _anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = (
        f"Look at these knowledge graph topics the user has been learning: {topics_list}. "
        "Generate one genuinely surprising, counterintuitive, or little-known fact that connects "
        "to something they've studied, or introduces a natural adjacent topic. "
        "Make it feel like something a brilliant friend just texted you — one or two sentences max, "
        "no preamble, no 'did you know', just the fact stated compellingly. "
        "End with a single open question that invites them to reply and go deeper."
    )

    try:
        resp = await _claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        hook_text = resp.content[0].text.strip()
        logger.info("Curiosity hook generated (%d chars)", len(hook_text))
    except Exception as e:
        logger.error("Curiosity hook Claude call failed: %s", e)
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            messages_url,
            data={"From": from_number, "To": my_number, "Body": hook_text},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        if r.status_code >= 400:
            logger.error("Failed to send curiosity hook: %s", r.text)
            return

    redis.set("microlearn:last_hook_date", json.dumps(today))
    logger.info("Curiosity hook sent for %s.", today)


async def check_and_send_topic_suggestion() -> None:
    """Send new topic suggestions every Sunday at 10:00 UK time."""
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(tz=uk_tz)

    if now_uk.weekday() != 6 or now_uk.hour != 10:
        return

    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    today = now_uk.strftime("%Y-%m-%d")
    last_date = redis.get("microlearn:last_suggestion_date")
    if last_date and last_date.strip('"') == today:
        logger.info("Topic suggestion already sent for %s.", today)
        return

    my_number = os.environ.get("MY_WHATSAPP_NUMBER", "")
    if not my_number:
        logger.warning("MY_WHATSAPP_NUMBER not set, cannot send topic suggestion.")
        return

    try:
        resp = httpx.get(
            sb_url("/rest/v1/knowledge_nodes"),
            headers=sb_headers(),
            params=[
                ("select", "domain,topic,bloom_score"),
                ("order", "updated_at.desc"),
            ],
            timeout=10,
        )
        nodes = resp.json() or []
    except Exception as e:
        logger.warning("Topic suggestion node fetch failed: %s", e)
        return

    if not nodes:
        logger.info("No topics for suggestion, skipping.")
        return

    topics_list = ", ".join(
        f"{n['topic']} ({n.get('domain', 'general')}, bloom {n.get('bloom_score') or 1})"
        for n in nodes
    )

    import anthropic as _anthropic
    _claude = _anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = (
        f"Here are the topics this person has been learning with their domains and bloom scores: {topics_list}. "
        "Identify two or three topics they haven't studied yet that would create genuinely interesting connections "
        "with what they already know. For each suggestion, give the topic name and one sentence explaining why it "
        "connects to something they've already covered. Format it as a casual WhatsApp message from a knowledgeable "
        "friend, max 60 words total."
    )

    try:
        resp = await _claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion_text = resp.content[0].text.strip()
        logger.info("Topic suggestion generated (%d chars)", len(suggestion_text))
    except Exception as e:
        logger.error("Topic suggestion Claude call failed: %s", e)
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            messages_url,
            data={"From": from_number, "To": my_number, "Body": suggestion_text},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        if r.status_code >= 400:
            logger.error("Failed to send topic suggestion: %s", r.text)
            return

    redis.set("microlearn:last_suggestion_date", json.dumps(today))
    logger.info("Topic suggestion sent for %s.", today)


async def run_worker() -> None:
    await check_and_send_morning_recall()
    await check_and_send_afternoon_story()
    await check_and_send_curiosity_hook()
    await check_and_send_topic_suggestion()
    await check_and_send_daily_report()

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]

    due = pop_due_replies()
    if not due:
        logger.info("No due replies.")
        return

    logger.info("Processing %d due replies.", len(due))
    tasks = []
    for payload in due:
        to = payload["to"]
        text = payload["text"]
        is_voice = payload.get("voice", False)

        if is_voice:
            tasks.append(send_voice(to, text, twilio_sid, twilio_token, from_number))
        else:
            tasks.append(send_text(to, text, twilio_sid, twilio_token, from_number))

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run_worker())
