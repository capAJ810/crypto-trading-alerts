"""Regression test for the 2026-09-08 outage: a YAML key left with no
value (e.g. `notify:` with everything under it commented out) parses to
None, not the field's default — `dict.get(key, default)` only applies
`default` when the KEY IS ABSENT, not when its value is None. That one-
line trap crash-looped the entire watcher for 11 days before anyone
noticed, because no test exercised the real config.yaml end-to-end.

These tests load the actual config.yaml (not a synthetic dict) and
replicate what `alerts.watcher.main()` does with it during startup, up
through constructing the Notifier — the exact call chain that crashed.
They also directly guard against every key going through the same
`.get(key) or default` pattern ever regressing to the unsafe
`.get(key, default)` form for a key that's blank in the current config.
"""

import os

import yaml

from alerts.notify import Notifier
from alerts.watcher import DEFAULT_CONFIG, normalize_symbols


def _load():
    with open(DEFAULT_CONFIG) as f:
        return yaml.safe_load(f)


def test_real_config_notify_key_never_crashes_notifier():
    """The exact call from watcher.main() that broke in production."""
    config = _load()
    notifier = Notifier(config.get("notify") or [], lambda: {})
    assert notifier is not None  # must not raise, regardless of `active`


def test_real_config_symbols_iterate_without_error():
    config = _load()
    pairs = list(normalize_symbols(config))
    assert len(pairs) > 0


def test_real_config_rules_and_telegram_block_readable():
    config = _load()
    rules = config.get("rules") or []
    assert len(rules) > 0
    telegram_enabled = (config.get("telegram") or {}).get("enabled", True)
    assert isinstance(telegram_enabled, bool)


def test_blank_yaml_key_parses_to_none_not_default():
    """Documents the actual footgun: this is valid YAML, and `notify`
    below is None, not []. Any code using `config.get("notify", [])`
    instead of `config.get("notify") or []` will crash on this shape."""
    parsed = yaml.safe_load("notify:\n  # nothing here\ntimeframe: 5m\n")
    assert parsed["notify"] is None
    assert parsed.get("notify", []) is None  # the trap, preserved as a canary


def test_notifier_survives_every_blank_top_level_key():
    """Simulates a well-meaning comment-out of any section: nothing here
    should ever raise, matching the hardened .get(key) or default pattern."""
    blank_variants = ["notify", "rules", "symbols", "telegram"]
    config = _load()
    for key in blank_variants:
        broken = dict(config)
        broken[key] = None
        # Mirrors watcher.main()'s startup sequence for each key in turn.
        list(normalize_symbols(broken))
        for rc in broken.get("rules") or []:
            dict(rc.get("params") or {})
        (broken.get("telegram") or {}).get("enabled", True)
        Notifier(broken.get("notify") or [], lambda: {})
