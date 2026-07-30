"""NSE official archive parsers.

Fixtures are trimmed excerpts of the REAL published files (24-Jul-2025), so the
column names, ordering and quirks are the exchange's own rather than something
convenient. No network is touched.
"""
from __future__ import annotations

from datetime import date

import pytest

from equisense.ingestion import nse_archive as NA

FO_HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,"
             "XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,"
             "LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
             "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
             "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")

FO_ROWS = "\n".join([
    FO_HEADER,
    # index future
    "2025-07-24,2025-07-24,FO,NSE,IDF,53001,,NIFTY,,2025-07-31,2025-07-31,,,NIFTY31JUL25FUT,"
    "25100.00,25200.00,25050.00,25095.40,25095.40,25120.00,25062.10,25095.40,12360525,"
    "-394350,63223,158000.00,45000,F1,75,,,,,",
    # index option, traded
    "2025-07-24,2025-07-24,FO,NSE,IDO,53002,,NIFTY,,2025-07-31,2025-07-31,25100.00,CE,"
    "NIFTY31JUL2525100CE,120.00,140.00,100.00,110.25,110.25,130.00,25062.10,110.25,"
    "30858600,1200000,900000,9000.00,5000,F1,75,,,,,",
    # index option, UNTRADED: close/volume 0 but settlement + OI are real
    "2025-07-24,2025-07-24,FO,NSE,IDO,53003,,NIFTY,,2025-07-31,2025-07-31,31000.00,CE,"
    "NIFTY31JUL2531000CE,0.00,0.00,0.00,0.00,0.00,17.00,25062.10,0.85,236800,0,0,0.00,0,"
    "F1,75,,,,,",
    # stock option
    "2025-07-24,2025-07-24,FO,NSE,STO,53004,,RELIANCE,,2025-08-28,2025-08-28,1400.00,PE,"
    "RELIANCE28AUG251400PE,20.00,25.00,18.00,22.50,22.50,21.00,1420.00,22.50,500000,"
    "10000,2000,45.00,300,F1,500,,,,,",
    # a row that must be skipped: unknown instrument type
    "2025-07-24,2025-07-24,FO,NSE,XXX,53005,,JUNK,,2025-07-31,2025-07-31,,,JUNK,"
    "0,0,0,0,0,0,0,0,0,0,0,0,0,F1,1,,,,,",
])

CM_ROWS = "\n".join([
    ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
     "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
     "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
     "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"),
    ("2025-07-24,2025-07-24,CM,NSE,STK,2885,INE002A01018,RELIANCE,EQ,,,,,RELIANCE,"
     "1425.00,1432.00,1415.00,1420.00,1420.00,1424.00,,1420.00,,,5000000,710000000.00,"
     "120000,F1,1,,,,,"),
    ("2025-07-24,2025-07-24,CM,NSE,STK,19078,IN0020200104,SGBJUN28,GB,,,,,"
     "2.5%GOLDBONDS2028SR-III,9977.00,9977.00,9900.00,9906.82,9900.00,9975.22,,9906.82,"
     ",,226,2239039.27,17,F1,1,,,,,"),
])

MTO_TEXT = "\n".join([
    "Security Wise Delivery Position - Compulsory Rolling Settlement",
    "10,MTO,24072025,1399798964,0002747",
    "Trade Date <24-JUL-2025>,Settlement Type <N>",
    ("Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity"
     "(gross across client level),% of Deliverable Quantity to Traded Quantity"),
    "20,1,RELIANCE,EQ,5000000,3200000,64.00",
    "20,2,KIOCL,EQ,6700000,441530,6.59",
    "20,3,AXISGOLD,EQ,3100000,2890750,93.25",
])

INDEX_CSV = "\n".join([
    ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
     "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,"
     "Div Yield"),
    "Nifty 50,24-07-2025,25243.3,25246.25,25018.7,25062.1,-157.8,-.63,338728860,27433.34,22.15,3.46,1.31",
    "Nifty Bank,24-07-2025,57500.0,57600.1,56900.2,57066.05,-400.0,-.70,120000000,15000.00,15.02,2.2,0.96",
    "Nifty Test Undefined,24-07-2025,100.0,101.0,99.0,100.5,0.5,.50,1000,1.00,-,-,-",
])


# ------------------------------------------------------------------- F&O

def test_parse_fo_bhavcopy_extracts_futures_and_options():
    rows = NA.parse_fo_bhavcopy(FO_ROWS)
    assert len(rows) == 4, "the unknown instrument type must be skipped"
    kinds = {r["instrument_type"] for r in rows}
    assert kinds == {"IDF", "IDO", "STO"}

    fut = next(r for r in rows if r["instrument_type"] == "IDF")
    assert fut["symbol"] == "NIFTY"
    assert fut["strike"] is None and fut["option_type"] is None, \
        "futures must not carry a strike or option type"
    assert fut["settlement_price"] == pytest.approx(25095.40)
    assert fut["open_interest"] == pytest.approx(12360525)
    assert fut["change_in_oi"] == pytest.approx(-394350)
    assert fut["lot_size"] == 75
    assert fut["expiry"] == date(2025, 7, 31)


def test_parse_fo_keeps_untraded_strikes():
    """Untraded strikes have close/volume 0 but real settlement and OI. Dropping
    them would truncate the wings of the volatility surface and distort the OI
    structure, which is much of what makes a chain informative."""
    rows = NA.parse_fo_bhavcopy(FO_ROWS)
    untraded = next(r for r in rows if r["strike"] == 31000.0)
    assert untraded["volume"] == 0
    assert untraded["close"] == 0
    assert untraded["settlement_price"] == pytest.approx(0.85)
    assert untraded["open_interest"] == pytest.approx(236800)


def test_parse_fo_reads_option_type_and_underlying():
    rows = NA.parse_fo_bhavcopy(FO_ROWS)
    put = next(r for r in rows if r["symbol"] == "RELIANCE")
    assert put["option_type"] == "PE"
    assert put["strike"] == pytest.approx(1400.0)
    assert put["underlying_price"] == pytest.approx(1420.0)
    assert put["lot_size"] == 500


# -------------------------------------------------------------------- cash

def test_parse_cm_bhavcopy_covers_more_than_equities():
    rows = NA.parse_cm_bhavcopy(CM_ROWS)
    assert len(rows) == 2
    symbols = {r["symbol"] for r in rows}
    assert "SGBJUN28" in symbols, "sovereign gold bonds are in the cash bhavcopy"
    rel = next(r for r in rows if r["symbol"] == "RELIANCE")
    assert rel["close"] == pytest.approx(1420.0)
    assert rel["series"] == "EQ"
    assert rel["isin"] == "INE002A01018"
    assert rel["trade_date"] == date(2025, 7, 24)


# ---------------------------------------------------------------- delivery

def test_parse_mto_reads_delivery_and_the_preamble_date():
    """The trade date appears ONLY in the preamble; the data rows carry none."""
    rows = NA.parse_mto(MTO_TEXT)
    assert len(rows) == 3
    assert all(r["trade_date"] == date(2025, 7, 24) for r in rows)
    by = {r["symbol"]: r for r in rows}
    assert by["KIOCL"]["delivery_pct"] == pytest.approx(6.59)
    assert by["AXISGOLD"]["delivery_pct"] == pytest.approx(93.25)
    assert by["RELIANCE"]["traded_qty"] == pytest.approx(5_000_000)
    assert by["RELIANCE"]["delivered_qty"] == pytest.approx(3_200_000)


def test_parse_mto_returns_nothing_without_a_date():
    assert NA.parse_mto("20,1,RELIANCE,EQ,100,50,50.00") == []


# ----------------------------------------------------------------- indices

def test_parse_index_close_reads_valuation_metrics():
    rows = NA.parse_index_close(INDEX_CSV)
    assert len(rows) == 3
    n50 = next(r for r in rows if r["index_name"] == "Nifty 50")
    assert n50["close"] == pytest.approx(25062.1)
    assert n50["pe"] == pytest.approx(22.15)
    assert n50["pb"] == pytest.approx(3.46)
    assert n50["div_yield"] == pytest.approx(1.31)
    assert n50["obs_date"] == date(2025, 7, 24)


def test_parse_index_close_handles_undefined_metrics():
    """NSE writes '-' where a metric is undefined; that must become None, not 0."""
    rows = NA.parse_index_close(INDEX_CSV)
    undef = next(r for r in rows if r["index_name"] == "Nifty Test Undefined")
    assert undef["pe"] is None and undef["pb"] is None and undef["div_yield"] is None
    assert undef["close"] == pytest.approx(100.5)


# ------------------------------------------------------------------- urls

def test_url_builders_use_the_documented_date_formats():
    d = date(2025, 7, 24)
    assert NA.fo_bhavcopy_url(d).endswith("BhavCopy_NSE_FO_0_0_0_20250724_F_0000.csv.zip")
    assert NA.cm_bhavcopy_url(d).endswith("BhavCopy_NSE_CM_0_0_0_20250724_F_0000.csv.zip")
    assert NA.mto_url(d).endswith("MTO_24072025.DAT")          # DDMMYYYY, not YYYYMMDD
    assert NA.index_close_url(d).endswith("ind_close_all_24072025.csv")


def test_parsers_tolerate_empty_input():
    assert NA.parse_fo_bhavcopy("") == []
    assert NA.parse_cm_bhavcopy("") == []
    assert NA.parse_mto("") == []
    assert NA.parse_index_close("") == []


# ------------------------------------------------- chain assembly from a DB

@pytest.fixture
def session_with_chain():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from equisense.db import Base
    from equisense.models import DerivativeQuote

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    s.bulk_insert_mappings(DerivativeQuote, NA.parse_fo_bhavcopy(FO_ROWS))
    s.commit()
    return s


def test_option_chain_skips_an_expiry_that_is_already_expiring(session_with_chain):
    """Observed against real data: on a weekly expiry day the nearest contract
    has zero time to run, every option is pure intrinsic, and 0 of 176 strikes
    solved for implied volatility. The chain must default to a LIVE expiry."""
    from equisense.models import DerivativeQuote
    s = session_with_chain
    s.bulk_insert_mappings(DerivativeQuote, [{
        "trade_date": date(2025, 7, 24), "symbol": "NIFTY",
        "instrument_type": "IDO", "expiry": date(2025, 7, 24), "strike": 25050.0,
        "option_type": "CE", "close": 12.0, "settlement_price": 12.0,
        "underlying_price": 25062.1, "open_interest": 100.0, "volume": 5.0,
        "lot_size": 75}])
    s.commit()
    ch = NA.option_chain(s, "NIFTY", date(2025, 7, 24))
    assert ch["expiry"] == date(2025, 7, 31)
    assert ch["days_to_expiry"] == 7
    assert date(2025, 7, 24).isoformat() in ch["expiries_available"]

    same_day = NA.option_chain(s, "NIFTY", date(2025, 7, 24),
                               include_expiring_today=True)
    assert same_day["days_to_expiry"] == 0


def test_option_chain_prefers_settlement_price_over_close(session_with_chain):
    """Untraded strikes have close 0; using it would make IV unsolvable."""
    ch = NA.option_chain(session_with_chain, "NIFTY", date(2025, 7, 24))
    deep = next(q for q in ch["quotes"] if q["strike"] == 31000.0)
    assert deep["price"] == pytest.approx(0.85), "must fall back to settlement price"


def test_futures_curve_is_sorted_by_expiry(session_with_chain):
    curve = NA.futures_curve(session_with_chain, "NIFTY", date(2025, 7, 24))
    assert curve == sorted(curve)
    assert curve[0][0] == date(2025, 7, 31)


def test_option_chain_reports_when_nothing_is_stored(session_with_chain):
    out = NA.option_chain(session_with_chain, "NOSUCH", date(2025, 7, 24))
    assert out["rows"] == 0
    assert "reason" in out
