"""
Notification dispatchers. Each is a no-op if the relevant secret/env var is
unset, so the bot runs fine with zero notification channels configured.

Channels:
  - Generic webhook (Discord, Slack, ntfy.sh, IFTTT, Zapier, etc.)
  - SMTP email (Gmail app password, etc.)
  - Browser push: handled client-side by index.html via Notification API +
    diffing report.json against localStorage; nothing to do server-side.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests


def _zone_emoji(zone: str) -> str:
    return {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}.get(zone, "⚪")


def _short_text(report: dict) -> str:
    z = report["zone"]
    s = report["risk_score"]
    return f"{_zone_emoji(z)} CRASH WATCH — Risk {s}/100 ({z})\n{report['headline']}"


def send_webhook(report: dict) -> None:
    """
    Sends a single POST to ALERT_WEBHOOK_URL. Auto-shapes the payload for
    Discord (if URL contains 'discord'), Slack (if 'slack'), or generic.
    """
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        print("[notify] ALERT_WEBHOOK_URL not set, skipping webhook")
        return

    text = _short_text(report)
    details = "\n".join(
        f"• {s['name']}: {s['score']}/100 — {s['note']}"
        for s in report["signals"]
    )

    if "discord.com" in url or "discordapp.com" in url:
        payload = {
            "username": "Crash Watch",
            "content": text + "\n\n" + details + f"\n\nExit checklist:\n" +
                       "\n".join(f"☐ {c}" for c in report["exit_checklist"]),
        }
    elif "slack.com" in url or "hooks.slack" in url:
        payload = {"text": text + "\n```" + details + "```"}
    else:
        # Generic — works with ntfy.sh, IFTTT, Zapier, custom endpoints
        payload = {
            "title": f"Crash Watch {report['zone']} — {report['risk_score']}/100",
            "message": text + "\n\n" + details,
            "priority": "high" if report["zone"] in ("RED", "ORANGE") else "default",
            "tags": ["chart_with_downwards_trend", report["zone"].lower()],
            "data": report,
        }

    try:
        r = requests.post(url, json=payload, timeout=20)
        r.raise_for_status()
        print(f"[notify] webhook OK ({r.status_code})")
    except Exception as e:
        print(f"[notify] webhook FAILED: {e}")


def send_email(report: dict) -> None:
    """
    Sends an email via SMTP. Requires:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_TO
    SMTP_TO can be comma-separated. For Gmail use an App Password.

    For phone SMS, set SMTP_TO to your carrier's email-to-SMS gateway, e.g.
      5551234567@vtext.com   (Verizon)
      5551234567@txt.att.net (AT&T)
      5551234567@tmomail.net (T-Mobile)
    """
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        print("[notify] SMTP_HOST not set, skipping email")
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pw = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM", user)
    to = [a.strip() for a in os.environ.get("SMTP_TO", "").split(",") if a.strip()]

    if not to:
        print("[notify] SMTP_TO empty, skipping email")
        return

    msg = EmailMessage()
    msg["Subject"] = f"[Crash Watch {report['zone']}] Risk {report['risk_score']}/100 — {report['date']}"
    msg["From"] = sender
    msg["To"] = ", ".join(to)

    body_lines = [_short_text(report), ""]
    body_lines.append("Signals:")
    for s in report["signals"]:
        body_lines.append(f"  - {s['name']}: {s['score']}/100 — {s['note']}")
        for b in s.get("bullets", []):
            body_lines.append(f"      · {b}")
    body_lines.append("")
    body_lines.append("Exit checklist:")
    for c in report["exit_checklist"]:
        body_lines.append(f"  [ ] {c}")
    body_lines.append("")
    body_lines.append("— Options Edge / Crash Watch (generated automatically)")
    msg.set_content("\n".join(body_lines))

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ctx)
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        print(f"[notify] email OK -> {to}")
    except Exception as e:
        print(f"[notify] email FAILED: {e}")
