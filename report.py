"""
report.py — Generate PDF learning report and upload to Cloudflare R2.
"""
import asyncio
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from supabase_client import sb_headers, sb_url
from voice import _r2_upload_sync

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "the", "and", "for", "that", "this", "with", "have", "from", "they",
    "will", "been", "what", "when", "your", "which", "about", "there",
    "their", "were", "into", "more", "also", "than", "then", "some",
    "but", "not", "are", "was", "its", "you", "can", "how", "why",
    "did", "had", "has", "one", "all", "out", "him", "her", "his",
    "she", "who", "our", "very", "just", "get", "got", "let", "any",
}
FILLER_WORDS = ["basically", "like", "sort of", "kind of", "you know", "literally"]


# ---------------------------------------------------------------------------
# Supabase fetches
# ---------------------------------------------------------------------------

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
                "order": "updated_at.desc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.warning("Failed to fetch knowledge nodes: %s", e)
        return []


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

def _build_html(transcripts: list[dict], nodes: list[dict], days: int) -> str:
    date_label = datetime.now(tz=timezone.utc).strftime("%d %B %Y")
    period_label = "today" if days == 1 else f"last {days} days"

    total_words = sum(t.get("word_count") or 0 for t in transcripts)
    voice_count = sum(1 for t in transcripts if t.get("is_voice_note"))
    texts = [t.get("content", "") for t in transcripts if t.get("content")]

    top_words = _top_words(texts)
    fillers = _count_fillers(texts)

    topics_html = "".join(
        f"<li><b>{n['topic'].title()}</b> ({n['domain']}) — bloom {n['bloom_score']}</li>"
        for n in nodes
    ) or "<li>No topics updated</li>"

    top_words_html = "".join(
        f"<li>{w} <span class='count'>×{c}</span></li>"
        for w, c in top_words
    ) or "<li>Not enough data yet</li>"

    fillers_html = "".join(
        f"<li>{f} <span class='count'>×{c}</span></li>"
        for f, c in fillers.items()
    ) or "<li>None detected — clean speech!</li>"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: Georgia, serif; max-width: 720px; margin: 40px auto; color: #1a1a2e; line-height: 1.6; }}
  h1 {{ color: #16213e; border-bottom: 3px solid #0f3460; padding-bottom: 8px; }}
  h2 {{ color: #0f3460; margin-top: 32px; font-size: 1.1em; text-transform: uppercase; letter-spacing: .05em; }}
  .stat-grid {{ display: flex; gap: 24px; margin: 20px 0; flex-wrap: wrap; }}
  .stat {{ background: #f0f4ff; border-left: 4px solid #0f3460; padding: 12px 18px; border-radius: 4px; flex: 1; min-width: 120px; }}
  .stat .n {{ font-size: 2em; font-weight: bold; color: #0f3460; }}
  .stat .l {{ font-size: .8em; color: #666; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 6px 0; }}
  .count {{ background: #e8eeff; border-radius: 12px; padding: 1px 8px; font-size: .85em; color: #0f3460; }}
  .footer {{ margin-top: 40px; font-size: .8em; color: #999; border-top: 1px solid #eee; padding-top: 12px; }}
</style>
</head>
<body>
<h1>MicroLearn Report</h1>
<p><b>{date_label}</b> — {period_label}</p>

<div class="stat-grid">
  <div class="stat"><div class="n">{len(transcripts)}</div><div class="l">messages</div></div>
  <div class="stat"><div class="n">{voice_count}</div><div class="l">voice notes</div></div>
  <div class="stat"><div class="n">{total_words:,}</div><div class="l">words spoken</div></div>
  <div class="stat"><div class="n">{len(nodes)}</div><div class="l">topics updated</div></div>
</div>

<h2>Topics Explored</h2>
<ul>{topics_html}</ul>

<h2>Top Words Used</h2>
<ul>{top_words_html}</ul>

<h2>Filler Words Detected</h2>
<ul>{fillers_html}</ul>

<div class="footer">Generated by MicroLearn · {datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def generate_report_pdf(days: int = 7) -> tuple[str, dict]:
    """
    Generate a PDF report for the last `days` days.
    Returns (public_r2_url, stats_dict). Returns ("", {}) if no data.
    """
    transcripts = _fetch_transcripts(days)
    nodes = _fetch_nodes(days)

    if not transcripts and not nodes:
        return "", {}

    html = _build_html(transcripts, nodes, days)

    try:
        from weasyprint import HTML as WP_HTML
        pdf_bytes = await asyncio.to_thread(lambda: WP_HTML(string=html).write_pdf())
    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        return "", {}

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    filename = f"daily-{date_str}-{uuid.uuid4()}.pdf"

    try:
        url = await asyncio.to_thread(_r2_upload_sync, pdf_bytes, filename)
    except Exception as e:
        logger.error("R2 upload failed: %s", e)
        return "", {}

    stats = {
        "total_messages": len(transcripts),
        "total_words": sum(t.get("word_count") or 0 for t in transcripts),
        "voice_count": sum(1 for t in transcripts if t.get("is_voice_note")),
        "topic_count": len(nodes),
    }
    logger.info("Report generated: %s (%d messages, %d words)", url, stats["total_messages"], stats["total_words"])
    return url, stats
