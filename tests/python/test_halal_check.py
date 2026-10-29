"""Halal gate: parsing and decisions from a saved Osooli page. No browser, no network."""
import pytest

import halal_check as hc


class FakeResponse:
    status_code = 200

    def json(self):
        raise ValueError("not JSON")


def test_verdict_words_are_normalised():
    assert hc._norm("Halal") == "halal"
    assert hc._norm("Non-compliant") == "haram"
    assert hc._norm("Questionable") == "doubtful"
    assert hc._norm("") == "none"


def test_osooli_page_is_parsed(osooli_text):
    info = hc.parse_osooli(osooli_text, "ARLO")
    assert info["verdicts_raw"] == {"Yaaqen": "Halal", "Zoya": "Halal", "Musaffa": "Halal"}
    assert info["purification_pct"] == 1.05
    assert info["data_date"] == "2026-10-01"
    assert info["debt_mcap"] == 12.3
    assert info["interest_rev"] == 0.4


def test_page_for_another_ticker_is_not_used(osooli_text):
    assert hc.parse_osooli(osooli_text, "MSFT") is None


def test_all_three_halal_is_compliant(monkeypatch, osooli_text):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "not-a-real-key")
    monkeypatch.setattr(hc, "_read_osooli", lambda ticker, log: (osooli_text, None))
    res = hc.check_halal("ARLO", log=lambda m: None)
    assert res["status"] == "compliant"
    assert res["checked"]


def test_login_expiry_is_reported_for_telegram(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "not-a-real-key")
    monkeypatch.setattr(hc, "_read_osooli", lambda ticker, log: (None, "login_expired"))
    res = hc.check_halal("ARLO", log=lambda m: None)
    assert res["login_expired"] is True
    assert res["status"] == "unchecked"


def test_yaqeen_only_rule_passes_when_own_ratios_pass(monkeypatch, osooli_text):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "not-a-real-key")
    text = osooli_text.replace("Yaaqen\nHalal", "Yaaqen\nHaram")
    monkeypatch.setattr(hc, "_read_osooli", lambda ticker, log: (text, None))
    monkeypatch.setattr(hc, "own_ratios", lambda ticker: {
        "industry": "software", "debt_mcap": 5.0, "cash_mcap": 10.0, "interest_rev": 0.1,
        "debt_mcap_check": "pass", "cash_mcap_check": "pass", "interest_rev_check": "pass"})
    res = hc.check_halal("ARLO", log=lambda m: None)
    assert res["status"] == "compliant"
    assert "Yaaqen dropped" in res["notes"]


def test_yaqeen_only_rule_asks_a_person_when_a_ratio_fails(monkeypatch, osooli_text):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "not-a-real-key")
    text = osooli_text.replace("Yaaqen\nHalal", "Yaaqen\nHaram")
    monkeypatch.setattr(hc, "_read_osooli", lambda ticker, log: (text, None))
    monkeypatch.setattr(hc, "own_ratios", lambda ticker: {
        "industry": "software", "debt_mcap": 35.0, "cash_mcap": 10.0, "interest_rev": 0.1,
        "debt_mcap_check": "FAIL", "cash_mcap_check": "pass", "interest_rev_check": "pass"})
    res = hc.check_halal("ARLO", log=lambda m: None)
    assert res["status"] == "doubtful"
    assert "Decide manually" in res["notes"]


def test_bad_public_page_does_not_discard_the_osooli_verdict(monkeypatch, osooli_text):
    # Regression: a non-JSON reply from the public-page scrape used to set the whole check to "unchecked".
    monkeypatch.setenv("FIRECRAWL_API_KEY", "not-a-real-key")
    text = osooli_text.replace("Zoya\nHalal", "Zoya\nQuestionable")
    monkeypatch.setattr(hc, "_read_osooli", lambda ticker, log: (text, None))
    monkeypatch.setattr(hc, "_req", lambda *a, **k: FakeResponse())
    res = hc.check_halal("ARLO", log=lambda m: None)
    assert res["status"] == "doubtful"
    assert res["verdicts"]["Zoya"].endswith("(doubtful)")
    assert res["reasons"][0]["screener"] == "Zoya"
    assert "not public" in res["reasons"][0]["reason"]


def test_missing_key_is_reported_not_raised(monkeypatch):
    res = hc.check_halal("ARLO", log=lambda m: None)
    assert res["status"] == "unchecked"
    assert "FIRECRAWL_API_KEY missing" in res["notes"]


@pytest.mark.parametrize("ticker", ["ARLO", "KEX"])
def test_last_known_comes_from_the_case_log(ticker):
    assert hc.last_known(ticker)["status"] == "compliant"
