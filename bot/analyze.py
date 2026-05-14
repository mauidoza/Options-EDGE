#!/usr/bin/env python3
"""
Crash Watch — weekly market crash-risk analyzer.

Pulls SPY / VIX / yield curve / credit / breadth / futures / news, computes
a composite 0-100 risk score, writes bot/report.json + bot/report.md, and
dispatches notifications.

Run locally:
    pip install -r bot/requirements.txt
    python bot/analyze.py

Environment (all optional):
    ANTHROPIC_API_KEY     enables LLM news synthesis
    ALERT_WEBHOOK_URL     Discord / Slack / ntfy / IFTTT / Zapier
    SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS SMTP_FROM SMTP_TO
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from signals import (  # noqa: E402
    price_trend_signal,
    vix_signal,
    yield_curve_signal,
    credit_breadth_signal,
    news_signal,
    futures_signal,
)
from notify import send_webhook, send_email  # noqa: E402


WEIGHTS = {
    "price_trend": 0.20,
    "vix": 0.20,
    "yield_curve": 0.15,
    "credit_breadth": 0.15,
    "futures": 0.05,
    "news": 0.25,
}


def zone_for(score: int) -> str:
    if score < 30:
        return "GREEN"
    if score < 50:
        return "YELLOW"
    if score < 70:
        return "ORANGE"
    return "RED"


def headline_for(zone: str, score: int) -> str:
    return {
        "GREEN":  f"Risk {score}/100 — market conditions calm. Stay invested, keep stops in place.",
        "YELLOW": f"Risk {score}/100 — early warning signals. Tighten stops, trim leveraged longs.",
        "ORANGE": f"Risk {score}/100 — material stress building. Reduce exposure, hedge with puts or VIX calls.",
        "RED":    f"Risk {score}/100 — crash signals firing. Move to defensive posture now.",
    }[zone]


def exit_checklist_for(zone: str, signals: list[dict]) -> list[str]:
    base = [
        "Re-check position sizes against current portfolio value",
        "Verify hard stops are active on every open position",
    ]
    if zone in ("YELLOW", "ORANGE", "RED"):
        base.append("Trim or close any position > 5% of portfolio")
        base.append("Roll long-dated calls to lower deltas or close")
    if zone in ("ORANGE", "RED"):
        base.append("Open SPY / QQQ put hedge (3-6 mo, 5-10% OTM)")
        base.append("Reduce gross long exposure by 30-50%")
        base.append("Raise cash to ≥ 25% of portfolio")
    if zone == "RED":
        base.append("Consider VIX calls or VXX calls for tail hedge")
        base.append("Close all naked short premium positions")
        base.append("Exit any margin / leveraged ETF holdings")

    # Signal-specific add-ons
    by_name = {s["name"]: s for s in signals}
    if by_name.get("yield_curve", {}).get("score", 0) > 70:
        base.append("Curve steepening from inversion — historical 6-18mo recession lead")
    if by_name.get("vix", {}).get("score", 0) > 70:
        base.append("VIX in stress regime — premium-sellers reduce size")
    if by_name.get("credit_breadth", {}).get("score", 0) > 70:
        base.append("Credit + breadth weak — rotate from junk/small to large/quality")
    return base


def build_report() -> dict:
    use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    anth_client = None
    if use_llm:
        try:
            from anthropic import Anthropic
            anth_client = Anthropic()
            print("[analyze] Anthropic client initialized")
        except Exception as e:
            print(f"[analyze] Anthropic init failed: {e}")
            use_llm = False

    signal_fns = {
        "price_trend":   lambda: price_trend_signal(),
        "vix":           lambda: vix_signal(),
        "yield_curve":   lambda: yield_curve_signal(),
        "credit_breadth": lambda: credit_breadth_signal(),
        "futures":       lambda: futures_signal(),
        "news":          lambda: news_signal(use_llm=use_llm, anthropic_client=anth_client),
    }

    signals: list[dict] = []
    weighted_total = 0.0
    weight_used = 0.0

    for name, fn in signal_fns.items():
        try:
            print(f"[analyze] computing {name}…")
            r = fn()
        except Exception as e:
            traceback.print_exc()
            r = {"name": name, "value": None, "score": 50,
                 "note": f"Error: {e}", "bullets": []}
        r.setdefault("name", name)
        signals.append(r)
        w = WEIGHTS.get(name, 0)
        weighted_total += r["score"] * w
        weight_used += w
        print(f"[analyze]   {name}: {r['score']}/100 — {r['note']}")

    risk_score = int(round(weighted_total / weight_used)) if weight_used else 50
    zone = zone_for(risk_score)

    report = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "risk_score": risk_score,
        "zone": zone,
        "headline": headline_for(zone, risk_score),
        "signals": signals,
        "weights": WEIGHTS,
        "exit_checklist": exit_checklist_for(zone, signals),
    }
    return report


def render_markdown(report: dict) -> str:
    z = report["zone"]
    emoji = {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}[z]
    lines = [
        f"# {emoji} Crash Watch — {report['date']}",
        "",
        f"**Risk score: {report['risk_score']} / 100 — {z}**",
        "",
        f"> {report['headline']}",
        "",
        "## Signals",
        "",
    ]
    for s in report["signals"]:
        lines.append(f"### {s['name']} — {s['score']}/100")
        lines.append("")
        lines.append(f"_{s['note']}_")
        lines.append("")
        for b in s.get("bullets", []):
            lines.append(f"- {b}")
        lines.append("")
    lines.append("## Exit checklist")
    lines.append("")
    for c in report["exit_checklist"]:
        lines.append(f"- [ ] {c}")
    lines.append("")
    lines.append(f"_Generated {report['generated_at']}_")
    return "\n".join(lines)


def main() -> int:
    report = build_report()

    out_json = ROOT / "report.json"
    out_md = ROOT / "report.md"
    out_json.write_text(json.dumps(report, indent=2))
    out_md.write_text(render_markdown(report))
    print(f"[analyze] wrote {out_json} ({report['zone']} {report['risk_score']}/100)")

    # Always notify when score crosses into YELLOW or higher, OR when explicitly
    # forced. To avoid spam during calm periods, GREEN updates skip webhook/email
    # unless ALWAYS_NOTIFY=1.
    always = os.environ.get("ALWAYS_NOTIFY") == "1"
    if always or report["zone"] != "GREEN":
        send_webhook(report)
        send_email(report)
    else:
        print("[analyze] zone GREEN, skipping notifications (set ALWAYS_NOTIFY=1 to force)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
