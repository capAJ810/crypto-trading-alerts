"""Notification fan-out via Apprise.

Channels are Apprise URLs listed in config.yaml under `notify:`.
Secrets are referenced as ${ENV_VAR} and expanded from the environment
(GitHub Actions secrets in CI, .env locally). A URL whose variables are
missing is skipped with a warning, so e.g. Telegram can be added later
without breaking email.

Add a recipient:  append to ALERT_EMAILS (comma-separated) or
                  TELEGRAM_CHAT_IDS (slash-separated).
Add a channel:    append any Apprise URL (Discord, Slack, SMS, ...)
                  to `notify:` in config.yaml — 100+ services supported.
"""

import logging
import os
import re
from typing import List

import apprise

log = logging.getLogger(__name__)

_UNEXPANDED = re.compile(r"\$\{?\w+\}?")


class Notifier:
    def __init__(self, urls: List[str]):
        self.apprise = apprise.Apprise()
        self.active = 0
        for raw in urls:
            expanded = os.path.expandvars(raw)
            if _UNEXPANDED.search(expanded):
                log.warning("Skipping notify URL with unset variables: %s", raw)
                continue
            if self.apprise.add(expanded):
                self.active += 1
            else:
                log.warning("Apprise rejected notify URL (bad format?): %s", raw)

    def send(self, title: str, body: str) -> bool:
        if self.active == 0:
            log.error("No active notification channels — alert NOT delivered: %s", title)
            return False
        ok = self.apprise.notify(title=title, body=body)
        if ok:
            log.info("Notified: %s", title)
        else:
            log.error("Notification delivery failed for: %s", title)
        return bool(ok)
