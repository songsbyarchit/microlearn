"""
worker.py — Cron worker that checks Redis for due replies and sends them.
Run every 60 seconds via Railway cron.
"""
import asyncio
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

from delay_queue import pop_due_replies
from voice import text_to_speech

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
            data={"From": from_number, "To": to, "Body": text},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.error("Twilio text send failed %d: %s", resp.status_code, resp.text)
        else:
            logger.info("Sent text reply to %s", to)


async def send_voice(to: str, text: str, twilio_sid: str, twilio_token: str, from_number: str) -> None:
    """
    Generate TTS audio and send as a WhatsApp voice note.

    WhatsApp voice notes sent via Twilio require a publicly accessible MP3/OGG URL.
    In V1 we generate the audio but need a storage layer (S3, Cloudflare R2, etc.)
    to host it. Until that's wired up, we fall back to text.

    To enable full voice:
    1. Upload `audio_bytes` to your storage bucket.
    2. Pass the public URL as `MediaUrl` in the Twilio request below.
    """
    try:
        audio_bytes = await text_to_speech(text)
        # TODO: upload audio_bytes to S3/R2 and get public_url
        # For now, log and fall back to text.
        logger.warning(
            "Voice storage not configured (%d bytes generated), falling back to text.", len(audio_bytes)
        )
        await send_text(to, text, twilio_sid, twilio_token, from_number)
    except Exception as e:
        logger.error("TTS failed, falling back to text: %s", e)
        await send_text(to, text, twilio_sid, twilio_token, from_number)


async def run_worker() -> None:
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
