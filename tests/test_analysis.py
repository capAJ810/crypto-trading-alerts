import pandas as pd

from alerts.analysis import (_rr_setup, bias_score, compose_prediction,
                             compose_status, fmt, read_frame, swing_levels)
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


# ── structure-aware example setup ────────────────────────────────────

def test_long_setup_targets_resistance_and_stops_below_support():
    lv = {"s1": 95.0, "s2": 90.0, "r1": 110.0, "r2": 120.0}
    txt = _rr_setup(entry=100.0, atr=4.0, lv=lv, side="long")
    assert "long setup" in txt
    assert "110" in txt and "120" in txt   # targets = the resistances above
    assert "94" in txt                     # stop = s1(95) − 0.25·atr(1)
    assert "R" in txt                      # reports risk-multiples


def test_short_setup_targets_support_and_stops_above_resistance():
    lv = {"s1": 90.0, "s2": 80.0, "r1": 105.0, "r2": 115.0}
    txt = _rr_setup(entry=100.0, atr=4.0, lv=lv, side="short")
    assert "short setup" in txt
    assert "90" in txt and "80" in txt     # targets = the supports below
    assert "106" in txt                    # stop = r1(105) + 0.25·atr(1)


def test_setup_falls_back_to_atr_on_breakout_with_no_resistance_above():
    # price above both resistances and far above support → pure ATR sizing
    lv = {"s1": 95.0, "s2": 90.0, "r1": 100.0, "r2": 101.0}
    txt = _rr_setup(entry=105.0, atr=4.0, lv=lv, side="long")
    assert "111" in txt and "117" in txt   # 105+1.5·atr, 105+3·atr
    assert "99" in txt                     # support 94 is >2.5·atr away → ATR stop 105−1.5·atr


def test_final_target_enforces_min_1_to_2_sl_tp():
    # Wide structural stop (support 91 → stop 90, risk 10) but nearby
    # resistances (108/109) can't offer 2R → T2 extends to the 2R point (120)
    # and the message says the 1:2 rule set it.
    lv = {"s1": 91.0, "s2": 85.0, "r1": 108.0, "r2": 109.0}
    txt = _rr_setup(entry=100.0, atr=4.0, lv=lv, side="long")
    assert "120" in txt                    # entry + 2 × risk(10)
    assert "2.0R" in txt
    assert "1:2 rule" in txt               # flagged as rule-set, not structural


def test_no_2r_extension_when_structure_already_beats_it():
    # Structural T2 (120) ≥ 2R (112) → kept as-is, no rule note.
    lv = {"s1": 95.0, "s2": 90.0, "r1": 110.0, "r2": 120.0}
    txt = _rr_setup(entry=100.0, atr=4.0, lv=lv, side="long")
    assert "120" in txt and "1:2 rule and sits past" not in txt


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
