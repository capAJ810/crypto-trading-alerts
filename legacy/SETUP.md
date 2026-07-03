# TradingView Webhook Alert Setup

## 1. Install dependencies

```bash
cd "Crypto trading alerts"
pip install -r requirements.txt
```

## 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

- **GMAIL_USER** — your Gmail address
- **GMAIL_APP_PASSWORD** — a Google App Password (NOT your regular password)
  - Go to: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
  - Create an app password for "Mail"
  - Paste the 16-character code

## 3. Run the server

```bash
python webhook_server.py
```

Server starts on `http://localhost:5000`. Test it:

```bash
curl http://localhost:5000/health
```

## 4. Expose it publicly with ngrok

TradingView needs a public URL to send webhooks to.

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 5000
```

You'll get a URL like `https://abc123.ngrok-free.app`. Copy it.

## 5. Set up alerts in TradingView

1. Open a chart → click **Alerts** (bell icon) → **Create Alert**
2. Set your **Condition** (e.g., RSI crosses 70, MACD signal cross, etc.)
3. In **Notifications**, enable **Webhook URL**
4. Paste: `https://your-ngrok-url.ngrok-free.app/webhook`
5. In the **Message** box, write your alert text. You can use TradingView placeholders:

```
{{ticker}} - RSI overbought! Price: {{close}} | Time: {{time}}
```

Or send JSON for richer emails:

```json
{
  "ticker": "{{ticker}}",
  "exchange": "{{exchange}}",
  "message": "RSI crossed 70 on {{ticker}}. Close: {{close}}. Time: {{time}}"
}
```

6. Click **Create** — you're live.

## TradingView alert message placeholders

| Placeholder | Value |
|-------------|-------|
| `{{ticker}}` | Symbol (e.g. BTCUSDT) |
| `{{exchange}}` | Exchange (e.g. BINANCE) |
| `{{close}}` | Closing price |
| `{{open}}` | Opening price |
| `{{high}}` / `{{low}}` | High / Low |
| `{{volume}}` | Volume |
| `{{time}}` | Alert trigger time |
| `{{interval}}` | Chart timeframe |

## Troubleshooting

- **Emails not arriving** — check spam, verify app password, confirm 2FA is on for your Google account
- **ngrok URL changes on restart** — free ngrok gives a new URL each time; restart TradingView alert with the new URL, or upgrade ngrok for a static domain
- **401 Unauthorized** — if you set `WEBHOOK_SECRET`, add header `X-Webhook-Secret: <your_secret>` in TradingView (not supported natively — leave blank unless using a proxy)
