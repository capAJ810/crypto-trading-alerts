"""Flicker guards on intrabar alerts (2026-07-06 audit: a false ASTER
INTRABAR SELL fired from one sub-minute tick with a 0.006% EMA gap, seconds
after a bullish cross had confirmed on the closed candle).

Guards under test:
  min_gap_pct      (rule param)  — hairline crosses are noise, not signals
  whipsaw_candles  (rule param)  — don't fade a cross that just confirmed
  confirm_polls    (watcher key) — the cross must survive consecutive checks
"""

import json

import pandas as pd
import pytest

from alerts import watcher
from alerts.indicators import ema
from alerts.rules import RULES, Signal, ema_cross_intrabar


def _df(closes):
    s = pd.Series(closes, dtype=float)
    return pd.DataFrame({"timestamp": range(len(s)), "open": s, "high": s * 1.01,
                         "low": s * 0.99, "close": s, "volume": [1.0] * len(s)})


def _bounce_series(extra_after_cross=0):
    """Downtrend, then a bounce that ends the moment closed EMA9 crosses
    above EMA21 (optionally +N more closed candles after the cross)."""
    closes = [100 - 0.15 * i for i in range(80)]
    for _ in range(60):
        closes.append(closes[-1] + 0.25)
        s = pd.Series(closes)
        if ema(s, 9).iloc[-1] > ema(s, 21).iloc[-1]:
            break
    else:
        pytest.fail("bounce never produced a closed cross")
    closes += [closes[-1] + 0.25 * (i + 1) for i in range(extra_after_cross)]
    return closes


def _flip_price(closes):
    """Highest forming price (searching downward) where live EMA9 < EMA21 —
    i.e. the hairline-cross point."""
    p = closes[-1]
    for _ in range(5000):
        s = pd.Series(closes + [p])
        if ema(s, 9).iloc[-1] < ema(s, 21).iloc[-1]:
            return p
        p -= 0.002
    pytest.fail("no forming price produces a cross")


# rsi_sell=100 keeps the RSI gate open, no trend filter: isolates the guards
BASE = {"fast": 9, "slow": 21, "rsi_len": 14, "rsi_buy": 55, "rsi_sell": 100}


def test_min_gap_blocks_hairline_cross_but_not_a_real_one():
    closes = _bounce_series(extra_after_cross=3)  # cross aged out of whipsaw
    hairline = closes + [_flip_price(closes)]
    real = closes + [_flip_price(closes) - 1.0]

    # hairline: fires with the guard off, blocked with it on
    assert ema_cross_intrabar(_df(hairline), {**BASE, "min_gap_pct": 0.0}) \
        is not None
    assert ema_cross_intrabar(_df(hairline), {**BASE, "min_gap_pct": 0.0125}) \
        is None
    # a substantive cross passes the same threshold, and reports its gap
    sig = ema_cross_intrabar(_df(real), {**BASE, "min_gap_pct": 0.0125})
    assert sig is not None and sig.side == "INTRABAR SELL"
    assert "gap" in sig.details and "EMA9" in sig.details


def test_whipsaw_guard_blocks_fading_a_fresh_cross():
    # Bullish cross confirmed on the LAST closed candle; big forming drop.
    closes = _bounce_series(extra_after_cross=0)
    forming = closes + [_flip_price(closes) - 1.0]
    assert ema_cross_intrabar(_df(forming),
                              {**BASE, "whipsaw_candles": 2}) is None
    assert ema_cross_intrabar(_df(forming),
                              {**BASE, "whipsaw_candles": 0}) is not None


def test_whipsaw_guard_ages_out():
    # Same shape but the bullish cross is 3+ closed candles old — a SELL
    # against it is no longer a whipsaw.
    closes = _bounce_series(extra_after_cross=3)
    forming = closes + [_flip_price(closes) - 1.0]
    assert ema_cross_intrabar(_df(forming),
                              {**BASE, "whipsaw_candles": 2}) is not None


# ── confirm_polls (watcher-level persistence) ─────────────────────────

class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.active = 1

    def send(self, title, body, tier="info", pair=None, category=None,
             timeframe=None):
        self.sent.append(title)
        return True


def _live_df(ts):
    n = 40
    return pd.DataFrame({
        "timestamp": [ts - (n - 1 - i) * 300000 for i in range(n)],
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "close": [1.0] * n, "volume": [1.0] * n,
    })


def _setup(monkeypatch, script):
    sig = Signal("INTRABAR SELL", "⏱️", "live cross", "details")
    it = iter(script)
    monkeypatch.setitem(RULES, "stub_intra",
                        lambda df, p: sig if next(it) else None)
    monkeypatch.setattr(watcher, "INTRABAR_RULES", {"stub_intra"})
    monkeypatch.setattr(watcher, "get_exchange", lambda name: None)
    monkeypatch.setattr(watcher, "fetch_closed_candles",
                        lambda *a, **kw: _live_df(1_000_000_000))
    return {"timeframe": "5m", "candles": 40, "symbols": ["X/USDT"],
            "rules": [{"name": "stub_intra", "confirm_polls": 2}]}


def test_confirm_polls_needs_two_consecutive_sightings(monkeypatch, tmp_path):
    config = _setup(monkeypatch, [True, True, True])
    state_path = str(tmp_path / "state.json")
    notifier = FakeNotifier()
    watcher.run_intrabar(config, notifier, state_path)
    assert notifier.sent == []          # first sighting: candidate only
    state = json.load(open(state_path))
    assert state["binance|X/USDT|stub_intra"]["pending"]["seen"] == 1
    watcher.run_intrabar(config, notifier, state_path)
    assert len(notifier.sent) == 1      # second consecutive sighting: alert
    state = json.load(open(state_path))
    assert "pending" not in state["binance|X/USDT|stub_intra"]
    watcher.run_intrabar(config, notifier, state_path)
    assert len(notifier.sent) == 1      # deduped for this forming candle


def test_confirm_polls_flicker_resets_the_count(monkeypatch, tmp_path):
    # sighting -> vanished -> sighting -> sighting: the vanish must reset,
    # so the alert fires only on the 4th check, not the 3rd.
    config = _setup(monkeypatch, [True, False, True, True])
    state_path = str(tmp_path / "state.json")
    notifier = FakeNotifier()
    for expected in (0, 0, 0, 1):
        watcher.run_intrabar(config, notifier, state_path)
        assert len(notifier.sent) == expected
