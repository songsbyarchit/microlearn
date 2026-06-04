"""
report.py — Generate a dark-themed HTML report, screenshot via Playwright, upload to R2.
"""
import asyncio
import logging
import os
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import anthropic
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
        context = await browser.new_context(
            viewport={"width": 1080, "height": 1920},
            device_scale_factor=1,
        )
        page = await context.new_page()
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


# ---------------------------------------------------------------------------
# Detailed PDF — additional fetches
# ---------------------------------------------------------------------------

def _fetch_nodes_with_content(days: int) -> list[dict]:
    since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    try:
        resp = httpx.get(
            sb_url("/rest/v1/knowledge_nodes"),
            headers=sb_headers(),
            params={
                "select": "domain,topic,bloom_score,content,updated_at",
                "updated_at": f"gte.{since}",
                "order": "bloom_score.desc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.warning("Failed to fetch nodes with content: %s", e)
        return []


def _group_by_day(transcripts: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in transcripts:
        ts = t.get("created_at", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            day = dt.strftime("%Y-%m-%d")
        except Exception:
            day = "unknown"
        groups[day].append(t)
    return dict(sorted(groups.items()))


def _filler_trends(transcripts: list[dict]) -> tuple[list[str], dict[str, dict[str, int]]]:
    """Returns (sorted_days, {day: {filler: count}})."""
    groups = _group_by_day(transcripts)
    trends: dict[str, dict[str, int]] = {}
    for day, ts in groups.items():
        combined = " ".join(t.get("content", "") for t in ts if t.get("content")).lower()
        trends[day] = {f: combined.count(f) for f in FILLER_WORDS}
    return sorted(trends.keys()), trends


async def _get_recommendations(nodes: list[dict]) -> str:
    """Call Claude for 3 topic recommendations based on low bloom scores."""
    if not nodes:
        return "No topics available for recommendations yet."

    low_bloom = sorted(nodes, key=lambda n: n.get("bloom_score") or 1)[:10]
    topic_list = "\n".join(
        f"- {n['topic']} ({n['domain']}, bloom {n.get('bloom_score', 1)}, "
        f"last seen {(n.get('updated_at') or '')[:10]})"
        for n in low_bloom
    )

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = (
        "You are a learning coach reviewing a student's knowledge graph. "
        "Here are their topics with low bloom scores and when they were last seen:\n\n"
        f"{topic_list}\n\n"
        "Suggest exactly 3 specific topics to revisit next week, with a one-sentence "
        "reason for each. Format as:\n"
        "1. **Topic Name** — reason why\n"
        "2. **Topic Name** — reason why\n"
        "3. **Topic Name** — reason why\n\n"
        "Be concise and motivating. Write in British English."
    )
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("Claude recommendations failed: %s", e)
        return "Unable to generate recommendations at this time."


# ---------------------------------------------------------------------------
# Detailed HTML builder
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Minimal HTML escaping."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_detailed_html(
    transcripts: list[dict],
    nodes: list[dict],
    days: int,
    recommendations: str,
) -> str:
    now = datetime.now(tz=timezone.utc)
    since_dt = now - timedelta(days=days)
    date_range = f"{since_dt.strftime('%d %b')} – {now.strftime('%d %b %Y')}"
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")

    total_msgs  = len(transcripts)
    voice_count = sum(1 for t in transcripts if t.get("is_voice_note"))
    text_count  = total_msgs - voice_count
    total_words = sum(t.get("word_count") or 0 for t in transcripts)
    topic_count = len(nodes)

    # ── Section 2: Transcripts by day ──────────────────────────────────────
    grouped = _group_by_day(transcripts)
    transcripts_html_parts = []
    for day, ts in grouped.items():
        try:
            day_label = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %d %B %Y")
        except Exception:
            day_label = day
        rows = []
        for t in ts:
            ts_str = t.get("created_at", "")
            try:
                time_label = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).strftime("%H:%M")
            except Exception:
                time_label = "—"
            kind = "voice" if t.get("is_voice_note") else "text"
            content = _esc(t.get("content", ""))
            rows.append(
                f'<div class="tx-row">'
                f'<div class="tx-meta">{time_label} <span class="tx-kind">{kind}</span></div>'
                f'<div class="tx-content">{content}</div>'
                f'</div>'
            )
        transcripts_html_parts.append(
            f'<div class="day-block">'
            f'<div class="day-heading">{_esc(day_label)}</div>'
            + "".join(rows) +
            f'</div>'
        )
    transcripts_html = "".join(transcripts_html_parts) or '<p class="empty">No transcripts in this period.</p>'

    # ── Section 3: Bloom progression table ─────────────────────────────────
    bloom_rows = "".join(
        f'<tr>'
        f'<td>{_esc(n["topic"].title())}</td>'
        f'<td>{_esc(n.get("domain", "—"))}</td>'
        f'<td class="score">{n.get("bloom_score") or 1}</td>'
        f'<td>{(n.get("updated_at") or "")[:10]}</td>'
        f'</tr>'
        for n in nodes
    ) or '<tr><td colspan="4" class="empty">No topics updated in this period.</td></tr>'

    # ── Section 4: Filler trends by day ────────────────────────────────────
    sorted_days, trends = _filler_trends(transcripts)
    active_fillers = [f for f in FILLER_WORDS if any(trends.get(d, {}).get(f, 0) > 0 for d in sorted_days)]
    if active_fillers and sorted_days:
        filler_header = "<tr><th>Day</th>" + "".join(f"<th>{_esc(f)}</th>" for f in active_fillers) + "</tr>"
        filler_rows = "".join(
            "<tr><td>" + d + "</td>" +
            "".join(
                f'<td class="{"hi" if trends[d].get(f, 0) > 2 else ""}">{trends[d].get(f, 0) or "·"}</td>'
                for f in active_fillers
            ) + "</tr>"
            for d in sorted_days
        )
        filler_html = f'<table class="filler-table"><thead>{filler_header}</thead><tbody>{filler_rows}</tbody></table>'
    else:
        filler_html = '<p class="empty">No filler words detected — clean speech throughout!</p>'

    # ── Section 5: Topic depth analysis ────────────────────────────────────
    depth_parts = []
    for n in nodes:
        content = (n.get("content") or "").strip()
        summary = _esc(content[:400] + ("…" if len(content) > 400 else "")) if content else "No summary available."
        depth_parts.append(
            f'<div class="depth-block">'
            f'<div class="depth-title">{_esc(n["topic"].title())}'
            f'<span class="depth-meta">{_esc(n.get("domain", ""))} · bloom {n.get("bloom_score") or 1}</span>'
            f'</div>'
            f'<p class="depth-text">{summary}</p>'
            f'</div>'
        )
    depth_html = "".join(depth_parts) or '<p class="empty">No topic content available.</p>'

    # ── Section 6: Recommendations (pre-rendered text → HTML) ──────────────
    rec_lines = []
    for line in recommendations.splitlines():
        line = line.strip()
        if not line:
            continue
        # Bold **text** → <strong>
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _esc(line))
        rec_lines.append(f'<p class="rec-line">{line}</p>')
    rec_html = "".join(rec_lines) or '<p class="empty">No recommendations available.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1200">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: #1a1a2e;
  color: #e2e8f0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
  padding: 80px 100px 100px;
  min-width: 1200px;
  max-width: 1200px;
  line-height: 1.6;
}}

/* Cover */
.cover {{ margin-bottom: 72px; padding-bottom: 48px; border-bottom: 2px solid #252a4a; }}
h1 {{ font-size: 56px; font-weight: 900; color: #4fc3f7; letter-spacing: -2px; line-height: 1; }}
.cover-sub {{ font-size: 22px; color: #6b7a99; margin-top: 12px; margin-bottom: 40px; }}
.cover-stats {{ display: flex; gap: 20px; }}
.cstat {{ background: #1e2240; border-left: 4px solid #4fc3f7; padding: 22px 28px; border-radius: 10px; flex: 1; }}
.cstat .n {{ font-size: 44px; font-weight: 800; color: #4fc3f7; line-height: 1; }}
.cstat .l {{ font-size: 13px; color: #6b7a99; margin-top: 6px; }}

/* Section heading */
.section {{ margin-bottom: 64px; }}
.section-label {{
  font-size: 11px; font-weight: 700; letter-spacing: 0.18em;
  text-transform: uppercase; color: #4fc3f7;
  border-left: 4px solid #4fc3f7; padding-left: 14px;
  margin-bottom: 24px;
}}

/* Transcripts */
.day-block {{ margin-bottom: 36px; }}
.day-heading {{ font-size: 15px; font-weight: 700; color: #94a3b8; margin-bottom: 12px; letter-spacing: 0.04em; text-transform: uppercase; }}
.tx-row {{ display: flex; gap: 16px; padding: 10px 0; border-bottom: 1px solid #1e2240; align-items: flex-start; }}
.tx-row:last-child {{ border-bottom: none; }}
.tx-meta {{ font-size: 12px; color: #475569; width: 72px; flex-shrink: 0; padding-top: 2px; }}
.tx-kind {{ display: inline-block; background: #252a4a; border-radius: 4px; padding: 1px 5px; font-size: 10px; color: #64748b; }}
.tx-content {{ font-size: 14px; color: #cbd5e1; flex: 1; }}

/* Bloom table */
.bloom-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.bloom-table th {{ text-align: left; padding: 10px 14px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #475569; border-bottom: 2px solid #252a4a; }}
.bloom-table td {{ padding: 12px 14px; border-bottom: 1px solid #1e2240; color: #cbd5e1; }}
.bloom-table tr:last-child td {{ border-bottom: none; }}
.bloom-table td.score {{ font-size: 20px; font-weight: 800; color: #4fc3f7; }}

/* Filler table */
.filler-table {{ border-collapse: collapse; font-size: 13px; }}
.filler-table th {{ padding: 8px 16px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #475569; border-bottom: 2px solid #252a4a; text-align: center; }}
.filler-table th:first-child {{ text-align: left; }}
.filler-table td {{ padding: 8px 16px; border-bottom: 1px solid #1e2240; color: #94a3b8; text-align: center; }}
.filler-table td:first-child {{ text-align: left; color: #64748b; }}
.filler-table td.hi {{ color: #f97316; font-weight: 700; }}

/* Topic depth */
.depth-block {{ margin-bottom: 28px; padding: 20px 24px; background: #1e2240; border-radius: 10px; border-left: 3px solid #4fc3f7; }}
.depth-title {{ font-size: 17px; font-weight: 700; color: #f8fafc; margin-bottom: 8px; display: flex; align-items: center; gap: 12px; }}
.depth-meta {{ font-size: 12px; color: #475569; font-weight: 400; }}
.depth-text {{ font-size: 13px; color: #94a3b8; line-height: 1.65; }}

/* Recommendations */
.rec-line {{ font-size: 15px; color: #cbd5e1; margin-bottom: 14px; line-height: 1.6; }}
.rec-line strong {{ color: #4fc3f7; }}

.empty {{ font-size: 15px; color: #3d4565; font-style: italic; }}

.footer {{
  margin-top: 72px; font-size: 12px; color: #3d4565;
  border-top: 1px solid #252a4a; padding-top: 20px;
}}
</style>
</head>
<body>

<!-- ── Cover ── -->
<div class="cover">
  <h1>MicroLearn</h1>
  <div class="cover-sub">7-Day Deep Dive &nbsp;·&nbsp; {_esc(date_range)}</div>
  <div class="cover-stats">
    <div class="cstat"><div class="n">{total_msgs}</div><div class="l">total messages</div></div>
    <div class="cstat"><div class="n">{voice_count}</div><div class="l">voice notes</div></div>
    <div class="cstat"><div class="n">{total_words:,}</div><div class="l">words spoken</div></div>
    <div class="cstat"><div class="n">{topic_count}</div><div class="l">topics updated</div></div>
  </div>
</div>

<!-- ── Section 1: Transcripts ── -->
<div class="section">
  <div class="section-label">Full Transcripts</div>
  {transcripts_html}
</div>

<!-- ── Section 2: Bloom Progression ── -->
<div class="section">
  <div class="section-label">Bloom Score by Topic</div>
  <table class="bloom-table">
    <thead><tr><th>Topic</th><th>Domain</th><th>Bloom</th><th>Last Updated</th></tr></thead>
    <tbody>{bloom_rows}</tbody>
  </table>
</div>

<!-- ── Section 3: Filler Trends ── -->
<div class="section">
  <div class="section-label">Filler Word Trends</div>
  {filler_html}
</div>

<!-- ── Section 4: Topic Depth ── -->
<div class="section">
  <div class="section-label">Topic Depth Analysis</div>
  {depth_html}
</div>

<!-- ── Section 5: Recommendations ── -->
<div class="section">
  <div class="section-label">Recommendations</div>
  {rec_html}
</div>

<div class="footer">Generated by MicroLearn &nbsp;·&nbsp; {_esc(timestamp)}</div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Detailed PDF entry point
# ---------------------------------------------------------------------------

async def generate_detailed_pdf(days: int = 7) -> str:
    """
    Build a multi-section deep-dive HTML report, screenshot via Playwright
    at device_scale_factor=2, upload to R2, return the public URL.
    Returns "" if no data.
    """
    transcripts = _fetch_transcripts(days)
    nodes       = _fetch_nodes_with_content(days)

    if not transcripts and not nodes:
        return ""

    recommendations = await _get_recommendations(nodes)
    html = _build_detailed_html(transcripts, nodes, days, recommendations)

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
            )
            await browser.close()
    except Exception as e:
        logger.error("Playwright PDF generation failed: %s", e)
        return ""

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    filename = f"report-detailed-{date_str}-{uuid.uuid4()}.pdf"

    try:
        url = await asyncio.to_thread(_r2_upload_sync, pdf_bytes, filename, "application/pdf")
    except Exception as e:
        logger.error("R2 upload failed for detailed PDF: %s", e)
        return ""

    logger.info("Detailed PDF generated: %s", url)
    return url
