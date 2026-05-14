"""
Signal calculators for the market crash watch bot.

Each function returns a dict with at least:
  - value: the raw numeric reading (or None on failure)
  - score: 0-100 sub-score where higher = more crash risk
  - note:  one-line human explanation
  - bullets: list of supporting data points
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf


def _fetch(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
            )
            if df is not None and len(df) > 0:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return df
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def _last(series: pd.Series) -> float | None:
    s = series.dropna()
    if len(s) == 0:
        return None
    return float(s.iloc[-1])


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100.0


# ---------------------------------------------------------------------------
# 1. PRICE / TREND  — SPY drawdown + moving averages
# ---------------------------------------------------------------------------
def price_trend_signal() -> dict:
    df = _fetch("SPY", period="2y")
    if df is None or len(df) < 210:
        return {"name": "price_trend", "value": None, "score": 50,
                "note": "SPY data unavailable", "bullets": []}

    close = df["Close"]
    last = _last(close)
    high_52w = float(close.tail(252).max())
    drawdown = _pct(last, high_52w) or 0.0

    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    ma200_30d_ago = float(close.rolling(200).mean().iloc[-30])

    pct_vs_50 = _pct(last, ma50) or 0.0
    pct_vs_200 = _pct(last, ma200) or 0.0
    death_cross = ma50 < ma200
    ma200_falling = ma200 < ma200_30d_ago

    # Sub-scores
    # Drawdown:  0% = 0,  -5% = 25,  -10% = 50,  -20% = 100
    dd_score = min(100, max(0, abs(min(drawdown, 0)) * 5))
    # Below 200d MA
    below200_score = 0 if pct_vs_200 >= 0 else min(100, abs(pct_vs_200) * 10)
    # Death cross / falling 200d
    structure_score = 0
    if death_cross:
        structure_score += 40
    if ma200_falling:
        structure_score += 30
    if pct_vs_50 < 0:
        structure_score += 20
    structure_score = min(100, structure_score)

    score = round(0.5 * dd_score + 0.3 * below200_score + 0.2 * structure_score)

    note = (
        f"SPY {last:.2f}, {drawdown:+.1f}% from 52-w high, "
        f"{pct_vs_200:+.1f}% vs 200d MA"
        + (", death cross active" if death_cross else "")
    )

    return {
        "name": "price_trend",
        "value": last,
        "score": int(score),
        "note": note,
        "bullets": [
            f"SPY close: ${last:.2f}",
            f"52-week high: ${high_52w:.2f} ({drawdown:+.1f}%)",
            f"50d MA: ${ma50:.2f} ({pct_vs_50:+.1f}%)",
            f"200d MA: ${ma200:.2f} ({pct_vs_200:+.1f}%){' — FALLING' if ma200_falling else ''}",
            "Death cross (50<200): " + ("YES" if death_cross else "no"),
        ],
    }


# ---------------------------------------------------------------------------
# 2. VIX  — spot level + term structure (VIX9D / VIX, VIX / VIX3M)
# ---------------------------------------------------------------------------
def vix_signal() -> dict:
    vix = _fetch("^VIX", period="1y")
    vix9d = _fetch("^VIX9D", period="1y")
    vix3m = _fetch("^VIX3M", period="1y")

    if vix is None:
        return {"name": "vix", "value": None, "score": 50,
                "note": "VIX data unavailable", "bullets": []}

    spot = _last(vix["Close"])
    spot_avg = float(vix["Close"].tail(60).mean())

    s9 = _last(vix9d["Close"]) if vix9d is not None else None
    s3m = _last(vix3m["Close"]) if vix3m is not None else None

    # VIX spot:  <15 calm, 15-20 normal, 20-25 elevated, 25-30 stressed, >30 panic
    if spot < 15:
        spot_score = 5
    elif spot < 20:
        spot_score = 25
    elif spot < 25:
        spot_score = 50
    elif spot < 30:
        spot_score = 75
    else:
        spot_score = 95

    # Backwardation: short-term > long-term => stress
    backwardation = False
    ts_score = 30  # neutral baseline
    bullets = [f"VIX spot: {spot:.2f} (60d avg {spot_avg:.1f})"]
    if s9 is not None and spot:
        r9 = s9 / spot
        bullets.append(f"VIX9D/VIX: {r9:.2f}")
        if r9 > 1.05:
            ts_score = max(ts_score, 80)
            backwardation = True
    if s3m is not None and spot:
        r3m = spot / s3m
        bullets.append(f"VIX/VIX3M: {r3m:.2f}")
        if r3m > 1.0:
            ts_score = max(ts_score, 70)
            backwardation = True
        elif r3m < 0.85:
            ts_score = 10  # deep contango = complacent (not crash, but unsustainable)

    rising = spot > spot_avg * 1.2
    if rising:
        ts_score = max(ts_score, 60)
        bullets.append("VIX 20%+ above 60d average")

    score = round(0.6 * spot_score + 0.4 * ts_score)
    note = f"VIX {spot:.1f}" + (" (backwardation — stress)" if backwardation else "")

    return {
        "name": "vix",
        "value": spot,
        "score": int(score),
        "note": note,
        "bullets": bullets,
    }


# ---------------------------------------------------------------------------
# 3. YIELD CURVE  — 10Y-2Y and 10Y-3M, both depth and steepening from inversion
# ---------------------------------------------------------------------------
def yield_curve_signal() -> dict:
    ten = _fetch("^TNX", period="2y")
    two = _fetch("^IRX", period="2y")  # ^IRX = 13-week T-bill (proxy for short end)
    fvx = _fetch("^FVX", period="2y")  # 5Y

    if ten is None or two is None:
        return {"name": "yield_curve", "value": None, "score": 50,
                "note": "Yield curve data unavailable", "bullets": []}

    # Yahoo serves these as yield * 10 in some cases — ^TNX is yield in percent.
    t10 = _last(ten["Close"])
    t3m = _last(two["Close"])  # IRX is the 3-month
    t5 = _last(fvx["Close"]) if fvx is not None else None

    spread_10y_3m = t10 - t3m
    bullets = [
        f"10Y: {t10:.2f}%",
        f"3M: {t3m:.2f}%",
        f"10Y-3M spread: {spread_10y_3m:+.2f}%",
    ]
    if t5 is not None:
        bullets.append(f"5Y: {t5:.2f}%")

    # Inversion depth (negative spread) is danger; steepening FROM inversion is
    # the recession trigger. Compute change over last 60d.
    spread_series = (ten["Close"] - two["Close"]).dropna()
    spread_60d_ago = float(spread_series.iloc[-60]) if len(spread_series) > 60 else spread_series.iloc[0]
    steepening = spread_10y_3m - spread_60d_ago

    if spread_10y_3m < -0.5:
        depth_score = 70
    elif spread_10y_3m < 0:
        depth_score = 50
    elif spread_10y_3m < 0.5:
        depth_score = 30
    else:
        depth_score = 10

    # If we WERE inverted and are now steepening fast, that's the historical
    # recession trigger.
    trigger_score = 0
    if spread_60d_ago < 0 and steepening > 0.5:
        trigger_score = 90
        bullets.append("Curve steepening from inversion — historical recession trigger")
    elif spread_60d_ago < 0 and steepening > 0.2:
        trigger_score = 60

    score = max(depth_score, trigger_score)
    note = f"10Y-3M {spread_10y_3m:+.2f}% (60d Δ {steepening:+.2f})"

    return {
        "name": "yield_curve",
        "value": spread_10y_3m,
        "score": int(score),
        "note": note,
        "bullets": bullets,
    }


# ---------------------------------------------------------------------------
# 4. CREDIT + BREADTH  — HYG/LQD spread proxy + % SPY constituents > 200d
# ---------------------------------------------------------------------------
def credit_breadth_signal() -> dict:
    hyg = _fetch("HYG", period="1y")
    lqd = _fetch("LQD", period="1y")
    rsp = _fetch("RSP", period="1y")  # equal-weight S&P as breadth proxy
    spy = _fetch("SPY", period="1y")

    bullets = []
    credit_score = 30
    breadth_score = 30

    if hyg is not None and lqd is not None:
        # HYG/LQD ratio: falling = HY underperforming IG = credit stress
        ratio = hyg["Close"] / lqd["Close"]
        r_now = float(ratio.iloc[-1])
        r_high = float(ratio.tail(252).max())
        deterioration = (r_now - r_high) / r_high * 100
        bullets.append(f"HYG/LQD: {r_now:.3f} ({deterioration:+.1f}% from 12-mo high)")
        # -2% = 30, -5% = 60, -10% = 100
        credit_score = min(100, max(0, abs(min(deterioration, 0)) * 10))

    if rsp is not None and spy is not None:
        # Equal-weight vs cap-weight ratio.  Falling = narrow leadership = fragile.
        ew_cw = rsp["Close"] / spy["Close"]
        now = float(ew_cw.iloc[-1])
        avg = float(ew_cw.tail(60).mean())
        narrowing = (now - avg) / avg * 100
        bullets.append(f"RSP/SPY (breadth): {now:.3f} ({narrowing:+.2f}% vs 60d avg)")
        if narrowing < -2:
            breadth_score = 80
        elif narrowing < -1:
            breadth_score = 60
        elif narrowing < 0:
            breadth_score = 45
        else:
            breadth_score = 20

    score = round(0.6 * credit_score + 0.4 * breadth_score)
    note = "Credit + breadth: " + ("deteriorating" if score > 55 else "stable")

    return {
        "name": "credit_breadth",
        "value": None,
        "score": int(score),
        "note": note,
        "bullets": bullets,
    }


# ---------------------------------------------------------------------------
# 5. NEWS / SENTIMENT  — headline-based risk via keyword scan + optional LLM
# ---------------------------------------------------------------------------
RISK_KEYWORDS = [
    ("recession", 8), ("layoff", 4), ("layoffs", 4), ("bankruptcy", 6),
    ("default", 5), ("crash", 6), ("plunge", 4), ("sell-off", 4),
    ("sell off", 4), ("selloff", 4), ("bear market", 6), ("correction", 3),
    ("inflation", 2), ("downgrade", 3), ("contagion", 7), ("liquidity crisis", 9),
    ("credit crunch", 7), ("yield curve", 2), ("rate hike", 2), ("hawkish", 3),
    ("war", 3), ("tariff", 3), ("trade war", 4), ("geopolitical", 2),
    ("bank run", 9), ("debt ceiling", 4), ("downgrade", 3), ("margin call", 6),
]
RELIEF_KEYWORDS = [
    ("rally", -3), ("record high", -3), ("all-time high", -3),
    ("rate cut", -3), ("dovish", -3), ("soft landing", -4),
    ("beats expectations", -2), ("strong earnings", -2),
]

NEWS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.ft.com/markets?format=rss",
    "https://seekingalpha.com/market_currents.xml",
]


def news_signal(use_llm: bool = False, anthropic_client=None) -> dict:
    import feedparser

    headlines: list[str] = []
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:25]:
                title = entry.get("title", "").strip()
                if title:
                    headlines.append(title)
        except Exception:
            continue

    if not headlines:
        return {"name": "news", "value": None, "score": 50,
                "note": "No news available", "bullets": []}

    headlines = list(dict.fromkeys(headlines))[:80]
    text = " ".join(h.lower() for h in headlines)

    risk_hits = []
    raw = 0
    for kw, w in RISK_KEYWORDS + RELIEF_KEYWORDS:
        count = text.count(kw)
        if count > 0:
            raw += w * count
            if w > 0:
                risk_hits.append(f"{kw} (×{count})")

    # Map raw to 0-100 with soft cap
    kw_score = max(0, min(100, 50 + raw * 1.5))

    bullets = [f"Scanned {len(headlines)} headlines from {len(NEWS_FEEDS)} sources"]
    if risk_hits:
        bullets.append("Risk terms: " + ", ".join(risk_hits[:8]))

    llm_score = None
    llm_summary = None
    if use_llm and anthropic_client is not None:
        try:
            sample = "\n".join(f"- {h}" for h in headlines[:50])
            msg = anthropic_client.messages.create(
                model="claude-opus-4-7",
                max_tokens=400,
                messages=[{
                    "role": "user",
                    "content": (
                        "You are a market risk analyst. Read these headlines and "
                        "return a single JSON object with keys:\n"
                        '  "risk_score": integer 0-100 (probability of significant US equity drawdown in next 30 days),\n'
                        '  "summary": one sentence of why,\n'
                        '  "top_themes": list of 3 short strings.\n'
                        "Respond with ONLY the JSON, no prose.\n\n"
                        f"Headlines:\n{sample}"
                    ),
                }],
            )
            import json, re
            content = msg.content[0].text
            m = re.search(r"\{.*\}", content, re.S)
            if m:
                parsed = json.loads(m.group(0))
                llm_score = int(parsed.get("risk_score", 50))
                llm_summary = parsed.get("summary")
                themes = parsed.get("top_themes", [])
                bullets.append(f"LLM read: {llm_summary}")
                if themes:
                    bullets.append("Themes: " + ", ".join(themes))
        except Exception as e:
            bullets.append(f"LLM analysis failed: {e}")

    if llm_score is not None:
        score = round(0.4 * kw_score + 0.6 * llm_score)
    else:
        score = round(kw_score)

    note = llm_summary or (f"Headline risk score {score}/100"
                           + (f" ({len(risk_hits)} risk keywords)" if risk_hits else ""))

    return {
        "name": "news",
        "value": None,
        "score": int(score),
        "note": note,
        "bullets": bullets,
        "headlines": headlines[:20],
    }


# ---------------------------------------------------------------------------
# 6. FUTURES  — overnight ES / NQ direction
# ---------------------------------------------------------------------------
def futures_signal() -> dict:
    es = _fetch("ES=F", period="5d", interval="1h")
    if es is None or len(es) < 2:
        return {"name": "futures", "value": None, "score": 50,
                "note": "Futures data unavailable", "bullets": []}
    last = _last(es["Close"])
    prev = float(es["Close"].iloc[0])
    change = _pct(last, prev) or 0.0
    score = max(0, min(100, 50 - change * 10))  # -1% futures => 60, -3% => 80
    bullets = [f"ES front-month: {last:.2f}", f"5-day change: {change:+.2f}%"]
    return {
        "name": "futures",
        "value": last,
        "score": int(score),
        "note": f"ES {change:+.2f}% past 5 sessions",
        "bullets": bullets,
    }
