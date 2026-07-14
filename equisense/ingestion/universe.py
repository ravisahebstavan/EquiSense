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
