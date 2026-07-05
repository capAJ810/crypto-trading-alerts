"""On-demand conversational coin reads for the Telegram bot.

Produces Finora-style "expectation" messages: multi-timeframe trend read
(5m / 1h / 4h), support/resistance from recent swings, and an ATR-sized
example setup — all deterministic indicator math (EMA 9/21, RSI 14,
ATR 14), clearly labeled as a rule-based read, not advice or real AI.

Pure functions (`read_frame`, `compose_*`) are separated from fetching so
they're unit-testable on synthetic candles.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict

import pandas as pd

from .indicators import atr, ema, rsi
from .market import fetch_closed_candles, get_exchange

log = logging.getLogger(__name__)

TIMEFRAMES = ["5m", "1h", "4h"]
TF_WEIGHT = {"5m": 1, "1h": 2, "4h": 3}


def fmt(p: float) -> str:
    """Price formatting with sane precision across BTC (~100k) and sub-$1 alts."""
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 10:
        return f"{p:,.2f}"
    if p >= 0.1:
        return f"{p:.4f}"
    return f"{p:.6f}"


def read_frame(df: pd.DataFrame) -> dict:
    """Extract the indicator read from one timeframe's closed candles."""
    close = df["close"]
    f = ema(close, 9)
    s = ema(close, 21)
    r = rsi(close, 14)
    return {
        "price": float(close.iloc[-1]),
        "bullish": bool(f.iloc[-1] > s.iloc[-1]),
        "rsi": float(r.iloc[-1]),
        "atr": float(atr(df, 14).iloc[-1]),
    }


def swing_levels(df_1h: pd.DataFrame) -> dict:
    """Nearest and wider support/resistance from recent 1h swings."""
    high, low = df_1h["high"], df_1h["low"]
    return {
        "r1": float(high.tail(24).max()),    # last ~day
        "r2": float(high.tail(96).max()),    # last ~4 days
        "s1": float(low.tail(24).min()),
        "s2": float(low.tail(96).min()),
    }


def bias_score(frames: Dict[str, dict]) -> int:
    """Weighted trend agreement: +6..-6 (4h counts 3x, 1h 2x, 5m 1x)."""
    return sum((1 if frames[tf]["bullish"] else -1) * TF_WEIGHT[tf]
               for tf in TIMEFRAMES if tf in frames)


def _tf_word(read: dict) -> str:
    arrow = "🟢 up" if read["bullish"] else "🔴 down"
    return f"{arrow} (RSI {read['rsi']:.0f})"


def _trend_line(frames: Dict[str, dict]) -> str:
    return " · ".join(f"{tf}: {_tf_word(frames[tf])}"
                      for tf in ("4h", "1h", "5m") if tf in frames)


def _distinct(near: float, far: float) -> bool:
    """Is the wider level meaningfully beyond the nearer one (>0.2%)?"""
    return abs(far - near) > 0.002 * near


def _expectation(frames: Dict[str, dict], lv: dict) -> str:
    score = bias_score(frames)
    rsi_1h = frames["1h"]["rsi"]
    r1, s1 = fmt(lv["r1"]), fmt(lv["s1"])
    # When price sits at the edge of its multi-day range, the near and wide
    # swing levels coincide — don't name the same number twice.
    r2 = fmt(lv["r2"]) if _distinct(lv["r1"], lv["r2"]) else None
    s2 = fmt(lv["s2"]) if _distinct(lv["s1"], lv["s2"]) else None

    if score >= 4:  # trend clearly up
        beyond = f"opens the door to {r2}" if r2 else \
            "puts it at the top of its recent range — fresh highs territory"
        if rsi_1h >= 70:
            cont = f"continuation toward {r1}" + (f", then {r2}" if r2 else "")
            return (f"Trend is up but the hourly RSI is running hot ({rsi_1h:.0f}), "
                    f"so I'd expect a cooling-off dip toward {s1} first — and if "
                    f"buyers defend it, {cont}.")
        if rsi_1h >= 50:
            return (f"Momentum favors the upside here. A push toward {r1} looks "
                    f"like the path of least resistance; a clean break and hold "
                    f"above it {beyond}. If it stalls instead, "
                    f"{s1} is the first support I'd watch.")
        return (f"The larger trend leans up but momentum is soft (hourly RSI "
                f"{rsi_1h:.0f}) — I'd expect chop between {s1} and {r1} until "
                f"RSI picks a side.")
    if score <= -4:  # trend clearly down
        below = f"opens {s2}" if s2 else \
            "puts it at the bottom of its recent range — breakdown territory"
        if rsi_1h <= 30:
            roll = f"a rollover toward {s1}" + (f" and {s2}" if s2 else "")
            return (f"Trend is down but the hourly RSI is stretched ({rsi_1h:.0f}), "
                    f"so I'd expect a reaction bounce toward {r1} first — then, if "
                    f"sellers lean on it, {roll}.")
        if rsi_1h <= 50:
            return (f"Momentum favors the downside. A drift toward {s1} looks "
                    f"likely; losing that level cleanly {below}. If bulls "
                    f"reclaim {r1}, that read is wrong.")
        return (f"The larger trend leans down but momentum is soft (hourly RSI "
                f"{rsi_1h:.0f}) — likely range-bound between {s1} and {r1} for now.")
    return (f"Timeframes disagree right now, which usually means range conditions. "
            f"I'd wait for a decisive close above {r1} or below {s1} before "
            f"trusting a direction.")


def _rr_setup(entry: float, atr: float, lv: dict, side: str) -> str:
    """Example entry / invalidation / targets anchored to swing structure.

    Targets sit at the swing levels price is likely to react at — resistance
    for a long, support for a short — and the stop sits just past the level
    that would prove the trade wrong (support for a long, resistance for a
    short). ATR is the fallback yardstick and guardrail: it fills in when a
    level isn't usefully placed (e.g. a breakout with no resistance overhead),
    caps a stop that would otherwise be absurdly far, and floors one that would
    otherwise hug price. R is measured from the ACTUAL stop distance, so the
    reported multiples reflect where the structure really is (not a fixed 1R/2R).
    """
    sign = 1.0 if side == "long" else -1.0          # +1 up-trade, -1 down-trade
    if side == "long":
        stop_anchor, tp1_anchor, tp2_anchor = lv["s1"], lv["r1"], lv["r2"]
    else:
        stop_anchor, tp1_anchor, tp2_anchor = lv["r1"], lv["s1"], lv["s2"]

    # Invalidation: 0.25·ATR past the adverse swing level, used only when that
    # level is on the losing side and within 2.5·ATR; otherwise a 1.5·ATR stop.
    struct_stop = stop_anchor - sign * 0.25 * atr
    adverse_dist = sign * (entry - struct_stop)     # >0 ⇒ correct (losing) side
    stop = struct_stop if 0 < adverse_dist <= 2.5 * atr else entry - sign * 1.5 * atr
    if sign * (entry - stop) < 0.75 * atr:          # too tight → widen to 0.75·ATR
        stop = entry - sign * 0.75 * atr
    risk = abs(entry - stop)

    # Target 1: nearest profit level if ≥ 0.5·ATR ahead of entry, else 1.5·ATR.
    t1 = tp1_anchor if sign * (tp1_anchor - entry) >= 0.5 * atr \
        else entry + sign * 1.5 * atr
    # Target 2: wider profit level if clearly beyond T1, else an ATR extension.
    t2 = tp2_anchor if sign * (tp2_anchor - t1) >= 0.3 * atr \
        else entry + sign * max(3 * atr, sign * (t1 - entry) + 1.5 * atr)

    m1, m2 = abs(t1 - entry) / risk, abs(t2 - entry) / risk
    return (f"Example {side} setup (if momentum holds): entry ~{fmt(entry)}, "
            f"invalidation ~{fmt(stop)}, targets {fmt(t1)} → {fmt(t2)} "
            f"(~{m1:.1f}R / {m2:.1f}R — swing levels, ATR-guarded).")


def _setup(frames: Dict[str, dict], lv: dict) -> str:
    score = bias_score(frames)
    p = frames["5m"]["price"]
    a = frames["1h"]["atr"]
    if a <= 0:
        return "No clean setup — volatility read unavailable. Patience."
    if score >= 4:
        return _rr_setup(p, a, lv, "long")
    if score <= -4:
        return _rr_setup(p, a, lv, "short")
    return "No clean setup — mixed signals are where accounts go to shrink. Patience."


def _level_span(near: float, far: float) -> str:
    return f"{fmt(near)} / {fmt(far)}" if _distinct(near, far) else fmt(near)


DISCLAIMER = ("🤖 Rule-based read (EMA 9/21 · 14-period RSI vs 55/45 gates · ATR "
              "· recent swing levels), not financial advice — crypto moves fast, "
              "size accordingly.")


def compose_prediction(pair: str, ex_name: str, frames: Dict[str, dict],
                       lv: dict) -> str:
    p = frames["5m"]["price"]
    score = bias_score(frames)
    mood = "leaning bullish" if score >= 4 else \
           "leaning bearish" if score <= -4 else "mixed / ranging"
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    return (
        f"🔮 {pair} — my read at {now}\n"
        f"Price: {fmt(p)} · Overall: {mood}\n"
        f"{_trend_line(frames)}\n\n"
        f"{_expectation(frames, lv)}\n\n"
        f"📌 Levels — resistance {_level_span(lv['r1'], lv['r2'])}, "
        f"support {_level_span(lv['s1'], lv['s2'])}\n"
        f"{_setup(frames, lv)}\n\n"
        f"{DISCLAIMER}"
    )


def compose_status(pair: str, ex_name: str, frames: Dict[str, dict]) -> str:
    f5 = frames["5m"]
    return (
        f"📊 {pair} ({ex_name})\n"
        f"Price: {fmt(f5['price'])}\n"
        f"{_trend_line(frames)}\n"
        f"Tap 🔮 Full read for levels and my expectation."
    )


def make_insight_fn(config: dict, pair_exchange: Dict[str, str]
                    ) -> Callable[[str, str], str]:
    """Build the fetch-and-compose callable the Telegram bot uses.

    insight(pair, kind) with kind "status" (quick) or "predict" (full read).
    """
    limit = int(config.get("candles", 150))

    def insight(pair: str, kind: str = "predict") -> str:
        ex_name = pair_exchange.get(pair, config.get("exchange", "binance"))
        try:
            ex = get_exchange(ex_name)
            dfs = {tf: fetch_closed_candles(ex, pair, tf, limit)
                   for tf in TIMEFRAMES}
            frames = {tf: read_frame(df) for tf, df in dfs.items()}
            if kind == "status":
                return compose_status(pair, ex_name, frames)
            return compose_prediction(pair, ex_name, frames, swing_levels(dfs["1h"]))
        except Exception as e:
            log.error("Insight failed for %s: %s", pair, e)
            return f"⚠️ Couldn't fetch {pair} right now ({e}). Try again in a minute."

    return insight
