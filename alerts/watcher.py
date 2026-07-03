"""Crypto alert watcher.

Fetches OHLCV candles from exchanges (public endpoints, no API keys),
evaluates the configured rules on the latest CLOSED candle, and sends
email/Telegram alerts via Apprise. Deduplicates across runs with
state.json so the same candle never alerts twice.

Also processes the interactive Telegram bot's pending button presses and
commands each cycle (see alerts/telegram_bot.py); Telegram alerts are
routed per-chat based on each chat's coin subscriptions.

Usage:
    python -m alerts.watcher                 # one check cycle
    python -m alerts.watcher --repeat 3 --sleep 80   # 3 cycles, 80s apart
    python -m alerts.watcher --test-notify   # send a test alert to all channels
    python -m alerts.watcher --dry-run       # evaluate but don't send
    python -m alerts.watcher --force         # ignore state (re-alert)
    python -m alerts.watcher --loop 180      # run forever, check every 180s
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from . import telegram_bot
from .analysis import make_insight_fn
from .market import fetch_closed_candles, get_exchange
from .notify import Notifier
from .rules import RULES

log = logging.getLogger("alerts")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "config.yaml")
DEFAULT_STATE = os.path.join(ROOT, "state.json")
DEFAULT_TG_STATE = os.path.join(ROOT, "telegram.json")


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def normalize_symbols(config: dict):
    """Yield (pair, exchange_name) from the symbols list.

    Entries are either a plain pair string ("BTC/USDT", uses the default
    exchange) or a mapping {pair: HYPE/USDT, exchange: kucoin}.
    """
    default_ex = config.get("exchange", "binance")
    for entry in config.get("symbols", []):
        if isinstance(entry, str):
            yield entry, default_ex
        else:
            yield entry["pair"], entry.get("exchange", default_ex)


def run_once(config: dict, notifier: Notifier, state_path: str,
             bot=None, tg_state=None,
             force: bool = False, dry_run: bool = False) -> int:
    timeframe = config.get("timeframe", "1h")
    limit = int(config.get("candles", 150))
    state = load_state(state_path)
    alerts_sent = 0

    for pair, ex_name in normalize_symbols(config):
        try:
            df = fetch_closed_candles(get_exchange(ex_name), pair, timeframe, limit)
        except Exception as e:
            log.error("Fetch failed for %s on %s: %s", pair, ex_name, e)
            continue
        if len(df) < 30:
            log.warning("Not enough candles for %s on %s (%d)", pair, ex_name, len(df))
            continue

        candle_ts = int(df["timestamp"].iloc[-1])
        candle_iso = datetime.fromtimestamp(candle_ts / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")

        for rule_cfg in config.get("rules", []):
            rule_name = rule_cfg["name"]
            rule_fn = RULES.get(rule_name)
            if rule_fn is None:
                log.error("Unknown rule '%s' — available: %s", rule_name, list(RULES))
                continue

            # Per-rule symbol restriction (optional `symbols:` on the rule)
            only = rule_cfg.get("symbols")
            if only and pair not in only:
                continue

            signal = rule_fn(df, rule_cfg.get("params", {}))
            key = f"{ex_name}|{pair}|{rule_name}"
            if signal is None:
                log.info("%-28s %s: no signal (candle %s)", key, timeframe, candle_iso)
                continue

            already = state.get(key, {}).get("last_candle") == candle_ts
            if already and not force:
                log.info("%-28s already alerted for candle %s — skipping", key, candle_iso)
                continue

            title = f"{signal.emoji} {signal.side} {pair} {timeframe} — {signal.headline}"
            body = (f"{signal.details}\n\n"
                    f"Pair: {pair} ({ex_name})\nTimeframe: {timeframe}\n"
                    f"Candle close: {candle_iso}\nRule: {rule_name}")
            log.info("SIGNAL %s", title)
            if dry_run:
                continue
            delivered = notifier.send(title, body)
            if bot is not None and tg_state is not None:
                tg_sent = bot.broadcast(tg_state, pair, f"{title}\n\n{body}")
                log.info("Telegram: sent to %d subscribed chat(s)", tg_sent)
                delivered = delivered or tg_sent > 0
            if delivered:
                alerts_sent += 1
                state[key] = {"last_candle": candle_ts, "side": signal.side,
                              "at": candle_iso}

    if not dry_run:
        save_state(state_path, state)
    return alerts_sent


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto trading alerts watcher")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--tg-state", default=DEFAULT_TG_STATE)
    parser.add_argument("--test-notify", action="store_true",
                        help="send a test alert to all channels and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate rules but send nothing, don't touch state")
    parser.add_argument("--force", action="store_true",
                        help="alert even if this candle was already alerted")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="check cycles per invocation (for near-continuous "
                             "coverage inside a scheduled CI run)")
    parser.add_argument("--sleep", type=int, default=80, metavar="SECONDS",
                        help="pause between --repeat cycles")
    parser.add_argument("--loop", type=int, metavar="SECONDS", default=0,
                        help="run forever, checking every SECONDS")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Apprise logs recipient addresses at INFO ("Sent Email to <addr>").
    # Workflow logs are public on a public repo, so keep it to warnings.
    logging.getLogger("apprise").setLevel(logging.WARNING)

    with open(args.config) as f:
        config = yaml.safe_load(f)

    notifier = Notifier(config.get("notify", []))
    symbols = [pair for pair, _ in normalize_symbols(config)]
    bot = None
    tg_state = {}
    if config.get("telegram", {}).get("enabled", True):
        insight = make_insight_fn(config, dict(normalize_symbols(config)))
        bot = telegram_bot.load_bot(symbols, insight)
        if bot is not None:
            tg_state = telegram_bot.load_state(args.tg_state)
            bot.ensure_default_chats(tg_state)

    def save_tg():
        if bot is not None:
            telegram_bot.save_state(args.tg_state, tg_state)

    if args.test_notify:
        title = "✅ Crypto alerts — test notification"
        body = ("If you can read this, the alert pipeline works.\n"
                f"Sent: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        ok = notifier.send(title, body)
        if bot is not None:
            sent = sum(bot.send(c, f"{title}\n\n{body}") for c in bot.allowed)
            log.info("Telegram test: sent to %d chat(s)", sent)
            ok = ok or sent > 0
            save_tg()
        return 0 if ok else 1

    def cycle():
        if bot is not None:
            bot.process_updates(tg_state)
        run_once(config, notifier, args.state, bot=bot, tg_state=tg_state,
                 force=args.force, dry_run=args.dry_run)
        if not args.dry_run:
            save_tg()

    if args.loop > 0:
        while True:
            cycle()
            log.info("Sleeping %ds ...", args.loop)
            time.sleep(args.loop)

    for i in range(max(1, args.repeat)):
        if i > 0:
            log.info("Cycle %d/%d in %ds ...", i + 1, args.repeat, args.sleep)
            time.sleep(args.sleep)
        cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
