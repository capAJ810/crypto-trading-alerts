"""Tests for the outcome logger (siglog) and the self-tuner's backtester."""

import pandas as pd

from alerts import siglog
from alerts.rules import ema_cross_rsi
from alerts.tuner import backtest_cross, backtest_soon, tune_rule, GRID_CROSS


def _df(closes, volumes=None):
    s = pd.Series(closes, dtype=float)
    v = pd.Series(volumes if volumes is not None else [1.0] * len(s), dtype=float)
    return pd.DataFrame({"timestamp": [1000 * i for i in range(len(s))],
                         "open": s, "high": s * 1.01, "low": s * 0.99,
                         "close": s, "volume": v})


# ── siglog ───────────────────────────────────────────────────────────

def test_append_dedupes_same_candle():
    entries = []
    for _ in range(2):
        siglog.append(entries, pair="BTC/USDT", exchange="binance",
                      rule="ema_cross_rsi", side="BUY", price=100.0,
                      candle_ts=5000, timeframe="5m")
    assert len(entries) == 1


def test_score_move_hit_and_miss():
    closes = [100.0] * 30
    df = _df(closes)
    up = df.copy()
    up.loc[len(up) - siglog.HORIZON_CANDLES:, "high"] = 101.0  # +1% spike after
    entries = []
    siglog.append(entries, pair="X/USDT", exchange="binance",
                  rule="ema_cross_rsi", side="BUY", price=100.0,
                  candle_ts=int(df["timestamp"].iloc[10]), timeframe="5m")
    assert siglog.score_pending(entries, lambda p, e: up) == 1
    assert entries[0]["outcome"] == "hit"

    entries2 = []
    siglog.append(entries2, pair="X/USDT", exchange="binance",
                  rule="ema_cross_rsi", side="BUY", price=100.0,
                  candle_ts=int(df["timestamp"].iloc[10]), timeframe="5m")
    flat = _df([100.0] * 30)
    flat["high"] = 100.05  # never reaches +0.3%
    siglog.score_pending(entries2, lambda p, e: flat)
    assert entries2[0]["outcome"] == "miss"


def test_score_waits_for_maturity():
    df = _df([100.0] * 30)
    entries = []
    siglog.append(entries, pair="X/USDT", exchange="binance",
                  rule="ema_cross_rsi", side="BUY", price=100.0,
                  candle_ts=int(df["timestamp"].iloc[-2]), timeframe="5m")
    siglog.score_pending(entries, lambda p, e: df)
    assert entries[0]["outcome"] is None  # horizon not elapsed yet


def test_stats_text_reports_rates():
    entries = [
        {"id": "1", "pair": "BTC/USDT", "rule": "ema_cross_rsi",
         "outcome": "hit"},
        {"id": "2", "pair": "BTC/USDT", "rule": "ema_cross_rsi",
         "outcome": "miss"},
        {"id": "3", "pair": "ETH/USDT", "rule": "ema_cross_soon",
         "outcome": None},
    ]
    text = siglog.stats_text(entries)
    assert "1/2" in text and "50%" in text and "not matured" in text


# ── tuner backtester ─────────────────────────────────────────────────

def _cross_series(rally_len):
    closes = [100 + 0.4 * i for i in range(280)]
    closes += [closes[-1] - 2.5 * i for i in range(1, 26)]
    closes += [closes[-1] + 4.0 * i for i in range(1, rally_len + 1)]
    volumes = [1.0] * len(closes)
    volumes[-1] = 5.0
    return _df(closes, volumes)


def test_backtest_agrees_with_live_rule():
    """The vectorized backtest must flag the same candle the live rule does."""
    params = {"fast": 9, "slow": 21, "rsi_len": 14, "rsi_buy": 55.0,
              "rsi_sell": 45.0, "volume_avg": 20, "trend_ema": 200}
    for rally in range(1, 40):
        df = _cross_series(rally)
        live = ema_cross_rsi(df, {**params, "alert_weak": False})
        vect = [i for i, d in backtest_cross(df, params)
                if i == len(df) - 1 and d == 1]
        assert (live is not None) == bool(vect), f"disagree at rally={rally}"
        if live is not None:
            return
    assert False, "no cross produced"


def test_backtest_soon_dedupes_episodes():
    closes = [200 - 0.6 * i for i in range(120)]
    closes += [closes[-1] + 0.55 * i for i in range(1, 30)]
    events = backtest_soon(_df(closes), {"fast": 9, "slow": 21, "gap_pct": 0.3})
    # consecutive qualifying candles must collapse into episode starts
    idxs = [i for i, _ in events]
    assert idxs == sorted(set(idxs))
    assert all(b - a > 1 for a, b in zip(idxs, idxs[1:])) or len(idxs) <= 1


def test_tune_rule_keeps_current_without_evidence():
    # Flat series: no signals anywhere -> tuner must not change anything
    df = _df([100.0 + (i % 3) * 0.01 for i in range(1500)])
    current = {"fast": 9, "slow": 21, "rsi_len": 14, "rsi_buy": 55.0,
               "rsi_sell": 45.0, "volume_avg": 20, "trend_ema": 200}
    change, why = tune_rule(df, current, GRID_CROSS, backtest_cross,
                            lambda d, i, dr: True, warmup=400)
    assert change is None
    assert "too few" in why
