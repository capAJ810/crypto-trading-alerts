"""Technical indicators implemented directly in pandas/numpy.

Kept dependency-free (no pandas-ta / TA-Lib) so the math is auditable
and the install surface stays small. Values match TradingView's
ta.ema() and ta.rsi() (Wilder's smoothing, SMA-seeded).
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average, seeded like TradingView (adjust=False)."""
    return series.ewm(span=length, adjust=False).mean()


def _rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (TradingView ta.rma): SMA seed, then
    avg = (prev_avg * (length-1) + value) / length.

    Expects a series whose first element is NaN (output of .diff()).
    """
    vals = series.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    if len(vals) > length:
        out[length] = np.mean(vals[1:length + 1])
        for i in range(length + 1, len(vals)):
            out[i] = (out[i - 1] * (length - 1) + vals[i]) / length
    return pd.Series(out, index=series.index)


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing."""
    delta = series.diff()
    avg_gain = _rma(delta.clip(lower=0.0), length)
    avg_loss = _rma(-delta.clip(upper=0.0), length)
    # avg_loss == 0 -> rs == inf -> RSI == 100, which is the defined value;
    # a completely flat series yields 0/0 == NaN (RSI undefined).
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
