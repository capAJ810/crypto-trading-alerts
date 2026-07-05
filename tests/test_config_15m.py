"""Guards for the parallel 15m config (config-15m.yaml).

The 15m pass must track the same coins as the 5m config; this test fails
loudly if someone adds/removes a coin in one file but not the other.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    with open(os.path.join(ROOT, name)) as f:
        return yaml.safe_load(f)


def test_15m_config_is_15m_timeframe():
    assert _load("config-15m.yaml")["timeframe"] == "15m"


def test_15m_symbols_match_5m_symbols():
    assert _load("config-15m.yaml")["symbols"] == _load("config.yaml")["symbols"]


def test_15m_has_no_intrabar_rule():
    # The once-per-run 15m pass can't do meaningful intrabar detection.
    names = {r["name"] for r in _load("config-15m.yaml")["rules"]}
    assert "ema_cross_intrabar" not in names
    assert "ema_cross_rsi" in names  # the core signal is still present
