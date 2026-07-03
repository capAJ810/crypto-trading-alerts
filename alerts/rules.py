"""Signal rules. To add a new tracking metric:

1. Write a function `def my_rule(df, params) -> Optional[Signal]` below.
   `df` has columns: timestamp, open, high, low, close, volume — the LAST
   row is the most recent CLOSED candle.
2. Register it: add `"my_rule": my_rule` to RULES.
3. Enable it in config.yaml under `rules:` with its params.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional

import pandas as pd

from .indicators import ema, rsi


@dataclass
class Signal:
    side: str       # "BUY" / "SELL" / "WEAK BUY" / "WEAK SELL" / "INFO"
    emoji: str
    headline: str   # short, goes in the subject line
    details: str    # multi-line body


def _crossed_over(fast: pd.Series, slow: pd.Series) -> bool:
    return fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]


def _crossed_under(fast: pd.Series, slow: pd.Series) -> bool:
    return fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]


def ema_cross_rsi(df: pd.DataFrame, params: dict) -> Optional[Signal]:
    """EMA fast/slow cross confirmed by RSI above/below a threshold.

    The goal signal: EMA 9 crosses EMA 21, confirmed by RSI > 50 (bullish)
    or RSI < 50 (bearish). Unconfirmed crosses are reported as WEAK
    (possible fakeout) when alert_weak is true.
    """
    fast_len = int(params.get("fast", 9))
    slow_len = int(params.get("slow", 21))
    rsi_len = int(params.get("rsi_len", 14))
    threshold = float(params.get("rsi_threshold", 50))
    alert_weak = bool(params.get("alert_weak", True))

    close = df["close"]
    fast = ema(close, fast_len)
    slow = ema(close, slow_len)
    r = rsi(close, rsi_len)

    rsi_now = float(r.iloc[-1])
    price = float(close.iloc[-1])
    ctx = (
        f"Price: {price:g}\n"
        f"EMA{fast_len}: {float(fast.iloc[-1]):g} | EMA{slow_len}: {float(slow.iloc[-1]):g}\n"
        f"RSI({rsi_len}): {rsi_now:.1f}"
    )

    if _crossed_over(fast, slow):
        if rsi_now > threshold:
            return Signal("BUY", "🟢",
                          f"EMA{fast_len}↑EMA{slow_len}, RSI {rsi_now:.0f}",
                          f"BUY SIGNAL: EMA {fast_len} crossed ABOVE EMA {slow_len} "
                          f"with RSI confirmation (> {threshold:g}).\n{ctx}")
        if alert_weak:
            return Signal("WEAK BUY", "⚠️",
                          f"EMA{fast_len}↑EMA{slow_len} but RSI {rsi_now:.0f}",
                          f"WEAK SIGNAL: EMA {fast_len} crossed ABOVE EMA {slow_len} but RSI "
                          f"is below {threshold:g} — possible fakeout.\n{ctx}")
    elif _crossed_under(fast, slow):
        if rsi_now < threshold:
            return Signal("SELL", "🔴",
                          f"EMA{fast_len}↓EMA{slow_len}, RSI {rsi_now:.0f}",
                          f"SELL SIGNAL: EMA {fast_len} crossed BELOW EMA {slow_len} "
                          f"with RSI confirmation (< {threshold:g}).\n{ctx}")
        if alert_weak:
            return Signal("WEAK SELL", "⚠️",
                          f"EMA{fast_len}↓EMA{slow_len} but RSI {rsi_now:.0f}",
                          f"WEAK SIGNAL: EMA {fast_len} crossed BELOW EMA {slow_len} but RSI "
                          f"is above {threshold:g} — possible fakeout.\n{ctx}")
    return None


def rsi_extreme(df: pd.DataFrame, params: dict) -> Optional[Signal]:
    """RSI enters overbought/oversold territory (fires on the crossing candle)."""
    rsi_len = int(params.get("rsi_len", 14))
    overbought = float(params.get("overbought", 70))
    oversold = float(params.get("oversold", 30))

    r = rsi(df["close"], rsi_len)
    prev, now = float(r.iloc[-2]), float(r.iloc[-1])
    price = float(df["close"].iloc[-1])

    if prev < overbought <= now:
        return Signal("SELL", "🔥", f"RSI overbought {now:.0f}",
                      f"RSI({rsi_len}) crossed above {overbought:g} (now {now:.1f}) — "
                      f"overbought.\nPrice: {price:g}")
    if prev > oversold >= now:
        return Signal("BUY", "🧊", f"RSI oversold {now:.0f}",
                      f"RSI({rsi_len}) crossed below {oversold:g} (now {now:.1f}) — "
                      f"oversold.\nPrice: {price:g}")
    return None


def price_cross_level(df: pd.DataFrame, params: dict) -> Optional[Signal]:
    """Close crosses a fixed price level (e.g. alert when BTC crosses 100000)."""
    level = float(params["level"])
    close = df["close"]
    prev, now = float(close.iloc[-2]), float(close.iloc[-1])

    if prev < level <= now:
        return Signal("INFO", "📈", f"crossed above {level:g}",
                      f"Price closed above {level:g} (now {now:g}).")
    if prev > level >= now:
        return Signal("INFO", "📉", f"crossed below {level:g}",
                      f"Price closed below {level:g} (now {now:g}).")
    return None


RULES: Dict[str, Callable[[pd.DataFrame, dict], Optional[Signal]]] = {
    "ema_cross_rsi": ema_cross_rsi,
    "rsi_extreme": rsi_extreme,
    "price_cross_level": price_cross_level,
}
