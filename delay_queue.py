"""
delay_queue.py — Redis-backed delay queue for scheduled WhatsApp replies.
"""
import json
import logging
import math
import os
import random
import time
from datetime import datetime, timedelta
from typing import Any

import pytz
from upstash_redis import Redis

logger = logging.getLogger(__name__)

UK_TZ = pytz.timezone("Europe/London")
WAKE_HOUR_START = 7   # 07:00 UK
WAKE_HOUR_END = 23    # 23:00 UK (exclusive)

QUEUE_KEY = "microlearn:pending_replies"
HISTORY_KEY_PREFIX = "microlearn:history:"

# Log-normal delay parameters (minutes)
# median = 25 min → mu = ln(25)
# sigma chosen so P5≈2 min, P95≈120 min
_LN_MEDIAN = 25.0
_LN_MU = math.log(_LN_MEDIAN)
_LN_SIGMA = 0.9


def _get_redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _sample_delay_minutes() -> float:
    """Draw a delay from log-normal distribution, clamped to [2, 120] minutes."""
    delay = random.lognormvariate(_LN_MU, _LN_SIGMA)
    return max(2.0, min(120.0, delay))


def _next_waking_send_time(from_dt: datetime) -> datetime:
    """
    Given a naive UTC datetime, return a UK-local aware datetime that falls
    within waking hours (07:00–23:00). If outside, defer to 08:00 next morning.
    """
    uk_dt = from_dt.astimezone(UK_TZ)
    hour = uk_dt.hour
    if WAKE_HOUR_START <= hour < WAKE_HOUR_END:
        return uk_dt
    # Outside waking hours — defer to 08:00 next day
    next_morning = (uk_dt + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    logger.info(
        "Send time %s is outside waking hours, deferring to %s",
        uk_dt.strftime("%H:%M"),
        next_morning.strftime("%Y-%m-%d %H:%M"),
    )
    return next_morning


def schedule_reply(
    to_number: str,
    reply_text: str,
    send_as_voice: bool,
) -> float:
    """
    Schedule a reply for future delivery. Returns the scheduled Unix timestamp.
    """
    redis = _get_redis()

    delay_minutes = _sample_delay_minutes()
    raw_send_dt = datetime.now(tz=pytz.utc) + timedelta(minutes=delay_minutes)
    send_dt = _next_waking_send_time(raw_send_dt)
    send_ts = send_dt.timestamp()

    payload = {
        "to": to_number,
        "text": reply_text,
        "voice": send_as_voice,
        "scheduled_at": send_ts,
        "created_at": time.time(),
    }

    # Use a Redis sorted set: score = send timestamp
    redis.zadd(QUEUE_KEY, {json.dumps(payload): send_ts})
    logger.info(
        "Scheduled reply to %s at %s (delay %.1f min)",
        to_number,
        send_dt.strftime("%Y-%m-%d %H:%M %Z"),
        delay_minutes,
    )
    return send_ts


def pop_due_replies() -> list[dict[str, Any]]:
    """
    Return and remove all replies whose scheduled timestamp is <= now.
    """
    redis = _get_redis()
    now_ts = time.time()

    # ZRANGEBYSCORE with scores up to now
    raw_items = redis.zrangebyscore(QUEUE_KEY, "-inf", now_ts)
    if not raw_items:
        return []

    results = []
    for item in raw_items:
        try:
            payload = json.loads(item)
            results.append(payload)
        except json.JSONDecodeError:
            logger.warning("Corrupt queue item, skipping: %s", item)

    # Remove processed items
    redis.zremrangebyscore(QUEUE_KEY, "-inf", now_ts)
    logger.info("Popped %d due replies from queue", len(results))
    return results


def pop_next_reply(to_number: str) -> dict[str, Any] | None:
    """
    Immediately pop the next pending reply for a given number (used for 'reply now' command).
    """
    redis = _get_redis()

    all_items = redis.zrange(QUEUE_KEY, 0, -1, withscores=True)
    for item, score in all_items:
        try:
            payload = json.loads(item)
        except json.JSONDecodeError:
            continue
        if payload.get("to") == to_number:
            redis.zrem(QUEUE_KEY, item)
            logger.info("Immediately popped reply for %s", to_number)
            return payload
    return None


# ---- Conversation history helpers ----

def append_history(phone_number: str, role: str, content: str, max_entries: int = 20) -> None:
    """Append a message to the conversation history for a number."""
    redis = _get_redis()
    key = f"{HISTORY_KEY_PREFIX}{phone_number}"
    entry = json.dumps({"role": role, "content": content})
    redis.rpush(key, entry)
    # Trim to last max_entries
    redis.ltrim(key, -max_entries, -1)


def get_history(phone_number: str) -> list[dict[str, str]]:
    """Retrieve the conversation history for a number."""
    redis = _get_redis()
    key = f"{HISTORY_KEY_PREFIX}{phone_number}"
    raw = redis.lrange(key, 0, -1)
    history = []
    for item in raw:
        try:
            history.append(json.loads(item))
        except json.JSONDecodeError:
            pass
    return history
