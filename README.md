# MicroLearn

A personal WhatsApp bot that acts as a curious, intelligent friend — teaching you things from first principles via text or voice notes, with human-like reply delays.

## Stack

| Layer | Tech |
|---|---|
| API server | FastAPI + Uvicorn |
| WhatsApp | Twilio WhatsApp API |
| AI brain | Anthropic Claude (claude-sonnet-4-6) |
| Voice in | OpenAI Whisper |
| Voice out | OpenAI TTS |
| Queue | Upstash Redis |
| Knowledge | Markdown files in `/knowledge` |
| Deployment | Railway |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/songsbyarchit/microlearn.git
cd microlearn
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Set environment variables

Copy `.env.example` to `.env` and fill in every value:

```bash
cp .env.example .env
```

| Variable | Where to get it |
|---|---|
| `TWILIO_ACCOUNT_SID` | [console.twilio.com](https://console.twilio.com) → Account Info |
| `TWILIO_AUTH_TOKEN` | Same page |
| `TWILIO_WHATSAPP_NUMBER` | Twilio Console → Messaging → Senders → WhatsApp |
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |
| `UPSTASH_REDIS_REST_URL` | [console.upstash.com](https://console.upstash.com) → Redis → REST API |
| `UPSTASH_REDIS_REST_TOKEN` | Same page |
| `MY_WHATSAPP_NUMBER` | Your WhatsApp number in `whatsapp:+44XXXXXXXXXX` format |

### 3. Run locally

```bash
# Terminal 1 — API server
uvicorn main:app --reload

# Terminal 2 — expose with ngrok for Twilio webhook
ngrok http 8000
```

Point your Twilio WhatsApp sandbox webhook to:
```
https://<ngrok-id>.ngrok.io/webhook
```

### 4. Run the worker locally (optional, for testing)

```bash
python worker.py
```

In production Railway runs this every 60 seconds automatically.

---

## Deploy to Railway

1. Push this repo to GitHub.
2. Create a new Railway project → "Deploy from GitHub repo".
3. Add all environment variables in Railway's Variables tab.
4. Railway auto-detects `railway.json` and runs the cron worker every minute.
5. Copy your Railway public URL and set it as the Twilio webhook.

---

## Usage

Just message the bot on WhatsApp. It will reply within 2–120 minutes (log-normal, median ~25 min), only during waking hours (07:00–23:00 UK).

**Special commands**
- Send `reply` or `r` → skip the delay and receive the next queued reply immediately.

---

## Knowledge graph

After every exchange, Claude writes structured markdown to `/knowledge/`:

```
knowledge/
  _index.md            — master topic list
  physics/
    thermodynamics.md  — bloom level, edges, vocab, history
  ...
```

---

## Voice notes (V1 limitation)

TTS audio is generated but requires a public URL to be sent via Twilio WhatsApp. V1 falls back to text. To enable full voice notes:

1. Set up an S3 or Cloudflare R2 bucket.
2. Upload audio bytes in `worker.py` → get a public URL.
3. Pass the URL as `MediaUrl` in the Twilio API call.
