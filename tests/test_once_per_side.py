"""once_per_side must alert once per approach EPISODE and re-arm when the
episode ends — not stay silent forever on that side."""

import json

import pandas as pd

from alerts import watcher
from alerts.rules import RULES, Signal


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.active = 1

    def send(self, title, body, tier="info"):
        self.sent.append(title)
        return True


def _df(ts):
    n = 40
    return pd.DataFrame({
        "timestamp": [ts - (n - 1 - i) * 300000 for i in range(n)],
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "close": [1.0] * n, "volume": [1.0] * n,
    })


def test_once_per_side_rearms_after_episode_ends(monkeypatch, tmp_path):
    near = Signal("NEAR-BUY", "🟡", "forming", "details")
    script = iter([near, near, None, near])
    monkeypatch.setitem(RULES, "stub_rule", lambda df, p: next(script))
    monkeypatch.setattr(watcher, "get_exchange", lambda name: None)

    config = {"timeframe": "5m", "candles": 40, "symbols": ["X/USDT"],
              "rules": [{"name": "stub_rule", "once_per_side": True}]}
    state_path = str(tmp_path / "state.json")
    notifier = FakeNotifier()

    for step, ts in enumerate([1_000_000_000, 1_000_300_000,
                               1_000_600_000, 1_000_900_000]):
        monkeypatch.setattr(watcher, "fetch_closed_candles",
                            lambda ex, p, tf, l, _ts=ts, **kw: _df(_ts))
        watcher.run_once(config, notifier, state_path)

    # candle 1: alert; candle 2: same side, same episode -> suppressed;
    # candle 3: no signal -> episode over, re-arm; candle 4: alert again
    assert len(notifier.sent) == 2

    state = json.load(open(state_path))
    assert state["binance|X/USDT|stub_rule"]["side"] == "NEAR-BUY"
