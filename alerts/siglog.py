"""Signal outcome log — the system's memory of whether alerts worked.

Every sent alert is appended to signals_log.json (committed by CI like
state.json). Once the evaluation horizon has passed, the scorer labels
each signal:

    hit  — price moved >= target_pct in the signal's direction within
           `horizon` candles of the signal candle (highs for buys,
           lows for sells)
    miss — it didn't

NEAR-BUY / NEAR-SELL heads-ups are judged by their own purpose instead:
hit = a matching EMA cross actually happened within the horizon.

This labeled history feeds the /accuracy bot command and the nightly
self-tuner (alerts/tuner.py).
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from .indicators import ema

log = logging.getLogger(__name__)

MAX_ENTRIES = 600          # keep the committed file bounded
TARGET_PCT = 0.3           # favorable move that counts as a hit (%)
HORIZON_CANDLES = 12       # evaluation window after the signal candle


def load(path: str) -> List[dict]:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save(path: str, entries: List[dict]) -> None:
    with open(path, "w") as f:
        json.dump(entries[-MAX_ENTRIES:], f, indent=1, sort_keys=True)
        f.write("\n")


def append(entries: List[dict], *, pair: str, exchange: str, rule: str,
           side: str, price: float, candle_ts: int, timeframe: str) -> None:
    uid = f"{exchange}|{pair}|{rule}|{candle_ts}"
    if any(e["id"] == uid for e in entries):
        return
    entries.append({
        "id": uid, "pair": pair, "exchange": exchange, "rule": rule,
        "side": side, "price": price, "candle_ts": candle_ts,
        "timeframe": timeframe,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "outcome": None, "move_pct": None,
    })


def _direction(side: str) -> Optional[int]:
    if "BUY" in side:
        return 1
    if "SELL" in side:
        return -1
    return None


def _score_move(entry: dict, df: pd.DataFrame) -> Optional[dict]:
    """Label a BUY/SELL/WEAK signal by its forward favorable move."""
    idx = df.index[df["timestamp"] == entry["candle_ts"]]
    if len(idx) == 0:
        return {"outcome": "unscorable", "move_pct": None}
    i = int(idx[0])
    window = df.iloc[i + 1: i + 1 + HORIZON_CANDLES]
    if len(window) < HORIZON_CANDLES:
        return None  # not matured yet
    price = float(entry["price"])
    if _direction(entry["side"]) == 1:
        move = (float(window["high"].max()) - price) / price * 100
    else:
        move = (price - float(window["low"].min())) / price * 100
    return {"outcome": "hit" if move >= TARGET_PCT else "miss",
            "move_pct": round(move, 3)}


def _score_near(entry: dict, df: pd.DataFrame) -> Optional[dict]:
    """Label a NEAR heads-up: did the predicted cross actually happen?"""
    idx = df.index[df["timestamp"] == entry["candle_ts"]]
    if len(idx) == 0:
        return {"outcome": "unscorable", "move_pct": None}
    i = int(idx[0])
    if len(df) < i + 1 + HORIZON_CANDLES:
        return None  # not matured yet
    close = df["close"]
    f, s = ema(close, 9), ema(close, 21)
    gap = (f - s).iloc[i: i + 1 + HORIZON_CANDLES]
    want = 1 if _direction(entry["side"]) == 1 else -1
    # cross = the gap changes to the predicted sign inside the horizon
    crossed = any((g > 0 if want == 1 else g < 0) for g in gap.iloc[1:])
    return {"outcome": "hit" if crossed else "miss", "move_pct": None}


def score_pending(entries: List[dict],
                  fetch_fn: Callable[[str, str], Optional[pd.DataFrame]]) -> int:
    """Label matured signals. fetch_fn(pair, exchange) -> recent candles df."""
    pending: Dict[tuple, List[dict]] = {}
    for e in entries:
        if e["outcome"] is None:
            pending.setdefault((e["pair"], e["exchange"]), []).append(e)

    scored = 0
    for (pair, exchange), items in pending.items():
        df = fetch_fn(pair, exchange)
        if df is None:
            continue
        for e in items:
            result = (_score_near if e["rule"] == "ema_cross_soon"
                      else _score_move)(e, df)
            if result is not None:
                e.update(result)
                scored += 1
                log.info("Scored %s -> %s (move %s%%)", e["id"],
                         e["outcome"], e["move_pct"])
    return scored


def stats_text(entries: List[dict]) -> str:
    """Human summary for the /accuracy Telegram command."""
    scored = [e for e in entries if e["outcome"] in ("hit", "miss")]
    unscored = sum(1 for e in entries if e["outcome"] is None)
    if not scored:
        return ("📈 Accuracy tracker: no scored signals yet "
                f"({unscored} waiting to mature, horizon "
                f"{HORIZON_CANDLES} candles). Every alert gets judged: "
                f"±{TARGET_PCT}% move for signals, cross-happened for "
                "heads-ups. Check back after a few alerts fire.")

    def line(group: List[dict], label: str) -> str:
        hits = sum(1 for e in group if e["outcome"] == "hit")
        return f"{label}: {hits}/{len(group)} hit ({hits / len(group):.0%})"

    lines = ["📈 Alert accuracy so far"]
    by_rule: Dict[str, List[dict]] = {}
    for e in scored:
        by_rule.setdefault(e["rule"], []).append(e)
    for rule, group in sorted(by_rule.items()):
        lines.append(line(group, rule))
        by_pair: Dict[str, List[dict]] = {}
        for e in group:
            by_pair.setdefault(e["pair"], []).append(e)
        for pair, pgroup in sorted(by_pair.items()):
            lines.append("   " + line(pgroup, pair))
    if unscored:
        lines.append(f"({unscored} recent signal(s) not matured yet)")
    lines.append(f"Hit = {TARGET_PCT}%+ favorable move (or cross happening, "
                 f"for heads-ups) within {HORIZON_CANDLES} candles.")
    return "\n".join(lines)
