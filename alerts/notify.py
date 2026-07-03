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

import html
import logging
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import quote

import apprise

log = logging.getLogger(__name__)

# Email tiers: confirmed signals must be recognizable at a glance, both in
# the inbox list (subject prefix) and inside the opened email (big colored
# headline); advisory tiers (NEAR/WEAK/INTRABAR) render small and muted.
TIERS = {
    "confirmed-buy": {"accent": "#1a7f37", "big": True,
                      "label": "CONFIRMED SIGNAL"},
    "confirmed-sell": {"accent": "#c62828", "big": True,
                       "label": "CONFIRMED SIGNAL"},
    "advisory": {"accent": "#8a8a8a", "big": False, "label": "fyi"},
    "info": {"accent": "#4a6fa5", "big": False, "label": "info"},
}


def tier_for_side(side: str) -> str:
    """Map a Signal.side to an email tier."""
    if side == "BUY":
        return "confirmed-buy"
    if side == "SELL":
        return "confirmed-sell"
    if any(w in side for w in ("WEAK", "NEAR", "INTRABAR")):
        return "advisory"
    return "info"


def render_email(title: str, body: str, tier: str = "info") -> Tuple[str, str]:
    """Build (subject, html_body) for an alert email."""
    style = TIERS.get(tier, TIERS["info"])
    accent = style["accent"]
    safe_body = html.escape(body)
    safe_title = html.escape(title)

    if style["big"]:
        subject = f"✅ CONFIRMED | {title}"
        head = (f'<div style="font-size:13px;font-weight:700;color:{accent};'
                f'letter-spacing:2px;text-transform:uppercase;">'
                f'{style["label"]}</div>'
                f'<div style="font-size:24px;font-weight:800;color:{accent};'
                f'line-height:1.3;margin:6px 0 14px;">{safe_title}</div>')
        body_css = "font-size:15px;color:#222;"
    else:
        subject = title
        head = (f'<div style="font-size:12px;color:{accent};'
                f'text-transform:uppercase;letter-spacing:1px;">'
                f'{style["label"]}</div>'
                f'<div style="font-size:14px;font-weight:600;color:{accent};'
                f'margin:4px 0 10px;">{safe_title}</div>')
        body_css = "font-size:12.5px;color:#777;"

    html_body = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;'
        f'border-left:6px solid {accent};padding:14px 18px;margin:8px 0;">'
        f'{head}'
        f'<div style="{body_css}white-space:pre-wrap;line-height:1.55;">'
        f'{safe_body}</div>'
        f'</div>')
    return subject, html_body

_VAR = re.compile(r"\$\{(\w+)\}")


def _expand(raw: str) -> Optional[str]:
    """Substitute ${VAR} with the URL-encoded env value.

    Values are percent-encoded (comma/slash preserved as list delimiters)
    so credentials containing '@', ':', etc. — e.g. a full Gmail address
    used as the mailto:// username — don't corrupt the URL's structure.
    Returns None if any referenced variable is unset.
    """
    missing = []

    def repl(m: re.Match) -> str:
        name = m.group(1)
        val = os.environ.get(name)
        if val is None:
            missing.append(name)
            return m.group(0)
        return quote(val, safe=",/")

    expanded = _VAR.sub(repl, raw)
    return None if missing else expanded


class Notifier:
    def __init__(self, urls: List[str]):
        self.apprise = apprise.Apprise()
        self.active = 0
        for raw in urls:
            expanded = _expand(raw)
            if expanded is None:
                log.warning("Skipping notify URL with unset variables: %s", raw)
                continue
            if self.apprise.add(expanded):
                self.active += 1
            else:
                log.warning("Apprise rejected notify URL (bad format?): %s", raw)

    def send(self, title: str, body: str, tier: str = "info") -> bool:
        if self.active == 0:
            log.error("No active notification channels — alert NOT delivered: %s", title)
            return False
        subject, html_body = render_email(title, body, tier)
        ok = self.apprise.notify(title=subject, body=html_body,
                                 body_format=apprise.NotifyFormat.HTML)
        if ok:
            log.info("Notified: %s", title)
        else:
            log.error("Notification delivery failed for: %s", title)
        return bool(ok)
