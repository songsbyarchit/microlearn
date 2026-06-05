"""
settings_manager.py — Per-user settings stored in Upstash Redis.
Key: microlearn:settings:{number}
"""
import json
import logging
import os

from upstash_redis import Redis

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict = {
    "speaking_rate": 2.0,
    "voice_id": "lUTamkMw7gOzZbFIwmq4",
    "max_words": 120,
    "bloom_target": 3,
    "recap_enabled": True,
}


def _redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _key(number: str) -> str:
    return f"microlearn:settings:{number}"


def get_settings(number: str) -> dict:
    """Return settings for a user, filling missing keys with defaults."""
    try:
        raw = _redis().get(_key(number))
        if raw:
            data = json.loads(raw)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
    except Exception as e:
        logger.warning("Failed to fetch settings for %s: %s", number, e)
    return dict(DEFAULT_SETTINGS)


def save_settings(number: str, settings: dict) -> None:
    """Persist user settings to Redis."""
    try:
        _redis().set(_key(number), json.dumps(settings))
    except Exception as e:
        logger.warning("Failed to save settings for %s: %s", number, e)
