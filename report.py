"""
report.py — Generate a dark-themed HTML report, screenshot via Playwright, upload to R2.
"""
import asyncio
import logging
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
                "order": "bloom_score.desc",
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
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(transcripts: list[dict], nodes: list[dict], days: int) -> str:
    date_label  = datetime.now(tz=timezone.utc).strftime("%d %B %Y")
    total_msgs  = len(transcripts)
    voice_count = sum(1 for t in transcripts if t.get("is_voice_note"))
    text_count  = total_msgs - voice_count
    total_words = sum(t.get("word_count") or 0 for t in transcripts)
    texts       = [t.get("content", "") for t in transcripts if t.get("content")]
    top_words   = _top_words(texts)
    fillers     = _count_fillers(texts)
    top_nodes   = nodes[:5]
    timestamp   = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Bar chart rows
    bar_max = top_words[0][1] if top_words else 1
    bar_chart_html = "".join(
        f'<div class="bar-row">'
        f'<div class="bar-label">{w}</div>'
        f'<div class="bar-track">'
        f'<div class="bar-fill" style="width:{int(c / bar_max * 100)}%"></div>'
        f'</div>'
        f'<div class="bar-count">{c}</div>'
        f'</div>'
        for w, c in top_words
    ) or '<p class="empty">Not enough data</p>'

    # Topics
    topics_html = "".join(
        f'<div class="topic-row">'
        f'<span class="topic-name">· {n["topic"].title()}</span>'
        f'<span class="topic-meta">{n["domain"]} &nbsp;·&nbsp; bloom {n.get("bloom_score", 0)}</span>'
        f'</div>'
        for n in top_nodes
    ) or '<p class="empty">No topics yet</p>'

    # Filler words
    fillers_html = "".join(
        f'<div class="filler-row">'
        f'<span class="filler-word">{f}</span>'
        f'<span class="filler-count">×{c}</span>'
        f'</div>'
        for f, c in fillers.items()
    ) or '<p class="empty">None detected — clean speech!</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1080">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #1a1a2e;
    color: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    padding: 72px 80px 80px;
    min-width: 1080px;
    max-width: 1080px;
  }}

  h1 {{ font-size: 48px; font-weight: 800; color: #4fc3f7; letter-spacing: -1px; line-height: 1; }}
  .subtitle {{ font-size: 20px; color: #6b7a99; margin-top: 10px; margin-bottom: 48px; }}

  .section {{ margin-bottom: 52px; }}
  .section-label {{
    font-size: 11px; font-weight: 700; letter-spacing: 0.15em;
    text-transform: uppercase; color: #4fc3f7;
    border-left: 4px solid #4fc3f7; padding-left: 14px;
    margin-bottom: 20px;
  }}

  .stat-row {{ display: flex; gap: 20px; }}
  .stat-box {{
    background: #1e2240; border-left: 3px solid #4fc3f7;
    padding: 20px 26px; border-radius: 8px; flex: 1;
  }}
  .stat-box .n {{ font-size: 42px; font-weight: 800; color: #4fc3f7; line-height: 1; }}
  .stat-box .l {{ font-size: 14px; color: #6b7a99; margin-top: 6px; }}

  .words-spoken {{ font-size: 26px; color: #fff; margin-bottom: 24px; font-weight: 600; }}

  .bar-row {{ display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }}
  .bar-label {{ font-size: 15px; color: #cbd5e1; width: 150px; flex-shrink: 0; }}
  .bar-track {{ flex: 1; height: 20px; background: #252a4a; border-radius: 4px; overflow: hidden; }}
  .bar-fill {{ height: 100%; background: #4fc3f7; border-radius: 4px; }}
  .bar-count {{ font-size: 14px; color: #6b7a99; width: 40px; text-align: right; flex-shrink: 0; }}

  .topic-row {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 0; border-bottom: 1px solid #1e2240;
  }}
  .topic-row:last-child {{ border-bottom: none; }}
  .topic-name {{ font-size: 18px; color: #fff; }}
  .topic-meta {{ font-size: 14px; color: #6b7a99; }}

  .filler-row {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 0; border-bottom: 1px solid #1e2240;
  }}
  .filler-row:last-child {{ border-bottom: none; }}
  .filler-word {{ font-size: 17px; color: #fff; }}
  .filler-count {{ font-size: 17px; color: #4fc3f7; font-weight: 700; }}

  .empty {{ font-size: 16px; color: #3d4565; font-style: italic; }}

  .footer {{
    margin-top: 56px; font-size: 13px; color: #3d4565;
    border-top: 1px solid #252a4a; padding-top: 20px;
  }}
</style>
</head>
<body>

<h1>MicroLearn</h1>
<div class="subtitle">7 Day Report &nbsp;·&nbsp; {date_label}</div>

<div class="section">
  <div class="section-label">Messages</div>
  <div class="stat-row">
    <div class="stat-box"><div class="n">{total_msgs}</div><div class="l">total</div></div>
    <div class="stat-box"><div class="n">{voice_count}</div><div class="l">voice notes</div></div>
    <div class="stat-box"><div class="n">{text_count}</div><div class="l">text messages</div></div>
  </div>
</div>

<div class="section">
  <div class="section-label">Speech</div>
  <div class="words-spoken">{total_words:,} words spoken</div>
  {bar_chart_html}
</div>

<div class="section">
  <div class="section-label">Topics</div>
  {topics_html}
</div>

<div class="section">
  <div class="section-label">Filler Words</div>
  {fillers_html}
</div>

<div class="footer">Generated by MicroLearn &nbsp;·&nbsp; {timestamp}</div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Screenshot via Playwright
# ---------------------------------------------------------------------------

async def _screenshot(html: str) -> bytes:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_viewport_size({"width": 1080, "height": 1920})
        await page.set_content(html, wait_until="networkidle")
        png_bytes = await page.screenshot(full_page=True)
        await browser.close()
    return png_bytes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def generate_report_image(days: int = 7) -> tuple[str, dict]:
    """
    Generate a PNG report card for the last `days` days.
    Returns (public_r2_url, stats_dict). Returns ("", {}) if no data.
    """
    transcripts = _fetch_transcripts(days)
    nodes       = _fetch_nodes(days)

    if not transcripts and not nodes:
        return "", {}

    html = _build_html(transcripts, nodes, days)

    try:
        png_bytes = await _screenshot(html)
    except Exception as e:
        logger.error("Playwright screenshot failed: %s", e)
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
