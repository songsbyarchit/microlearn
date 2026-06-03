"""
main.py — FastAPI app with Twilio WhatsApp webhook and knowledge viewer.
"""
import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from brain import get_reply
from delay_queue import append_history, get_history, pop_next_reply, schedule_reply
from knowledge_graph import INDEX_FILE, get_all_topics
from voice import AUDIO_DIR, clean_for_text, save_audio_file, text_to_speech, transcribe_audio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
MY_NUMBER = os.environ.get("MY_WHATSAPP_NUMBER", "")
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://microlearn-production.up.railway.app"
).rstrip("/")

IMMEDIATE_COMMANDS = {"reply", "r"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("MicroLearn bot starting up. Audio dir: %s", AUDIO_DIR)
    yield
    logger.info("MicroLearn bot shutting down.")


app = FastAPI(title="MicroLearn", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Audio file serving
# ---------------------------------------------------------------------------

@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    """Serve a generated TTS audio file from /tmp/audio/."""
    # Prevent path traversal
    safe_name = Path(filename).name
    audio_path = AUDIO_DIR / safe_name
    if not audio_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(audio_path, media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# Knowledge viewer
# ---------------------------------------------------------------------------

@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_viewer():
    """Simple HTML knowledge graph viewer."""
    topics = get_all_topics()
    index_md = INDEX_FILE.read_text(encoding="utf-8") if INDEX_FILE.exists() else "(empty)"

    total = len(topics)
    recent = topics[:5]

    domains: dict[str, list[dict]] = {}
    for t in topics:
        domains.setdefault(t["domain"], []).append(t)

    domain_rows = ""
    for domain, domain_topics in sorted(domains.items()):
        domain_rows += f"<h3>{domain.title()}</h3><ul>"
        for t in domain_topics:
            domain_rows += (
                f"<li><strong>{t['title']}</strong> "
                f"&nbsp; bloom: <code>{t['bloom_level']}</code>"
                f"&nbsp; <em>{t['summary'][:120]}</em>"
                f"&nbsp; <small>{t['last_updated']}</small></li>"
            )
        domain_rows += "</ul>"

    recent_rows = "".join(
        f"<li>{t['title']} ({t['domain']}) &mdash; bloom {t['bloom_level']} &mdash; {t['last_updated']}</li>"
        for t in recent
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MicroLearn Knowledge Graph</title>
  <style>
    body {{ font-family: monospace; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
    h1 {{ border-bottom: 2px solid #333; }}
    h2 {{ margin-top: 2em; }}
    code {{ background: #eee; padding: 2px 4px; }}
    pre {{ background: #f4f4f4; padding: 1em; overflow-x: auto; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>MicroLearn Knowledge Graph</h1>

  <h2>Stats</h2>
  <p>Total topics: <strong>{total}</strong></p>

  <h2>Most Recent</h2>
  <ul>{recent_rows}</ul>

  <h2>All Topics</h2>
  {domain_rows}

  <h2>Raw Index</h2>
  <pre>{index_md}</pre>
</body>
</html>"""
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

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

    if MY_NUMBER and sender != MY_NUMBER:
        logger.warning("Ignoring message from unknown sender: %s", sender)
        return PlainTextResponse("", status_code=200)

    if body.lower() in IMMEDIATE_COMMANDS:
        pending = pop_next_reply(sender)
        if pending:
            await _send_reply(pending)
        else:
            logger.info("No pending reply to send immediately.")
        return PlainTextResponse("", status_code=200)

    user_text = body

    if num_media > 0 and MediaUrl0:
        content_type = MediaContentType0.lower()
        if "audio" in content_type or "ogg" in content_type or "mpeg" in content_type:
            try:
                user_text = await transcribe_audio(MediaUrl0, TWILIO_SID, TWILIO_TOKEN)
                logger.info("Transcribed voice note: %r", user_text)
            except Exception as e:
                logger.error("Transcription failed: %s", e)
                user_text = body or "[voice note -- transcription failed]"
        else:
            logger.info("Non-audio media attachment, ignoring media.")

    if not user_text:
        logger.info("Empty message, ignoring.")
        return PlainTextResponse("", status_code=200)

    history = get_history(sender)
    try:
        reply_text, _kg = await get_reply(user_text, history)
    except Exception as e:
        logger.error("Brain call failed: %s", e)
        return PlainTextResponse("", status_code=200)

    append_history(sender, "user", user_text)
    append_history(sender, "assistant", reply_text)

    send_as_voice = random.random() < 0.70
    schedule_reply(to_number=sender, reply_text=reply_text, send_as_voice=send_as_voice)

    return PlainTextResponse("", status_code=200)


async def _send_reply(payload: dict) -> None:
    """Send a single reply payload (text or voice) via Twilio."""
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
                filename = save_audio_file(audio_bytes)
                media_url = f"{PUBLIC_BASE_URL}/audio/{filename}"
                logger.info("Sending voice note via URL: %s", media_url)
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
                return
            except Exception as e:
                logger.error("Voice send failed, falling back to text: %s", e)

        # Text fallback
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
