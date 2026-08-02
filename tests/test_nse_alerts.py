"""Runtime governance veto.

The value here is entirely in the failure modes: what happens when the feed is
down, and whether an ordinary bad-news announcement can trigger a veto it should
not. Both decide whether the filter is trusted or overridden.
"""
from equisense.ingestion.nse_alerts import VETO_TERMS, governance_vetoes


def _ann(desc, symbol=None):
    row = {"desc": desc}
    if symbol:
        row["symbol"] = symbol
    return row


def test_an_insolvency_filing_vetoes_the_name():
    alerts = {"available": True, "corporate_actions": [],
              "announcements": [_ann("Intimation of insolvency proceedings under IBC",
                                     "ACME")]}
    out = governance_vetoes(["ACME", "OTHER"], alerts)
    assert "ACME" in out["vetoed"] and "OTHER" not in out["vetoed"]
    assert "lower circuits" in out["vetoed"]["ACME"]


def test_ordinary_bad_news_does_not_veto():
    """A filter that fires on any negative headline would abstain constantly and
    train the user to override it, which destroys its value entirely."""
    alerts = {"available": True, "corporate_actions": [],
              "announcements": [
                  _ann("Q2 results: revenue declined 12% year on year", "ACME"),
                  _ann("Change of registered office address", "ACME"),
                  _ann("Board meeting intimation for dividend", "ACME")]}
    assert governance_vetoes(["ACME"], alerts)["vetoed"] == {}


def test_the_filter_fails_OPEN_but_says_so():
    """Failing closed would halt the whole book on a network blip — a worse
    failure than briefly losing the filter. Failing open SILENTLY would be worse
    than either, so the caller must be told the protection is absent."""
    out = governance_vetoes(["ACME"], {"available": False, "reason": "timeout"})
    assert out["available"] is False
    assert out["vetoed"] == {}
    assert "OFF for this scan" in out["note"]
    assert "absent" in out["note"]


def test_veto_terms_target_permanent_impairment_not_volatility():
    """Each term must describe capital that does not come back, not a bad
    quarter."""
    for t in ("insolvency", "forensic audit", "auditor resignation", "fraud",
              "invocation of pledge", "sebi order"):
        assert t in VETO_TERMS
    for t in ("results", "decline", "loss", "downgrade", "guidance"):
        assert t not in VETO_TERMS, f"'{t}' would fire on ordinary bad news"


def test_matching_falls_back_to_company_name_text():
    """The announcements feed is inconsistent about populating `symbol`, so a
    symbol-only match would silently miss most rows."""
    alerts = {"available": True, "corporate_actions": [],
              "announcements": [_ann("ACME Limited — forensic audit ordered")]}
    assert "ACME" in governance_vetoes(["ACME"], alerts)["vetoed"]
