# Crypto Trading Alerts — Session Handoff

> **Read this top-to-bottom before touching anything.** It is the ground truth
> for where the project stands and what every file does.

---

## What this system does

Watches BTC, ETH, SOL, BNB, ASTER, and HYPE/USDT for EMA 9/21 cross signals
on 5-minute candles. Sends tiered email (Gmail SMTP via Apprise) and
Telegram alerts — one per signal per candle, never duplicates. Runs 24/7
on GitHub Actions with zero cost (no API keys, all public exchange data).

A **parallel 15-minute pass** runs alongside the 5m stream (same coins, same
subscribers) — every alert is tagged `5m` or `15m` in its title. See
"Parallel 15m pass" below. Subscribers receive both streams.

**The full signal hierarchy (highest to lowest confidence):**

| Alert type | Trigger | Email style |
|---|---|---|
| 🟢 BUY / 🔴 SELL | EMA9 crosses EMA21 · RSI>55/<45 · vol>avg · close>/<EMA200. Closed candle. | 24px bold green/red, "✅ CONFIRMED \|" subject prefix |
| ⚠️ WEAK BUY/SELL | Cross on closed candle but one or more filters failed (listed in body) | 12.5px muted gray, no prefix |
| ⏱️ INTRABAR BUY/SELL | Cross visible on the FORMING candle right now (can still fade) | 12.5px muted gray |
| 🟡 NEAR-BUY/SELL | EMA9 closing in on EMA21 (gap <0.08% and shrinking), no cross yet | 12.5px muted gray |

---

## Repository

**GitHub repo:** `capAJ810/crypto-trading-alerts` (private)  
**Branch:** `main` — CI runs and commits back to this branch  
**Local path:** `/Users/abhijatmanohar/Claude/Projects/Crypto trading alerts/`  
**gh CLI path:** `~/.local/bin/gh` — add to PATH if needed  

---

## How CI works (critical — cron does NOT drive this)

GitHub's scheduled cron proved completely unreliable for sub-hourly schedules
(hours of silence with no runs). **The solution is self-chaining:**

Each run's last step (`Chain next run`) calls:
```
gh workflow run alerts.yml --ref main -R capAJ810/crypto-trading-alerts
```
via `GITHUB_TOKEN` (a documented exception to GitHub's recursion guard).
The `concurrency: group: crypto-alerts` setting queues the next run until
the current one finishes, so at most one run is ever active.

The `*/5` cron in `alerts.yml` is a **dead-chain restarter only** — it fires
if somehow the chain dies. Normal operation never relies on it.

**Each run:**
1. Checks out the repo
2. Runs `python -m alerts.watcher --run-for 250` (4 minutes 10 seconds) — the 5m stream
3. Inside `--run-for`, the watcher aligns to candle closes (BUFFER=4s past each 5m close), evaluates rules, polls Telegram every ~30s in between
4. Runs the **15m pass** (one-shot, see below)
5. Runs `python -m alerts.tuner --if-due` (nightly self-tune, skips if last run <24h ago)
6. Commits `state.json`, `telegram.json`, `signals_log.json`, `tuned.yaml`, `state-15m.json`, `signals_log-15m.json` back to main
7. Dispatches the next run

### Parallel 15m pass

Runs alongside the 5m stream **inside the same job** (not a second workflow —
two independent workflows would race on every push to main). After the 5m
`--run-for`, one step runs:
```
python -m alerts.watcher --config config-15m.yaml --state state-15m.json \
  --siglog signals_log-15m.json --tuned tuned-15m.yaml --no-bot-poll
```
- **One-shot** (no `--run-for`): evaluates the latest CLOSED 15m candle. Since
  runs chain every ~5 min and 15m candles close every 15 min, each 15m close is
  caught by the first run after it — ≤~5 min latency, fine for a 15m timeframe.
- **Own state/log files** (`state-15m.json`, `signals_log-15m.json`) so dedup
  never collides with the 5m stream.
- **Intrabar on 15m**: the one-shot's `intrabar()` call checks the FORMING 15m
  candle once per run (~5 min), so 15m ⏱ intrabar alerts land at ~5-min
  resolution (vs the 5m loop's ~30s). A 15m candle forms over 15 min, so a
  cross is usually still visible at the next check; `state-15m.json` dedup
  (forming-candle ts + side) prevents repeats. For true ~30s 15m intrabar the
  15m pass would need its own `--run-for` loop — deferred (serializing two
  --run-for sessions eats the 10-min job budget and creates blind windows).
- **`--no-bot-poll`**: broadcasts alerts but does NOT drain/save Telegram updates
  — the 5m pass owns the getUpdates offset + telegram.json, so the two never
  fight over it. The 15m pass still reads telegram.json for subscriptions.
- **`--tuned tuned-15m.yaml`** points at a non-existent file on purpose, so the
  5m tuner's `tuned.yaml` doesn't bleed into the 15m params. The 15m pass is not
  self-tuned; it uses `config-15m.yaml` params as-is.
- Alerts are tagged `15m` in the title (vs `5m`), so subscribers can tell them
  apart. Everyone who gets 5m alerts also gets 15m alerts for their coins.
- To disable: delete the "Run 15m watcher" step and drop `state-15m.json` /
  `signals_log-15m.json` from the persist `git add`.

---

## File map

```
alerts/
  __init__.py          empty package marker
  indicators.py        EMA (ewm adjust=False), Wilder RSI (_rma SMA-seeded), ATR
  market.py            ccxt exchange cache; Binance geo-block workaround; fetch_closed_candles
  rules.py             Signal dataclass + 5 rule functions + RULES dict + INTRABAR_RULES set
  analysis.py          Multi-TF (5m/1h/4h) coin reads for Telegram bot; structure-aware example setup (_rr_setup: targets at swing resistance/support, stop past the adverse level, ATR fallback+guardrail)
  notify.py            Apprise fan-out; tier-based HTML email; per-recipient coin filtering
  telegram_bot.py      Interactive bot: /coins /mode /status /email /guide /accuracy; conversational NLP
  siglog.py            Signal outcome log: append, score_pending, stats_text (/accuracy)
  watcher.py           Main entry point: run_once, run_intrabar, candle-aligned --run-for loop
  tuner.py             Nightly walk-forward self-tuner (autoresearch pattern)

tests/
  test_indicators.py   EMA/RSI math vs known reference values
  test_new_rules.py    ema_cross_rsi, ema_cross_soon, rsi_extreme, price_cross_level
  test_notify.py       parse_alert_emails, email_recipients routing, HTML tiers
  test_once_per_side.py  once_per_side re-arm logic (this was a real bug, now fixed)
  test_selflearn.py    tuner backtest and walk-forward validation
  test_telegram_bot.py  bot state, toggle, email link flow
  test_analysis.py     read_frame, bias_score, compose_* on synthetic data

config.yaml            Symbols, rules, notify URLs (no secrets here, only ${VAR} refs)
config-15m.yaml        Parallel 15m pass: same symbols + full rule set (intrabar checked once/run against the forming 15m candle, ~5-min resolution)
state-15m.json         Dedup for the 15m pass (separate from state.json)
signals_log-15m.json   Signal outcome log for the 15m pass (separate from signals_log.json)
state.json             Dedup: last alerted candle per (exchange|pair|rule)
telegram.json          Bot state: chat subs, email links, getUpdates offset
tuned.yaml             Self-tuner output: per-pair param overrides
signals_log.json       Signal outcome log (appended each run, scored after 12 candles)
requirements.txt       ccxt>=4.3, pandas>=2.0, apprise>=1.9, PyYAML>=6.0, python-dotenv>=1.0, requests>=2.31
.github/workflows/alerts.yml  CI workflow (see above)
```

---

## GitHub Secrets (all required)

| Secret | Value / format |
|---|---|
| `GMAIL_USER` | `manoharabhijat@gmail.com` |
| `GMAIL_APP_PASSWORD` | Gmail app password (16-char, no spaces) |
| `ALERT_EMAILS` | `manoharabhijat@gmail.com;realestatewithsajal@gmail.com` — semicolon-separated; can add `:BTC,ETH` coin filter suffix per address |
| `TELEGRAM_BOT_TOKEN` | Bot token for @AstroTaco_Bot |
| `TELEGRAM_CHAT_IDS` | `1023807933/6381685292/60354093` — slash-separated |

`TELEGRAM_CHAT_IDS` is currently `1023807933/6381685292/60354093` (chat
`60354093` was added to the allowlist secret via `gh secret set` on 2026-07-04).
`telegram.json` already had this chat with default subs; the secret is the
allowlist gate.

---

## Telegram bot users

| Chat ID | Who | Email linked | Coins (as of last CI run) |
|---|---|---|---|
| `1023807933` | Owner (Abhijat) | manoharabhijat@gmail.com | BTC, ASTER, HYPE |
| `6381685292` | Sajal | realestatewithsajal@gmail.com | All 6 |
| `60354093` | New user (just added) | not linked yet | All 6 (default) |

Bot: **@AstroTaco_Bot** (pre-existing bot, not created for this project)  
State file: `telegram.json` — committed by CI after each run

**Bot commands:**
- `/coins` — toggle which coins alert this chat (controls both Telegram AND email if linked)
- `/alerts` — personalize alert types (✅ confirmed / weak / intrabar / near) and candle sizes (✅ 5m / 15m). Stored per chat as `cats` and `tfs` lists in telegram.json; absent keys = everything (backward compatible). Gates BOTH Telegram (`chats_for`) and linked email (`link_filters` → `Notifier._prefs_pass`). Also reachable via the 🎚 Alert types button. `category_for_side()` in telegram_bot.py buckets a Signal.side into confirmed/weak/intrabar/near; the watcher passes category+timeframe on every broadcast/send. ⚠️ `ALERT_TIMEFRAMES` must list the timeframes the CI passes actually run (5m, 15m) — update it if configs change.
- `/mode` — choose delivery: 📱 Telegram only · 📧 Email only · 🔔 Both (default). Changeable anytime; stored as `mode` in each chat's telegram.json entry (absent = "both")
- `/status` — pick a coin for an immediate 3-line snapshot
- `/email you@example.com` — **self-service**: any allowlisted chat can register its OWN email (validated by regex, no owner pre-approval needed); `/email` alone shows current address; `/email off` removes it. Also reachable via the 📧 Email button, which prompts for the address and captures the next plain message (per-chat `awaiting: "email"` flag, cleared by any command or other button). Registered addresses persist in telegram.json and are merged into email routing even when NOT in `ALERT_EMAILS`.
- `/guide` — glossary of every alert type (confirmed/weak/intrabar/near) and trading term (long/short, bullish/bearish, EMA/RSI/volume/ATR, support/resistance)
- `/accuracy` — hit rate of past alerts (from signals_log.json)
- `/help` — help text
- Conversational: "btc?", "how's eth doing", "predict sol" → full multi-TF read

The `/coins` keyboard footer now has 🔔 Alert mode and 📖 Guide buttons alongside 📊 Status / 👍 Done.

---

## Per-recipient email coin filtering

Resolution at send time (`Notifier.email_recipients`) merges two sources:

- **`ALERT_EMAILS` secret** (owner-set), honoring any static `:BTC,ETH` filter.
- **Self-service addresses** users registered via the bot's `/email`, surfaced
  by `link_filters()` as email→coin-set — included even if the address is NOT
  in `ALERT_EMAILS` (an address in both is owned by `ALERT_EMAILS`; the static
  filter wins and the self-service copy is skipped).

Per-address precedence:

1. **Static filter** in `ALERT_EMAILS` secret wins if present:
   `manoharabhijat@gmail.com:BTC,ETH` → only BTC and ETH emails
2. **Bot link** — if no static filter and the address is linked via `/email` in
   the bot, that chat's `/coins` selections filter email too. **Delivery mode
   also gates this:** a chat in `mode: telegram` maps its linked address to an
   **empty coin set**, so it gets no email even while linked; `mode: email` or
   `both` route email normally.
3. **No filter** → all coins

The `link_filters()` closure in `watcher.py:300` feeds the live bot state
(subs + mode) into `Notifier` at construction time. Telegram delivery is gated
separately by `TelegramBot.chats_for()`, which drops chats whose mode is
`email`.

---

## Self-tuner (alerts/tuner.py)

Runs nightly via `--if-due` (skips if `tuned.yaml:last_run` < 24h ago).

**Walk-forward approach:**
- Grid search over bounded params (GRID_CROSS, GRID_SOON in tuner.py)
- Train on last 10 days, validate on subsequent 4 days
- A new param set is **only accepted** if val-window precision > current + 5pp with ≥3 val signals
- Accepted changes → `tuned.yaml`, announced in Telegram

**Current tuned.yaml state (as of 2026-07-03):**
```yaml
BTC:   rsi_buy→52, rsi_sell→40, volume_avg→30, gap_pct→0.05
ETH:   rsi_buy→60, volume_avg→10, gap_pct→0.05
ASTER: rsi_buy→52, rsi_sell→40, gap_pct→0.05
BNB:   gap_pct→0.05
HYPE:  gap_pct→0.05
SOL:   (no overrides, using config.yaml defaults)
```

Watcher merges these on top of config.yaml params per pair per rule at runtime
(`load_tuned()` in `watcher.py`).

---

## Key technical decisions (don't change without understanding why)

### Binance geo-block
GitHub Actions runs on US IPs. `api.binance.com` returns HTTP 451 for US IPs.
**Fix:** `market.py` overrides ccxt's Binance `api.public` URL to
`https://data-api.binance.vision/api/v3` (Binance's public data mirror,
no geo-block). Also sets `fetchMarkets: {types: ["spot"]}` to avoid
`fapi.binance.com` (futures API, also geo-blocked, not needed).

### HYPE/USDT
Not listed on Binance spot. Config uses `{pair: HYPE/USDT, exchange: kucoin}`.
KuCoin does not geo-block.

### RSI implementation
Wilder's RMA smoothing (SMA seed at index `length`, then `(prev*(len-1)+val)/len`),
matching TradingView's `ta.rsi()`. The "14" in status messages is the
**calculation period**, not a signal threshold. Signal thresholds are `rsi_buy`
(default 55) and `rsi_sell` (default 45).

### once_per_side re-arm
`ema_cross_soon` uses `once_per_side: true` to avoid spamming NEAR alerts every
candle during an approach. The re-arm fix (watcher.py:139-142): when the rule
returns `None` on a **new candle timestamp**, `state[key]["side"]` is cleared so
the next approach episode gets a fresh alert. Without this, a NEAR-SELL from a
past episode would latch the side forever.

### Candle-aligned scheduling
`--run-for 250` runs a session aligned to candle closes. The watcher computes
`seconds_to_next_close = tf_sec - ((now - BUFFER) % tf_sec)` and wakes up
immediately after each close (+BUFFER=4s for exchange finalization). Telegram
updates are polled every ~30s in between. This eliminates the old 80s blind
polling latency.

### Intrabar rules
`INTRABAR_RULES = {"ema_cross_intrabar"}` — this set is checked by `run_intrabar()`
which calls `fetch_closed_candles(..., drop_forming=False)`. Every other rule sees
only closed candles (`drop_forming=True`).

---

## How to run locally

```bash
cd "/Users/abhijatmanohar/Claude/Projects/Crypto trading alerts"
cp .env.example .env   # fill in secrets
pip install -r requirements.txt

# Dry-run (evaluate signals, print, don't send or save state):
python -m alerts.watcher --dry-run

# Send test notification to all channels:
python -m alerts.watcher --test-notify

# One real check cycle (sends if signals fire, updates state.json):
python -m alerts.watcher

# Candle-aligned session (same as CI):
python -m alerts.watcher --run-for 250

# Force re-alert (ignore dedup state):
python -m alerts.watcher --force

# Run all tests:
pytest tests/ -v
```

---

## Indicators math (for debugging signal disagreements with TradingView)

```python
# indicators.py

def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def _rma(series, length):   # Wilder's — SMA seed at index `length`
    # out[length] = mean(vals[1:length+1])
    # out[i] = (out[i-1] * (length-1) + vals[i]) / length

def rsi(series, length=14):
    delta = series.diff()
    avg_gain = _rma(delta.clip(lower=0), length)
    avg_loss = _rma(-delta.clip(upper=0), length)
    return 100 - 100 / (1 + avg_gain / avg_loss)

def atr(df, length=14):     # Wilder ATR using _rma on true range
```

---

## Adding things

### Add a coin
In `config.yaml`:
```yaml
symbols:
  - NEW/USDT                                    # if on Binance spot
  - {pair: NEW/USDT, exchange: kucoin}          # if not on Binance
```
Add the name alias to `COIN_NAMES` in `telegram_bot.py` so the bot understands
"new" or "newcoin" in conversational queries.

### Add an email recipient
Update the `ALERT_EMAILS` GitHub secret. Append `;newperson@email.com` (all coins)
or `;newperson@email.com:BTC,ETH` (filtered). No code change needed.

They can then `/email newperson@email.com` in the bot to link it (if you add their
chat ID to `TELEGRAM_CHAT_IDS` too).

### Add a Telegram user
1. Get their chat ID (they can message the bot; it replies with their ID if unauthorized)
2. Update `TELEGRAM_CHAT_IDS` secret: append `/NEWCHATID`
3. No code change needed — `telegram.json` gets their default entry on first bot interaction

### Add a signal rule
1. Write `def my_rule(df, params) -> Optional[Signal]` in `alerts/rules.py`
2. Add to `RULES` dict at the bottom
3. Enable in `config.yaml` under `rules:`
4. If it needs the forming candle, add its name to `INTRABAR_RULES`

### Add a notification channel
Any Apprise URL works. Add to `notify:` in `config.yaml`:
```yaml
notify:
  - "mailto://..."    # existing email
  - "discord://..."   # new Discord webhook
  - "slack://..."     # Slack
```
See https://github.com/caronc/apprise/wiki for 100+ supported services.

---

## Known gotchas

- **Keyboard buttons must carry explicit target state, not "toggle".** The bot
  polls every ~30s (minutes during run handoffs), so users double-tap; and if a
  run dies before committing telegram.json, the getUpdates offset regresses and
  Telegram REDELIVERS presses to the next run. Blind toggles then flip settings
  back ("unticking 5m re-ticked itself" — real bug, 2026-07-05). All t|/a|/f|
  buttons now send `…|on` / `…|off` (`_apply_toggle`); two-part legacy data
  from old keyboards still toggles. Keep this pattern for any new toggle.

- **`gh` not in PATH in this shell.** The binary lives at `~/.local/bin/gh`.
  In Bash: `export PATH="$HOME/.local/bin:$PATH"`. Or use full path.
- **`git push` may fail** if CI committed during your session. Always
  `git pull --rebase` before pushing local changes.
- **telegram.json conflicts** are common — CI writes it every run. When rebasing,
  resolve by taking both sets of changes (don't drop either party's chat entries).
- **tuned.yaml last_run check:** tuner uses a `<86000s` window (not exactly 24h)
  to allow for run timing jitter. Don't manually edit `last_run` to force a re-tune;
  just run `python -m alerts.tuner` without `--if-due`.
- **Intrabar alerts for SELL** require `rsi < rsi_sell` (default 45) AND
  `close < EMA200`. If either fails, no intrabar SELL fires. This is correct — a
  cross on the forming candle without RSI confirmation isn't worth alerting.
- **Intrabar flicker guards** (post-audit): `min_gap_pct` + `whipsaw_candles`
  (rule params) and `confirm_polls` (rule_cfg key, handled in run_intrabar like
  once_per_side). Rule defaults are OFF — the values live in the configs. The
  `pending` record in state.json is the confirm_polls candidate; it's normal
  to see it come and go between runs.
- **ASTER/USDT** on Binance — verify it still exists on Binance spot if you see
  fetch errors. It was listed but low-liquidity coins get delisted.

---

## What was done in the last session

**Intrabar flicker guards (2026-07-06, latest — audit + fix).** User got a
false ⏱ INTRABAR SELL ASTER 5m at 07:31 UTC. Audit (replayed spot 1m/5m data)
showed the watcher's ~30s poll caught a single sub-minute tick to 0.6360: at
that instant live EMA9 dipped 0.0057% under EMA21, RSI 44.6 (gate 45), 60s
after a bullish cross had confirmed on the 07:25 close (the WEAK BUY). All
gates passed on a tick that left only a wick — mechanically correct, noise.
Scored intrabar precision was 50% (46/92). Fixes, all in ema_cross_intrabar
params + one watcher key:
- `min_gap_pct: 0.0125` — live |EMA9−EMA21| ≥ 0.0125% of price (threshold
  chosen by replaying all 92 logged intrabar signals: 50%→~60% precision,
  ~64% with whipsaw; kills the audited 0.0057% alert, keeps the verified
  0.0165% hit). Rule default 0 = off; configs set it.
- `whipsaw_candles: 2` — skip a cross AGAINST a cross confirmed on the last
  2 closed candles (the audited alert faded a 60s-old bullish cross).
- `confirm_polls: 2` (rule_cfg key, 5m config only) — run_intrabar stashes a
  `pending {candle, side, seen}` candidate in state; alerts only when the
  cross survives N consecutive ~30s checks; a vanished cross clears the
  candidate. NOT set on 15m (checks are ~5 min apart; gap/whipsaw guard it).
- Alert body now includes EMA9/EMA21/gap% for instant future audits.
- Regression-verified: the exact 07:31 scenario is BLOCKED, the 08:35 hit
  still FIRES. tests/test_intrabar_guards.py covers all three guards. 75 tests.
- Expect intrabar volume to drop well over half — that's the point. If too
  quiet, lower min_gap_pct toward 0.0075 before touching the other guards.
- Note: user charts may be PERP with EMA(7)/21 (Binance app default overlay);
  alerts are SPOT EMA(9)/21 — flag this when someone reports a "false" alert.

**15m intrabar enabled (2026-07-05).** Added `ema_cross_intrabar` to
`config-15m.yaml` (had been intentionally omitted). The piggyback one-shot's
`intrabar()` call now fires ⏱ 15m intrabar alerts against the forming candle at
~5-min resolution (see the Parallel 15m pass caveat). No workflow change needed
— the step already runs `intrabar()`. test_config_15m flipped to assert the
rule is present.

**Idempotent keyboard presses (2026-07-05 — bugfix).** User reported
unticking "5m candles" re-ticked itself. Root cause: toggle-semantics buttons
+ ~30s poll latency → impatient double-taps undo themselves; and Telegram
redelivery after a failed run re-applies presses. Fix: `_apply_toggle()` +
explicit `|on`/`|off` targets in all t|/a|/f| callback_data (legacy two-part
data still toggles). See Known gotchas. Tests: idempotent replay ×3,
keyboard target rendering, legacy toggle. Suite: 70 tests.

**Personalized alerts (2026-07-05).** Each chat can now filter WHAT
kind of alerts it gets, not just which coins:
- `/alerts` command + 🎚 Alert types button → toggles for the 4 alert-type
  categories (confirmed/weak/intrabar/near) and candle sizes (5m/15m).
- Stored as `cats`/`tfs` lists per chat entry; absent = all (existing users
  unaffected). Callbacks `a|<cat>` and `f|<tf>`.
- `chats_for()`/`broadcast()` take optional `category`/`timeframe`;
  `category_for_side()` maps Signal.side → category. Watcher passes both from
  `run_once`/`run_intrabar` (the 15m pass passes timeframe="15m").
- Email honors the same prefs: `link_filters()` now returns
  `{email: {"coins", "cats", "tfs"}}` (legacy bare-set still accepted via
  `Notifier._norm_link`), and `email_recipients(pair, category, timeframe)`
  gates linked addresses through `_prefs_pass`. Static ALERT_EMAILS recipients
  without a link are unaffected.
- Tests: category mapping, toggle flow, confirmed-only/15m-only profile,
  email type+tf gating; FakeNotifier signature updated. Suite: 67 tests.

**Structure-aware example setup (2026-07-05).** The 🔮 Full read's
example entry/invalidation/targets (`analysis.py:_setup`) used to be pure ATR
multiples (±1.5·ATR stop, +1.5/+3·ATR targets = fixed 1R/2R). Now `_rr_setup`
anchors to swing structure:
- Targets snap to resistance (long) / support (short): T1 = nearest level if
  ≥0.5·ATR ahead, T2 = wider level if clearly beyond T1; ATR fills in on a
  breakout with no level overhead.
- Invalidation sits 0.25·ATR past the adverse level (support for long,
  resistance for short) when it's within 2.5·ATR; else a 1.5·ATR stop. Risk is
  floored at 0.75·ATR so a level hugging price can't make a hair-trigger stop.
- R-multiples are computed from the ACTUAL stop distance (e.g. "~0.9R / 1.6R"),
  not assumed 1R/2R.
- **Min 1:2 SL/TP rule:** the final target must offer ≥2R. If the structural
  T2 falls short, it's extended to the 2R point and the message flags that T2
  "is set by the 1:2 rule and sits past the mapped levels" (trail/take early
  if momentum stalls). Structure that already beats 2R is left untouched.
- `_setup(frames)` → `_setup(frames, lv)`; tests in test_analysis.py. 63 total.

**Parallel 15m pass (2026-07-05).** Added a 15-minute reference-candle
stream running alongside the existing 5m stream (see "Parallel 15m pass" above).
- New `config-15m.yaml` (same symbols, timeframe 15m; intrabar added later),
  `state-15m.json` (`{}`), `signals_log-15m.json` (`[]`).
- `watcher.py`: new `--no-bot-poll` flag (broadcast only; skips
  `process_updates`/`save_tg`) so the 15m pass doesn't fight the 5m pass over
  the bot offset / telegram.json.
- `.github/workflows/alerts.yml`: new "Run 15m watcher" one-shot step; persist
  step now also `git add`s `state-15m.json` and `signals_log-15m.json`.
- Tests: `tests/test_config_15m.py` guards that the 15m symbols/timeframe stay
  in sync with the 5m config. Local dry-run confirmed real 15m candles fetch and
  evaluate (BTC/ETH/ASTER fired `15m`-tagged signals). Suite now 58 tests.
- Trade-off chosen: piggyback in one workflow (robust, no push races) over a
  literal second parallel workflow. Latency ≤~5 min on 15m closes.

**Self-service email registration (2026-07-04).** Previously `/email`
only let a chat link an address the owner had ALREADY put in `ALERT_EMAILS`;
users couldn't add their own. Now any allowlisted chat registers its own email:
- `telegram_bot.py`: `EMAIL_RE` validation; `_register_email()` helper (accepts
  any valid address, `off`/`none`/`remove` to clear); `_on_email_command`
  rewritten (no more `allowed_addresses` gate); 📧 Email button (`m|email`) arms
  a per-chat `awaiting:"email"` prompt captured by the next plain message; any
  command/other button cancels it.
- `notify.py`: `email_recipients()` now merges self-service addresses (from
  `link_filters`) with `ALERT_EMAILS`, deduped (ALERT_EMAILS owns shared addrs).
- Trust model: the `TELEGRAM_CHAT_IDS` allowlist still gates who can reach the
  bot, and mail is sent from the owner's own SMTP — so a valid-format check is
  enough. No email-verification handshake (acceptable for this trusted group;
  an allowlisted user could enter someone else's address).
- Tests: `test_self_service_email_add_change_remove`,
  `test_email_button_prompts_then_captures_reply`,
  `test_command_cancels_pending_email_prompt` (test_telegram_bot.py),
  `test_self_registered_email_not_in_alert_emails_is_included` (test_notify.py).

Also chat `8912039448` added to the `TELEGRAM_CHAT_IDS` allowlist secret
(`1023807933/6381685292/60354093/8912039448`) via `gh secret set`.

### Earlier this session — three changes (2026-07-04):

1. **Chat `60354093` fully allowlisted.** `TELEGRAM_CHAT_IDS` secret set to
   `1023807933/6381685292/60354093` via `gh secret set` (gh was available this
   session at `~/.local/bin/gh`, authed as `capAJ810`). No longer pending.

2. **Delivery-mode selection (`/mode`).** Each chat can choose 📱 Telegram only /
   📧 Email only / 🔔 Both (default), changeable anytime. Stored as `mode` in the
   chat's telegram.json entry (absent = "both", fully backward-compatible).
   - `telegram_bot.py`: `ALERT_MODES`, `_mode_keyboard`, `_chat_mode`,
     `_channels_desc`; `chats_for` now drops `email`-mode chats; `/mode` command
     + `md|<mode>` callback + `m|mode` button; picking email/both with no linked
     address nudges the user to `/email`.
   - `watcher.py`: `link_filters()` maps a `telegram`-mode chat's address to an
     empty coin set so email is suppressed for it.

3. **Glossary (`/guide`).** `GUIDE_TEXT` constant + `/guide` command + 📖 Guide
   button explains every alert type (confirmed/weak/intrabar/near buy/sell) and
   trading term (long/short, bullish/bearish, EMA/EMA200/RSI/volume/ATR,
   support/resistance). ~2.2k chars, under Telegram's 4096 limit.

Tests added: `test_email_only_mode_stops_telegram_alerts`,
`test_mode_callback_sets_mode_and_warns_without_email`,
`test_guide_command_sends_glossary` (test_telegram_bot.py),
`test_empty_link_filter_suppresses_email` (test_notify.py). Suite now 52 tests.

Before that, an earlier session implemented self-serve email coin selection:
- `alerts/notify.py`: `Notifier.email_recipients(pair)` resolves per-address at
  send time using static secret filter > bot link filter > no filter
- `alerts/telegram_bot.py`: `/email` command links/unlinks email addresses;
  "Done" button shows linked email
- `alerts/watcher.py`: `link_filters()` closure feeds live bot state into `Notifier`
- Both original users pre-linked in `telegram.json`

---

## Test suite (75 tests, all passing)

```
tests/test_indicators.py      EMA/RSI numeric accuracy vs reference
tests/test_new_rules.py       All 5 rule functions
tests/test_notify.py          Email routing, HTML tiers, tier_for_side
tests/test_once_per_side.py   Re-arm logic (regression test for a real bug)
tests/test_selflearn.py       Tuner backtest + walk-forward
tests/test_telegram_bot.py    Bot state, toggles, /email flow
tests/test_analysis.py        Multi-TF reads and compose functions
```

Run with `pytest tests/ -v` before pushing any change to rules, indicators,
notify, or the bot.
