# Session Handoff — Crypto Trading Alerts via TradingView Webhooks

## Goal

Build a system that:
1. Receives webhook alerts from TradingView (triggered by indicator conditions on a chart)
2. Sends the user an email notification via Gmail SMTP when an alert fires
3. The specific trading signal: **EMA 9 crosses EMA 21, confirmed by RSI > 50 (bullish) or RSI < 50 (bearish)**

The user's email is `manoharabhijat@gmail.com`.

---

## What Was Built (Complete)

### Files in `/Users/abhijatmanohar/Claude/Projects/Crypto trading alerts/`

| File | Status | Purpose |
|------|--------|---------|
| `webhook_server.py` | ✅ Done | Flask server that receives POST `/webhook`, sends email via Gmail SMTP |
| `.env.example` | ✅ Done | Template for Gmail credentials (`GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_TO_EMAIL`, `PORT`) |
| `requirements.txt` | ✅ Done | `flask>=3.0.0`, `python-dotenv>=1.0.0` |
| `SETUP.md` | ✅ Done | Full setup guide: install deps, configure Gmail App Password, run server, expose via ngrok, add TradingView alert |
| `ema_cross_rsi_alert.pine` | ✅ Done | Pine Script v5 indicator — EMA 9/21 cross + RSI confirmation, with 4 `alertcondition()` blocks for BUY/SELL/Weak signals |

### How the system works
```
TradingView alert fires
  → POST https://<ngrok>.app/webhook  (JSON payload with ticker, close, message)
    → webhook_server.py receives it
      → sends email to manoharabhijat@gmail.com via Gmail SMTP
```

---

## What Was NOT Completed — The Blocker

### Task: Add the Pine Script to TradingView's Pine Editor via browser automation

**Root cause:** TradingView's Monaco-based Pine Script editor converts **all whitespace characters (U+0020) to non-breaking spaces (U+00A0)** during automated text input — both via the `type` keyboard action and via `document.execCommand('insertText')`. Pine Script's compiler does **not** recognise U+00A0 as whitespace, so any code with NBSP between tokens fails with:

```
Syntax error at input 'plotshape'
```

**Approaches tried and why each failed:**

1. **`type` keyboard action** — Monaco's post-comma auto-formatter converts spaces after `,` to NBSP. Affected: all function argument lists (`plot()`, `plotshape()`, `alertcondition()`).

2. **`execCommand('insertText')`** — Worse: converts ALL spaces to NBSP, including around `and`, `>`, `<`, `=`.

3. **No-spaces-after-commas rewrite** — Removed spaces after commas. Still failed because Monaco auto-inserts a space (NBSP) between `=` and the next token in named parameters (e.g., `color=⍽color.green` where ⍽ = NBSP).

4. **Monaco `find/replace` widget via JS** — Could not open the widget programmatically; `dispatchEvent(KeyboardEvent)` on the Monaco textarea does not reliably trigger Monaco's command bindings.

5. **`window.monaco.editor.getEditors()`** — Monaco is bundled in TradingView's webpack and not exposed on `window.monaco`. Could not access the editor model programmatically.

6. **`mcp__computer-use__write_clipboard`** — Clipboard write via computer-use tool timed out (request_access never resolved).

7. **`pbcopy` via bash** — Bash runs in a sandboxed Linux environment, not the user's macOS. `pbcopy` not available.

**Secondary blocker:** TradingView enforces a **one-active-session** policy per account. The Claude Chrome extension opens a competing session, which repeatedly triggered TradingView's "Session disconnected" modal, interrupting automation.

---

## Current State of the Pine Editor

The Pine Editor in TradingView is open with the script typed in but **containing NBSP characters** that cause the compile error. The user is on a **Basic TradingView plan** (max 2 indicators per chart). They have already removed their existing indicators to make room.

---

## Plan for Claude Code to Resolve

### Option A — Preferred: Direct Monaco model manipulation (investigate)

TradingView's webpack bundle contains the Monaco editor. The goal is to find the editor instance and call `model.setValue(cleanScript)` directly.

**Approach:**
1. Open browser DevTools on the TradingView chart page
2. Search the webpack bundle for Monaco's editor registry: try `Object.values(webpackChunk_N_E)` or iterate over `webpackChunk` to find the module that exports `getEditors`
3. Once the Monaco editor API is found, call `editors[0].getModel().setValue(script)` with the clean Pine Script

**Known facts:**
- `window.MonacoEnvironment` exists — Monaco IS loaded
- The `.monaco-editor` element exists in the DOM
- The `textarea.inputarea.monaco-mouse-cursor-text` is Monaco's input element
- `window.monaco` is NOT exposed directly

### Option B — Fallback: OS-level clipboard write

Use a method that writes to the macOS system clipboard (not the sandboxed bash env), then `Cmd+V` to paste:

```python
# Run on the user's actual machine (not in sandbox)
import subprocess
script = open('ema_cross_rsi_alert.pine').read()
subprocess.run('pbcopy', input=script.encode(), check=True)
```

Then automate `Cmd+V` in the Pine Editor via computer use.

This should bypass Monaco's text-processing entirely since clipboard paste goes through the browser's native paste handler, not Monaco's `insertText` command.

### Option C — Fallback: Ask user to paste manually

If both above fail, the simplest path: give the user `ema_cross_rsi_alert.pine` (already saved), tell them to open it in any text editor, Cmd+A, Cmd+C, then paste in TradingView's Pine Editor. Manual paste from clipboard avoids the NBSP issue because the browser's paste handler delivers raw clipboard bytes to Monaco.

**Note for manual paste:** Make sure the user pastes into a **freshly cleared editor** (Cmd+A → Delete first), otherwise old content gets mixed in.

---

## After the Script is Added — Next Steps

Once the script compiles and is added to the chart:

1. **Create alerts in TradingView:**
   - Click the bell icon → Create Alert
   - Condition: pick the indicator → select "Confirmed BUY - EMA cross + RSI > 50"
   - Enable Webhook URL: `https://<ngrok-url>/webhook`
   - Leave message field as-is (it sends pre-formatted JSON)
   - Repeat for SELL signal

2. **Run the webhook server:**
   ```bash
   cd "Crypto trading alerts"
   cp .env.example .env   # fill in GMAIL_USER and GMAIL_APP_PASSWORD
   pip install -r requirements.txt
   python webhook_server.py
   ```

3. **Expose publicly via ngrok:**
   ```bash
   ngrok http 5000
   # Copy the https URL → paste into TradingView's webhook field
   ```

4. **Test:** Trigger a manual alert in TradingView → confirm email arrives at manoharabhijat@gmail.com

---

## Key Context About the User

- TradingView plan: **Basic** (max 2 indicators per chart) — the new Pine Script counts as 1 indicator combining EMA + RSI, so this is fine
- Email: `manoharabhijat@gmail.com`
- Project folder: `/Users/abhijatmanohar/Claude/Projects/Crypto trading alerts/`
- The user wants more alert conditions in future (they mentioned EMA cross + RSI as the first "command" — more signals are planned)
