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
            is_voice = t.get("is_voice_note")
            kind = "voice" if is_voice else "text"
            badge_class = "tx-badge" if is_voice else "tx-badge text"
            content = _esc(t.get("content", ""))
            rows.append(
                f'<div class="tx-row">'
                f'<span class="tx-time">{time_label}</span>'
                f'<span class="{badge_class}">{kind}</span>'
                f'<span class="tx-content">{content}</span>'
                f'</div>'
            )
        transcripts_html_parts.append(
            f'<div class="day-label">{_esc(day_label)}</div>'
            + "".join(rows)
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
    def _parse_node(content: str) -> dict:
        """Extract summary, vocab, edges from markdown node content."""
        summary = ""
        vocab: list[str] = []
        m = re.search(r"## Summary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
        if m:
            summary = m.group(1).strip()
        m2 = re.search(r"## Vocabulary\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
        if m2:
            vocab = [v.lstrip("- ").strip() for v in m2.group(1).strip().splitlines() if v.strip().lstrip("- ")]
            vocab = [v for v in vocab if v and v.lower() not in ("(none)", "none")]
        return {"summary": summary, "vocab": vocab}

    depth_parts = []
    for n in nodes:
        content = (n.get("content") or "").strip()
        parsed = _parse_node(content)
        summary = _esc(parsed["summary"]) if parsed["summary"] else "No summary available yet."
        vocab_chips = "".join(f'<span class="vocab-chip">{_esc(v)}</span>' for v in parsed["vocab"][:8])
        vocab_html = f'<div class="depth-vocab">{vocab_chips}</div>' if vocab_chips else ""
        depth_parts.append(
            f'<div class="depth-block">'
            f'<div class="depth-title">{_esc(n["topic"].title())}</div>'
            f'<div class="depth-tags">{_esc(n.get("domain", ""))} &nbsp;·&nbsp; bloom {n.get("bloom_score") or 1}/8</div>'
            f'<p class="depth-summary">{summary}</p>'
            f'{vocab_html}'
            f'</div>'
        )
    depth_html = "".join(depth_parts) or '<p class="empty">No topic content available.</p>'

    # ── Section 6: Recommendations ──────────────────────────────────────────
    rec_items = []
    num = 0
    for line in recommendations.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading "1. " / "- " numbering
        line = re.sub(r"^\d+\.\s*", "", line)
        line = re.sub(r"^-\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _esc(line))
        num += 1
        rec_items.append(
            f'<div class="rec-item">'
            f'<div class="rec-num">{num}</div>'
            f'<div class="rec-text">{line}</div>'
            f'</div>'
        )
    rec_html = "".join(rec_items) or '<p class="empty">No recommendations available.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: #ffffff;
  color: #1a1a1a;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 11pt;
  line-height: 1.6;
  padding: 0;
}}

/* ── Page layout ── */
.page {{
  width: 100%;
  padding: 40px 56px;
}}

/* ── Header ── */
.header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  padding-bottom: 20px;
  border-bottom: 2px solid #1a1a1a;
  margin-bottom: 32px;
}}
.logo {{ font-size: 26pt; font-weight: 800; color: #1a1a1a; letter-spacing: -1px; }}
.logo span {{ color: #2563eb; }}
.header-meta {{ text-align: right; font-size: 9pt; color: #6b7280; line-height: 1.8; }}
.header-meta strong {{ color: #1a1a1a; font-weight: 600; }}

/* ── Stats row ── */
.stats {{
  display: flex;
  gap: 16px;
  margin-bottom: 40px;
}}
.stat {{
  flex: 1;
  border: 1.5px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px 20px;
  background: #f9fafb;
}}
.stat .n {{ font-size: 28pt; font-weight: 800; color: #2563eb; line-height: 1; }}
.stat .l {{ font-size: 8.5pt; color: #6b7280; margin-top: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; }}

/* ── Section ── */
.section {{ margin-bottom: 36px; page-break-inside: avoid; }}
.section-title {{
  font-size: 7.5pt;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #2563eb;
  margin-bottom: 14px;
  padding-bottom: 6px;
  border-bottom: 1px solid #e5e7eb;
}}

/* ── Transcripts ── */
.day-label {{
  font-size: 8.5pt;
  font-weight: 700;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
  margin-top: 16px;
}}
.day-label:first-child {{ margin-top: 0; }}
.tx-row {{
  display: flex;
  gap: 14px;
  padding: 6px 0;
  border-bottom: 1px solid #f3f4f6;
  align-items: baseline;
}}
.tx-row:last-child {{ border-bottom: none; }}
.tx-time {{ font-size: 8.5pt; color: #9ca3af; width: 38px; flex-shrink: 0; }}
.tx-badge {{
  font-size: 7pt; font-weight: 600; letter-spacing: 0.04em;
  background: #eff6ff; color: #2563eb; border-radius: 3px;
  padding: 1px 5px; flex-shrink: 0; align-self: center;
}}
.tx-badge.text {{ background: #f3f4f6; color: #6b7280; }}
.tx-content {{ font-size: 10pt; color: #374151; flex: 1; line-height: 1.5; }}

/* ── Bloom table ── */
.bloom-table {{ width: 100%; border-collapse: collapse; font-size: 10pt; }}
.bloom-table th {{
  text-align: left; padding: 8px 12px;
  font-size: 7.5pt; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: #6b7280;
  border-bottom: 1.5px solid #e5e7eb;
  background: #f9fafb;
}}
.bloom-table td {{ padding: 9px 12px; border-bottom: 1px solid #f3f4f6; color: #374151; }}
.bloom-table tr:last-child td {{ border-bottom: none; }}
.bloom-table td.score {{
  font-size: 14pt; font-weight: 800; color: #2563eb; text-align: center;
}}
.bloom-table td.topic {{ font-weight: 600; color: #111827; }}
.bloom-table td.domain {{
  font-size: 8.5pt; color: #9ca3af; font-weight: 500;
  text-transform: capitalize;
}}

/* ── Filler table ── */
.filler-table {{ border-collapse: collapse; font-size: 9.5pt; }}
.filler-table th {{
  padding: 7px 14px; font-size: 7.5pt; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase; color: #6b7280;
  border-bottom: 1.5px solid #e5e7eb; text-align: center;
  background: #f9fafb;
}}
.filler-table th:first-child {{ text-align: left; }}
.filler-table td {{
  padding: 7px 14px; border-bottom: 1px solid #f3f4f6;
  color: #6b7280; text-align: center;
}}
.filler-table td:first-child {{ text-align: left; color: #374151; }}
.filler-table td.hi {{ color: #dc2626; font-weight: 700; }}

/* ── Topic depth ── */
.depth-block {{
  margin-bottom: 16px;
  padding: 14px 18px;
  border: 1px solid #e5e7eb;
  border-left: 3px solid #2563eb;
  border-radius: 6px;
  background: #f9fafb;
  page-break-inside: avoid;
}}
.depth-title {{
  font-size: 11pt; font-weight: 700; color: #111827;
  margin-bottom: 4px;
}}
.depth-tags {{
  font-size: 8pt; color: #9ca3af; margin-bottom: 8px;
  text-transform: capitalize;
}}
.depth-summary {{ font-size: 9.5pt; color: #4b5563; line-height: 1.6; margin-bottom: 6px; }}
.depth-vocab {{
  font-size: 8.5pt; color: #6b7280;
  display: flex; flex-wrap: wrap; gap: 6px;
}}
.vocab-chip {{
  background: #eff6ff; color: #2563eb; border-radius: 3px;
  padding: 2px 7px; font-weight: 500;
}}

/* ── Recommendations ── */
.rec-item {{ margin-bottom: 14px; display: flex; gap: 12px; align-items: flex-start; }}
.rec-num {{
  font-size: 9pt; font-weight: 800; color: #2563eb;
  background: #eff6ff; border-radius: 50%;
  width: 22px; height: 22px; display: flex;
  align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 1px;
}}
.rec-text {{ font-size: 10pt; color: #374151; line-height: 1.6; }}
.rec-text strong {{ color: #111827; }}

/* ── Footer ── */
.footer {{
  margin-top: 40px;
  padding-top: 14px;
  border-top: 1px solid #e5e7eb;
  font-size: 8pt;
  color: #9ca3af;
  display: flex;
  justify-content: space-between;
}}

.empty {{ font-size: 10pt; color: #9ca3af; font-style: italic; }}
</style>
</head>
<body>
<div class="page">

<!-- ── Header ── -->
<div class="header">
  <div class="logo">Micro<span>Learn</span></div>
  <div class="header-meta">
    <strong>7-Day Deep Dive</strong><br>
    {_esc(date_range)}
  </div>
</div>

<!-- ── Stats ── -->
<div class="stats">
  <div class="stat"><div class="n">{total_msgs}</div><div class="l">Messages</div></div>
  <div class="stat"><div class="n">{voice_count}</div><div class="l">Voice Notes</div></div>
  <div class="stat"><div class="n">{total_words:,}</div><div class="l">Words Spoken</div></div>
  <div class="stat"><div class="n">{topic_count}</div><div class="l">Topics</div></div>
</div>

<!-- ── Transcripts ── -->
<div class="section">
  <div class="section-title">Full Transcripts</div>
  {transcripts_html}
</div>

<!-- ── Bloom table ── -->
<div class="section">
  <div class="section-title">Knowledge Progress</div>
  <table class="bloom-table">
    <thead><tr><th>Topic</th><th>Domain</th><th style="text-align:center">Bloom</th><th>Last Updated</th></tr></thead>
    <tbody>{bloom_rows}</tbody>
  </table>
</div>

<!-- ── Filler ── -->
<div class="section">
  <div class="section-title">Speech Patterns</div>
  {filler_html}
</div>

<!-- ── Depth ── -->
<div class="section">
  <div class="section-title">Topic Depth</div>
  {depth_html}
</div>

<!-- ── Recommendations ── -->
<div class="section">
  <div class="section-title">What to Focus on Next</div>
  {rec_html}
</div>

<div class="footer">
  <span>Generated by MicroLearn</span>
  <span>{_esc(timestamp)}</span>
</div>

</div>
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
            await page.set_content(html, wait_until="networkidle", timeout=60000)
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
