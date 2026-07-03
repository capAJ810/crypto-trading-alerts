from alerts.notify import Notifier, parse_alert_emails, render_email, tier_for_side


def test_parse_alert_emails_legacy_comma_list():
    groups = parse_alert_emails("a@x.com,b@y.com")
    assert groups == [(["a@x.com", "b@y.com"], None)]


def test_parse_alert_emails_with_filters():
    groups = parse_alert_emails("a@x.com:BTC,eth; b@y.com:all ;c@z.com")
    assert groups[0] == (["a@x.com"], {"BTC", "ETH"})
    assert groups[1] == (["b@y.com"], None)   # 'all' keyword
    assert groups[2] == (["c@z.com"], None)   # no filter given


def test_email_recipients_static_linked_and_default(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("ALERT_EMAILS", "a@x.com:BTC;b@y.com;c@z.com")
    links = {"B@Y.com": {"ETH"}}  # self-service link (case-insensitive)
    n = Notifier(["mailto://${GMAIL_USER}:${GMAIL_APP_PASSWORD}@gmail.com"
                  "?to=${ALERT_EMAILS}"], lambda: links)
    assert n.active == 1
    # a = static BTC-only; b = linked ETH-only via bot; c = no filter (all)
    assert n.email_recipients("BTC/USDT") == ["a@x.com", "c@z.com"]
    assert n.email_recipients("ETH/USDT") == ["b@y.com", "c@z.com"]
    assert n.email_recipients("HYPE/USDT") == ["c@z.com"]
    assert n.email_recipients(None) == ["a@x.com", "b@y.com", "c@z.com"]


def test_static_secret_filter_beats_bot_link(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("ALERT_EMAILS", "a@x.com:BTC")
    n = Notifier(["mailto://${GMAIL_USER}:${GMAIL_APP_PASSWORD}@gmail.com"
                  "?to=${ALERT_EMAILS}"], lambda: {"a@x.com": {"ETH"}})
    assert n.email_recipients("ETH/USDT") == []  # owner's static filter wins
    assert n.email_recipients("BTC/USDT") == ["a@x.com"]


def test_tier_mapping():
    assert tier_for_side("BUY") == "confirmed-buy"
    assert tier_for_side("SELL") == "confirmed-sell"
    for side in ("WEAK BUY", "WEAK SELL", "NEAR-BUY", "NEAR-SELL",
                 "INTRABAR BUY", "INTRABAR SELL"):
        assert tier_for_side(side) == "advisory"
    assert tier_for_side("INFO") == "info"


def test_confirmed_email_is_big_bold_and_prefixed():
    subject, body = render_email("🟢 BUY BTC/USDT 5m", "details", "confirmed-buy")
    assert subject.startswith("✅ CONFIRMED |")
    assert "font-size:24px" in body and "font-weight:800" in body
    assert "#1a7f37" in body  # green accent
    subject, body = render_email("🔴 SELL ETH/USDT 5m", "details", "confirmed-sell")
    assert "#c62828" in body  # red accent


def test_advisory_email_is_small_and_muted():
    subject, body = render_email("🟡 NEAR-SELL SOL/USDT", "details", "advisory")
    assert subject == "🟡 NEAR-SELL SOL/USDT"  # no CONFIRMED prefix
    assert "font-size:12.5px" in body and "#8a8a8a" in body
    assert "font-size:24px" not in body


def test_email_body_html_is_escaped():
    _, body = render_email("t", "1 < 2 & <script>", "info")
    assert "<script>" not in body and "&lt;script&gt;" in body
