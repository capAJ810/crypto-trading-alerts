# Crypto Trading Alerts

Free, self-hosted crypto signal alerts. A self-chaining GitHub Actions
job runs continuously and evaluates the rules **seconds after each candle
closes** (candle-aligned scheduling) against exchange **public APIs**
(no account, no API key), then sends **email + Telegram** alerts.
No TradingView, no ngrok, no paid services, and it runs in the cloud even
when your computer is off.

**The core signals** (evaluated on closed 5m candles only):

- 🟢 **BUY** — EMA 9 crosses above EMA 21 · RSI > 55 · volume > 20-period
  average · close > EMA 200
- 🔴 **SELL** — EMA 9 crosses below EMA 21 · RSI < 45 · volume > 20-period
  average · close < EMA 200
- ⚠️ **WEAK** — a cross that fails some filters (each failed check listed)
- 🟡 **NEAR-BUY / NEAR-SELL** — early heads-up while EMA 9 converges toward
  EMA 21, *before* the cross (once per approach episode)
- ⏱️ **INTRABAR BUY / SELL** — chart-time alert the moment the *forming*
  candle shows the cross (RSI + trend gated, volume n/a on a partial
  candle). Unconfirmed: only the close-confirmed 🟢/🔴 alert counts.

Watching: BTC, ETH, SOL, BNB, ASTER (Binance) and HYPE (KuCoin) — alerts
land ~10–15 seconds after the candle close that confirms the signal.
(The chart shows crosses earlier because it draws the still-forming
candle; the "Candle Closed" condition deliberately waits out that
intrabar noise.)

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

## The Telegram bot is interactive and conversational

Talk to it naturally — it detects the coin and what you want:

- **"btc?"** or **"predict sol"** → a full prediction-style read
  ([alerts/analysis.py](alerts/analysis.py)): multi-timeframe trend
  (5m/1h/4h EMA + RSI), support/resistance from recent 1h swings, an
  expectation narrative ("push toward X likely; if it stalls, Y is first
  support"), and an ATR-sized example setup (entry / invalidation /
  1R–2R targets). Every read carries a "rule-based, not financial advice"
  label — it's deterministic indicator math, no AI oracle.
- **"how's eth doing"** / **"hype price now"** → quick status snapshot,
  with a 🔮 button for the full read.
- **/coins** — one button per coin; toggle which coins alert that chat
  (✅/☐), with "All on/off" shortcuts.
- **/status** — pick a coin from buttons for an immediate update.

Each allowlisted chat has its own subscription set, so two people can
watch different coins. Messages and button presses are answered by the
same scheduled job, so expect responses within ~1–2 minutes (worst
case ~5).

Only chat IDs in the `TELEGRAM_CHAT_IDS` secret may use the bot; anyone
else is refused (the bot tells them their chat ID so you can choose to add
them to the secret — slash-separated).

## Customizing (no code changes needed)

| I want to… | Do this |
|---|---|
| Track another coin | Add one line under `symbols:` in [config.yaml](config.yaml). If it's not on Binance: `{pair: XYZ/USDT, exchange: kucoin}` |
| Change timeframe | Edit `timeframe:` in config.yaml (`15m`, `4h`, `1d`, …) |
| Add an email recipient | Append to the `ALERT_EMAILS` secret: groups `;`-separated, each optionally coin-limited — `a@x.com:BTC,ETH;b@y.com` (no `:filter` = all coins) |
| Limit which coins email me | Add `:BTC,ETH` after your address in `ALERT_EMAILS` |
| Limit which coins ping me on Telegram | Send the bot `/coins`, tap to toggle ✅/☐, then Done — per chat, doesn't affect others |
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

## Self-learning (autoresearch-style loop)

The system measures itself and tunes itself, using the mutate → evaluate →
keep-if-better pattern from [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
— minus the LLM, since parameter search doesn't need one:

- **Outcome log** ([alerts/siglog.py](alerts/siglog.py)): every alert is
  recorded in `signals_log.json`; an hour later it's scored — *hit* if
  price moved ≥0.3% in the signal's direction (for 🟡 heads-ups: if the
  predicted cross actually happened). Ask the bot **/accuracy** for live
  hit-rates per rule and coin.
- **Nightly self-tune** ([alerts/tuner.py](alerts/tuner.py)): once a day a
  run replays the last 14 days of candles through a bounded parameter grid
  (RSI gates, volume window, trend EMA, gap threshold), **walk-forward
  validated**: best-on-train params are deployed only if they also beat the
  current params by ≥5 percentage points on the newest 4 days the search
  never saw. Winners land in `tuned.yaml` (per-coin overrides merged over
  config.yaml), each change is a bot-announced git commit, and `git revert`
  undoes any tune. No validation edge → nothing changes.

Overfitting is the failure mode of any market self-tuner; the validation
gate, minimum-signal counts, and bounded grid are the defenses. Expect
fewer false alerts over time, not a crystal ball.

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
- **Timing:** GitHub's cron proved unreliable for minutes-level schedules
  (hours without firing), so each run dispatches the next one at the end
  ("Chain next run" step — `workflow_dispatch` via `GITHUB_TOKEN` is a
  documented exception to GitHub's recursion guard). The `*/5` cron remains
  only as a dead-chain restarter. Within each run the watcher sleeps until
  a few seconds past each candle close and evaluates immediately
  (candle-aligned), extending briefly at the deadline if a close is
  imminent so no candle falls into the run-handoff gap.
- `legacy/` holds the earlier TradingView-webhook attempt (abandoned:
  webhooks need a paid TradingView plan). The Pine scripts there still work
  for eyeballing the same signals on TradingView charts manually.
