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
        media_url = await generate_and_upload_audio(text)
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
