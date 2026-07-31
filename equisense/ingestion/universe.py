"""Universe definition (RESEARCH_BLUEPRINT §5: bounded, curated).

NIFTY-50 core (as of mid-2026), with sector labels and cap bands. Yahoo
symbol = f"{ticker}.NS". Banks/financials are included in the universe for
price-based engines but statement-based engines skip them (bank statements
don't fit the industrial canonical schema — flagged, not silently wrong).
"""
from __future__ import annotations

FINANCIAL_SECTORS = {"Financials"}

# ticker: (name, sector, cap_band, peer_group, is_financial)
NIFTY50 = {
    "RELIANCE":   ("Reliance Industries", "Energy", "large", "energy_conglomerate"),
    "TCS":        ("Tata Consultancy Services", "Information Technology", "large", "it_services"),
    "HDFCBANK":   ("HDFC Bank", "Financials", "large", "private_banks"),
    "ICICIBANK":  ("ICICI Bank", "Financials", "large", "private_banks"),
    "INFY":       ("Infosys", "Information Technology", "large", "it_services"),
    "HINDUNILVR": ("Hindustan Unilever", "Consumer Staples", "large", "fmcg"),
    "ITC":        ("ITC", "Consumer Staples", "large", "fmcg"),
    "SBIN":       ("State Bank of India", "Financials", "large", "psu_banks"),
    "BHARTIARTL": ("Bharti Airtel", "Communication Services", "large", "telecom"),
    "KOTAKBANK":  ("Kotak Mahindra Bank", "Financials", "large", "private_banks"),
    "LT":         ("Larsen & Toubro", "Industrials", "large", "infra_engineering"),
    "AXISBANK":   ("Axis Bank", "Financials", "large", "private_banks"),
    "ASIANPAINT": ("Asian Paints", "Consumer Discretionary", "large", "paints"),
    "MARUTI":     ("Maruti Suzuki India", "Consumer Discretionary", "large", "autos"),
    "SUNPHARMA":  ("Sun Pharmaceutical", "Healthcare", "large", "pharma"),
    "TITAN":      ("Titan Company", "Consumer Discretionary", "large", "jewellery_retail"),
    "ULTRACEMCO": ("UltraTech Cement", "Materials", "large", "cement"),
    "NESTLEIND":  ("Nestle India", "Consumer Staples", "large", "fmcg"),
    "WIPRO":      ("Wipro", "Information Technology", "large", "it_services"),
    "M&M":        ("Mahindra & Mahindra", "Consumer Discretionary", "large", "autos"),
    "HCLTECH":    ("HCL Technologies", "Information Technology", "large", "it_services"),
    "NTPC":       ("NTPC", "Utilities", "large", "power"),
    "POWERGRID":  ("Power Grid Corporation", "Utilities", "large", "power"),
    "TATAMOTORS": ("Tata Motors", "Consumer Discretionary", "large", "autos"),
    "TATASTEEL":  ("Tata Steel", "Materials", "large", "metals"),
    "JSWSTEEL":   ("JSW Steel", "Materials", "large", "metals"),
    "BAJFINANCE": ("Bajaj Finance", "Financials", "large", "nbfc"),
    "ADANIENT":   ("Adani Enterprises", "Industrials", "large", "conglomerate"),
    "ADANIPORTS": ("Adani Ports & SEZ", "Industrials", "large", "ports_logistics"),
    "COALINDIA":  ("Coal India", "Energy", "large", "mining"),
    "ONGC":       ("Oil & Natural Gas Corp", "Energy", "large", "oil_gas"),
    "GRASIM":     ("Grasim Industries", "Materials", "large", "cement"),
    "TECHM":      ("Tech Mahindra", "Information Technology", "large", "it_services"),
    "INDUSINDBK": ("IndusInd Bank", "Financials", "large", "private_banks"),
    "CIPLA":      ("Cipla", "Healthcare", "large", "pharma"),
    "DRREDDY":    ("Dr. Reddy's Laboratories", "Healthcare", "large", "pharma"),
    "APOLLOHOSP": ("Apollo Hospitals Enterprise", "Healthcare", "large", "hospitals"),
    "DIVISLAB":   ("Divi's Laboratories", "Healthcare", "large", "pharma"),
    "BAJAJ-AUTO": ("Bajaj Auto", "Consumer Discretionary", "large", "autos"),
    "EICHERMOT":  ("Eicher Motors", "Consumer Discretionary", "large", "autos"),
    "HEROMOTOCO": ("Hero MotoCorp", "Consumer Discretionary", "large", "autos"),
    "BRITANNIA":  ("Britannia Industries", "Consumer Staples", "large", "fmcg"),
    "TATACONSUM": ("Tata Consumer Products", "Consumer Staples", "large", "fmcg"),
    "HINDALCO":   ("Hindalco Industries", "Materials", "large", "metals"),
    "SHRIRAMFIN": ("Shriram Finance", "Financials", "large", "nbfc"),
    "BPCL":       ("Bharat Petroleum", "Energy", "large", "oil_gas"),
    "SBILIFE":    ("SBI Life Insurance", "Financials", "large", "insurance"),
    "HDFCLIFE":   ("HDFC Life Insurance", "Financials", "large", "insurance"),
    "TRENT":      ("Trent", "Consumer Discretionary", "large", "retail"),
    "BEL":        ("Bharat Electronics", "Industrials", "large", "defence"),
}

# Macro series (all keyless via Yahoo): symbol -> (name, role)
MACRO_SERIES = {
    "^NSEI":     ("NIFTY 50", "index"),
    # Bank Nifty carries its own option chain and is one of the two underlyings
    # whose implied volatility is captured daily. Without a deep close history
    # the realised-vol side of its variance premium has nothing to compare
    # against, leaving half that study untestable for years.
    "^NSEBANK":  ("NIFTY Bank", "index"),
    "^INDIAVIX": ("India VIX", "vix"),
    "INR=X":     ("USD/INR", "currency"),
    "BZ=F":      ("Brent Crude", "commodity"),
    "GC=F":      ("Gold", "commodity"),
    "^GSPC":     ("S&P 500", "global_index"),
}


def yahoo_symbol(ticker: str) -> str:
    return f"{ticker}.NS"


def is_financial(sector: str) -> bool:
    return sector in FINANCIAL_SECTORS


# --------------------------------------------------- exchange-derived universe
# The NIFTY50 map above is now a PINNED FALLBACK, not the source of truth. NSE
# publishes index membership as a free keyless CSV, so the universe, the
# industry classification and the ISINs all come from the exchange itself and
# stay correct through index reshuffles without anyone editing this file.

# NSE macro-industry -> the platform's internal sector taxonomy. Explicit rather
# than fuzzy-matched, because a silent misclassification would put a company in
# the wrong peer set and quietly corrupt every relative comparison.
NSE_INDUSTRY_TO_SECTOR = {
    "Financial Services": "Financials",
    "Information Technology": "Information Technology",
    "Oil Gas & Consumable Fuels": "Energy",
    "Fast Moving Consumer Goods": "Consumer Staples",
    "Automobile and Auto Components": "Consumer Discretionary",
    "Consumer Durables": "Consumer Discretionary",
    "Consumer Services": "Consumer Discretionary",
    "Healthcare": "Healthcare",
    "Metals & Mining": "Materials",
    "Construction Materials": "Materials",
    "Chemicals": "Materials",
    "Capital Goods": "Industrials",
    "Construction": "Industrials",
    "Services": "Industrials",
    "Power": "Utilities",
    "Telecommunication": "Communication Services",
    "Media Entertainment & Publication": "Communication Services",
    "Realty": "Real Estate",
    "Textiles": "Consumer Discretionary",
    "Forest Materials": "Materials",
    "Diversified": "Industrials",
}

INDEX_CAP_BAND = {
    "nifty50": "large", "niftynext50": "large", "nifty100": "large",
    "nifty200": "large", "niftymidcap150": "mid", "niftysmallcap250": "small",
    "nifty500": "large",
}


def sector_from_nse_industry(industry: str) -> str:
    """Map NSE's macro-industry label to the internal sector taxonomy."""
    return NSE_INDUSTRY_TO_SECTOR.get((industry or "").strip(), "Other")


def resolve_universe(index_key: str = "nifty50",
                     allow_fallback: bool = True) -> tuple[dict, str]:
    """(universe, source) — live NSE membership, or the pinned snapshot.

    `universe` is {ticker: (name, sector, cap_band, peer_group)}, matching the
    shape of the NIFTY50 constant so every caller is unchanged.

    Falling back is reported, never silent: running against a stale pinned list
    after an index reshuffle means analysing companies the index no longer holds
    and missing the ones it added, which is a survivorship problem in miniature.
    """
    from .nse_archive import fetch_index_constituents

    rows = fetch_index_constituents(index_key)
    if not rows:
        if not allow_fallback:
            return {}, "unavailable (NSE constituent list unreachable)"
        return dict(NIFTY50), ("PINNED FALLBACK — NSE constituent list "
                               "unreachable; membership may be stale")
    band = INDEX_CAP_BAND.get(index_key.lower(), "large")
    out: dict[str, tuple] = {}
    for r in rows:
        sector = sector_from_nse_industry(r["nse_industry"])
        peer = (r["nse_industry"] or "other").lower().replace(" ", "_").replace("&", "and")
        out[r["ticker"]] = (r["name"], sector, band, peer)
    return out, f"NSE published constituents ({index_key}, {len(out)} names)"
