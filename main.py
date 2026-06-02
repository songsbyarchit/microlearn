"""
main.py — FastAPI app with Twilio WhatsApp webhook.
"""
import logging
import os
import random
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import PlainTextResponse

from brain import get_reply
from delay_queue import append_history, get_history, pop_next_reply, schedule_reply
from voice import transcribe_audio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
MY_NUMBER = os.environ.get("MY_WHATSAPP_NUMBER", "")

IMMEDIATE_COMMANDS = {"reply", "r"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MicroLearn bot starting up.")
    yield
    logger.info("MicroLearn bot shutting down.")


app = FastAPI(title="MicroLearn", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    From: str = Form(default=""),
    Body: str = Form(default=""),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
    NumMedia: str = Form(default="0"),
):
    """Receive inbound WhatsApp messages from Twilio."""
    sender = From.strip()
    body = Body.strip()
    num_media = int(NumMedia or 0)

    logger.info("Inbound from %s | body=%r | media=%d", sender, body, num_media)

    # Only respond to my own number
    if MY_NUMBER and sender != MY_NUMBER:
        logger.warning("Ignoring message from unknown sender: %s", sender)
        return PlainTextResponse("", status_code=200)

    # Handle immediate-send commands
    if body.lower() in IMMEDIATE_COMMANDS:
        pending = pop_next_reply(sender)
        if pending:
            await _send_reply(pending)
        else:
            logger.info("No pending reply to send immediately.")
        return PlainTextResponse("", status_code=200)

    # Determine the actual text content
    user_text = body

    if num_media > 0 and MediaUrl0:
        content_type = MediaContentType0.lower()
        if "audio" in content_type or "ogg" in content_type or "mpeg" in content_type:
            try:
                user_text = await transcribe_audio(MediaUrl0, TWILIO_SID, TWILIO_TOKEN)
                logger.info("Transcribed voice note: %r", user_text)
            except Exception as e:
                logger.error("Transcription failed: %s", e)
                user_text = body or "[voice note — transcription failed]"
        else:
            logger.info("Non-audio media attachment, ignoring media.")

    if not user_text:
        logger.info("Empty message, ignoring.")
        return PlainTextResponse("", status_code=200)

    # Fetch history and generate reply
    history = get_history(sender)
    try:
        reply_text, _kg = await get_reply(user_text, history)
    except Exception as e:
        logger.error("Brain call failed: %s", e)
        return PlainTextResponse("", status_code=200)

    # Persist to history
    append_history(sender, "user", user_text)
    append_history(sender, "assistant", reply_text)

    # Decide voice vs text (70/30)
    send_as_voice = random.random() < 0.70

    # Schedule with delay
    schedule_reply(
        to_number=sender,
        reply_text=reply_text,
        send_as_voice=send_as_voice,
    )

    # Twilio expects a 200 with empty TwiML or plain body
    return PlainTextResponse("", status_code=200)


async def _send_reply(payload: dict) -> None:
    """Send a single reply payload (text or voice) via Twilio."""
    import httpx
    from voice import text_to_speech

    to = payload["to"]
    text = payload["text"]
    send_as_voice = payload.get("voice", False)

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]

    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    async with httpx.AsyncClient() as client:
        if send_as_voice:
            try:
                audio_bytes = await text_to_speech(text)
                # Upload audio to Twilio MMS / hosted media
                # Twilio WhatsApp voice notes require a publicly accessible URL.
                # We use Twilio's own media hosting: upload via helper endpoint.
                # For simplicity in V1, fall back to text if upload not configured.
                # TODO: integrate with a storage bucket (S3/R2) for audio hosting.
                logger.warning(
                    "Voice note storage not yet configured — falling back to text."
                )
                send_as_voice = False
            except Exception as e:
                logger.error("TTS generation failed: %s", e)
                send_as_voice = False

        if not send_as_voice:
            data = {
                "From": from_number,
                "To": to,
                "Body": text,
            }
            resp = await client.post(
                messages_url,
                data=data,
                auth=(twilio_sid, twilio_token),
                timeout=15,
            )
            if resp.status_code >= 400:
                logger.error("Twilio send failed %d: %s", resp.status_code, resp.text)
            else:
                logger.info("Sent text reply to %s", to)
