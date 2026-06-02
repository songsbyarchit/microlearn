"""
voice.py — Whisper transcription and OpenAI TTS generation
"""
import logging
import os
import tempfile

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


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

    # Write to a temp file so the OpenAI SDK can read it
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


async def text_to_speech(text: str) -> bytes:
    """Convert text to speech using OpenAI TTS and return raw MP3 bytes."""
    logger.info("Generating TTS for text (%d chars)", len(text))
    response = await openai_client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=text,
        response_format="mp3",
    )
    audio_bytes = response.content
    logger.info("TTS generated (%d bytes)", len(audio_bytes))
    return audio_bytes
