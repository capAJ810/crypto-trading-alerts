"""Notification fan-out via Apprise.

Channels are Apprise URLs listed in config.yaml under `notify:`.
Secrets are referenced as ${ENV_VAR} and expanded from the environment
(GitHub Actions secrets in CI, .env locally). A URL whose variables are
missing is skipped with a warning, so e.g. Telegram can be added later
without breaking email.

ALERT_EMAILS supports per-recipient coin filters. Groups are separated
by ';', each group is 'email[:COIN,COIN,...]':

    a@x.com:BTC,ETH;b@y.com          # a@x only BTC+ETH, b@y everything
    a@x.com:all;b@y.com:HYPE         # 'all' keyword = no filter
    a@x.com,b@y.com                  # legacy comma list = everyone, all coins

Add a recipient:  append to ALERT_EMAILS or TELEGRAM_CHAT_IDS
                  (slash-separated).
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


def parse_alert_emails(value: str) -> List[Tuple[List[str], Optional[set]]]:
    """Parse ALERT_EMAILS into [(emails, coin_filter_or_None), ...].

    None filter = all coins. Coin names are base symbols ('BTC'),
    case-insensitive; 'all' disables the filter for that recipient.
    """
    groups = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            addr, _, coins = part.partition(":")
            wanted = {c.strip().upper() for c in coins.split(",") if c.strip()}
            filt = None if (not wanted or "ALL" in wanted) else wanted
            groups.append(([addr.strip()], filt))
        else:  # legacy comma-separated list, no filters
            emails = [e.strip() for e in part.split(",") if e.strip()]
            if emails:
                groups.append((emails, None))
    return groups


class Notifier:
    """Fan-out with per-recipient coin filtering.

    Each notify URL becomes one or more buckets of (coin_filter, Apprise).
    A URL containing ${ALERT_EMAILS} is expanded into one bucket per
    recipient group so different addresses can watch different coins.
    """

    def __init__(self, urls: List[str]):
        self.buckets: List[Tuple[Optional[set], apprise.Apprise]] = []
        for raw in urls:
            if "${ALERT_EMAILS}" in raw:
                for emails, filt in parse_alert_emails(
                        os.environ.get("ALERT_EMAILS", "")):
                    url = raw.replace("${ALERT_EMAILS}",
                                      quote(",".join(emails), safe=","))
                    self._add(url, raw, filt)
            else:
                self._add(raw, raw, None)

    def _add(self, url: str, raw: str, filt: Optional[set]) -> None:
        expanded = _expand(url)
        if expanded is None:
            log.warning("Skipping notify URL with unset variables: %s", raw)
            return
        app = apprise.Apprise()
        if app.add(expanded):
            self.buckets.append((filt, app))
        else:
            log.warning("Apprise rejected notify URL (bad format?): %s", raw)

    @property
    def active(self) -> int:
        return len(self.buckets)

    def send(self, title: str, body: str, tier: str = "info",
             pair: Optional[str] = None) -> bool:
        """Send to every bucket whose coin filter matches `pair`.
        pair=None (tests, system notices) goes to everyone."""
        if not self.buckets:
            log.error("No active notification channels — alert NOT delivered: %s", title)
            return False
        base = pair.split("/")[0].upper() if pair else None
        subject, html_body = render_email(title, body, tier)
        delivered = skipped = 0
        for filt, app in self.buckets:
            if base is not None and filt is not None and base not in filt:
                skipped += 1
                continue
            if app.notify(title=subject, body=html_body,
                          body_format=apprise.NotifyFormat.HTML):
                delivered += 1
        if delivered:
            log.info("Notified %d channel group(s) (%d filtered out): %s",
                     delivered, skipped, title)
        elif skipped == len(self.buckets):
            log.info("All %d channel group(s) filtered out %s — nothing sent",
                     skipped, pair)
        else:
            log.error("Notification delivery failed for: %s", title)
        return delivered > 0 or skipped == len(self.buckets)
