"""Runtime corporate-governance veto, stored nowhere.

The single-name catastrophe this system cannot survive is not a bad momentum
reading — it is a promoter default, a forensic audit, an auditor resignation or
an insolvency filing on a name the book already holds. Those arrive as NSE
announcements hours or days before the price gaps down through consecutive lower
circuits with no exit available.

NSE publishes both feeds live and keyless. Nothing here is persisted: the
payload is fetched during the daily scan, matched against the candidate list,
and discarded. That keeps the 512 MB Neon ceiling untouched while adding the one
filter that protects against permanent capital impairment rather than ordinary
drawdown.

The feeds require a cookie handshake — a request to the homepage first — which
is why a naive fetch returns nothing.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import urllib.request
from typing import Optional

log = logging.getLogger("equisense.ingest")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
HOME = "https://www.nseindia.com"
ANNOUNCEMENTS = f"{HOME}/api/corporate-announcements?index=equities"
CORP_ACTIONS = f"{HOME}/api/corporates-corporateActions?index=equities"
TIMEOUT = 20

# Phrases that describe permanent impairment rather than a bad quarter. Kept
# deliberately narrow: a filter that vetoes on ordinary bad news would abstain
# constantly and train the user to override it.
VETO_TERMS = (
    "insolvency", "ibc", "nclt", "liquidation", "winding up",
    "forensic audit", "auditor resignation", "resignation of auditor",
    "qualified opinion", "adverse opinion", "disclaimer of opinion",
    "default", "invocation of pledge", "pledge invoked",
    "fraud", "misappropriation", "sebi order", "debarred",
    "suspension of trading",
)


def _opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _get_json(url: str, op) -> Optional[list]:
    req = urllib.request.Request(url, headers=UA)
    with op.open(req, timeout=TIMEOUT) as f:
        payload = json.loads(f.read())
    if isinstance(payload, list):
        return payload
    return payload.get("data") or []


def fetch_governance_alerts() -> dict:
    """Live announcements and corporate actions. Never stored.

    Fails OPEN — an unreachable feed vetoes nothing — but says so. Failing
    closed would halt the whole book on a network blip, which is a worse
    failure than briefly losing the filter; failing open SILENTLY would be worse
    than either, so the caller is told which state it is in.
    """
    op = _opener()
    try:
        op.open(urllib.request.Request(HOME, headers=UA), timeout=TIMEOUT).read()
    except Exception as exc:                           # noqa: BLE001
        return {"available": False, "reason": f"cookie handshake failed: {exc}",
                "announcements": [], "corporate_actions": []}
    out: dict = {"available": True, "announcements": [], "corporate_actions": []}
    for key, url in (("announcements", ANNOUNCEMENTS),
                     ("corporate_actions", CORP_ACTIONS)):
        try:
            out[key] = _get_json(url, op) or []
        except Exception as exc:                       # noqa: BLE001
            out[key] = []
            out.setdefault("errors", []).append(f"{key}: {type(exc).__name__}")
    if not out["announcements"] and not out["corporate_actions"]:
        out["available"] = False
        out["reason"] = "both feeds returned empty"
    return out


def _text_of(row: dict) -> str:
    parts = [str(row.get(k, "")) for k in
             ("desc", "attchmntText", "smIndustry", "sm_name", "subject", "ind")]
    return " ".join(parts).lower()


def _symbol_of(row: dict) -> str:
    for k in ("symbol", "sm_symbol", "series", "comp"):
        v = row.get(k)
        if v:
            return str(v).strip().upper()
    return ""


def governance_vetoes(tickers: list[str],
                      alerts: Optional[dict] = None) -> dict:
    """{ticker: reason} for candidates carrying a disqualifying announcement.

    Matching is on the SYMBOL field where present, falling back to a
    word-boundary check on the company-name text, because the announcements feed
    is inconsistent about which it populates.
    """
    alerts = alerts if alerts is not None else fetch_governance_alerts()
    if not alerts.get("available"):
        return {"available": False,
                "reason": alerts.get("reason", "feed unavailable"),
                "vetoed": {},
                "note": ("Governance filter is OFF for this scan. It fails open "
                         "so a network blip cannot halt the book, but the "
                         "protection against insolvency and forensic-audit "
                         "events is absent until the feed returns.")}
    want = {t.upper() for t in tickers}
    vetoed: dict[str, str] = {}
    for row in alerts.get("announcements", []):
        sym = _symbol_of(row)
        text = _text_of(row)
        hit = next((term for term in VETO_TERMS if term in text), None)
        if not hit:
            continue
        match = sym if sym in want else next(
            (t for t in want if t and f" {t.lower()} " in f" {text} "), None)
        if match:
            vetoed[match] = (f"NSE announcement mentions '{hit}' — withheld. "
                             "This is the class of event that gaps a name down "
                             "through consecutive lower circuits with no exit.")
    return {"available": True, "vetoed": vetoed,
            "scanned_announcements": len(alerts.get("announcements", [])),
            "note": ("Fetched at scan time and discarded — nothing is persisted, "
                     "so the storage ceiling is untouched.")}
