# Crypto Trading Alerts

Free, self-hosted crypto signal alerts. Every 5 minutes a GitHub Actions
job wakes up and checks signals 3 times (~80s apart) against exchange
**public APIs** (no account, no API key), evaluates indicator rules in
~200 lines of auditable Python, and sends **email + Telegram** alerts.
No TradingView, no ngrok, no paid services, and it runs in the cloud even
when your computer is off.

**The core signal:** EMA 9 crosses EMA 21, confirmed by RSI > 50 (bullish)
or RSI < 50 (bearish). Unconfirmed crosses are flagged as ⚠️ possible
fakeouts. Watching: BTC, ETH, SOL, BNB, ASTER (Binance) and HYPE (KuCoin),
5-minute candles — alerts typically land within ~1–3 minutes of a cross.

```
GitHub Actions cron (*/5, 3 check cycles per run)
  → alerts/watcher.py
      → answer Telegram buttons   (alerts/telegram_bot.py → subscriptions)
      → fetch closed 5m candles   (ccxt → Binance public data / KuCoin)
      → compute EMA 9/21 + RSI 14 (alerts/indicators.py, Wilder-correct, unit-tested)
      → evaluate rules            (alerts/rules.py)
      → dedupe via state.json     (committed back to the repo)
      → notify                    (Apprise → Gmail SMTP; Telegram per-chat routing)
```

## The Telegram bot is interactive

Message the bot: **/coins** shows one button per coin — tap to toggle
which coins alert that chat (✅/☐), with "All on/off" shortcuts.
**/status** shows a button per coin; tap one for a live price + EMA 9/21 +
RSI snapshot. Each allowlisted chat has its own subscription set, so two
people can watch different coins. Button presses are answered by the same
scheduled job, so expect responses within ~1–2 minutes (worst case ~5).

Only chat IDs in the `TELEGRAM_CHAT_IDS` secret may use the bot; anyone
else is refused (the bot tells them their chat ID so you can choose to add
them to the secret — slash-separated).

## Customizing (no code changes needed)

| I want to… | Do this |
|---|---|
| Track another coin | Add one line under `symbols:` in [config.yaml](config.yaml). If it's not on Binance: `{pair: XYZ/USDT, exchange: kucoin}` |
| Change timeframe | Edit `timeframe:` in config.yaml (`15m`, `4h`, `1d`, …) |
| Add an email recipient | Append to the `ALERT_EMAILS` secret (comma-separated) |
| Add a Telegram chat/group | Append its chat ID to `TELEGRAM_CHAT_IDS` (slash-separated, e.g. `111/222`) — the new chat then picks its coins with /coins |
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

- **Dedup:** `state.json` records the last-alerted candle per (symbol, rule)
  and `telegram.json` holds per-chat coin subscriptions + the bot's update
  cursor; the workflow commits both back, so a signal alerts exactly once.
  Those bot commits also keep the repo active, preventing GitHub's 60-day
  auto-disable of scheduled workflows.
- **Geo-blocking:** GitHub runners have US IPs and Binance blocks the US, so
  the watcher uses `data-api.binance.vision` (Binance's unrestricted public
  market-data host) and loads spot markets only (the futures API is blocked).
  HYPE isn't on Binance spot → served by KuCoin.
- **Quota:** the repo is **public** because */5 cron with ~4-minute runs far
  exceeds the private-repo free tier (2,000 min/month); public repos get
  unlimited free Actions minutes. Credentials stay in encrypted Actions
  secrets and are masked in logs; Apprise INFO logs are suppressed so
  recipient emails never appear in the public logs. The numeric Telegram
  chat IDs in `telegram.json` are visible but are not credentials — nothing
  can be sent or read without the bot token, which stays secret.
- **Timing:** GitHub's minimum cron interval is 5 minutes (`*/3` isn't
  possible) and can drift a few minutes under load; the 3-cycles-per-run
  loop compensates, giving ~80–100s effective checking granularity.
- `legacy/` holds the earlier TradingView-webhook attempt (abandoned:
  webhooks need a paid TradingView plan). The Pine scripts there still work
  for eyeballing the same signals on TradingView charts manually.
