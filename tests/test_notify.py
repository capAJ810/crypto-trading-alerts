from alerts.notify import render_email, tier_for_side


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
