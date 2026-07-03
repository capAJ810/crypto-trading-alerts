"""Interactive Telegram bot: per-chat coin subscriptions via inline buttons.

Runs in the same scheduled process as the watcher (no server needed):
each cycle drains pending updates with getUpdates, answers commands and
button presses, and persists state to telegram.json (committed by CI).

Security: only chat IDs in the TELEGRAM_CHAT_IDS allowlist (slash-
separated env/secret) may use the bot. Anyone else is shown their chat
ID so the owner can decide to add them.

Commands:  /start /help — welcome + coin picker
           /coins       — choose which coins alert this chat (✅/☐ toggles)
           /status      — pick a coin, get a live price/EMA/RSI snapshot
"""

import json
import logging
import os
from typing import Callable, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 Crypto alerts bot\n\n"
    "You get an alert when EMA 9 crosses EMA 21 (RSI-confirmed) on any coin "
    "you're subscribed to.\n\n"
    "/coins — choose which coins alert you\n"
    "/status — live price + indicator snapshot\n"
    "/help — this message"
)


def _mask(chat_id) -> str:
    s = str(chat_id)
    return "…" + s[-4:] if len(s) > 4 else s


class TelegramBot:
    def __init__(self, token: str, allowed_chats: List[str], symbols: List[str],
                 snapshot_fn: Optional[Callable[[str], str]] = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.allowed = {str(c) for c in allowed_chats}
        self.symbols = symbols
        self.snapshot_fn = snapshot_fn

    # ── raw API ──────────────────────────────────────────────────────
    def _api(self, method: str, **params) -> dict:
        try:
            resp = requests.post(f"{self.base}/{method}", json=params, timeout=20)
            data = resp.json()
            if not data.get("ok"):
                log.warning("Telegram %s failed: %s", method, data.get("description"))
            return data
        except Exception as e:
            log.error("Telegram %s error: %s", method, e)
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
        text = (msg.get("text") or "").strip().lower()
        if not self._authorized(chat_id):
            log.info("Unauthorized chat %s", _mask(chat_id))
            self.send(chat_id,
                      "🔒 This bot is private.\n"
                      f"Your chat ID is {chat_id} — ask the owner to add it "
                      "to the TELEGRAM_CHAT_IDS secret.")
            return
        subs = self._chat_subs(state, chat_id)
        if text.startswith("/coins"):
            self.send(chat_id, "Choose which coins alert this chat:",
                      self._coin_keyboard(subs))
        elif text.startswith("/status"):
            self.send(chat_id, "Pick a coin:", self._status_keyboard())
        else:  # /start, /help, anything else
            self.send(chat_id, HELP_TEXT, self._coin_keyboard(subs))

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
            snap = self.snapshot_fn(arg) if self.snapshot_fn else "Status unavailable."
            self.send(chat_id, snap)
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
             snapshot_fn: Optional[Callable[[str], str]] = None
             ) -> Optional[TelegramBot]:
    """Build the bot from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS env, or None."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split("/") if c.strip()]
    if not token or not chats:
        log.warning("Telegram disabled (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS unset)")
        return None
    return TelegramBot(token, chats, symbols, snapshot_fn)


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
