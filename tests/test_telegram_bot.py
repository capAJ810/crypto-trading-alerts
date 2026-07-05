from alerts.telegram_bot import GUIDE_TEXT, TelegramBot, category_for_side

SYMBOLS = ["BTC/USDT", "ETH/USDT", "HYPE/USDT"]


def make_bot(calls):
    bot = TelegramBot("dummy-token", ["111"], SYMBOLS)
    bot._api = lambda method, **params: calls.append((method, params)) or {"ok": True,
                                                                           "result": []}
    return bot


def test_allowlisted_chat_defaults_to_all_coins():
    bot = make_bot([])
    state = {}
    bot.ensure_default_chats(state)
    assert state["chats"]["111"]["subs"] == SYMBOLS
    assert bot.chats_for(state, "BTC/USDT") == ["111"]


def test_unauthorized_chat_gets_no_alerts():
    bot = make_bot([])
    state = {"chats": {"999": {"subs": SYMBOLS}}}  # not in allowlist
    assert bot.chats_for(state, "BTC/USDT") == []


def test_callback_toggle_unsubscribes_and_resubscribes():
    calls = []
    bot = make_bot(calls)
    state = {}
    bot.ensure_default_chats(state)
    cb = {"id": "1", "data": "t|ETH/USDT",
          "message": {"chat": {"id": 111}, "message_id": 5}}
    bot._on_callback(state, cb)
    assert state["chats"]["111"]["subs"] == ["BTC/USDT", "HYPE/USDT"]
    bot._on_callback(state, cb)
    assert state["chats"]["111"]["subs"] == SYMBOLS  # config order preserved


def test_callback_all_off_then_alerts_route_nowhere():
    bot = make_bot([])
    state = {}
    bot.ensure_default_chats(state)
    cb = {"id": "1", "data": "t|ALL_OFF",
          "message": {"chat": {"id": 111}, "message_id": 5}}
    bot._on_callback(state, cb)
    assert bot.chats_for(state, "BTC/USDT") == []


def test_removed_config_symbol_is_pruned_from_subs():
    bot = make_bot([])
    state = {"chats": {"111": {"subs": ["BTC/USDT", "DOGE/USDT"]}}}
    assert bot._chat_subs(state, "111") == ["BTC/USDT"]


def test_email_only_mode_stops_telegram_alerts():
    bot = make_bot([])
    state = {}
    bot.ensure_default_chats(state)
    # default (no mode) still routes Telegram
    assert bot.chats_for(state, "BTC/USDT") == ["111"]
    state["chats"]["111"]["mode"] = "email"
    assert bot.chats_for(state, "BTC/USDT") == []
    state["chats"]["111"]["mode"] = "telegram"
    assert bot.chats_for(state, "BTC/USDT") == ["111"]
    state["chats"]["111"]["mode"] = "both"
    assert bot.chats_for(state, "BTC/USDT") == ["111"]


def test_mode_callback_sets_mode_and_warns_without_email():
    calls = []
    bot = make_bot(calls)
    state = {}
    bot.ensure_default_chats(state)
    cb = {"id": "1", "data": "md|email",
          "message": {"chat": {"id": 111}, "message_id": 5}}
    bot._on_callback(state, cb)
    assert state["chats"]["111"]["mode"] == "email"
    # picking email/both with no linked address nudges the user
    sends = [p["text"] for m, p in calls if m == "sendMessage"]
    assert any("haven't linked an email" in t for t in sends)


def test_guide_command_sends_glossary():
    calls = []
    bot = make_bot(calls)
    state = {}
    bot.ensure_default_chats(state)
    bot._on_message(state, {"chat": {"id": 111}, "text": "/guide"})
    sends = [p["text"] for m, p in calls if m == "sendMessage"]
    assert GUIDE_TEXT in sends
    assert "CONFIRMED BUY" in GUIDE_TEXT and "Bearish" in GUIDE_TEXT


def test_category_for_side_mapping():
    assert category_for_side("BUY") == "confirmed"
    assert category_for_side("SELL") == "confirmed"
    assert category_for_side("WEAK BUY") == "weak"
    assert category_for_side("WEAK SELL") == "weak"
    assert category_for_side("NEAR-BUY") == "near"
    assert category_for_side("NEAR-SELL") == "near"
    assert category_for_side("INTRABAR BUY") == "intrabar"
    assert category_for_side("INTRABAR SELL") == "intrabar"


def test_alert_type_and_timeframe_personalization():
    bot = make_bot([])
    state = {}
    bot.ensure_default_chats(state)
    cb = lambda d: {"id": "1", "data": d,
                    "message": {"chat": {"id": 111}, "message_id": 5}}
    # default (no prefs stored): everything gets through
    assert bot.chats_for(state, "BTC/USDT", "near", "15m") == ["111"]
    # turn NEAR alerts and the 15m stream off
    bot._on_callback(state, cb("a|near"))
    bot._on_callback(state, cb("f|15m"))
    assert state["chats"]["111"]["cats"] == ["confirmed", "weak", "intrabar"]
    assert state["chats"]["111"]["tfs"] == ["5m"]
    assert bot.chats_for(state, "BTC/USDT", "near", "5m") == []       # type off
    assert bot.chats_for(state, "BTC/USDT", "confirmed", "15m") == []  # tf off
    assert bot.chats_for(state, "BTC/USDT", "confirmed", "5m") == ["111"]
    # toggle NEAR back on
    bot._on_callback(state, cb("a|near"))
    assert bot.chats_for(state, "BTC/USDT", "near", "5m") == ["111"]


def test_confirmed_only_15m_only_profile():
    # The exact profile from the feature request: only confirmed alerts,
    # only 15m candles, one coin.
    bot = make_bot([])
    state = {"chats": {"111": {"subs": ["BTC/USDT"],
                               "cats": ["confirmed"], "tfs": ["15m"]}}}
    assert bot.chats_for(state, "BTC/USDT", "confirmed", "15m") == ["111"]
    assert bot.chats_for(state, "BTC/USDT", "confirmed", "5m") == []
    assert bot.chats_for(state, "BTC/USDT", "near", "15m") == []
    assert bot.chats_for(state, "BTC/USDT", "weak", "15m") == []
    assert bot.chats_for(state, "ETH/USDT", "confirmed", "15m") == []


def test_self_service_email_add_change_remove():
    bot = make_bot([])
    state = {}
    msg = lambda t: {"chat": {"id": 111}, "text": t}
    # any well-formed address the user picks is accepted (no owner allowlist)
    bot._on_message(state, msg("/email me@x.com"))
    assert state["chats"]["111"]["email"] == "me@x.com"
    bot._on_message(state, msg("/email me@newprovider.io"))
    assert state["chats"]["111"]["email"] == "me@newprovider.io"
    # garbage is rejected and keeps the previous value
    bot._on_message(state, msg("/email not-an-email"))
    assert state["chats"]["111"]["email"] == "me@newprovider.io"
    bot._on_message(state, msg("/email off"))
    assert "email" not in state["chats"]["111"]


def test_email_button_prompts_then_captures_reply():
    bot = make_bot([])
    state = {}
    bot.ensure_default_chats(state)
    # tapping 📧 Email arms the "type your address" prompt
    bot._on_callback(state, {"id": "1", "data": "m|email",
                             "message": {"chat": {"id": 111}, "message_id": 5}})
    assert state["chats"]["111"].get("awaiting") == "email"
    # the next plain message becomes the address, and the prompt is cleared
    bot._on_message(state, {"chat": {"id": 111}, "text": "trader@mail.com"})
    assert state["chats"]["111"]["email"] == "trader@mail.com"
    assert "awaiting" not in state["chats"]["111"]


def test_command_cancels_pending_email_prompt():
    bot = make_bot([])
    state = {}
    bot.ensure_default_chats(state)
    state["chats"]["111"]["awaiting"] = "email"
    bot._on_message(state, {"chat": {"id": 111}, "text": "/status"})
    # a command clears the prompt instead of being stored as an email
    assert "awaiting" not in state["chats"]["111"]
    assert "email" not in state["chats"]["111"]
