import pandas as pd

from alerts.analysis import (bias_score, compose_prediction, compose_status,
                             fmt, read_frame, swing_levels)
from alerts.telegram_bot import parse_intent

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ASTER/USDT",
           "HYPE/USDT"]


def _df(closes):
    s = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "timestamp": range(len(s)), "open": s, "high": s * 1.01,
        "low": s * 0.99, "close": s, "volume": [1.0] * len(s),
    })


UP = _df([100 + i for i in range(120)])
DOWN = _df([220 - i for i in range(120)])


def test_read_frame_uptrend_is_bullish():
    r = read_frame(UP)
    assert r["bullish"] and r["rsi"] > 50 and r["atr"] > 0


def test_bias_score_weights_higher_timeframes():
    frames = {"5m": read_frame(DOWN), "1h": read_frame(UP), "4h": read_frame(UP)}
    assert bias_score(frames) == 4  # -1 + 2 + 3


def test_swing_levels_ordering():
    lv = swing_levels(UP)
    assert lv["r2"] >= lv["r1"] > lv["s1"] >= lv["s2"]


def test_compose_prediction_bullish_mentions_levels_and_disclaimer():
    frames = {tf: read_frame(UP) for tf in ("5m", "1h", "4h")}
    text = compose_prediction("BTC/USDT", "binance", frames, swing_levels(UP))
    assert "leaning bullish" in text
    assert "resistance" in text and "support" in text
    assert "long setup" in text
    assert "not financial advice" in text


def test_compose_prediction_mixed_suggests_patience():
    frames = {"5m": read_frame(UP), "1h": read_frame(UP), "4h": read_frame(DOWN)}
    text = compose_prediction("ETH/USDT", "binance", frames, swing_levels(UP))
    assert "mixed / ranging" in text
    assert "No clean setup" in text


def test_compose_status_is_short_and_has_price():
    frames = {tf: read_frame(UP) for tf in ("5m", "1h", "4h")}
    text = compose_status("SOL/USDT", "binance", frames)
    assert "SOL/USDT" in text and "219" in text


def test_fmt_precision_scales():
    assert fmt(104532.123) == "104,532"
    assert fmt(67.328) == "67.33"
    assert fmt(0.7345) == "0.7345"
    assert fmt(0.001234) == "0.001234"


# ── conversational intent parsing ────────────────────────────────────

def test_parse_intent_bare_coin_gives_prediction():
    assert parse_intent("btc?", SYMBOLS) == ("BTC/USDT", "predict")


def test_parse_intent_full_names_and_status_words():
    assert parse_intent("how is ethereum doing", SYMBOLS) == ("ETH/USDT", "status")
    assert parse_intent("solana price now", SYMBOLS) == ("SOL/USDT", "status")


def test_parse_intent_predict_words_win():
    assert parse_intent("should i buy hype right now?", SYMBOLS) == \
        ("HYPE/USDT", "predict")
    assert parse_intent("predict aster", SYMBOLS) == ("ASTER/USDT", "predict")


def test_parse_intent_no_coin():
    assert parse_intent("good morning!", SYMBOLS) == (None, None)
