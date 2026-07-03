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
    """EMA fast/slow cross with confirmation filters (evaluated on the
    latest CLOSED candle only — the forming candle is never included).

    BUY:  EMA9 crosses above EMA21 · RSI > rsi_buy (55)
          · volume > volume_avg-period average (20) · close > EMA trend_ema (200)
    SELL: EMA9 crosses below EMA21 · RSI < rsi_sell (45)
          · volume > average · close < EMA 200

    The volume and trend filters only apply when their params are set.
    A cross that fails some filters is reported as ⚠️ WEAK (listing the
    failed checks) when alert_weak is true.
    """
    fast_len = int(params.get("fast", 9))
    slow_len = int(params.get("slow", 21))
    rsi_len = int(params.get("rsi_len", 14))
    # rsi_threshold kept for backward compatibility with older configs
    legacy = float(params.get("rsi_threshold", 50))
    rsi_buy = float(params.get("rsi_buy", legacy))
    rsi_sell = float(params.get("rsi_sell", legacy))
    vol_len = params.get("volume_avg")     # e.g. 20; unset disables the filter
    trend_len = params.get("trend_ema")    # e.g. 200; unset disables the filter
    alert_weak = bool(params.get("alert_weak", True))

    close = df["close"]
    fast = ema(close, fast_len)
    slow = ema(close, slow_len)

    if _crossed_over(fast, slow):
        side, arrow, word = "BUY", "↑", "ABOVE"
    elif _crossed_under(fast, slow):
        side, arrow, word = "SELL", "↓", "BELOW"
    else:
        return None

    rsi_now = float(rsi(close, rsi_len).iloc[-1])
    price = float(close.iloc[-1])

    checks = []  # (passed, description)
    if side == "BUY":
        checks.append((rsi_now > rsi_buy, f"RSI {rsi_now:.1f} > {rsi_buy:g}"))
    else:
        checks.append((rsi_now < rsi_sell, f"RSI {rsi_now:.1f} < {rsi_sell:g}"))
    if vol_len:
        vol_now = float(df["volume"].iloc[-1])
        vol_avg = float(df["volume"].rolling(int(vol_len)).mean().iloc[-1])
        checks.append((vol_now > vol_avg,
                       f"volume {vol_now:g} > {int(vol_len)}-period avg {vol_avg:g}"))
    if trend_len:
        trend = float(ema(close, int(trend_len)).iloc[-1])
        rel = ">" if side == "BUY" else "<"
        ok = price > trend if side == "BUY" else price < trend
        checks.append((ok, f"close {price:g} {rel} EMA{int(trend_len)} {trend:g}"))

    ctx = (f"Price: {price:g}\n"
           f"EMA{fast_len}: {float(fast.iloc[-1]):g} | "
           f"EMA{slow_len}: {float(slow.iloc[-1]):g}\n"
           + "\n".join(("✅ " if ok else "❌ ") + desc for ok, desc in checks))

    if all(ok for ok, _ in checks):
        emoji = "🟢" if side == "BUY" else "🔴"
        return Signal(side, emoji,
                      f"EMA{fast_len}{arrow}EMA{slow_len}, all filters passed",
                      f"{side} SIGNAL: EMA {fast_len} crossed {word} EMA {slow_len} "
                      f"on a closed candle with all confirmations.\n{ctx}")
    if alert_weak:
        failed = ", ".join(desc for ok, desc in checks if not ok)
        return Signal(f"WEAK {side}", "⚠️",
                      f"EMA{fast_len}{arrow}EMA{slow_len} unconfirmed",
                      f"WEAK SIGNAL: EMA {fast_len} crossed {word} EMA {slow_len} "
                      f"but failed: {failed} — possible fakeout.\n{ctx}")
    return None


def ema_cross_intrabar(df: pd.DataFrame, params: dict) -> Optional[Signal]:
    """Chart-time cross alert: evaluated with the FORMING candle included
    (df's last row is the live candle — unlike every other rule).

    Fires the moment the live candle shows EMA fast crossing slow with the
    RSI gate and trend filter met. The volume filter is deliberately
    skipped — a partial candle's volume can't be compared to full ones.
    Explicitly labeled unconfirmed: the cross can vanish before the close.
    The confirmed 🟢/🔴 alert still fires separately if it holds.
    """
    fast_len = int(params.get("fast", 9))
    slow_len = int(params.get("slow", 21))
    rsi_len = int(params.get("rsi_len", 14))
    rsi_buy = float(params.get("rsi_buy", 55))
    rsi_sell = float(params.get("rsi_sell", 45))
    trend_len = params.get("trend_ema")

    close = df["close"]
    fast = ema(close, fast_len)
    slow = ema(close, slow_len)

    if _crossed_over(fast, slow):
        side, arrow, word = "INTRABAR BUY", "↑", "ABOVE"
    elif _crossed_under(fast, slow):
        side, arrow, word = "INTRABAR SELL", "↓", "BELOW"
    else:
        return None

    rsi_now = float(rsi(close, rsi_len).iloc[-1])
    price = float(close.iloc[-1])
    if side.endswith("BUY") and rsi_now <= rsi_buy:
        return None
    if side.endswith("SELL") and rsi_now >= rsi_sell:
        return None
    if trend_len:
        trend = float(ema(close, int(trend_len)).iloc[-1])
        if side.endswith("BUY") and price <= trend:
            return None
        if side.endswith("SELL") and price >= trend:
            return None

    return Signal(side, "⏱️",
                  f"EMA{fast_len}{arrow}EMA{slow_len} on the LIVE candle",
                  f"UNCONFIRMED: EMA {fast_len} is crossing {word} EMA "
                  f"{slow_len} on the still-forming candle — this is what the "
                  f"chart shows right now. It only counts if the candle CLOSES "
                  f"this way; watch for the confirmed alert (or nothing, if it "
                  f"fades).\nPrice: {price:g}\nRSI({rsi_len}): {rsi_now:.1f}")


# Rules that must see the forming candle; the watcher polls these every
# ~30s instead of only after candle closes.
INTRABAR_RULES = {"ema_cross_intrabar"}


def ema_cross_soon(df: pd.DataFrame, params: dict) -> Optional[Signal]:
    """Early heads-up while EMA fast/slow are CONVERGING toward a cross.

    Fires when the gap between the EMAs is under gap_pct of price and has
    been shrinking, before the actual cross — the 'get ready' alert.
    Pair with once_per_side in config so one approach episode alerts once.
    """
    fast_len = int(params.get("fast", 9))
    slow_len = int(params.get("slow", 21))
    gap_pct = float(params.get("gap_pct", 0.1)) / 100.0

    close = df["close"]
    gap = (ema(close, fast_len) - ema(close, slow_len)) / close
    g_now, g_prev = float(gap.iloc[-1]), float(gap.iloc[-3])

    converging = abs(g_now) < abs(g_prev)
    if not (abs(g_now) < gap_pct and converging and g_now * g_prev > 0):
        return None

    price = float(close.iloc[-1])
    if g_now < 0:  # fast below slow, closing in -> potential bullish cross
        return Signal("NEAR-BUY", "🟡",
                      f"bullish EMA{fast_len}/{slow_len} cross forming",
                      f"HEADS-UP: EMA {fast_len} is closing in on EMA {slow_len} "
                      f"from below (gap {abs(g_now) * 100:.3f}% of price and "
                      f"shrinking). A bullish cross may be next — watch for the "
                      f"confirmed BUY alert.\nPrice: {price:g}")
    return Signal("NEAR-SELL", "🟡",
                  f"bearish EMA{fast_len}/{slow_len} cross forming",
                  f"HEADS-UP: EMA {fast_len} is closing in on EMA {slow_len} "
                  f"from above (gap {g_now * 100:.3f}% of price and shrinking). "
                  f"A bearish cross may be next — watch for the confirmed SELL "
                  f"alert.\nPrice: {price:g}")


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
    "ema_cross_intrabar": ema_cross_intrabar,
    "ema_cross_soon": ema_cross_soon,
    "rsi_extreme": rsi_extreme,
    "price_cross_level": price_cross_level,
}
