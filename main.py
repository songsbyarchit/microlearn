"""
main.py — FastAPI app: Twilio webhook + D3.js knowledge graph dashboard.
"""
import asyncio
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from brain import get_reply
from delay_queue import pop_next_reply, schedule_reply
from knowledge_graph import get_all_topics
from quiz import generate_question, get_quiz_topics, grade_answer
from settings_manager import get_settings, save_settings
from supabase_client import ensure_table_exists, sb_headers, sb_url
from voice import clean_for_text, generate_and_upload_audio, transcribe_audio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
MY_NUMBER = os.environ.get("MY_WHATSAPP_NUMBER", "")

IMMEDIATE_COMMANDS = {"reply", "r"}
SETTINGS_COMMANDS = {
    "faster", "slower", "american", "british", "shorter", "longer",
    "simpler", "deeper", "recap on", "recap off", "settings", "graph",
    "test me", "teach me", "help", "report", "pdf report", "pause", "resume", "topics", "streak", "study",
}

# ---------------------------------------------------------------------------
# Knowledge graph HTML — fetches /knowledge/data at runtime
# ---------------------------------------------------------------------------
_KNOWLEDGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MicroLearn — Knowledge Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1120;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── Stats bar ── */
#topbar{padding:12px 20px;background:#0f172a;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:20px;flex-wrap:wrap;flex-shrink:0}
#topbar h1{font-size:16px;font-weight:700;color:#f8fafc;letter-spacing:-.3px;margin-right:4px}
.stat{font-size:12px;color:#64748b}.stat b{color:#cbd5e1}
#loading{font-size:12px;color:#475569;margin-left:auto}

/* ── Main area ── */
#main{display:flex;flex:1;overflow:hidden}

/* ── Graph ── */
#graph-wrap{flex:1;position:relative;overflow:hidden}
#graph-wrap svg{width:100%;height:100%;touch-action:none}
.link{stroke-opacity:.35}
.node-circle{cursor:pointer;transition:filter .15s}
.node-circle:hover{filter:brightness(1.3)}
.node-label{pointer-events:none;dominant-baseline:central;font-size:11px;font-weight:500;fill:#e2e8f0;text-shadow:0 1px 3px #0b1120,0 0 8px #0b1120}
#empty-msg{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#334155;text-align:center;font-size:14px;display:none}

/* ── Legend ── */
#legend{position:absolute;top:12px;left:12px;background:#0f172a99;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:11px;display:flex;flex-direction:column;gap:6px;max-height:calc(100% - 24px);overflow-y:auto;backdrop-filter:blur(4px)}
#legend h3{font-size:10px;font-weight:600;color:#475569;text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px}
.legend-row{display:flex;align-items:center;gap:7px;color:#94a3b8}
.legend-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}

/* ── Side panel ── */
#panel{width:0;border-left:1px solid #1e293b;overflow:hidden;transition:width .2s ease;background:#0f172a;display:flex;flex-direction:column;flex-shrink:0}
#panel.open{width:320px}
#panel-inner{width:320px;padding:20px;display:flex;flex-direction:column;gap:14px;overflow-y:auto;height:100%}
#panel-close{align-self:flex-end;background:none;border:none;color:#475569;cursor:pointer;font-size:20px;line-height:1;padding:0}
#panel-close:hover{color:#e2e8f0}
.panel-title{font-size:17px;font-weight:700;color:#f8fafc;line-height:1.3}
.panel-badge{display:inline-block;padding:3px 10px;border-radius:9999px;font-size:11px;font-weight:600;margin-top:2px}
.panel-row{display:flex;justify-content:space-between;font-size:12px;color:#64748b;border-top:1px solid #1e293b;padding-top:10px}
.panel-row b{color:#94a3b8}
.bloom-track{height:5px;background:#1e293b;border-radius:3px;margin-top:6px}
.bloom-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#3b82f6,#8b5cf6)}
.panel-section{font-size:12px;font-weight:600;color:#475569;text-transform:uppercase;letter-spacing:.05em}
.panel-summary{font-size:13px;color:#94a3b8;line-height:1.6}
.edge-list{display:flex;flex-direction:column;gap:4px}
.edge-item{font-size:11px;color:#64748b;padding:4px 8px;background:#1e293b;border-radius:4px}
.edge-item b{color:#94a3b8}

/* ── Mobile ── */
@media(max-width:640px){
  #panel.open{width:100%;position:absolute;inset:0;z-index:10}
  #legend{font-size:10px;padding:7px 10px}
  #topbar{gap:12px;padding:10px 14px}
}
</style>
</head>
<body>

<div id="topbar">
  <h1>MicroLearn</h1>
  <div class="stat">Topics: <b id="s-topics">—</b></div>
  <div class="stat">Domains: <b id="s-domains">—</b></div>
  <div class="stat">Updated: <b id="s-updated">—</b></div>
  <div id="loading">Loading…</div>
</div>

<div id="main">
  <div id="graph-wrap">
    <div id="empty-msg">No topics yet.<br>Start a conversation to build your graph.</div>
    <div id="legend"><h3>Domains</h3></div>
  </div>
  <div id="panel">
    <div id="panel-inner">
      <button id="panel-close">&#x2715;</button>
      <div class="panel-title" id="p-title"></div>
      <span class="panel-badge" id="p-badge"></span>
      <div class="bloom-track"><div class="bloom-fill" id="p-bloom-fill"></div></div>
      <div class="panel-row"><span>Bloom score</span><b id="p-bloom"></b></div>
      <div class="panel-row"><span>Last updated</span><b id="p-updated"></b></div>
      <div class="panel-section">Summary</div>
      <div class="panel-summary" id="p-summary"></div>
      <div class="panel-section" id="p-edges-hdr" style="display:none">Connections</div>
      <div class="edge-list" id="p-edges"></div>
    </div>
  </div>
</div>

<script>
const DOMAIN_COLOURS = {
  physics:'#3b82f6', chemistry:'#10b981', mathematics:'#8b5cf6', maths:'#8b5cf6',
  cooking:'#f97316', food:'#f97316', history:'#a16207', biology:'#14b8a6',
  computing:'#ef4444', programming:'#ef4444', economics:'#eab308',
  music:'#ec4899', philosophy:'#6366f1', literature:'#06b6d4',
  geography:'#84cc16', psychology:'#f43f5e', general:'#64748b',
};
const EXTRA = ['#06b6d4','#84cc16','#f43f5e','#a78bfa','#fb923c','#22d3ee','#4ade80','#f472b6'];
let _ei = 0;
const _dc = {};
function dc(domain) {
  const k = (domain||'general').toLowerCase();
  if (DOMAIN_COLOURS[k]) return DOMAIN_COLOURS[k];
  if (!_dc[k]) _dc[k] = EXTRA[_ei++ % EXTRA.length];
  return _dc[k];
}

fetch('/knowledge/data')
  .then(r => r.json())
  .then(data => build(data))
  .catch(e => { document.getElementById('loading').textContent = 'Error loading data'; console.error(e); });

function build(data) {
  document.getElementById('loading').remove();

  const topics = data.topics || [];
  if (!topics.length) { document.getElementById('empty-msg').style.display = 'block'; return; }

  // Stats
  const domains = [...new Set(topics.map(t => t.domain))];
  document.getElementById('s-topics').textContent = topics.length;
  document.getElementById('s-domains').textContent = domains.length;
  const latest = topics.reduce((a, b) => (a.updated_at||'') > (b.updated_at||'') ? a : b);
  document.getElementById('s-updated').textContent = (latest.updated_at||'').slice(0,10);

  // Legend
  const legend = document.getElementById('legend');
  domains.sort().forEach(d => {
    const row = document.createElement('div');
    row.className = 'legend-row';
    row.innerHTML = `<div class="legend-dot" style="background:${dc(d)}"></div>${d}`;
    legend.appendChild(row);
  });

  // Build node & link arrays
  const nodeById = {};
  const nodes = topics.map(t => {
    const n = { id: t.name, ...t, r: 10 + (t.bloom_score||1) * 2.5 };
    nodeById[t.name] = n;
    return n;
  });

  const links = [];
  nodes.forEach(n => {
    (n.edges||[]).forEach(e => {
      const target = e.target || e;
      if (nodeById[target] && target !== n.id) {
        links.push({ source: n.id, target, type: e.type||'related' });
      }
    });
  });

  // SVG setup
  const wrap = document.getElementById('graph-wrap');
  const W = wrap.clientWidth, H = wrap.clientHeight;
  const svg = d3.select('#graph-wrap').append('svg')
    .attr('width', W).attr('height', H);
  const g = svg.append('g');

  svg.call(d3.zoom().scaleExtent([0.15, 5])
    .on('zoom', e => g.attr('transform', e.transform)));

  // Simulation
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(d => 80 + d.source.r + d.target.r).strength(0.35))
    .force('charge', d3.forceManyBody().strength(d => -180 - d.r * 8))
    .force('center', d3.forceCenter(W/2, H/2).strength(0.08))
    .force('collide', d3.forceCollide().radius(d => d.r + 14).iterations(2))
    .alphaDecay(0.025);

  // Links
  const linkSel = g.append('g').selectAll('line').data(links).join('line')
    .attr('class', 'link')
    .attr('stroke', d => d.type === 'conflicting' ? '#ef4444' : d.type === 'prerequisite' ? '#6366f1' : '#334155')
    .attr('stroke-width', d => d.type === 'prerequisite' ? 2 : 1.5);

  // Drag (works for both mouse and touch via pointer events)
  function drag(sim) {
    return d3.drag()
      .on('start', (e,d) => { if (!e.active) sim.alphaTarget(0.25).restart(); d.fx=d.x; d.fy=d.y; })
      .on('drag',  (e,d) => { d.fx=e.x; d.fy=e.y; })
      .on('end',   (e,d) => { if (!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; });
  }

  // Nodes
  const nodeSel = g.append('g').selectAll('g').data(nodes).join('g')
    .call(drag(sim))
    .on('click', (e,d) => { e.stopPropagation(); openPanel(d); });

  nodeSel.append('circle')
    .attr('class', 'node-circle')
    .attr('r', d => d.r)
    .attr('fill', d => dc(d.domain))
    .attr('fill-opacity', 0.88)
    .attr('stroke', '#0b1120')
    .attr('stroke-width', 2);

  // Glow ring on hover
  nodeSel.append('circle')
    .attr('r', d => d.r + 6)
    .attr('fill', 'none')
    .attr('stroke', d => dc(d.domain))
    .attr('stroke-width', 1)
    .attr('stroke-opacity', 0)
    .attr('class', 'node-ring');

  nodeSel
    .on('mouseenter', function() { d3.select(this).select('.node-ring').attr('stroke-opacity', 0.4); })
    .on('mouseleave', function() { d3.select(this).select('.node-ring').attr('stroke-opacity', 0); });

  nodeSel.append('text')
    .attr('class', 'node-label')
    .attr('text-anchor', 'middle')
    .attr('dy', d => d.r + 15)
    .text(d => d.name.length > 18 ? d.name.slice(0,16)+'…' : d.name);

  svg.on('click', closePanel);

  sim.on('tick', () => {
    linkSel
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Panel
  const panel = document.getElementById('panel');
  function openPanel(d) {
    document.getElementById('p-title').textContent = d.name;
    const badge = document.getElementById('p-badge');
    badge.textContent = d.domain;
    badge.style.background = dc(d.domain) + '28';
    badge.style.color = dc(d.domain);
    const pct = Math.round(((d.bloom_score||1) / 8) * 100);
    document.getElementById('p-bloom-fill').style.width = pct + '%';
    document.getElementById('p-bloom').textContent = `${d.bloom_score||1} / 8`;
    document.getElementById('p-updated').textContent = (d.updated_at||'').slice(0,10) || '—';
    document.getElementById('p-summary').textContent = d.summary || '—';
    const edgesHdr = document.getElementById('p-edges-hdr');
    const edgesList = document.getElementById('p-edges');
    edgesList.innerHTML = '';
    const edges = d.edges || [];
    if (edges.length) {
      edgesHdr.style.display = 'block';
      edges.forEach(e => {
        const item = document.createElement('div');
        item.className = 'edge-item';
        item.innerHTML = `<b>${e.type||'related'}</b>: ${e.target||e}`;
        edgesList.appendChild(item);
      });
    } else {
      edgesHdr.style.display = 'none';
    }
    panel.classList.add('open');
  }
  function closePanel() { panel.classList.remove('open'); }
  document.getElementById('panel-close').addEventListener('click', closePanel);
}
</script>
</body>
</html>"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_table_exists()
    logger.info("MicroLearn bot starting up.")
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
# Knowledge graph — data API + dashboard
# ---------------------------------------------------------------------------

@app.get("/knowledge/data")
async def knowledge_data():
    """Return all knowledge topics as JSON for the D3 graph."""
    topics = get_all_topics()
    return JSONResponse({
        "topics": [
            {
                "name": t["topic"],
                "domain": t["domain"],
                "bloom_score": t["bloom_level"],
                "summary": t["summary"],
                "edges": t["edges"],
                "updated_at": t["last_updated"],
            }
            for t in topics
        ]
    })


@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_viewer():
    """D3.js force-layout knowledge graph dashboard."""
    return HTMLResponse(content=_KNOWLEDGE_HTML)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


async def _send_text_now(to: str, text: str) -> None:
    """Send a text message immediately (not via delay queue)."""
    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                messages_url,
                data={"From": from_number, "To": to, "Body": text},
                auth=(twilio_sid, twilio_token),
                timeout=10,
            )
            if resp.status_code >= 400:
                logger.error("_send_text_now failed %d: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("_send_text_now error: %s", e)


async def _send_voice_now(to: str, text: str) -> None:
    """Generate TTS and send immediately as a voice note."""
    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    try:
        media_url = await generate_and_upload_audio(text, sender=to)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                messages_url,
                data={"From": from_number, "To": to, "MediaUrl": media_url},
                auth=(twilio_sid, twilio_token),
                timeout=30,
            )
            if resp.status_code >= 400:
                logger.error("_send_voice_now failed %d: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("_send_voice_now error, falling back to text: %s", e)
        await _send_text_now(to, text)


async def _save_transcript(content: str, is_voice_note: bool) -> None:
    """Persist a user message to the transcripts table."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                sb_url("/rest/v1/transcripts"),
                headers=sb_headers(),
                content=json.dumps({
                    "content": content,
                    "word_count": len(content.split()),
                    "is_voice_note": is_voice_note,
                    "created_at": datetime.now(tz=timezone.utc).isoformat(),
                }),
                timeout=10,
            )
            resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to save transcript: %s", e)


# ---------------------------------------------------------------------------
# Quiz state helpers (Redis)
# ---------------------------------------------------------------------------

def _quiz_key(phone: str) -> str:
    return f"microlearn:quiz:{phone}"


def _get_quiz_state(phone: str) -> dict | None:
    from upstash_redis import Redis
    r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
    raw = r.get(_quiz_key(phone))
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None


def _set_quiz_state(phone: str, state: dict) -> None:
    from upstash_redis import Redis
    r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
    r.set(_quiz_key(phone), json.dumps(state), ex=3600)  # 1-hour TTL


def _clear_quiz_state(phone: str) -> None:
    from upstash_redis import Redis
    r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
    r.delete(_quiz_key(phone))


# ---------------------------------------------------------------------------
# Teach-it-back state helpers
# ---------------------------------------------------------------------------

def _teach_key(phone: str) -> str:
    return f"microlearn:teach:{phone}"

def _get_teach_state(phone: str) -> dict | None:
    from upstash_redis import Redis
    r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
    raw = r.get(_teach_key(phone))
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None

def _set_teach_state(phone: str, state: dict) -> None:
    from upstash_redis import Redis
    r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
    r.set(_teach_key(phone), json.dumps(state), ex=3600)

def _clear_teach_state(phone: str) -> None:
    from upstash_redis import Redis
    r = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
    r.delete(_teach_key(phone))


# ---------------------------------------------------------------------------
# Quiz flow
# ---------------------------------------------------------------------------

async def _handle_test_me(sender: str) -> None:
    """Show topic menu and store awaiting_topic_choice state in Redis."""
    topics = get_quiz_topics(4)
    if not topics:
        await _send_text_now(sender, "No topics to quiz you on yet — keep learning!")
        return

    lines = ["Pick a topic to be quizzed on:\n"]
    for i, t in enumerate(topics, 1):
        lines.append(f"{i}. {t['topic'].title()} ({t['domain']}, bloom {t['bloom_score']})")
    lines.append("5. Surprise me")
    lines.append("\nReply with 1–5")

    await _send_text_now(sender, "\n".join(lines))
    _set_quiz_state(sender, {"state": "awaiting_topic_choice", "topics": topics})


async def _handle_quiz_state(sender: str, user_text: str, quiz_state: dict) -> bool:
    """
    Handle a message that maps to an in-progress quiz.
    Returns True if the message was consumed by the quiz flow, False otherwise.
    """
    state = quiz_state.get("state")
    reply = user_text.strip().lower()

    if state == "awaiting_topic_choice":
        if reply not in {"1", "2", "3", "4", "5"}:
            return False  # let normal flow handle it

        topics = quiz_state.get("topics", [])
        if reply == "5" or not topics:
            # Surprise: pick any low-bloom topic at random
            all_topics = get_quiz_topics(10)
            chosen = random.choice(all_topics) if all_topics else None
        else:
            idx = int(reply) - 1
            chosen = topics[idx] if idx < len(topics) else random.choice(topics)

        if not chosen:
            await _send_text_now(sender, "Couldn't find a topic. Try again later.")
            _clear_quiz_state(sender)
            return True

        await _send_text_now(sender, f"Generating a question on *{chosen['topic'].title()}*…")
        try:
            q = await generate_question(chosen["topic"], chosen["domain"], chosen.get("content", ""))
            q["topic"] = chosen["topic"]
            q["domain"] = chosen["domain"]
        except Exception as e:
            logger.error("generate_question failed: %s", e)
            await _send_text_now(sender, "Couldn't generate a question right now. Try again later.")
            _clear_quiz_state(sender)
            return True

        msg_lines = [q["question"], ""]
        for opt in q["options"]:
            msg_lines.append(opt)
        msg_lines.append("\nReply A, B, C or D")
        await _send_text_now(sender, "\n".join(msg_lines))
        _set_quiz_state(sender, {"state": "awaiting_answer", "question": q})
        return True

    if state == "awaiting_answer":
        if reply not in {"a", "b", "c", "d", "1", "2", "3", "4"}:
            return False  # unrecognised reply — let normal flow handle it

        q = quiz_state.get("question", {})
        is_correct, explanation = grade_answer(reply, q)
        if is_correct:
            result_line = "Correct! ✅"
        else:
            correct_letter = ["A", "B", "C", "D"][q.get("correct_index", 0)]
            result_line = f"Not quite — the answer was {correct_letter}."

        # Update SRS interval for this topic
        try:
            from knowledge_graph import update_srs
            update_srs(q.get("domain", "general"), q.get("topic", ""), is_correct)
        except Exception as _srs_err:
            logger.warning("SRS update failed (non-fatal): %s", _srs_err)

        pending = quiz_state.get("pending", [])
        if pending:
            # More questions queued — grade this one then advance to the next
            await _send_text_now(sender, f"{result_line}\n\n{explanation}")
            next_q = pending[0]
            opts = next_q["options"]
            msg = (
                f"{next_q['question']}\n\n"
                f"{opts[0]}\n{opts[1]}\n{opts[2]}\n{opts[3]}\n\n"
                "Reply A, B, C or D"
            )
            await _send_text_now(sender, msg)
            _set_quiz_state(sender, {"state": "awaiting_answer", "question": next_q, "pending": pending[1:]})
        else:
            await _send_text_now(sender, f"{result_line}\n\n{explanation}\n\nWant another? Reply *test me* to go again.")
            _clear_quiz_state(sender)
        return True

    return False


async def _handle_report(sender: str) -> None:
    """Generate a 7-day PDF report and send immediately."""
    try:
        from report import generate_report_image
        url, _stats = await generate_report_image(7)
    except Exception as e:
        logger.error("Report generation failed: %s", e)
        await _send_text_now(sender, "No data yet — keep learning!")
        return

    if not url:
        await _send_text_now(sender, "No data yet — keep learning!")
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            messages_url,
            data={"From": from_number, "To": sender, "MediaUrl": url, "Body": "Your last 7 days 📚"},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.error("Failed to send report to %s: %s", sender, resp.text)


async def _handle_study(sender: str, domain: str) -> None:
    """
    Show the user a structured summary of what they know in a domain,
    what's weak, what's due for review, and invite them to dive in.
    """
    from knowledge_graph import _get
    from datetime import datetime, timezone

    try:
        rows = _get({
            "select": "topic,bloom_score,next_review_at,updated_at",
            "domain": f"eq.{domain}",
            "order": "bloom_score.asc",
        })
    except Exception as e:
        logger.error("study fetch failed: %s", e)
        await _send_text_now(sender, f"Couldn't load topics for *{domain}* — try again.")
        return

    if not rows:
        await _send_text_now(
            sender,
            f"No topics in *{domain}* yet. Just start chatting about it and I'll build your graph as we go."
        )
        return

    now = datetime.now(tz=timezone.utc)
    bloom_labels = {1: "just introduced", 2: "familiar", 3: "familiar", 4: "comfortable",
                    5: "comfortable", 6: "strong", 7: "strong", 8: "mastered"}

    due = [r for r in rows if r.get("next_review_at") and r["next_review_at"] <= now.isoformat()]
    strong = [r for r in rows if (r.get("bloom_score") or 1) >= 6]
    weak = [r for r in rows if (r.get("bloom_score") or 1) <= 3]

    lines = [f"*{domain.title()}* — {len(rows)} topic{'s' if len(rows) != 1 else ''} in your graph\n"]

    if due:
        lines.append(f"🔁 *Due for review ({len(due)}):*")
        for r in due[:5]:
            lines.append(f"  • {r['topic'].title()} ({bloom_labels.get(r.get('bloom_score') or 1, '')})")

    if weak:
        lines.append(f"\n⚠️ *Needs work ({len(weak)}):*")
        for r in weak[:5]:
            lines.append(f"  • {r['topic'].title()}")

    if strong:
        lines.append(f"\n✅ *Strong ({len(strong)}):*")
        for r in strong[:5]:
            lines.append(f"  • {r['topic'].title()}")

    lines.append(f"\nJust talk to me about any of these, or ask me something new in {domain.title()}.")
    if due:
        lines.append(f"Or reply *test me* to get quizzed on the ones due for review.")

    await _send_text_now(sender, "\n".join(lines))


async def _handle_help_text(sender: str) -> None:
    """Send a plain-text command reference."""
    msg = (
        "*MicroLearn — all commands* 📖\n\n"
        "*Learning*\n"
        "Just chat — ask me anything to learn it\n"
        "*study <domain>* — e.g. study history, study geography\n"
        "*test me* — quiz on your weakest topics\n"
        "*teach me* — explain a topic back to me; I'll grade you\n\n"
        "*Pacing*\n"
        "*simpler* / *deeper* — adjust difficulty\n"
        "*shorter* / *longer* — adjust reply length\n"
        "*faster* / *slower* — adjust voice speed\n"
        "*recap on* / *recap off* — session recaps\n"
        "*reply* or *r* — skip delay, get reply now\n\n"
        "*Progress*\n"
        "*topics* — list your knowledge graph topics\n"
        "*streak* — your learning streak\n"
        "*graph* — visual knowledge map\n"
        "*report* — one-page PNG summary\n"
        "*pdf report* — full deep-dive PDF\n\n"
        "*Other*\n"
        "*pause* / *resume* — stop/start replies\n"
        "*settings* — show current settings\n"
        "*help* — this message (text)\n"
        "🎤 voice note saying 'help' — personalised advice on how to get more from the app"
    )
    await _send_text_now(sender, msg)


async def _handle_help_voice(sender: str) -> None:
    """Send a personalised voice note about the user's usage and what to do more of."""
    import anthropic as _anthropic
    from knowledge_graph import _get

    # Gather usage signals
    try:
        now_utc = datetime.now(tz=timezone.utc)
        week_ago = (now_utc - timedelta(days=7)).isoformat()
        month_ago = (now_utc - timedelta(days=30)).isoformat()

        recent_resp = httpx.get(
            sb_url("/rest/v1/transcripts"),
            headers=sb_headers(),
            params={"select": "content,is_voice_note,created_at", "created_at": f"gte.{week_ago}", "order": "created_at.desc", "limit": "50"},
            timeout=10,
        )
        recent = recent_resp.json() or []

        nodes_resp = _get({"select": "domain,topic,bloom_score,updated_at", "order": "updated_at.desc"})
    except Exception as e:
        logger.error("help voice data fetch failed: %s", e)
        await _send_text_now(sender, "Couldn't load your usage data right now — try again later.")
        return

    total_msgs = len(recent)
    voice_msgs = sum(1 for r in recent if r.get("is_voice_note"))
    text_msgs = total_msgs - voice_msgs
    domains = {}
    for n in nodes_resp:
        d = n.get("domain", "general")
        domains[d] = domains.get(d, 0) + 1
    top_domains = sorted(domains.items(), key=lambda x: x[1], reverse=True)[:5]
    bloom_avg = sum(n.get("bloom_score") or 1 for n in nodes_resp) / max(len(nodes_resp), 1)
    weak_topics = [n for n in nodes_resp if (n.get("bloom_score") or 1) <= 2]

    context = f"""User usage summary (last 7 days):
- Total messages: {total_msgs} ({voice_msgs} voice, {text_msgs} text)
- Topics in knowledge graph: {len(nodes_resp)} across {len(domains)} domains
- Top domains: {', '.join(f"{d} ({c} topics)" for d, c in top_domains) or 'none yet'}
- Average bloom score: {bloom_avg:.1f}/8
- Weak topics (bloom ≤ 2): {len(weak_topics)} — {', '.join(n['topic'] for n in weak_topics[:5]) or 'none'}
- Features probably not used recently: {'teach me' if total_msgs < 5 else ''} {'study <domain>' if not domains else ''}
"""

    _claude = _anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = f"""{context}

You are MicroLearn, a personal learning companion on WhatsApp. The user has sent a voice note asking for help.
Give them a warm, honest, specific 90-second personalised voice note that:
1. Tells them what they're getting real value from already
2. Points to one or two things they haven't used much that would genuinely help them
3. Gives a concrete suggestion for what to do today based on their weak spots or domains

Write as spoken British English. After each sentence write [pause]. Before any question write [long pause].
No bullet points, no headers. Conversational. Around 200 words."""

    try:
        resp = await _claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        script = resp.content[0].text.strip()
    except Exception as e:
        logger.error("help voice Claude call failed: %s", e)
        await _send_text_now(sender, "Couldn't generate your personalised help right now.")
        return

    try:
        audio_url = await generate_and_upload_audio(script, sender=sender)
        twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
        twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
        from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
        messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
        async with httpx.AsyncClient() as client:
            await client.post(
                messages_url,
                data={"From": from_number, "To": sender, "MediaUrl": audio_url},
                auth=(twilio_sid, twilio_token),
                timeout=30,
            )
    except Exception as e:
        logger.error("help voice send failed: %s", e)
        await _send_text_now(sender, "Couldn't send voice note — try typing *help* for the text version.")


async def _handle_teach_me(sender: str) -> None:
    """Pick a topic the user knows and ask them to explain it from scratch."""
    from knowledge_graph import _get
    try:
        nodes = _get({"select": "domain,topic,bloom_score,content", "order": "bloom_score.desc", "limit": "10"})
    except Exception as e:
        logger.error("teach me fetch failed: %s", e)
        await _send_text_now(sender, "Couldn't load your topics right now.")
        return

    # Pick a topic with bloom 3-7 — known enough to explain, not so easy it's trivial
    candidates = [n for n in nodes if 3 <= (n.get("bloom_score") or 1) <= 7]
    if not candidates:
        candidates = nodes
    if not candidates:
        await _send_text_now(sender, "No topics to teach back yet — have a few conversations first.")
        return

    chosen = random.choice(candidates[:5])
    topic = chosen["topic"]
    domain = chosen.get("domain", "general")

    _set_teach_state(sender, {"topic": topic, "domain": domain, "bloom_score": chosen.get("bloom_score", 3)})

    await _send_text_now(
        sender,
        f"Alright — explain *{topic.title()}* to me as if I've never heard of it. "
        f"Voice note or text, whatever feels natural. Don't look anything up — just go from memory."
    )


async def _handle_teach_response(sender: str, user_text: str, teach_state: dict) -> bool:
    """Grade the user's explanation and update SRS. Returns True if consumed."""
    import anthropic as _anthropic
    from knowledge_graph import update_srs

    topic = teach_state.get("topic", "")
    domain = teach_state.get("domain", "general")
    bloom = teach_state.get("bloom_score", 3)

    _clear_teach_state(sender)

    _claude = _anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = f"""Topic: {topic} (domain: {domain}, current bloom score: {bloom}/8)

The user's explanation:
\"\"\"{user_text}\"\"\"

Evaluate this explanation. Respond with JSON only:
{{
  "score": 0-10,
  "correct": ["things they got right"],
  "gaps": ["important things missing or wrong"],
  "verdict": "one warm sentence summarising how well they did",
  "follow_up": "one question to push their understanding one level deeper"
}}"""

    try:
        resp = await _claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
    except Exception as e:
        logger.error("teach grading failed: %s", e)
        await _send_text_now(sender, "Couldn't grade that right now — but good effort. Try again with *teach me*.")
        return True

    score = data.get("score", 5)
    correct = data.get("correct", [])
    gaps = data.get("gaps", [])
    verdict = data.get("verdict", "")
    follow_up = data.get("follow_up", "")

    # Update SRS: score >= 7 counts as correct
    update_srs(domain, topic, correct=(score >= 7))

    # Build reply
    lines = [verdict]
    if correct:
        lines.append("✅ " + " · ".join(correct[:3]))
    if gaps:
        lines.append("⚠️ Gaps: " + " · ".join(gaps[:3]))
    if follow_up:
        lines.append(f"\n{follow_up}")

    await _send_text_now(sender, "\n\n".join(lines))
    return True


async def _handle_graph(sender: str) -> None:
    """Screenshot the live D3 knowledge graph and send as a WhatsApp image."""
    try:
        from playwright.async_api import async_playwright
        from voice import _r2_upload_sync
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(
                viewport={"width": 1400, "height": 900},
                device_scale_factor=2,
            )
            page = await context.new_page()
            graph_url = f"{os.environ['PUBLIC_BASE_URL']}/knowledge"
            await page.goto(graph_url, wait_until="networkidle")
            # Let D3 simulation settle
            await asyncio.sleep(3)
            png_bytes = await page.screenshot(full_page=False)
            await browser.close()
    except Exception as e:
        logger.error("Graph screenshot failed: %s", e)
        await _send_text_now(sender, "Couldn't generate graph image — try again later.")
        return

    import uuid
    filename = f"graph-{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}-{uuid.uuid4()}.png"
    try:
        url = await asyncio.to_thread(_r2_upload_sync, png_bytes, filename, "image/png")
    except Exception as e:
        logger.error("Graph R2 upload failed: %s", e)
        await _send_text_now(sender, "Couldn't upload graph — try again later.")
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            messages_url,
            data={"From": from_number, "To": sender, "MediaUrl": url, "Body": "Your knowledge graph 🧠"},
            auth=(twilio_sid, twilio_token),
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.error("Failed to send graph image: %s", resp.text)


async def _handle_topics(sender: str) -> None:
    """Fetch all knowledge nodes and send a domain-grouped list."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                sb_url("/rest/v1/knowledge_nodes"),
                headers=sb_headers(),
                params={"select": "domain,topic,bloom_score", "order": "domain.asc,bloom_score.desc"},
                timeout=15,
            )
            resp.raise_for_status()
            nodes = resp.json() or []
    except Exception as e:
        logger.error("Failed to fetch nodes for topics: %s", e)
        await _send_text_now(sender, "Couldn't fetch topics right now.")
        return

    if not nodes:
        await _send_text_now(sender, "No topics in your knowledge graph yet — start chatting!")
        return

    # Group by domain
    groups: dict[str, list[dict]] = {}
    for n in nodes:
        domain = n.get("domain", "general")
        groups.setdefault(domain, []).append(n)

    lines = [f"Your knowledge graph ({len(nodes)} topics):\n"]
    for domain in sorted(groups):
        lines.append(f"*{domain.title()}*")
        for n in groups[domain]:
            lines.append(f"  · {n['topic'].title()}  [bloom {n.get('bloom_score') or 1}]")
    await _send_text_now(sender, "\n".join(lines))


async def _handle_streak(sender: str) -> None:
    """Count consecutive days with at least one transcript and report."""
    since = (datetime.now(tz=timezone.utc) - timedelta(days=90)).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                sb_url("/rest/v1/transcripts"),
                headers=sb_headers(),
                params={"select": "created_at", "created_at": f"gte.{since}", "order": "created_at.desc"},
                timeout=15,
            )
            resp.raise_for_status()
            rows = resp.json() or []
    except Exception as e:
        logger.error("Failed to fetch transcripts for streak: %s", e)
        await _send_text_now(sender, "Couldn't calculate your streak right now.")
        return

    # Collect unique dates (UK local)
    import pytz as _pytz
    uk_tz = _pytz.timezone("Europe/London")
    days_with_activity: set[str] = set()
    for row in rows:
        ts = row.get("created_at", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(uk_tz)
            days_with_activity.add(dt.strftime("%Y-%m-%d"))
        except Exception:
            pass

    # Count consecutive days ending today (or yesterday)
    streak = 0
    check = datetime.now(tz=uk_tz).date()
    while check.strftime("%Y-%m-%d") in days_with_activity:
        streak += 1
        check = check - timedelta(days=1)

    # If today has no activity yet, try streak ending yesterday
    if streak == 0:
        check = (datetime.now(tz=uk_tz) - timedelta(days=1)).date()
        while check.strftime("%Y-%m-%d") in days_with_activity:
            streak += 1
            check = check - timedelta(days=1)

    if streak == 0:
        await _send_text_now(sender, "No streak yet — send a message today to start one!")
    else:
        await _send_text_now(sender, f"Your current streak: {streak} day{'s' if streak != 1 else ''} \U0001f525")


async def _handle_pdf_report(sender: str) -> None:
    """Generate a detailed 7-day deep-dive PDF and send immediately."""
    await _send_text_now(sender, "Generating your deep-dive report, this may take a moment…")
    try:
        from report import generate_detailed_pdf
        url = await generate_detailed_pdf(7)
    except Exception as e:
        logger.error("Detailed PDF generation failed: %s", e)
        await _send_text_now(sender, "Report generation failed — try again later.")
        return

    if not url:
        await _send_text_now(sender, "No data yet — keep learning!")
        return

    twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
    twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_NUMBER"]
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            messages_url,
            data={"From": from_number, "To": sender, "MediaUrl": url, "Body": "Your 7-day deep dive 📖"},
            auth=(twilio_sid, twilio_token),
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error("Failed to send detailed PDF to %s: %s", sender, resp.text)
        else:
            logger.info("Detailed PDF sent to %s: %s", sender, url)


async def _handle_settings_command(sender: str, cmd: str) -> None:
    """Mutate user settings or respond to info commands."""
    settings = get_settings(sender)

    if cmd == "faster":
        settings["speaking_rate"] = round(min(4.0, settings["speaking_rate"] + 0.25), 2)
        save_settings(sender, settings)
        logger.info("speaking_rate for %s set to %.2f", sender, settings["speaking_rate"])
        await _send_text_now(sender, f"Speaking rate set to {settings['speaking_rate']}x ✓")

    elif cmd == "slower":
        settings["speaking_rate"] = round(max(0.5, settings["speaking_rate"] - 0.25), 2)
        save_settings(sender, settings)
        logger.info("speaking_rate for %s set to %.2f", sender, settings["speaking_rate"])
        await _send_text_now(sender, f"Speaking rate set to {settings['speaking_rate']}x ✓")

    elif cmd == "american":
        settings["voice_id"] = "pNInz6obpgDQGcFmaJgB"
        save_settings(sender, settings)
        await _send_text_now(sender, "Switched to American voice ✓")

    elif cmd == "british":
        settings["voice_id"] = "lUTamkMw7gOzZbFIwmq4"
        save_settings(sender, settings)
        await _send_text_now(sender, "Switched to British voice ✓")

    elif cmd == "shorter":
        settings["max_words"] = max(30, settings["max_words"] - 10)
        save_settings(sender, settings)
        await _send_text_now(sender, f"Max reply length set to {settings['max_words']} words ✓")

    elif cmd == "longer":
        settings["max_words"] = min(150, settings["max_words"] + 10)
        save_settings(sender, settings)
        await _send_text_now(sender, f"Max reply length set to {settings['max_words']} words ✓")

    elif cmd == "simpler":
        settings["bloom_target"] = max(1, settings["bloom_target"] - 1)
        save_settings(sender, settings)
        await _send_text_now(sender, f"Bloom target set to {settings['bloom_target']}/8 ✓")

    elif cmd == "deeper":
        settings["bloom_target"] = min(8, settings["bloom_target"] + 1)
        save_settings(sender, settings)
        await _send_text_now(sender, f"Bloom target set to {settings['bloom_target']}/8 ✓")

    elif cmd == "recap on":
        settings["recap_enabled"] = True
        save_settings(sender, settings)
        await _send_text_now(sender, "Recap messages enabled ✓")

    elif cmd == "recap off":
        settings["recap_enabled"] = False
        save_settings(sender, settings)
        await _send_text_now(sender, "Recap messages disabled ✓")

    elif cmd == "settings":
        voice = "British" if settings["voice_id"] == "lUTamkMw7gOzZbFIwmq4" else "American"
        recap = "on" if settings["recap_enabled"] else "off"
        await _send_text_now(sender, (
            f"Your settings:\n"
            f"• Speed: {settings['speaking_rate']}x\n"
            f"• Voice: {voice}\n"
            f"• Max words: {settings['max_words']}\n"
            f"• Bloom target: {settings['bloom_target']}/8\n"
            f"• Recap: {recap}"
        ))

    elif cmd == "graph":
        await _handle_graph(sender)

    elif cmd == "test me":
        await _handle_test_me(sender)

    elif cmd == "report":
        await _handle_report(sender)

    elif cmd == "pdf report":
        await _handle_pdf_report(sender)

    elif cmd == "pause":
        from upstash_redis import Redis as _Redis
        _r = _Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
        _r.set(f"microlearn:paused:{sender}", "true")
        await _send_text_now(sender, "Paused — I won't send replies until you say resume.")

    elif cmd == "resume":
        from upstash_redis import Redis as _Redis
        _r = _Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
        _r.delete(f"microlearn:paused:{sender}")
        await _send_text_now(sender, "Back on. I'll reply as normal.")

    elif cmd == "topics":
        await _handle_topics(sender)

    elif cmd == "streak":
        await _handle_streak(sender)

    elif cmd == "study":
        await _send_text_now(sender, "Which domain? e.g. *study history* or *study geography*")

    elif cmd.startswith("study "):
        domain_arg = cmd[len("study "):].strip()
        await _handle_study(sender, domain_arg)

    elif cmd == "help":
        await _handle_help_text(sender)

    elif cmd == "teach me":
        await _handle_teach_me(sender)


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

    # Resolve message text and track whether it came from a voice note.
    # Must happen BEFORE the immediate-command check so a transcribed voice note
    # containing the word "reply" never accidentally triggers it.
    is_voice_note = False
    user_text = body

    if num_media > 0 and MediaUrl0:
        content_type = MediaContentType0.lower()
        if "audio" in content_type or "ogg" in content_type or "mpeg" in content_type:
            try:
                user_text = await transcribe_audio(MediaUrl0, TWILIO_SID, TWILIO_TOKEN)
                is_voice_note = True
                logger.info("Transcribed voice note: %r", user_text)
            except Exception as e:
                logger.error("Transcription failed: %s", e)
                user_text = body or "[voice note -- transcription failed]"
                is_voice_note = True
        else:
            logger.info("Non-audio media attachment, ignoring media.")

    # Immediate-send command: only valid for plain text messages, not voice notes.
    if not is_voice_note and body.lower() in IMMEDIATE_COMMANDS:
        pending = pop_next_reply(sender)
        if pending:
            await _send_reply(pending)
        else:
            logger.info("No pending reply to send immediately.")
        return PlainTextResponse("", status_code=200)

    if not user_text:
        logger.info("Empty message, ignoring.")
        return PlainTextResponse("", status_code=200)

    # Settings commands
    cmd = user_text.strip().lower()
    if cmd in SETTINGS_COMMANDS or cmd.startswith("study "):
        await _handle_settings_command(sender, cmd)
        return PlainTextResponse("", status_code=200)

    # Voice note "help" — personalised usage advice
    if is_voice_note and any(w in user_text.lower() for w in ("help", "how do i", "how can i", "what can i", "what should i", "get more", "respond instantly", "no delay", "remove delay")):
        await _handle_help_voice(sender)
        return PlainTextResponse("", status_code=200)

    # Teach-it-back state machine — check before quiz and brain
    teach_state = _get_teach_state(sender)
    if teach_state:
        consumed = await _handle_teach_response(sender, user_text, teach_state)
        if consumed:
            return PlainTextResponse("", status_code=200)

    # Quiz state machine — check before normal brain flow
    quiz_state = _get_quiz_state(sender)
    if quiz_state:
        consumed = await _handle_quiz_state(sender, user_text, quiz_state)
        if consumed:
            return PlainTextResponse("", status_code=200)

    # Save transcript (fire and forget — don't block reply generation)
    asyncio.create_task(_save_transcript(user_text, is_voice_note))

    try:
        reply_text, _kg = await get_reply(user_text, sender)
    except Exception as e:
        logger.error("Brain call failed: %s", e)
        return PlainTextResponse("", status_code=200)

    # --- Delay queue disabled for testing (comment back in to restore delays) ---
    # schedule_reply(to_number=sender, reply_text=reply_text, send_as_voice=True)

    # Check if brain signalled end of mini session
    end_session = "[END_SESSION]" in reply_text
    clean_reply = reply_text.replace("[END_SESSION]", "").strip()

    # Send main reply immediately as voice note
    await _send_voice_now(sender, clean_reply)

    # If end of session, send a short closing message so user knows to come back later
    if end_session:
        closing = "I'll leave that with you. [pause] Come back whenever you're ready. 👋"
        await _send_voice_now(sender, closing)

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
                media_url = await generate_and_upload_audio(text)
                logger.info("Sending voice note via R2 URL: %s", media_url)
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
