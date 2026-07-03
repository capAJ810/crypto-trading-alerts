"""Interactive, conversational Telegram bot.

Runs in the same scheduled process as the watcher (no server needed):
each cycle drains pending updates with getUpdates, answers commands,
button presses, and plain-language questions, and persists state to
telegram.json (committed by CI).

Talk to it naturally: "btc?", "how's eth doing", "predict sol" — it
detects the coin and whether you want a quick status or a full
prediction-style read (levels + expectation + example setup, computed
by alerts/analysis.py).

Security: only chat IDs in the TELEGRAM_CHAT_IDS allowlist (slash-
separated env/secret) may use the bot. Anyone else is shown their chat
ID so the owner can decide to add them.

Commands:  /start /help — welcome + coin picker
           /coins       — choose which coins alert this chat (✅/☐ toggles)
           /status      — pick a coin for an immediate update
"""

import json
import logging
import os
import re
from typing import Callable, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 Hey! I watch the market for you and ping you when EMA 9 crosses "
    "EMA 21 (RSI-confirmed) on your coins.\n\n"
    "Just talk to me:\n"
    "• \"btc?\" or \"how's eth doing\" → live update\n"
    "• \"predict sol\" → my full read: trend, levels, example setup\n\n"
    "/coins — choose which coins alert you\n"
    "/status — pick a coin for an immediate update\n"
    "/accuracy — how often my alerts have been right\n"
    "/help — this message"
)

# Common names people type for the bases we track (extend freely).
COIN_NAMES = {
    "bitcoin": "BTC", "btc": "BTC", "xbt": "BTC",
    "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "bnb": "BNB", "binance": "BNB",
    "aster": "ASTER",
    "hype": "HYPE", "hyperliquid": "HYPE",
}

PREDICT_WORDS = ("predict", "prediction", "forecast", "expect", "analysis",
                 "analyse", "analyze", "signal", "setup", "read", "levels",
                 "target", "should i", "buy", "sell", "long", "short")
STATUS_WORDS = ("price", "status", "update", "now", "quick", "how", "doing",
                "chart", "current")


def parse_intent(text: str, symbols: List[str]):
    """Map free text to (pair, kind) — kind 'status' | 'predict' — or
    (None, None) when no tracked coin is mentioned."""
    t = text.lower()
    words = set(re.findall(r"[a-z]+", t))

    pair = None
    aliases = dict(COIN_NAMES)
    for p in symbols:  # symbols' own bases always work, e.g. "aster"
        aliases.setdefault(p.split("/")[0].lower(), p.split("/")[0])
    for alias, base in aliases.items():
        if alias in words:
            match = next((p for p in symbols if p.split("/")[0] == base), None)
            if match:
                pair = match
                break
    if pair is None:
        return None, None

    if any(w in t for w in PREDICT_WORDS):
        return pair, "predict"
    if any(w in t for w in STATUS_WORDS):
        return pair, "status"
    return pair, "predict"  # bare coin mention → give the full read


def _mask(chat_id) -> str:
    s = str(chat_id)
    return "…" + s[-4:] if len(s) > 4 else s


class TelegramBot:
    def __init__(self, token: str, allowed_chats: List[str], symbols: List[str],
                 insight_fn: Optional[Callable[[str, str], str]] = None,
                 stats_fn: Optional[Callable[[], str]] = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.allowed = {str(c) for c in allowed_chats}
        self.symbols = symbols
        # insight_fn(pair, kind) -> str, kind in {"status", "predict"}
        self.insight_fn = insight_fn
        self.stats_fn = stats_fn

    def _insight(self, pair: str, kind: str) -> str:
        if self.insight_fn is None:
            return "Insights unavailable right now."
        return self.insight_fn(pair, kind)

    # ── raw API ──────────────────────────────────────────────────────
    def _api(self, method: str, **params) -> dict:
        last_err = None
        for attempt in (1, 2):  # one retry on transient network errors
            try:
                resp = requests.post(f"{self.base}/{method}", json=params, timeout=20)
                data = resp.json()
                if not data.get("ok"):
                    log.warning("Telegram %s failed: %s", method,
                                data.get("description"))
                return data
            except Exception as e:
                last_err = e
        log.error("Telegram %s error after retry: %s", method, last_err)
        return {"ok": False}

    def send(self, chat_id, text: str, keyboard: Optional[list] = None) -> bool:
        params = {"chat_id": chat_id, "text": text}
        if keyboard is not None:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        return bool(self._api("sendMessage", **params).get("ok"))

    # ── keyboards ────────────────────────────────────────────────────
    def _coin_keyboard(self, subs: List[str]) -> list:
        rows = []
        for pair in self.symbols:
            mark = "✅" if pair in subs else "☐"
            rows.append([{"text": f"{mark} {pair}", "callback_data": f"t|{pair}"}])
        rows.append([{"text": "✅ All on", "callback_data": "t|ALL_ON"},
                     {"text": "🚫 All off", "callback_data": "t|ALL_OFF"}])
        rows.append([{"text": "📊 Status", "callback_data": "m|status"},
                     {"text": "👍 Done", "callback_data": "m|done"}])
        return rows

    def _status_keyboard(self) -> list:
        rows = [[{"text": pair, "callback_data": f"s|{pair}"}] for pair in self.symbols]
        rows.append([{"text": "⚙️ Choose coins", "callback_data": "m|coins"}])
        return rows

    def _insight_keyboard(self, pair: str, kind: str) -> list:
        """Follow-up actions under a status/prediction message."""
        first = {"text": "🔮 Full read", "callback_data": f"p|{pair}"} \
            if kind == "status" else \
            {"text": "🔄 Update read", "callback_data": f"p|{pair}"}
        return [
            [first, {"text": "📊 Quick status", "callback_data": f"s|{pair}"}],
            [{"text": "🪙 Other coins", "callback_data": "m|status"},
             {"text": "⚙️ Alerts", "callback_data": "m|coins"}],
        ]

    # ── state helpers ────────────────────────────────────────────────
    def _chat_subs(self, state: dict, chat_id) -> List[str]:
        chats = state.setdefault("chats", {})
        entry = chats.setdefault(str(chat_id), {"subs": list(self.symbols)})
        # keep only pairs still present in config, in config order
        entry["subs"] = [p for p in self.symbols if p in entry["subs"]]
        return entry["subs"]

    def chats_for(self, state: dict, pair: str) -> List[str]:
        """Chat IDs subscribed to `pair` (allowlisted chats only)."""
        out = []
        for chat_id, entry in state.get("chats", {}).items():
            if chat_id in self.allowed and pair in entry.get("subs", []):
                out.append(chat_id)
        return out

    def broadcast(self, state: dict, pair: str, text: str) -> int:
        sent = 0
        for chat_id in self.chats_for(state, pair):
            if self.send(chat_id, text):
                sent += 1
        return sent

    def ensure_default_chats(self, state: dict) -> None:
        """Allowlisted chats are subscribed to everything until they customize."""
        for chat_id in self.allowed:
            self._chat_subs(state, chat_id)

    # ── update processing ────────────────────────────────────────────
    def process_updates(self, state: dict) -> bool:
        """Drain pending updates. Returns True if state changed."""
        offset = int(state.get("offset", 0))
        data = self._api("getUpdates", offset=offset + 1, timeout=0)
        if not data.get("ok"):
            return False
        changed = False
        for update in data.get("result", []):
            state["offset"] = update["update_id"]
            changed = True
            try:
                if "message" in update:
                    self._on_message(state, update["message"])
                elif "callback_query" in update:
                    self._on_callback(state, update["callback_query"])
            except Exception as e:
                log.error("Error handling Telegram update: %s", e)
        return changed

    def _authorized(self, chat_id) -> bool:
        return str(chat_id) in self.allowed

    def _on_message(self, state: dict, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        lower = text.lower()
        if not self._authorized(chat_id):
            log.info("Unauthorized chat %s", _mask(chat_id))
            self.send(chat_id,
                      "🔒 This bot is private.\n"
                      f"Your chat ID is {chat_id} — ask the owner to add it "
                      "to the TELEGRAM_CHAT_IDS secret.")
            return
        subs = self._chat_subs(state, chat_id)
        if lower.startswith("/coins"):
            self.send(chat_id, "Choose which coins alert this chat:",
                      self._coin_keyboard(subs))
        elif lower.startswith("/status"):
            self.send(chat_id, "Which coin do you want an update on?",
                      self._status_keyboard())
        elif lower.startswith("/accuracy"):
            self.send(chat_id, self.stats_fn() if self.stats_fn
                      else "Accuracy tracking unavailable.")
        elif lower.startswith("/start") or lower.startswith("/help"):
            self.send(chat_id, HELP_TEXT, self._coin_keyboard(subs))
        else:
            # Conversational: "btc?", "how's eth doing", "predict sol" ...
            pair, kind = parse_intent(text, self.symbols)
            if pair is not None:
                self.send(chat_id, self._insight(pair, kind),
                          self._insight_keyboard(pair, kind))
            else:
                coins = ", ".join(p.split("/")[0] for p in self.symbols)
                self.send(chat_id,
                          f"Hmm, I didn't catch a coin I track in that 🤔\n"
                          f"I watch: {coins}. Try \"btc?\" for a quick update or "
                          f"\"predict sol\" for my full read — or pick one below:",
                          self._status_keyboard())

    def _on_callback(self, state: dict, cb: dict) -> None:
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        action, _, arg = (cb.get("data") or "").partition("|")
        if not self._authorized(chat_id):
            self._api("answerCallbackQuery", callback_query_id=cb["id"],
                      text="This bot is private.")
            return

        subs = self._chat_subs(state, chat_id)
        toast = ""
        if action == "t":
            if arg == "ALL_ON":
                subs[:] = list(self.symbols)
                toast = "Subscribed to all coins"
            elif arg == "ALL_OFF":
                subs.clear()
                toast = "All alerts off for this chat"
            elif arg in self.symbols:
                if arg in subs:
                    subs.remove(arg)
                    toast = f"{arg} alerts OFF"
                else:
                    subs[:] = [p for p in self.symbols if p in subs or p == arg]
                    toast = f"{arg} alerts ON"
            self._api("editMessageReplyMarkup", chat_id=chat_id, message_id=msg_id,
                      reply_markup={"inline_keyboard": self._coin_keyboard(subs)})
        elif action == "s" and arg in self.symbols:
            toast = "Fetching…"
            self.send(chat_id, self._insight(arg, "status"),
                      self._insight_keyboard(arg, "status"))
        elif action == "p" and arg in self.symbols:
            toast = "Crunching the numbers…"
            self.send(chat_id, self._insight(arg, "predict"),
                      self._insight_keyboard(arg, "predict"))
        elif action == "m":
            if arg == "coins":
                self.send(chat_id, "Choose which coins alert this chat:",
                          self._coin_keyboard(subs))
            elif arg == "status":
                self.send(chat_id, "Pick a coin:", self._status_keyboard())
            elif arg == "done":
                on = ", ".join(subs) if subs else "none"
                toast = "Saved"
                self.send(chat_id, f"👍 Saved. Alerting this chat for: {on}")
        self._api("answerCallbackQuery", callback_query_id=cb["id"], text=toast)


def load_bot(symbols: List[str],
             insight_fn: Optional[Callable[[str, str], str]] = None,
             stats_fn: Optional[Callable[[], str]] = None
             ) -> Optional[TelegramBot]:
    """Build the bot from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS env, or None."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split("/") if c.strip()]
    if not token or not chats:
        log.warning("Telegram disabled (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS unset)")
        return None
    return TelegramBot(token, chats, symbols, insight_fn, stats_fn)


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
