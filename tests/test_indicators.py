import math

import pandas as pd
import pytest

from alerts.indicators import ema, rsi
from alerts.rules import ema_cross_rsi, price_cross_level


def test_ema_constant_series_is_constant():
    s = pd.Series([42.0] * 50)
    assert ema(s, 9).iloc[-1] == pytest.approx(42.0)


def test_ema_matches_manual_recurrence():
    # EMA with span=3 -> alpha = 2/(3+1) = 0.5, seeded at the first value
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    expected = [1.0, 1.5, 2.25, 3.125]
    assert list(ema(s, 3)) == pytest.approx(expected)


def test_rsi_all_gains_is_100():
    s = pd.Series(range(1, 40), dtype=float)
    assert rsi(s, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    s = pd.Series(range(40, 1, -1), dtype=float)
    assert rsi(s, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_wilder_reference_value():
    # Classic 14-period RSI reference series (Wilder / StockCharts example)
    closes = pd.Series([
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
        46.03, 46.41, 46.22, 45.64,
    ])
    r = rsi(closes, 14)
    # Reference values from the classic Wilder/StockCharts worked example
    assert r.iloc[14] == pytest.approx(70.46, abs=0.1)
    assert r.iloc[19] == pytest.approx(57.92, abs=0.2)
    assert not math.isnan(r.iloc[-1])


def _df(closes):
    n = len(closes)
    return pd.DataFrame({
        "timestamp": range(n), "open": closes, "high": closes,
        "low": closes, "close": pd.Series(closes, dtype=float), "volume": [1.0] * n,
    })


def test_ema_cross_rsi_fires_buy_on_bullish_cross():
    # Long downtrend then a sharp rally: EMA9 crosses above EMA21 on the 5th
    # rally bar (verified numerically) while RSI > 50
    closes = [100 - i * 0.5 for i in range(60)] + [70 + i * 3 for i in range(1, 6)]
    sig = ema_cross_rsi(_df(closes), {"fast": 9, "slow": 21, "rsi_len": 14,
                                      "rsi_threshold": 50})
    assert sig is not None and sig.side == "BUY"


def test_ema_cross_rsi_silent_without_cross():
    closes = [100 + i for i in range(60)]  # steady uptrend, EMAs never cross
    sig = ema_cross_rsi(_df(closes), {"fast": 9, "slow": 21})
    assert sig is None


def test_price_cross_level():
    closes = [95.0] * 30 + [99.0, 101.0]
    sig = price_cross_level(_df(closes), {"level": 100})
    assert sig is not None and "above" in sig.headline
