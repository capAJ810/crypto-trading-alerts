# Crypto Trading Alerts

Free, self-hosted crypto signal alerts. Every 30 minutes a GitHub Actions
job fetches candles from exchange **public APIs** (no account, no API key),
evaluates indicator rules in ~200 lines of auditable Python, and sends
**email + Telegram** alerts. No TradingView, no ngrok, no paid services,
and it runs in the cloud even when your computer is off.

**The core signal:** EMA 9 crosses EMA 21, confirmed by RSI > 50 (bullish)
or RSI < 50 (bearish). Unconfirmed crosses are flagged as ⚠️ possible
fakeouts. Watching: BTC, ETH, SOL, BNB, ASTER (Binance) and HYPE (KuCoin),
1-hour candles.

```
GitHub Actions cron (*/30)
  → alerts/watcher.py
      → fetch closed 1h candles  (ccxt → Binance public data / KuCoin)
      → compute EMA 9/21 + RSI 14 (alerts/indicators.py, Wilder-correct, unit-tested)
      → evaluate rules            (alerts/rules.py)
      → dedupe via state.json     (committed back to the repo)
      → notify                    (Apprise → Gmail SMTP + Telegram bot)
```

## Customizing (no code changes needed)

| I want to… | Do this |
|---|---|
| Track another coin | Add one line under `symbols:` in [config.yaml](config.yaml). If it's not on Binance: `{pair: XYZ/USDT, exchange: kucoin}` |
| Change timeframe | Edit `timeframe:` in config.yaml (`15m`, `4h`, `1d`, …) |
| Add an email recipient | Append to the `ALERT_EMAILS` secret (comma-separated) |
| Add a Telegram chat/group | Append to `TELEGRAM_CHAT_IDS` (slash-separated, e.g. `111/222`) |
| Add another channel (Discord, Slack, SMS, push…) | Append an [Apprise URL](https://github.com/caronc/apprise/wiki) to `notify:` in config.yaml |
| Enable more built-in metrics | Uncomment `rsi_extreme` / `price_cross_level` in config.yaml |
| Invent a new metric | Add a small function to [alerts/rules.py](alerts/rules.py) (instructions at the top of that file) and reference it in config.yaml |

## Replicating on a new device / new account

1. `git clone <this repo> && cd <repo>`
2. `cp .env.example .env` and fill in the five values (see below).
3. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Test: `.venv/bin/python -m alerts.watcher --test-notify` → you should
   receive an email and a Telegram message.
5. Either push to your own GitHub repo and set the same five values as
   **Actions secrets** (Settings → Secrets and variables → Actions), or run
   it always-on locally with `docker compose up -d`.

### The five secrets

| Name | What it is |
|---|---|
| `GMAIL_USER` | Gmail address that sends the alerts |
| `GMAIL_APP_PASSWORD` | App password from <https://myaccount.google.com/apppasswords> — **remove the spaces** |
| `ALERT_EMAILS` | Recipient list, comma-separated |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_IDS` | Your chat ID (message the bot once, then read it from `https://api.telegram.org/bot<TOKEN>/getUpdates`), slash-separated for multiple |

## Running locally

```bash
.venv/bin/python -m alerts.watcher               # one check cycle
.venv/bin/python -m alerts.watcher --dry-run     # evaluate, send nothing
.venv/bin/python -m alerts.watcher --test-notify # test the channels
.venv/bin/python -m alerts.watcher --loop 900    # keep checking every 15 min
.venv/bin/python -m pytest tests/                # indicator math unit tests
```

## Operational notes

- **Dedup:** `state.json` records the last-alerted candle per (symbol, rule);
  the workflow commits it back, so a signal alerts exactly once. Those bot
  commits also keep the repo active, preventing GitHub's 60-day auto-disable
  of scheduled workflows.
- **Geo-blocking:** GitHub runners have US IPs and Binance blocks the US, so
  the watcher uses `data-api.binance.vision` (Binance's unrestricted public
  market-data host). HYPE isn't on Binance spot → served by KuCoin.
- **Quota:** every-30-min ≈ 1,440 Actions minutes/month, inside the
  2,000-minute free tier for private repos. For faster checks (e.g. 15m
  candles), make the repo public (unlimited minutes — secrets stay secret)
  and tighten the cron in [.github/workflows/alerts.yml](.github/workflows/alerts.yml).
- **Timing:** Actions cron can drift 5–15 min under load; worst-case alert
  latency on 1h candles is ~45 min after candle close.
- `legacy/` holds the earlier TradingView-webhook attempt (abandoned:
  webhooks need a paid TradingView plan). The Pine scripts there still work
  for eyeballing the same signals on TradingView charts manually.
