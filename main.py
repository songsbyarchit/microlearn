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
from fastapi.responses import HTMLResponse, PlainTextResponse

from brain import get_reply
from delay_queue import pop_next_reply, schedule_reply
from knowledge_graph import get_all_topics, get_knowledge_index_markdown
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
# D3 knowledge graph HTML template
# Placeholder __GRAPH_DATA__ is replaced at request time with JSON.
# ---------------------------------------------------------------------------
_KNOWLEDGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MicroLearn</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f172a; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
  #header { padding: 16px 24px; border-bottom: 1px solid #1e293b; display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }
  #header h1 { font-size: 18px; font-weight: 700; color: #f8fafc; }
  .stat { font-size: 13px; color: #94a3b8; }
  .stat strong { color: #e2e8f0; }
  #container { display: flex; height: calc(100vh - 56px); }
  #graph-wrap { flex: 1; overflow: hidden; position: relative; }
  #graph-wrap svg { width: 100%; height: 100%; }
  #panel { width: 340px; border-left: 1px solid #1e293b; overflow-y: auto; padding: 20px; display: none; flex-direction: column; gap: 12px; background: #0f172a; }
  #panel.open { display: flex; }
  #panel-close { align-self: flex-end; background: none; border: none; color: #64748b; cursor: pointer; font-size: 18px; }
  #panel h2 { font-size: 16px; font-weight: 600; color: #f8fafc; }
  #panel .meta { font-size: 12px; color: #64748b; }
  #panel .domain-badge { display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
  #panel .bloom-bar { height: 4px; border-radius: 2px; background: #1e293b; margin: 4px 0 8px; }
  #panel .bloom-fill { height: 100%; border-radius: 2px; background: #3b82f6; }
  #panel pre { font-size: 11px; color: #94a3b8; white-space: pre-wrap; line-height: 1.6; background: #1e293b; padding: 12px; border-radius: 6px; }
  .node circle { cursor: pointer; stroke: #0f172a; stroke-width: 2; transition: opacity 0.15s; }
  .node circle:hover { stroke: #fff; stroke-width: 2.5; }
  .node text { pointer-events: none; fill: #cbd5e1; font-size: 10px; }
  .link { stroke-opacity: 0.4; }
  #timeline { border-top: 1px solid #1e293b; padding: 12px 24px; display: flex; gap: 16px; overflow-x: auto; flex-shrink: 0; }
  .tl-item { flex-shrink: 0; display: flex; flex-direction: column; gap: 2px; }
  .tl-topic { font-size: 12px; font-weight: 500; color: #e2e8f0; }
  .tl-meta { font-size: 11px; color: #64748b; }
  #empty { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); text-align: center; color: #475569; }
  @media (max-width: 640px) {
    #container { flex-direction: column; }
    #panel { width: 100%; height: 50vh; border-left: none; border-top: 1px solid #1e293b; }
    #graph-wrap { height: 50vh; }
  }
</style>
</head>
<body>
<div id="header">
  <h1>MicroLearn</h1>
  <div class="stat">Topics: <strong id="s-topics">0</strong></div>
  <div class="stat">Domains: <strong id="s-domains">0</strong></div>
  <div class="stat">Last updated: <strong id="s-last">-</strong></div>
</div>
<div id="container">
  <div id="graph-wrap">
    <div id="empty" style="display:none">No topics yet. Start a conversation!</div>
  </div>
  <div id="panel">
    <button id="panel-close">&#x2715;</button>
    <h2 id="panel-title"></h2>
    <div style="display:flex;align-items:center;gap:8px">
      <span id="panel-badge" class="domain-badge"></span>
      <span id="panel-bloom" class="meta"></span>
    </div>
    <div class="bloom-bar"><div class="bloom-fill" id="panel-bloom-bar"></div></div>
    <p id="panel-summary" style="font-size:13px;color:#94a3b8;line-height:1.6"></p>
    <pre id="panel-content"></pre>
  </div>
</div>
<div id="timeline"></div>

<script>
const RAW = __GRAPH_DATA__;

const DOMAIN_COLOURS = {
  physics:      '#3b82f6',
  chemistry:    '#10b981',
  mathematics:  '#8b5cf6',
  maths:        '#8b5cf6',
  cooking:      '#f97316',
  food:         '#f97316',
  history:      '#a16207',
  biology:      '#14b8a6',
  computing:    '#ef4444',
  programming:  '#ef4444',
  economics:    '#eab308',
  music:        '#ec4899',
  philosophy:   '#6366f1',
  general:      '#6b7280',
};
const PALETTE = ['#06b6d4','#84cc16','#f43f5e','#a78bfa','#fb923c','#22d3ee'];
let paletteIdx = 0;
const domainColourCache = {};
function domainColour(d) {
  const k = d.toLowerCase();
  if (DOMAIN_COLOURS[k]) return DOMAIN_COLOURS[k];
  if (!domainColourCache[k]) { domainColourCache[k] = PALETTE[paletteIdx++ % PALETTE.length]; }
  return domainColourCache[k];
}

// Build node map
const nodeById = {};
RAW.nodes.forEach(n => { nodeById[n.id] = n; });

// Resolve links (skip if target node doesn't exist)
const links = [];
RAW.nodes.forEach(n => {
  (n.edges || []).forEach(e => {
    const targetId = RAW.nodes.find(x => x.topic === e.target || x.title.toLowerCase() === e.target.toLowerCase());
    if (targetId && targetId.id !== n.id) {
      links.push({ source: n.id, target: targetId.id, type: e.type || 'related' });
    }
  });
});

// Stats
document.getElementById('s-topics').textContent = RAW.nodes.length;
const domains = [...new Set(RAW.nodes.map(n => n.domain))];
document.getElementById('s-domains').textContent = domains.length;
if (RAW.nodes.length) {
  const last = RAW.nodes.reduce((a, b) => a.last_updated > b.last_updated ? a : b);
  document.getElementById('s-last').textContent = last.last_updated.slice(0, 10);
}

// Timeline (last 8 by updated_at)
const sorted = [...RAW.nodes].sort((a, b) => b.last_updated.localeCompare(a.last_updated)).slice(0, 8);
const tl = document.getElementById('timeline');
sorted.forEach(n => {
  const el = document.createElement('div');
  el.className = 'tl-item';
  el.innerHTML = `<div class="tl-topic" style="color:${domainColour(n.domain)}">${n.title}</div><div class="tl-meta">${n.domain} &middot; bloom ${n.bloom_level} &middot; ${n.last_updated.slice(0,10)}</div>`;
  tl.appendChild(el);
});

if (!RAW.nodes.length) {
  document.getElementById('empty').style.display = 'block';
}

// D3 force graph
const wrap = document.getElementById('graph-wrap');
const W = wrap.clientWidth || 800;
const H = wrap.clientHeight || 600;

const svg = d3.select('#graph-wrap').append('svg')
  .attr('viewBox', `0 0 ${W} ${H}`)
  .attr('preserveAspectRatio', 'xMidYMid meet');

const g = svg.append('g');

svg.call(d3.zoom().scaleExtent([0.2, 4]).on('zoom', e => g.attr('transform', e.transform)));

const nodes = RAW.nodes.map(n => ({ ...n, r: 8 + n.bloom_level * 2 }));

const simulation = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(links).id(d => d.id).distance(90).strength(0.4))
  .force('charge', d3.forceManyBody().strength(-220))
  .force('center', d3.forceCenter(W / 2, H / 2))
  .force('collide', d3.forceCollide().radius(d => d.r + 8));

const link = g.append('g').selectAll('line')
  .data(links).join('line')
  .attr('class', 'link')
  .attr('stroke', d => d.type === 'conflicting' ? '#ef4444' : '#334155')
  .attr('stroke-width', d => d.type === 'prerequisite' ? 2 : 1.5);

function drag(sim) {
  return d3.drag()
    .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; });
}

const node = g.append('g').selectAll('g')
  .data(nodes).join('g')
  .attr('class', 'node')
  .call(drag(simulation))
  .on('click', (e, d) => { e.stopPropagation(); openPanel(d); });

node.append('circle')
  .attr('r', d => d.r)
  .attr('fill', d => domainColour(d.domain))
  .attr('fill-opacity', 0.85);

node.append('text')
  .text(d => d.title.length > 16 ? d.title.slice(0, 14) + '..' : d.title)
  .attr('text-anchor', 'middle')
  .attr('dy', d => d.r + 13)
  .style('font-size', '10px');

svg.on('click', () => closePanel());

simulation.on('tick', () => {
  link
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  node.attr('transform', d => `translate(${d.x},${d.y})`);
});

// Side panel
const panel = document.getElementById('panel');
function openPanel(d) {
  document.getElementById('panel-title').textContent = d.title;
  const badge = document.getElementById('panel-badge');
  badge.textContent = d.domain;
  badge.style.background = domainColour(d.domain) + '33';
  badge.style.color = domainColour(d.domain);
  document.getElementById('panel-bloom').textContent = `Bloom level ${d.bloom_level} / 8`;
  document.getElementById('panel-bloom-bar').style.width = `${(d.bloom_level / 8) * 100}%`;
  document.getElementById('panel-summary').textContent = d.summary;
  document.getElementById('panel-content').textContent = d.raw || '';
  panel.classList.add('open');
}
function closePanel() { panel.classList.remove('open'); }
document.getElementById('panel-close').addEventListener('click', closePanel);
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
# Knowledge graph dashboard
# ---------------------------------------------------------------------------

@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_viewer():
    """D3.js force-layout knowledge graph dashboard."""
    topics = get_all_topics()

    graph_data = json.dumps({
        "nodes": [
            {
                "id": t["id"],
                "title": t["title"],
                "topic": t["topic"],
                "domain": t["domain"],
                "bloom_level": t["bloom_level"],
                "last_updated": t["last_updated"],
                "summary": t["summary"],
                "raw": t["raw"],
                "edges": t["edges"],
            }
            for t in topics
        ]
    })

    html = _KNOWLEDGE_HTML.replace("__GRAPH_DATA__", graph_data)
    return HTMLResponse(content=html)


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
    # This must happen BEFORE the immediate-command check so that transcribed
    # voice notes containing the word "reply" never accidentally trigger it.
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
