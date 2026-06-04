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


async def run_worker() -> None:
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
