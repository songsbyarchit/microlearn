"""
voice.py — Whisper transcription, ElevenLabs TTS (primary), OpenAI TTS (fallback).
"""
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ELEVENLABS_VOICE_ID = "IKne3meq5aSn9XLyUdCD"  # Charlie (British male)
ELEVENLABS_API_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

AUDIO_DIR = Path("/tmp/audio")


# ---------------------------------------------------------------------------
# Text post-processing
# ---------------------------------------------------------------------------

def post_process_for_tts(text: str) -> str:
    """Replace [pause] / [long pause] markers with SSML break tags for ElevenLabs."""
    text = text.replace("[long pause]", '<break time="0.9s"/>')
    text = text.replace("[pause]", '<break time="0.5s"/>')
    return text.strip()


def clean_for_text(text: str) -> str:
    """Strip [pause] / [long pause] markers before sending a plain-text WhatsApp message."""
    text = text.replace("[long pause]", "")
    text = text.replace("[pause]", "")
    text = re.sub(r"  +", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Audio file storage
# ---------------------------------------------------------------------------

def save_audio_file(audio_bytes: bytes) -> str:
    """
    Save MP3 bytes to /tmp/audio/{uuid}.mp3 and return the filename (uuid.mp3).
    The FastAPI /audio/{filename} endpoint serves files from this directory.
    """
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename
    path.write_bytes(audio_bytes)
    logger.info("Saved audio file: %s (%d bytes)", path, len(audio_bytes))
    return filename


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

async def transcribe_audio(audio_url: str, twilio_sid: str, twilio_token: str) -> str:
    """Download a Twilio media URL and transcribe it with Whisper."""
    logger.info("Downloading audio from Twilio: %s", audio_url)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            audio_url,
            auth=(twilio_sid, twilio_token),
            follow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        audio_bytes = resp.content

    suffix = ".ogg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        logger.info("Transcribing audio with Whisper (%d bytes)", len(audio_bytes))
        with open(tmp_path, "rb") as audio_file:
            result = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
        transcript = result.text.strip()
        logger.info("Transcript: %s", transcript)
        return transcript
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

async def _elevenlabs_tts(text: str, api_key: str) -> bytes:
    """Call ElevenLabs TTS and return raw MP3 bytes."""
    logger.info(f"ElevenLabs key starts with: {api_key[:8] if api_key else 'EMPTY'}")
    ssml_text = post_process_for_tts(text)
    payload = {
        "text": ssml_text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.8,
            "speaking_rate": 2,
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ELEVENLABS_API_URL,
            json=payload,
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content


async def _openai_tts(text: str) -> bytes:
    """OpenAI TTS fallback -- returns raw MP3 bytes."""
    response = await openai_client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=clean_for_text(text),
        response_format="mp3",
    )
    return response.content


async def text_to_speech(text: str) -> bytes:
    """
    Generate speech from text. Tries ElevenLabs first, falls back to OpenAI TTS.
    Returns raw MP3 bytes.
    """
    logger.info("Generating TTS (%d chars)", len(text))

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if api_key:
        try:
            audio = await _elevenlabs_tts(text, api_key)
            logger.info("ElevenLabs TTS ok (%d bytes)", len(audio))
            return audio
        except Exception as e:
            logger.warning("ElevenLabs TTS failed, falling back to OpenAI: %s", e)

    audio = await _openai_tts(text)
    logger.info("OpenAI TTS ok (%d bytes)", len(audio))
    return audio
