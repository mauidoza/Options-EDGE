# Crash Watch — passive market crash-risk bot

Weekly market-crash early-warning bot for **Options Edge**. Runs every Monday
pre-market via GitHub Actions, pulls SPY / VIX / yield curve / credit / breadth /
futures / news, computes a 0–100 composite risk score, writes
`bot/report.json` + `bot/report.md`, and pushes notifications to a webhook
(Discord / Slack / ntfy / etc.) and/or email/SMS.

The dashboard's **🚨 CRASH WATCH** tab reads `report.json` and shows a
traffic-light gauge, signal breakdown, and exit checklist. The browser also
fires a native push notification when risk crosses into YELLOW or above
(requires you to click "ENABLE" once to grant permission).

## How it scores

| Signal | Weight | What it looks at |
|--------|--------|------------------|
| `news` | 25% | Top-of-feed headlines from Reuters / CNBC / MarketWatch / FT / SeekingAlpha. Keyword-scored, plus optional Claude LLM synthesis if `ANTHROPIC_API_KEY` is set. |
| `price_trend` | 20% | SPY drawdown from 52-w high, distance from 50d / 200d MA, death-cross, 200d slope. |
| `vix` | 20% | VIX spot level + term structure (VIX9D/VIX, VIX/VIX3M). Backwardation = stress. |
| `yield_curve` | 15% | 10Y-3M spread + 60-day change. Detects "steepening from inversion" (the classic recession trigger). |
| `credit_breadth` | 15% | HYG/LQD ratio (HY-vs-IG credit) + RSP/SPY ratio (equal-weight vs cap-weight = breadth). |
| `futures` | 5% | ES front-month overnight direction. |

**Zones:**
- 🟢 0–29 **GREEN** — calm. Stay invested.
- 🟡 30–49 **YELLOW** — early warning. Tighten stops, trim leverage.
- 🟠 50–69 **ORANGE** — material stress. Hedge with puts, reduce gross.
- 🔴 70–100 **RED** — crash signals firing. Defensive posture.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r bot/requirements.txt
python bot/analyze.py
```

Output: `bot/report.json` (machine-readable) and `bot/report.md` (human-readable).
Open `index.html` in a browser and the Crash Watch tab will pick it up.

## Configure notifications (GitHub Secrets)

The weekly workflow is `.github/workflows/market-watch.yml`. Add any of these
secrets in **Settings → Secrets and variables → Actions**:

### Webhook (one URL, any service)
| Secret | Notes |
|--------|-------|
| `ALERT_WEBHOOK_URL` | Discord, Slack, [ntfy.sh](https://ntfy.sh), IFTTT, Zapier, etc. Auto-shapes the payload. |

**For phone push:** use [ntfy.sh](https://ntfy.sh) (free, install the app, pick
a topic name, your webhook is `https://ntfy.sh/your-topic-name`). Or a Discord
DM webhook — Discord's mobile app pushes those.

### Email / SMS-via-email
| Secret | Example |
|--------|---------|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS) or `465` (SSL) |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASS` | Gmail [App Password](https://myaccount.google.com/apppasswords) |
| `SMTP_FROM` | `you@gmail.com` |
| `SMTP_TO` | `you@gmail.com,5551234567@vtext.com` (comma-separated) |

**SMS via email-to-text gateways:**
- Verizon: `5551234567@vtext.com`
- AT&T: `5551234567@txt.att.net`
- T-Mobile: `5551234567@tmomail.net`
- Sprint: `5551234567@messaging.sprintpcs.com`

### LLM news synthesis (optional but recommended)
| Secret | Notes |
|--------|-------|
| `ANTHROPIC_API_KEY` | Enables Claude to read 50 headlines and produce a one-sentence risk read + themes. Falls back to keyword scoring if absent. |

## Schedule

Cron: `30 12 * * 1` → Mondays 12:30 UTC ≈ 7:30 AM ET, one hour before US cash
open. GitHub may delay a few minutes during peak hours; this is normal.

To run on-demand: **Actions → Crash Watch → Run workflow**. Toggle
**always_notify** to force a notification even in GREEN zone (useful for
testing).

## Notification policy

- **GitHub-side webhook + email:** fires only when zone is YELLOW or higher,
  to avoid weekly spam during calm markets. Set `always_notify=true` on
  workflow_dispatch to override.
- **Browser push:** fires every time the dashboard sees a new
  date/zone/score combination, but only when zone ≠ GREEN. Click ENABLE on
  the Crash Watch tab once to grant permission.

## Data sources

All public, no API keys required:
- **Prices/yields/futures:** Yahoo Finance via `yfinance`
- **News:** RSS from Reuters, CNBC, MarketWatch, FT, SeekingAlpha
- **LLM (optional):** Anthropic Claude

## ⚠️ Disclaimer

The risk score is a heuristic built from public signals — it is **not a
forecast** and is **not financial advice**. Markets can crash without warning
and stay calm despite obvious signals. Treat the output as one input among
many in your own process.
