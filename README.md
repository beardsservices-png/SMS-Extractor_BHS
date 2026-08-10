# BHS SMS Lead Extractor

Receives SMS conversations forwarded from Brian's Android phone, extracts structured lead information using Claude AI, and pushes Lead Intake Cards to Brian's phone via ntfy — same format as the existing Bill callback notifications.

---

## How It Works

1. **SMS Forwarder** (Android app) forwards texts to this service's `/sms` webhook — both
   the ones Brian receives and (optionally) the ones he sends
2. Messages are accumulated per customer into a conversation thread (SQLite). Both sides of
   the conversation land in the same thread, keyed on the customer's number
3. After each new message **from the customer**, Claude analyzes the full thread and
   extracts lead fields as JSON
4. If meaningful new information was found (new address, scope, urgency, etc.), an ntfy push fires
5. After 4 hours of silence from a sender, a final "complete" card is sent automatically

### Why forward your own replies too

Customers answer questions instead of restating them. "142 Oak Ridge Rd" or a bare "yeah,
it's a rental" means nothing on its own — it only makes sense next to the question Brian
asked. With outgoing messages forwarded, the extractor sees both halves and can attribute
those answers correctly.

Brian's own messages never trigger a notification or an extraction of their own — he wrote
them, so a card about them would just be his own text read back to him. They're stored as
context for the next message the customer sends. His details are also never mistaken for
the customer's: an address or a time *he* offers doesn't become the customer's address or
availability.

---

## Android SMS Forwarder Setup

1. Install **[SMS Forwarder](https://play.google.com/store/apps/details?id=com.frzinapps.smsforwarder)** on Brian's Android phone
2. Open the app → tap **+** to add a new rule
3. Set **Delivery method** → **HTTP**
4. Set **URL**:
   ```
   https://YOUR-RAILWAY-URL.railway.app/sms?token=YOUR_WEBHOOK_SECRET
   ```
5. Set **Method** → **POST**
6. Set **Content-Type** → **application/json**
7. Set the **body template** to exactly:
   ```json
   {"from":"{{from}}","message":"{{message}}","sentStamp":"{{sentStamp}}","receivedStamp":"{{receivedStamp}}"}
   ```
8. Tap **Test** to send a test message, then enable the rule

### Second rule: forwarding Brian's own replies

Add a **separate rule** for sent/outgoing messages, configured the same way as above with
one change — add `&direction=sent` to the end of the URL:

```
https://YOUR-RAILWAY-URL.railway.app/sms?token=YOUR_WEBHOOK_SECRET&direction=sent
```

That literal `direction=sent` is what tells the service the message came from Brian. It's
hardcoded text rather than a `{{token}}`, so unlike the body template it cannot fail to
resolve.

**Identifying who the reply went to.** The service needs the customer's number to file the
reply in the right thread, and tries three things in order:

1. A recipient field in the payload — `to`, `recipient`, `destination` or `address`. If the
   app offers a `{{to}}`-style token on sent messages, add it to the URL as `&to={{to}}`.
2. The `from` field, when it isn't Brian's own number — some forwarders put the *other*
   party there on sent messages.
3. Nothing usable → the message is dropped and an error is logged naming the fields that
   did arrive, so the mapping can be corrected.

Set the `OWNER_PHONE` environment variable to Brian's mobile number. It lets the service
recognize his number and skip past it when hunting for the customer's, and it acts as a
backup direction signal if `&direction=sent` is ever missing from a rule.

Unresolved template tokens are ignored everywhere — if a field arrives as the literal text
`{{to}}`, it's treated as absent rather than used as a phone number.

### Which contacts to whitelist / filter

You want to forward unknown/customer texts but **not** personal contacts. Two options:

**Option A — Exclude known contacts (recommended)**  
In SMS Forwarder, add a filter: skip forwarding if the sender is in your phone contacts. This passes through all unknown numbers automatically.

**Option B — Whitelist by area code**  
Forward only messages from `870` and `417` area codes (Mountain Home market). Works well if most of your leads are local.

**Option C — Manual trigger only**  
Don't use auto-forwarding at all. Just hit the `/sms/extract/{phone}` endpoint manually after any conversation you want processed.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sms?token=SECRET` | SMS Forwarder webhook — receives forwarded texts |
| `POST` | `/sms/extract/{phone}?token=SECRET` | Force immediate extraction for an active thread |
| `GET` | `/sms/lockbox/{phone}?token=SECRET` | Retrieve a stored lockbox code |
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |

---

## Manual Extraction Trigger

After reading a text thread that looks like a lead, hit this to get an immediate ntfy card:

```
POST https://YOUR-URL.railway.app/sms/extract/+18705551234?token=YOUR_SECRET
```

You can bookmark this in your browser, save it as a shortcut, or use an app like **HTTP Shortcuts** on Android to fire it with one tap.

---

## Retrieving a Lockbox Code

Lockbox codes are **never** shown in ntfy notifications (they appear on your lock screen). To get the code:

```
GET https://YOUR-URL.railway.app/sms/lockbox/+18705551234?token=YOUR_SECRET
```

Returns:
```json
{"phone": "+18705551234", "lockbox_code": "2847"}
```

---

## ntfy Notification Format

Notifications match the style of the existing Bill call notifications. Example Lead Intake Card:

```
Title:    📋 New Lead — Realtor Referral — Jane Doe
Priority: default (moderate urgency)
Tags:     sms, lead, realtor_referral

Body:
  ☎️ Phone: +18705551234
  📍 Property: 142 Oak Ridge Rd, Mountain Home AR
  🏠 Type: Sale
  🔧 Scope: Fence repair — sagging section, two rotted posts in backyard
  📅 Available: Weekends / after 3pm weekdays
  ⚡ Urgency: Moderate
  🤝 Realtor: Sarah Mills
     Realtor phone: +18705559876
  🔑 Lockbox: [PRESENT — check app]
  📊 Confidence: High
```

**Priority mapping:**
- `urgent` → max (red — emergency)
- `high` → high (orange)
- `moderate` → default
- `low` → low

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key from console.anthropic.com |
| `NTFY_TOPIC` | Yes | Your ntfy topic (same as Bill's: `beards-bhs-calls-8703`) |
| `NTFY_URL` | No | ntfy server URL — default: `https://ntfy.sh` |
| `NTFY_TOKEN` | No | Bearer token if your ntfy topic requires auth |
| `WEBHOOK_SECRET` | No | Token appended as `?token=` to protect the `/sms` endpoint |
| `OWNER_PHONE` | No | Brian's mobile number — identifies forwarded outgoing texts so replies join the customer's thread. Any format. |
| `THREAD_TTL_HOURS` | No | Hours of silence before thread completion (default: `4`) |
| `PORT` | No | Railway sets this automatically |
| `DATABASE_PATH` | No | SQLite path — default: `./bhs_sms.db` |

---

## Railway Deployment

1. Push this repo to GitHub under `beardsservices-png`:
   ```bash
   git init
   git add .
   git commit -m "Initial BHS SMS lead extractor"
   git remote add origin https://github.com/beardsservices-png/bhs-sms-lead-extractor.git
   git push -u origin main
   ```

2. Railway dashboard → **New Project** → **Deploy from GitHub repo** → select `bhs-sms-lead-extractor`

3. Add environment variables under **Variables** tab (copy from `.env.example`)

4. (Recommended) Add a **persistent volume** mounted at `/data/` and set `DATABASE_PATH=/data/bhs_sms.db` so the SQLite database survives redeploys

5. **Settings → Networking → Generate Domain** — copy the Railway URL and put it in SMS Forwarder

---

## Local Development

```bash
cd bhs-sms-lead-extractor
pip install -r requirements.txt
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, NTFY_TOPIC at minimum
python main.py
```

Test with curl:
```bash
curl -X POST "http://localhost:8000/sms?token=YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"from":"+18705551234","message":"Hi, got your number from a neighbor. Need fence work at 142 Oak Ridge Rd — two posts are rotted.","sentStamp":1717430000000}'
```
