"""Tests for the confirmed-cross conditions and the near-cross heads-up."""

import pandas as pd
import pytest

from alerts.rules import ema_cross_rsi, ema_cross_soon

FULL_PARAMS = {"fast": 9, "slow": 21, "rsi_len": 14, "rsi_buy": 55,
               "rsi_sell": 45, "volume_avg": 20, "trend_ema": 200,
               "alert_weak": True}


def _df(closes, volumes=None):
    s = pd.Series(closes, dtype=float)
    v = pd.Series(volumes if volumes is not None else [1.0] * len(s), dtype=float)
    return pd.DataFrame({"timestamp": range(len(s)), "open": s, "high": s * 1.01,
                         "low": s * 0.99, "close": s, "volume": v})


def _uptrend_dip_recross(rally_len, last_volume=5.0):
    """Long uptrend (close > EMA200), a dip that bends EMA9 under EMA21,
    then a sharp rally of `rally_len` candles with a volume spike."""
    closes = [100 + 0.4 * i for i in range(280)]           # long uptrend
    closes += [closes[-1] - 2.5 * i for i in range(1, 26)]  # sharp dip
    closes += [closes[-1] + 4.0 * i for i in range(1, rally_len + 1)]
    volumes = [1.0] * len(closes)
    volumes[-1] = last_volume
    return _df(closes, volumes)


def _find_cross(last_volume=5.0):
    for rally in range(1, 40):
        sig = ema_cross_rsi(_uptrend_dip_recross(rally, last_volume), FULL_PARAMS)
        if sig is not None:
            return sig
    return None


def test_all_confirmations_give_buy():
    sig = _find_cross(last_volume=5.0)
    assert sig is not None and sig.side == "BUY"
    assert "all confirmations" in sig.details


def test_low_volume_downgrades_to_weak():
    sig = _find_cross(last_volume=0.1)   # cross candle volume below 20-avg
    assert sig is not None and sig.side == "WEAK BUY"
    assert "volume" in sig.details and "failed" in sig.details


def test_below_ema200_downgrades_to_weak():
    # Downtrend then rally: EMA9 crosses above EMA21 while close < EMA200
    closes = [300 - 0.5 * i for i in range(280)]
    for rally in range(1, 40):
        c = closes + [closes[-1] + 3.0 * i for i in range(1, rally + 1)]
        v = [1.0] * len(c); v[-1] = 5.0
        sig = ema_cross_rsi(_df(c, v), FULL_PARAMS)
        if sig is not None:
            assert sig.side == "WEAK BUY"
            assert "EMA200" in sig.details
            return
    pytest.fail("no cross produced")


def test_legacy_rsi_threshold_still_works():
    df = _uptrend_dip_recross(1)
    for rally in range(1, 40):
        sig = ema_cross_rsi(_uptrend_dip_recross(rally),
                            {"fast": 9, "slow": 21, "rsi_threshold": 50})
        if sig is not None:
            assert sig.side in ("BUY", "WEAK BUY")
            return
    pytest.fail("no cross produced")


def test_near_cross_fires_before_actual_cross():
    """The 🟡 heads-up must fire on an earlier candle than the cross itself."""
    def series(recover):
        closes = [200 - 0.6 * i for i in range(120)]
        return _df(closes + [closes[-1] + 0.55 * i for i in range(1, recover + 1)])

    soon_at = cross_at = None
    for k in range(1, 60):
        df = series(k)
        if soon_at is None and ema_cross_soon(df, {"fast": 9, "slow": 21,
                                                   "gap_pct": 0.15}):
            soon_at = k
        if cross_at is None and ema_cross_rsi(df, {"fast": 9, "slow": 21}):
            cross_at = k
    assert soon_at is not None, "heads-up never fired"
    assert cross_at is not None, "cross never happened"
    assert soon_at < cross_at


def test_near_cross_silent_when_diverging():
    closes = [100 + i for i in range(80)]  # EMAs separating, not converging
    assert ema_cross_soon(_df(closes), {"fast": 9, "slow": 21}) is None
