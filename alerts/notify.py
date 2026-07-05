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


def allowed_addresses() -> List[str]:
    """All addresses the owner has allowlisted in ALERT_EMAILS."""
    return [addr for emails, _ in
            parse_alert_emails(os.environ.get("ALERT_EMAILS", ""))
            for addr in emails]


class Notifier:
    """Fan-out with per-recipient coin filtering.

    Coin filters come from two places, checked per address at SEND time:
      1. a static ':BTC,ETH' suffix in the ALERT_EMAILS secret (owner-set,
         wins if present), else
      2. `link_filters_fn()` — a live map of email -> set of coin bases,
         fed by the Telegram bot's /email + /coins self-service (each
         user's Telegram coin picks also route their email).
    No filter anywhere = all coins.
    """

    def __init__(self, urls: List[str], link_filters_fn=None):
        self.static_buckets: List[apprise.Apprise] = []
        self.email_template: Optional[str] = None
        self.link_filters_fn = link_filters_fn or (lambda: {})
        for raw in urls:
            if "${ALERT_EMAILS}" in raw:
                if _expand(raw.replace("${ALERT_EMAILS}", "x@x")) is None:
                    log.warning("Skipping notify URL with unset variables: %s", raw)
                    continue
                self.email_template = raw
            else:
                expanded = _expand(raw)
                if expanded is None:
                    log.warning("Skipping notify URL with unset variables: %s", raw)
                    continue
                app = apprise.Apprise()
                if app.add(expanded):
                    self.static_buckets.append(app)
                else:
                    log.warning("Apprise rejected notify URL (bad format?): %s", raw)

    @property
    def active(self) -> int:
        return len(self.static_buckets) + (1 if self.email_template else 0)

    @staticmethod
    def _norm_link(v) -> dict:
        """Normalize a link_filters value to {"coins":…, "cats":…, "tfs":…}.

        Legacy shape was a bare coin-set; the current watcher supplies a dict
        that also carries the chat's /alerts type & candle-size preferences.
        """
        if isinstance(v, dict):
            return v
        return {"coins": v}

    @staticmethod
    def _prefs_pass(link: Optional[dict], category: Optional[str],
                    timeframe: Optional[str]) -> bool:
        """Does this link's type/candle preference allow the alert through?
        None preference (or no link at all) = no restriction."""
        if not link:
            return True
        cats = link.get("cats")
        if category and cats is not None and category not in cats:
            return False
        tfs = link.get("tfs")
        if timeframe and tfs is not None and timeframe not in tfs:
            return False
        return True

    def email_recipients(self, pair: Optional[str],
                         category: Optional[str] = None,
                         timeframe: Optional[str] = None) -> List[str]:
        """Addresses that should receive an alert for `pair`.

        Two sources are merged:
          1. ALERT_EMAILS (owner-set), honoring any static ':BTC,ETH' filter.
          2. Self-service addresses users added themselves via the Telegram
             bot's /email — surfaced by link_filters_fn — for any address not
             already covered by ALERT_EMAILS.
        Coin filter None = all coins; empty set = none (e.g. a chat in
        'telegram only' mode). A linked chat's /alerts preferences (alert-type
        categories, candle timeframes) gate its email too — even when the
        owner's static coin filter decides the coins.
        """
        base = pair.split("/")[0].upper() if pair else None
        links = {k.lower(): self._norm_link(v)
                 for k, v in self.link_filters_fn().items()}
        out: List[str] = []
        seen = set()

        for emails, static_filt in parse_alert_emails(
                os.environ.get("ALERT_EMAILS", "")):
            for addr in emails:
                seen.add(addr.lower())  # ALERT_EMAILS owns this address
                link = links.get(addr.lower())
                coins = static_filt if static_filt is not None \
                    else (link or {}).get("coins")
                if base is not None and coins is not None and base not in coins:
                    continue
                if not self._prefs_pass(link, category, timeframe):
                    continue
                out.append(addr)

        for addr, link in links.items():  # bot self-service addresses
            if addr in seen:
                continue
            coins = link.get("coins")
            if base is not None and coins is not None and base not in coins:
                continue
            if not self._prefs_pass(link, category, timeframe):
                continue
            out.append(addr)
        return out

    def send(self, title: str, body: str, tier: str = "info",
             pair: Optional[str] = None, category: Optional[str] = None,
             timeframe: Optional[str] = None) -> bool:
        if self.active == 0:
            log.error("No active notification channels — alert NOT delivered: %s", title)
            return False
        subject, html_body = render_email(title, body, tier)
        delivered = attempted = 0

        for app in self.static_buckets:
            attempted += 1
            delivered += int(app.notify(title=subject, body=html_body,
                                        body_format=apprise.NotifyFormat.HTML))

        if self.email_template:
            recipients = self.email_recipients(pair, category, timeframe)
            if recipients:
                attempted += 1
                url = self.email_template.replace(
                    "${ALERT_EMAILS}", quote(",".join(recipients), safe=","))
                app = apprise.Apprise()
                app.add(_expand(url))
                delivered += int(app.notify(title=subject, body=html_body,
                                            body_format=apprise.NotifyFormat.HTML))
            else:
                log.info("No email recipients opted in to %s — email skipped", pair)

        if attempted == 0:
            return True  # everything legitimately filtered out
        if delivered:
            log.info("Notified %d/%d channel group(s): %s", delivered,
                     attempted, title)
        else:
            log.error("Notification delivery failed for: %s", title)
        return delivered > 0
