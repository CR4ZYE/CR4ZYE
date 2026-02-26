# Crazy — Local AI Executive Assistant (Free Tier)

Crazy is a local-first automation assistant that monitors **WhatsApp + Gmail + Outlook** every 15 minutes, finds action items, prioritizes work, and generates concise executive summaries.

- **Wake word / agent name:** `Crazy`
- **Cost target:** free tools and free API tiers only
- **Runs locally:** on your own computer

---

## What Crazy does

Crazy continuously detects:

- Work to be done
- Replies required
- Pending tasks and follow-ups
- Urgent items vs can-wait items
- Deadlines (especially within 48 hours)

It also supports WhatsApp command messages (with automatic WhatsApp reply):

- `STATUS` → returns current work status report
- `STOP ASSISTANT` → pauses monitoring
- `START ASSISTANT` → resumes monitoring

---

## Features implemented

- 15-minute auto scan schedule
- Daily summary trigger at 9:00 AM (auto-delivered to WhatsApp owner chat)
- Gmail Inbox/Unread/Important/Starred monitoring (Gmail API)
- Outlook Inbox/Unread/Flagged/Importance monitoring (Microsoft Graph)
- WhatsApp local monitoring (Baileys bridge, no paid API)
- Task extraction with executive-priority rules
- Anti-spam filtering (OTP/promo/social/bank alerts)
- Duplicate task prevention using deterministic task IDs
- Local SQLite storage (no cloud dependency)
- Learning mode contact statistics (frequent/work contacts)
- Failsafe retries with connection failure logging

---

## 1) Software required (simple)

Install these first:

1. **Python 3.11+**
2. **Node.js 20+**
3. **Git**
4. (Optional) VS Code

No Docker required for this version.

---

## 2) Installation steps (exact commands)

Open terminal:

```bash
git clone <your-repo-url>
cd CR4ZYE
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
npm install
cp .env.example .env
mkdir -p secrets data
# set CRAZY_OWNER_CHAT_ID in .env after first WhatsApp message event appears
```

---

## 3) API keys / credentials (free only)

### A) Gmail API (free)

1. Go to Google Cloud Console.
2. Create a project.
3. Enable **Gmail API**.
4. Configure OAuth consent screen as External (testing mode is fine).
5. Create OAuth Client ID → **Desktop app**.
6. Download JSON credentials.
7. Save file as:
   - `secrets/gmail_credentials.json`

First run will open browser for local OAuth and generate:
- `secrets/gmail_token.json`

### B) Microsoft Graph API (free)

1. Go to Azure Portal → App registrations → New registration.
2. Create app (single tenant or personal + org accounts is okay).
3. Copy **Application (client) ID**.
4. API Permissions → Microsoft Graph → Delegated → `Mail.Read`.
5. In `.env`, set:
   - `CRAZY_MS_CLIENT_ID=<your_client_id>`
   - `CRAZY_MS_TENANT_ID=common`

On first run, device code login will be shown in terminal.

### C) WhatsApp connection (free, local)

No paid key required.

- Uses Baileys library with QR login.
- Session data stored in `secrets/wa_auth`.

---

## 4) Account connection (step-by-step)

### Connect WhatsApp

```bash
npm run whatsapp:bridge
```

- A QR login prompt appears in terminal.
- Open WhatsApp on your phone → Linked Devices → Link a device.
- After successful scan, events are stored in `data/whatsapp_events.jsonl`.

### Connect Gmail and Outlook

In a second terminal:

```bash
source .venv/bin/activate  # Windows equivalent
python run_crazy.py
```

- Gmail browser OAuth opens once.
- Outlook device code appears once (follow terminal instructions).

After that, credentials are cached locally.

### Set owner chat for daily summary

1. Send any message to your linked WhatsApp account.
2. Open `data/whatsapp_events.jsonl` and copy the `chatId`.
3. Put it in `.env` as `CRAZY_OWNER_CHAT_ID=<that_chatId>`.
4. Restart both processes.

---

## 5) Running the assistant

### Start (2 terminals)

Terminal 1:
```bash
npm run whatsapp:bridge
```

Terminal 2:
```bash
source .venv/bin/activate
python run_crazy.py
```

### Stop

- Press `Ctrl + C` in both terminals.
- Or send WhatsApp message `STOP ASSISTANT` to pause scan logic.
- Crazy will send confirmations back on WhatsApp for STOP/START/STATUS.

### Resume

- Send `START ASSISTANT` on WhatsApp.

---

## 6) Testing checklist (simple)

1. Send yourself WhatsApp message: `Please send courier today`.
2. Send email in Gmail/Outlook with a request and deadline.
3. Wait for scan cycle (or restart app to force immediate scan).
4. Send WhatsApp message: `STATUS`.
5. Confirm generated report includes urgent/reply/follow-up buckets.

---

## 7) Cost requirements

This setup is designed for **FREE usage**:

- Gmail API: free tier for normal personal use
- Microsoft Graph delegated personal usage: free
- WhatsApp via local Baileys: free
- Local SQLite DB: free

No paid LLM API is required in current rules-based version.

---

## Security hardening built in

- OAuth tokens stored locally in `secrets/` (not hardcoded)
- SQL operations use parameterized queries
- Outbox delivery uses idempotent message IDs to avoid duplicate sends
- OAuth cache/token files are written with restricted permissions where possible
- No dangerous shell execution from messages
- Anti-noise filtering to reduce malicious content processing
- Minimal API permissions (`gmail.readonly`, `Mail.Read`)
- Local-only data persistence by default

> Important: Add `secrets/` and `.env` to `.gitignore` in your private fork before publishing.

---

## Output format used by Crazy

```text
WORK STATUS

URGENT
• ...

REPLIES NEEDED
• ...

FOLLOW-UP
• ...

END REPORT
```

---

## Notes

- This project uses deterministic heuristics, not paid AI APIs.
- You can later plug in a local LLM (Ollama) if you want deeper semantic extraction.
