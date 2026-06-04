"""
report.py — Generate a dark-background PNG report card and upload to Cloudflare R2.
"""
import asyncio
import io
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from PIL import Image, ImageDraw, ImageFont

from supabase_client import sb_headers, sb_url
from voice import _r2_upload_sync

logger = logging.getLogger(__name__)

# ── Canvas & palette ──────────────────────────────────────────────────────────
W, H   = 720, 1080
BG     = (26, 26, 46)       # #1a1a2e
ACCENT = (79, 195, 247)     # #4fc3f7
WHITE  = (255, 255, 255)
DIM    = (120, 130, 160)
RULE   = (45, 50, 80)
BAR_BG = (40, 45, 75)
PAD    = 44

STOP_WORDS = {
    "the", "and", "for", "that", "this", "with", "have", "from", "they",
    "will", "been", "what", "when", "your", "which", "about", "there",
    "their", "were", "into", "more", "also", "than", "then", "some",
    "but", "not", "are", "was", "its", "you", "can", "how", "why",
    "did", "had", "has", "one", "all", "out", "him", "her", "his",
    "she", "who", "our", "very", "just", "get", "got", "let", "any",
}
FILLER_WORDS = ["basically", "like", "sort of", "kind of", "you know", "literally"]


# ── Fonts ─────────────────────────────────────────────────────────────────────

def _load_fonts() -> dict:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    reg  = next((p for p in candidates if os.path.exists(p) and "Bold" not in p), None)
    bold = next((p for p in candidates if os.path.exists(p) and "Bold" in p), None)
    try:
        if not reg and not bold:
            raise OSError("no system fonts found")
        return {
            "title":   ImageFont.truetype(bold or reg, 34),
            "section": ImageFont.truetype(bold or reg, 15),
            "body":    ImageFont.truetype(reg  or bold, 18),
            "small":   ImageFont.truetype(reg  or bold, 13),
        }
    except Exception:
        return {
            "title":   ImageFont.load_default(size=34),
            "section": ImageFont.load_default(size=15),
            "body":    ImageFont.load_default(size=18),
            "small":   ImageFont.load_default(size=13),
        }


# ── Supabase fetches ──────────────────────────────────────────────────────────

def _fetch_transcripts(days: int) -> list[dict]:
    since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    try:
        resp = httpx.get(
            sb_url("/rest/v1/transcripts"),
            headers=sb_headers(),
            params={
                "select": "content,created_at,word_count,is_voice_note",
                "created_at": f"gte.{since}",
                "order": "created_at.asc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.warning("Failed to fetch transcripts: %s", e)
        return []


def _fetch_nodes(days: int) -> list[dict]:
    since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    try:
        resp = httpx.get(
            sb_url("/rest/v1/knowledge_nodes"),
            headers=sb_headers(),
            params={
                "select": "domain,topic,bloom_score,updated_at",
                "updated_at": f"gte.{since}",
                "order": "bloom_score.desc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.warning("Failed to fetch knowledge nodes: %s", e)
        return []


# ── Analysis helpers ──────────────────────────────────────────────────────────

def _top_words(texts: list[str], n: int = 5) -> list[tuple[str, int]]:
    words = []
    for t in texts:
        for w in re.split(r"\W+", t.lower()):
            if len(w) > 3 and w not in STOP_WORDS:
                words.append(w)
    return Counter(words).most_common(n)


def _count_fillers(texts: list[str]) -> dict[str, int]:
    combined = " ".join(texts).lower()
    return {f: combined.count(f) for f in FILLER_WORDS if combined.count(f) > 0}


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _section_header(draw: ImageDraw.ImageDraw, fonts: dict, y: int, label: str) -> int:
    """Draw an accent-bar section heading. Returns new y."""
    draw.rectangle([PAD, y, PAD + 3, y + 20], fill=ACCENT)
    draw.text((PAD + 14, y + 2), label, font=fonts["section"], fill=ACCENT)
    return y + 32


def _draw_bar_chart(
    draw: ImageDraw.ImageDraw,
    fonts: dict,
    top_words: list[tuple[str, int]],
    x: int, y: int, width: int,
) -> int:
    """Draw horizontal bars for top words. Returns new y."""
    if not top_words:
        draw.text((x, y), "Not enough data", font=fonts["small"], fill=DIM)
        return y + 22

    max_count = top_words[0][1] or 1
    label_w = 140
    count_w = 36
    bar_w   = width - label_w - count_w - 12
    bar_h   = 18
    gap     = 9

    for word, count in top_words:
        draw.text((x, y + 2), word, font=fonts["small"], fill=WHITE)
        draw.rectangle([x + label_w, y, x + label_w + bar_w, y + bar_h], fill=BAR_BG)
        fill_px = max(4, int(bar_w * count / max_count))
        draw.rectangle([x + label_w, y, x + label_w + fill_px, y + bar_h], fill=ACCENT)
        draw.text((x + label_w + bar_w + 8, y + 2), str(count), font=fonts["small"], fill=DIM)
        y += bar_h + gap
    return y


# ── Image builder ─────────────────────────────────────────────────────────────

def _build_image(transcripts: list[dict], nodes: list[dict]) -> bytes:
    img   = Image.new("RGB", (W, H), BG)
    draw  = ImageDraw.Draw(img)
    fonts = _load_fonts()

    total_msgs  = len(transcripts)
    voice_count = sum(1 for t in transcripts if t.get("is_voice_note"))
    text_count  = total_msgs - voice_count
    total_words = sum(t.get("word_count") or 0 for t in transcripts)
    texts       = [t.get("content", "") for t in transcripts if t.get("content")]
    top_words   = _top_words(texts)
    fillers     = _count_fillers(texts)
    top_nodes   = nodes[:5]

    y = 44

    # ── Header ──────────────────────────────────────────────────────────────
    draw.text((PAD, y), "MicroLearn", font=fonts["title"], fill=ACCENT)
    y += 46
    date_str = datetime.now(tz=timezone.utc).strftime("%d %B %Y")
    draw.text((PAD, y), f"7 Day Report  ·  {date_str}", font=fonts["body"], fill=DIM)
    y += 36
    draw.rectangle([PAD, y, W - PAD, y + 1], fill=RULE)
    y += 22

    # ── MESSAGES ────────────────────────────────────────────────────────────
    y = _section_header(draw, fonts, y, "MESSAGES")
    draw.text(
        (PAD + 14, y),
        f"{total_msgs} total  ·  {voice_count} voice  ·  {text_count} text",
        font=fonts["body"], fill=WHITE,
    )
    y += 36
    draw.rectangle([PAD, y, W - PAD, y + 1], fill=RULE)
    y += 22

    # ── SPEECH ──────────────────────────────────────────────────────────────
    y = _section_header(draw, fonts, y, "SPEECH")
    draw.text((PAD + 14, y), f"{total_words:,} words spoken", font=fonts["body"], fill=WHITE)
    y += 34
    y = _draw_bar_chart(draw, fonts, top_words, PAD + 14, y, W - PAD * 2 - 14)
    y += 14
    draw.rectangle([PAD, y, W - PAD, y + 1], fill=RULE)
    y += 22

    # ── TOPICS ──────────────────────────────────────────────────────────────
    y = _section_header(draw, fonts, y, "TOPICS")
    if top_nodes:
        for node in top_nodes:
            topic  = node["topic"].title()
            domain = node["domain"]
            bloom  = node.get("bloom_score", 0)
            draw.text((PAD + 14, y), f"·  {topic}", font=fonts["body"], fill=WHITE)
            draw.text((PAD + 300, y), f"{domain}  bloom {bloom}", font=fonts["small"], fill=DIM)
            y += 30
    else:
        draw.text((PAD + 14, y), "No topics yet", font=fonts["body"], fill=DIM)
        y += 30
    y += 8
    draw.rectangle([PAD, y, W - PAD, y + 1], fill=RULE)
    y += 22

    # ── FILLER WORDS ────────────────────────────────────────────────────────
    y = _section_header(draw, fonts, y, "FILLER WORDS")
    if fillers:
        for filler, count in fillers.items():
            draw.text((PAD + 14, y), filler, font=fonts["body"], fill=WHITE)
            draw.text((PAD + 250, y), f"×{count}", font=fonts["body"], fill=ACCENT)
            y += 30
    else:
        draw.text((PAD + 14, y), "None detected — clean speech!", font=fonts["body"], fill=DIM)

    # ── Footer ──────────────────────────────────────────────────────────────
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    draw.rectangle([PAD, H - 52, W - PAD, H - 51], fill=RULE)
    draw.text((PAD, H - 38), f"Generated by MicroLearn  ·  {ts}", font=fonts["small"], fill=DIM)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_report_image(days: int = 7) -> tuple[str, dict]:
    """
    Generate a PNG report card for the last `days` days.
    Returns (public_r2_url, stats_dict). Returns ("", {}) if no data.
    """
    transcripts = _fetch_transcripts(days)
    nodes       = _fetch_nodes(days)

    if not transcripts and not nodes:
        return "", {}

    try:
        png_bytes = await asyncio.to_thread(_build_image, transcripts, nodes)
    except Exception as e:
        logger.error("Image generation failed: %s", e)
        return "", {}

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    filename  = f"report-{date_str}-{uuid.uuid4()}.png"

    try:
        url = await asyncio.to_thread(_r2_upload_sync, png_bytes, filename, "image/png")
    except Exception as e:
        logger.error("R2 upload failed: %s", e)
        return "", {}

    stats = {
        "total_messages": len(transcripts),
        "total_words":    sum(t.get("word_count") or 0 for t in transcripts),
        "voice_count":    sum(1 for t in transcripts if t.get("is_voice_note")),
        "topic_count":    len(nodes),
    }
    logger.info("Report image generated: %s (%d msgs, %d words)", url, stats["total_messages"], stats["total_words"])
    return url, stats
