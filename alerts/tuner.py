"""Nightly self-tuner — the autoresearch loop, without the LLM.

Pattern borrowed from karpathy/autoresearch: mutate parameters, evaluate
against an objective, keep only what measurably improves, repeat daily.

For each coin it replays the last TRAIN+VAL days of candles through a
vectorized backtest of the alert rules over a bounded parameter grid,
walk-forward validated:

  1. pick the grid point with the best precision on the TRAIN window
     (needs >= MIN_SIGNALS signals there)
  2. accept it ONLY if it also beats the currently deployed params on the
     later, unseen VAL window by >= MIN_GAIN percentage points
     (with >= MIN_VAL_SIGNALS signals)
  3. otherwise keep what we have

Accepted params land in tuned.yaml (per-pair overrides the watcher merges
over config.yaml), each change is a git commit made by CI, and the bot
announces it in Telegram. Guardrails: the grid is bounded, nothing outside
it can ever be deployed, and `git revert` undoes any tune.

Usage:
    python -m alerts.tuner            # tune now
    python -m alerts.tuner --if-due   # only if last run > 24h ago (CI mode)
    python -m alerts.tuner --dry-run  # show decisions, write/send nothing
"""

import argparse
import itertools
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from . import telegram_bot
from .indicators import ema, rsi
from .market import fetch_closed_candles, get_exchange, timeframe_ms
from .siglog import HORIZON_CANDLES, TARGET_PCT
from .watcher import DEFAULT_CONFIG, DEFAULT_TUNED, normalize_symbols

log = logging.getLogger("tuner")

TRAIN_DAYS, VAL_DAYS = 10, 4
MIN_SIGNALS = 5        # train-window signals needed to trust a grid point
MIN_VAL_SIGNALS = 3    # validation signals needed to accept a change
MIN_GAIN = 0.05        # validation precision must improve by >= 5pp

GRID_CROSS = {
    "rsi_buy": [52.0, 55.0, 60.0],
    "rsi_sell": [40.0, 45.0, 48.0],
    "volume_avg": [10, 20, 30],
    "trend_ema": [100, 200],
}
GRID_SOON = {"gap_pct": [0.05, 0.08, 0.12, 0.2]}


def fetch_history(ex, pair: str, timeframe: str, days: int) -> pd.DataFrame:
    """Paginated OHLCV fetch covering `days` of history."""
    tf_ms = timeframe_ms(timeframe)
    since = ex.milliseconds() - days * 86_400_000
    rows = []
    while True:
        batch = ex.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        if len(batch) < 2:
            break
        since = batch[-1][0] + tf_ms
        if batch[-1][0] + 2 * tf_ms > ex.milliseconds():
            break
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low",
                                     "close", "volume"])
    df = df.drop_duplicates("timestamp").reset_index(drop=True)
    return df[df["timestamp"] + tf_ms <= ex.milliseconds()].reset_index(drop=True)


def _forward_hit(df: pd.DataFrame, i: int, direction: int) -> Optional[bool]:
    """Did price move TARGET_PCT favorably within HORIZON_CANDLES of bar i?"""
    window = df.iloc[i + 1: i + 1 + HORIZON_CANDLES]
    if len(window) < HORIZON_CANDLES:
        return None  # signal too close to the end of history to judge
    price = float(df["close"].iloc[i])
    if direction == 1:
        move = (float(window["high"].max()) - price) / price * 100
    else:
        move = (price - float(window["low"].min())) / price * 100
    return move >= TARGET_PCT


def backtest_cross(df: pd.DataFrame, params: dict):
    """Vectorized signal detection for the ema_cross_rsi conditions.
    Returns list of (bar_index, direction)."""
    close, vol = df["close"], df["volume"]
    f = ema(close, int(params.get("fast", 9)))
    s = ema(close, int(params.get("slow", 21)))
    r = rsi(close, int(params.get("rsi_len", 14)))
    vol_ok = vol > vol.rolling(int(params.get("volume_avg", 20))).mean()
    trend = ema(close, int(params.get("trend_ema", 200)))

    up = (f.shift(1) <= s.shift(1)) & (f > s) & (r > float(params["rsi_buy"])) \
        & vol_ok & (close > trend)
    dn = (f.shift(1) >= s.shift(1)) & (f < s) & (r < float(params["rsi_sell"])) \
        & vol_ok & (close < trend)
    out = [(int(i), 1) for i in df.index[up.fillna(False)]]
    out += [(int(i), -1) for i in df.index[dn.fillna(False)]]
    return sorted(out)


def backtest_soon(df: pd.DataFrame, params: dict):
    """Near-cross events (first bar of each approach episode) and whether
    the predicted cross followed. Returns list of (bar_index, direction)."""
    close = df["close"]
    f = ema(close, int(params.get("fast", 9)))
    s = ema(close, int(params.get("slow", 21)))
    gap = (f - s) / close
    th = float(params["gap_pct"]) / 100.0
    cond = (gap.abs() < th) & (gap.abs() < gap.shift(2).abs()) \
        & (gap * gap.shift(2) > 0)
    episode_start = cond & ~cond.shift(1, fill_value=False)
    return [(int(i), 1 if gap.iloc[i] < 0 else -1)
            for i in df.index[episode_start]]


def _soon_hit(df: pd.DataFrame, i: int, direction: int) -> Optional[bool]:
    close = df["close"]
    f, s = ema(close, 9), ema(close, 21)
    gap = (f - s).iloc[i: i + 1 + HORIZON_CANDLES]
    if len(gap) < HORIZON_CANDLES:
        return None
    return any((g > 0 if direction == 1 else g < 0) for g in gap.iloc[1:])


def precision(df: pd.DataFrame, signals, hit_fn, lo: int, hi: int):
    """(hits, judged) for signals whose bar index falls in [lo, hi)."""
    hits = judged = 0
    for i, direction in signals:
        if not (lo <= i < hi):
            continue
        hit = hit_fn(df, i, direction)
        if hit is None:
            continue
        judged += 1
        hits += int(hit)
    return hits, judged


def tune_rule(df: pd.DataFrame, current: dict, grid: dict, backtest_fn, hit_fn,
              warmup: int):
    """Walk-forward: best-on-train, accepted only if better-on-validation."""
    split = warmup + int((len(df) - warmup) * TRAIN_DAYS / (TRAIN_DAYS + VAL_DAYS))

    def train_score(p):
        h, n = precision(df, backtest_fn(df, p), hit_fn, warmup, split)
        return (h / n, n) if n >= MIN_SIGNALS else (-1.0, n)

    def val_score(p):
        h, n = precision(df, backtest_fn(df, p), hit_fn, split, len(df))
        return (h / n, n) if n else (None, 0)

    keys = sorted(grid)
    candidates = [dict(zip(keys, combo), **{k: v for k, v in current.items()
                                            if k not in grid})
                  for combo in itertools.product(*(grid[k] for k in keys))]
    best = max(candidates, key=lambda p: train_score(p)[0])
    if train_score(best)[0] < 0:
        return None, "too few train signals"

    cur_val, _ = val_score(current)
    new_val, new_n = val_score(best)
    changed = {k: best[k] for k in keys if best.get(k) != current.get(k)}
    if not changed:
        return None, "current params already best"
    if new_val is None or new_n < MIN_VAL_SIGNALS:
        return None, "too few validation signals"
    if cur_val is not None and new_val < cur_val + MIN_GAIN:
        return None, (f"no validation edge ({new_val:.0%} vs "
                      f"{cur_val:.0%} current)")
    return (changed,
            f"val precision {'?' if cur_val is None else format(cur_val, '.0%')}"
            f" -> {new_val:.0%} ({new_n} signals)")


def load_tuned_file(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly parameter self-tuner")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--tuned", default=DEFAULT_TUNED)
    parser.add_argument("--if-due", action="store_true",
                        help="skip unless the last tune was >24h ago")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    tuned_doc = load_tuned_file(args.tuned)
    now = datetime.now(timezone.utc)
    if args.if_due:
        last = tuned_doc.get("last_run")
        if last and (now - datetime.fromisoformat(last)).total_seconds() < 86_000:
            log.info("Tuner not due (last run %s)", last)
            return 0

    with open(args.config) as f:
        config = yaml.safe_load(f)
    timeframe = config.get("timeframe", "5m")
    rule_params = {r["name"]: dict(r.get("params", {}))
                   for r in config.get("rules", [])}
    pairs_doc = tuned_doc.setdefault("pairs", {})
    announcements = []

    for pair, ex_name in normalize_symbols(config):
        try:
            df = fetch_history(get_exchange(ex_name), pair, timeframe,
                               TRAIN_DAYS + VAL_DAYS)
        except Exception as e:
            log.error("History fetch failed for %s: %s", pair, e)
            continue
        if len(df) < 1000:
            log.warning("Skipping %s: only %d candles", pair, len(df))
            continue
        overrides = pairs_doc.get(pair, {})

        for rule, grid, bt, hit_fn, warmup in (
                ("ema_cross_rsi", GRID_CROSS, backtest_cross, _forward_hit, 400),
                ("ema_cross_soon", GRID_SOON, backtest_soon, _soon_hit, 50)):
            if rule not in rule_params:
                continue
            current = {**rule_params[rule], **overrides.get(rule, {})}
            change, why = tune_rule(df, current, grid, bt, hit_fn, warmup)
            if change:
                overrides.setdefault(rule, {}).update(change)
                pairs_doc[pair] = overrides
                delta = ", ".join(f"{k} {current.get(k)}→{v}"
                                  for k, v in change.items())
                announcements.append(f"{pair} {rule}: {delta} ({why})")
                log.info("TUNED %s %s: %s (%s)", pair, rule, delta, why)
            else:
                log.info("kept %s %s (%s)", pair, rule, why)

    tuned_doc["last_run"] = now.isoformat()
    if args.dry_run:
        log.info("Dry run — not writing tuned.yaml")
        return 0
    with open(args.tuned, "w") as f:
        yaml.safe_dump(tuned_doc, f, sort_keys=True)

    if announcements:
        symbols = [p for p, _ in normalize_symbols(config)]
        bot = telegram_bot.load_bot(symbols)
        if bot:
            text = ("🧪 Nightly self-tune (walk-forward validated):\n"
                    + "\n".join("• " + a for a in announcements)
                    + "\nEvery change is a git commit — revertable anytime.")
            for chat in bot.allowed:
                bot.send(chat, text)
    else:
        log.info("Self-tune complete: no parameter changes earned deployment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
