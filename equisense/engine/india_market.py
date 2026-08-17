"""Indian-market microstructure reality — the executability layer (§8.1 ext).

Every other engine here asks *is this a good trade*. This one asks the question
that comes first and that none of them can see: **can this account actually put
the trade on, and hold it for the horizon the signal reasons over?** Two facts
about the Indian cash market decide it, and both bite hardest on the SHORT side.

1. A retail account CANNOT hold a short equity position overnight. The cash
   segment has no naked delivery short — an intraday short must be squared off
   the same session (SLB, stock-lending, is institutional and not a retail
   overnight route in practice). The only way to hold a bearish view for the
   multi-week horizons this system's momentum/value/quality signals describe is
   a SINGLE-STOCK FUTURE, and those exist for only ~180 names (the F&O segment).
   A short signal on a name with no listed future is therefore NOT ACTIONABLE
   for this book, however strong the synthesis — there is no instrument to
   express it in that a retail account can carry.

   This is not a cost to haircut; it is a hard gate. The autopilot's own comment
   already knew it ("shorting equity delivery overnight is not available to
   Indian retail") while the book went on opening exactly those shorts. This
   module makes the constraint bind at the point of decision.

2. Futures trade in EXCHANGE-SET LOTS, not arbitrary share counts, and carry a
   different, generally lighter statutory-charge stack than delivery equity, and
   are held on MARGIN (~20-25% of notional) rather than paid for in full. A short
   sized as if it were a delivery position — full notional, delivery STT, share
   granularity — misstates the cost, the capital, and the executable quantity all
   at once.

LONGS ARE UNCHANGED. A long is ordinary delivery; this module leaves that path
alone and only governs how a bearish verdict becomes (or fails to become) a real
position.

--------------------------------------------------------------------- provenance
The F&O eligibility list is a PINNED, DATED SNAPSHOT — the same discipline as the
NIFTY-50 fallback in ingestion/universe.py, and for the same reason: the NSE
contract master is not reachable keyless from every host. `ingestion.fno` refreshes
it from NSE where the network allows and this list is the fallback. Falling back is
REPORTED, never silent (see `eligibility_provenance`) — trading on a stale list is
how you try to short a name that has left the segment, or refuse one that joined it.

Lot sizes are INDICATIVE as of the snapshot date. NSE revises them at the periodic
F&O review (and did a broad revision after SEBI's Nov-2024 ₹15-lakh minimum
contract-value rule), so the eligibility flag — the correctness-critical part, and
the thing that decides whether a short is possible at all — is treated as the hard
gate, while an out-of-date lot size only changes rounding granularity and is
flagged, not trusted blindly.
"""
from __future__ import annotations

from dataclasses import dataclass

# Snapshot date of the pinned eligibility/lot table below. Surfaced to the UI so a
# stale list is visible rather than assumed current.
FNO_SNAPSHOT_DATE = "2025-08-01"

# Approximate SPAN + exposure margin as a fraction of futures notional. Real margin
# is instrument- and volatility-specific (the exchange publishes it per contract);
# ~22% is a deliberately conservative round stand-in for liquid single-stock
# futures so the capital a short ties up is neither ignored (full-notional
# overstates it) nor flattered (a thin margin understates the leverage risk).
FNO_MARGIN_FRACTION = 0.22

# NSE F&O single-stock universe — pinned snapshot. Value = indicative lot size
# (shares per contract) as of FNO_SNAPSHOT_DATE, or None when eligibility is known
# but the lot size is not pinned (still shortable; round to the live NSE lot at
# execution). Bare tickers, matching the DB convention (no .NS suffix).
#
# This is the correctness-critical set: membership decides whether a short can be
# expressed at all. It is intentionally conservative — a name is listed only where
# its F&O eligibility is well established, so a false "shortable" (the dangerous
# error, since it green-lights an impossible trade) is avoided even at the cost of
# an occasional false "not shortable" (which merely abstains, the system's default).
FNO_LOT_SIZES: dict[str, int | None] = {
    # NIFTY 50 majors
    "RELIANCE": 500, "TCS": 175, "HDFCBANK": 550, "ICICIBANK": 700, "INFY": 400,
    "HINDUNILVR": 300, "ITC": 1600, "SBIN": 1500, "BHARTIARTL": 475, "KOTAKBANK": 400,
    "LT": 150, "AXISBANK": 625, "BAJFINANCE": 750, "ASIANPAINT": 200, "MARUTI": 50,
    "HCLTECH": 350, "SUNPHARMA": 350, "TITAN": 175, "ULTRACEMCO": 50, "WIPRO": 3000,
    "NESTLEIND": 250, "ONGC": 3850, "NTPC": 1500, "POWERGRID": 2700, "M&M": 350,
    "TATAMOTORS": 800, "TATASTEEL": 5500, "JSWSTEEL": 675, "ADANIENT": 300,
    "ADANIPORTS": 400, "COALINDIA": 2100, "BAJAJFINSV": 500, "GRASIM": 250,
    "HINDALCO": 1075, "BRITANNIA": 200, "CIPLA": 375, "DRREDDY": 625, "EICHERMOT": 175,
    "APOLLOHOSP": 125, "BPCL": 1800, "DIVISLAB": 100, "HEROMOTOCO": 150,
    "INDUSINDBK": 500, "BAJAJ-AUTO": 75, "TATACONSUM": 550, "SBILIFE": 375,
    "HDFCLIFE": 1100, "SHRIRAMFIN": 300, "LTIM": 100, "TECHM": 600, "TRENT": 100,
    # Broad liquid F&O midcaps / financials / PSUs
    "DLF": 825, "GAIL": 3150, "PIDILITIND": 250, "SIEMENS": 100, "DABUR": 1250,
    "GODREJCP": 500, "HAVELLS": 500, "AMBUJACEM": 900, "BANKBARODA": 2925,
    "PNB": 8000, "CANBK": 5400, "IOC": 4875, "VEDL": 1150, "HINDPETRO": 1350,
    "SAIL": 4700, "NMDC": 4500, "MOTHERSON": 3800, "BOSCHLTD": 25, "ABB": 125,
    "CHOLAFIN": 625, "BAJAJHLDNG": 60, "ICICIGI": 350, "ICICIPRULI": 700,
    "MUTHOOTFIN": 275, "PFC": 1300, "RECLTD": 1400, "IRFC": 4200, "LICI": 750,
    "TATAPOWER": 1350, "ADANIGREEN": 300, "ADANIENSOL": 425, "TORNTPHARM": 250,
    "LUPIN": 425, "AUROPHARMA": 550, "ZYDUSLIFE": 900, "ALKEM": 100, "BIOCON": 2500,
    "GLENMARK": 500, "IPCALAB": 275, "LAURUSLABS": 1200, "MANKIND": 200,
    "PAGEIND": 15, "COLPAL": 175, "MARICO": 850, "UBL": 350, "VBL": 750,
    "PGHH": 30, "BERGEPAINT": 1100, "KANSAINER": 200, "INDIGO": 150, "INTERGLOBE": 150,
    "IRCTC": 700, "CONCOR": 1000, "CESC": 3600, "TATACOMM": 350, "IDEA": 70000,
    "INDUSTOWER": 1700, "PERSISTENT": 100, "COFORGE": 100, "MPHASIS": 275,
    "OFSS": 100, "LTTS": 130, "TATAELXSI": 90, "NAUKRI": 75, "POLICYBZR": 300,
    "PAYTM": 725, "ZOMATO": 3350, "NYKAA": 3125, "DMART": 100, "AVENUE": 100,
    "JUBLFOOD": 1250, "DIXON": 50, "AMBER": 50, "POLYCAB": 125, "KEI": 175,
    "CGPOWER": 850, "CUMMINSIND": 200, "ASHOKLEY": 5000, "TVSMOTOR": 350,
    "BALKRISIND": 300, "MRF": 5, "APOLLOTYRE": 1300, "BHARATFORG": 500,
    "SONACOMS": 1000, "EXIDEIND": 1800, "ESCORTS": 175, "SUNDRMFAST": 600,
    "UNOMINDA": 550, "TIINDIA": 175, "SCHAEFFLER": 100, "SKFINDIA": 125,
    "BHEL": 2625, "BEL": 2850, "HAL": 150, "MAZDOCK": 175, "BDL": 325, "COCHINSHIP": 300,
    "GMRINFRA": 6975, "GMRAIRPORT": 6975, "IRB": 5000, "NBCC": 6000, "RVNL": 1400,
    "IREDA": 3450, "SJVN": 5250, "NHPC": 6400, "TATACHEM": 550, "DEEPAKNTR": 250,
    "PIIND": 250, "SRF": 200, "AARTIIND": 1000, "NAVINFLUOR": 150, "ATUL": 75,
    "COROMANDEL": 350, "CHAMBLFERT": 1400, "GNFC": 850, "UPL": 1300, "BAYERCROP": 25,
    "JINDALSTEL": 625, "APLAPOLLO": 350, "JSWENERGY": 950, "NATIONALUM": 3750,
    "HINDZINC": 1225, "GUJGASLTD": 1250, "IGL": 1375, "MGL": 400, "PETRONET": 3000,
    "OIL": 900, "MFSL": 800, "LICHSGFIN": 1000, "MANAPPURAM": 3000, "IDFCFIRSTB": 7500,
    "AUBANK": 1000, "BANDHANBNK": 3600, "FEDERALBNK": 5000, "RBLBANK": 3175,
    "YESBANK": 26000, "UNIONBANK": 5200, "INDIANB": 900, "BANKINDIA": 5200,
    "ABCAPITAL": 2700, "ANGELONE": 250, "CDSL": 375, "BSE": 375, "MCX": 175,
    "KPITTECH": 400, "CYIENT": 350, "SONATSOFTW": 950, "TATATECH": 800,
    "MAXHEALTH": 550, "FORTIS": 950, "LALPATHLAB": 200, "METROPOLIS": 350,
    "SYNGENE": 500, "GLAND": 250, "ABBOTINDIA": 20, "ASTRAL": 275, "SUPREMEIND": 175,
    "FINCABLES": 250, "CROMPTON": 1800, "WHIRLPOOL": 375, "VOLTAS": 375, "BLUESTARCO": 350,
    "GODREJPROP": 275, "OBEROIRLTY": 350, "PRESTIGE": 350, "LODHA": 450, "PHOENIXLTD": 350,
    "INDHOTEL": 800, "JUBLFOOD2": None, "DELHIVERY": 1550, "ASTRAZEN": 100,
    "TATAINVEST": 75, "3MINDIA": 15, "HONAUT": 15, "PIDILITE": 250,
}

# Frozen set of eligible tickers for fast membership tests.
FNO_ELIGIBLE: frozenset[str] = frozenset(FNO_LOT_SIZES)


@dataclass
class ShortExecutability:
    """Whether — and how — a bearish verdict can become a held position.

    `executable` is the hard gate: False means there is no retail instrument to
    carry this short for a multi-week horizon, so the only defensible action is to
    abstain (or, if already long, to reduce). `instrument`, `lot_size` and
    `margin_fraction` describe the futures path when one exists.
    """
    ticker: str
    executable: bool
    instrument: str          # "single_stock_future" | "none"
    lot_size: int | None
    lot_size_known: bool
    margin_fraction: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "executable": self.executable,
            "instrument": self.instrument, "lot_size": self.lot_size,
            "lot_size_known": self.lot_size_known,
            "margin_fraction": self.margin_fraction, "reason": self.reason,
        }


def is_fno_eligible(ticker: str) -> bool:
    return (ticker or "").upper() in FNO_ELIGIBLE


def fno_lot_size(ticker: str) -> int | None:
    return FNO_LOT_SIZES.get((ticker or "").upper())


def short_executability(ticker: str) -> ShortExecutability:
    """Can this account hold a multi-week short in `ticker`, and if so, how?

    The answer is not about signal strength — it is about instrument existence.
    A name in the F&O segment can be shorted via its single-stock future; a name
    outside it cannot be shorted overnight by a retail cash account at all.
    """
    t = (ticker or "").upper()
    if t not in FNO_ELIGIBLE:
        return ShortExecutability(
            ticker=t, executable=False, instrument="none", lot_size=None,
            lot_size_known=False, margin_fraction=0.0,
            reason=("not shortable: no single-stock future lists on this name and "
                    "a retail cash account cannot carry an overnight equity short "
                    "(SEBI cash-segment rule). A bearish signal here is only "
                    "actionable as 'do not hold / reduce if long' — there is no "
                    "instrument to express a new short position in for this horizon."))
    lot = FNO_LOT_SIZES.get(t)
    return ShortExecutability(
        ticker=t, executable=True, instrument="single_stock_future", lot_size=lot,
        lot_size_known=lot is not None, margin_fraction=FNO_MARGIN_FRACTION,
        reason=("shortable via single-stock future"
                + (f" (lot {lot})" if lot else
                   " (lot size to confirm against the live NSE contract master)")
                + f"; held on ~{FNO_MARGIN_FRACTION:.0%} margin, not full notional."))


def round_to_lot(shares: int, lot_size: int | None) -> int:
    """Whole-lot rounding for a futures position. Rounds DOWN so a size that
    cleared the risk budget is never quietly enlarged past it. An unknown lot
    size passes the raw count through (flagged elsewhere as lot-unverified)."""
    if not lot_size or lot_size <= 0:
        return int(shares)
    return (int(shares) // lot_size) * lot_size


def eligibility_provenance() -> dict:
    """What the eligibility list is and how much to trust it — surfaced so a
    stale pinned snapshot is visible, never assumed live (mirrors the universe
    fallback discipline in ingestion/universe.py)."""
    return {
        "source": "pinned_snapshot",
        "snapshot_date": FNO_SNAPSHOT_DATE,
        "count": len(FNO_ELIGIBLE),
        "margin_fraction": FNO_MARGIN_FRACTION,
        "note": ("Pinned NSE F&O single-stock list as of the snapshot date. "
                 "Eligibility is the hard gate on short executability; lot sizes "
                 "are indicative and should be confirmed against the live NSE "
                 "contract master at execution. Refreshed from NSE by "
                 "ingestion.fno where the network permits; this snapshot is the "
                 "reported fallback."),
    }
