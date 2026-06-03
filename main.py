"""
main.py — FastAPI app: Twilio webhook + D3.js knowledge graph dashboard.
"""
import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from brain import get_reply
from delay_queue import pop_next_reply, schedule_reply
from knowledge_graph import get_all_topics
from supabase_client import ensure_table_exists
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

async def _send_reaction(to: str, emoji: str) -> None:
    """Send a single emoji as a quick acknowledgement message."""
    messages_url = f"https://api.twilio.com/2010-04-01/Accounts/{os.environ['TWILIO_ACCOUNT_SID']}/Messages.json"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                messages_url,
                data={
                    "From": os.environ["TWILIO_WHATSAPP_NUMBER"],
                    "To": to,
                    "Body": emoji,
                },
                auth=(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]),
                timeout=10,
            )
        logger.info("Sent reaction %s to %s", emoji, to)
    except Exception as e:
        logger.warning("Failed to send reaction %s: %s", emoji, e)


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

    # 👀 — seen, thinking
    await _send_reaction(sender, "👀")

    try:
        reply_text, _kg = await get_reply(user_text)
    except Exception as e:
        logger.error("Brain call failed: %s", e)
        return PlainTextResponse("", status_code=200)

    schedule_reply(to_number=sender, reply_text=reply_text, send_as_voice=True)

    # ✅ — reply queued
    await _send_reaction(sender, "✅")

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
