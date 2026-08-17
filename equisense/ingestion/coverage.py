"""Price-series completeness: holes, and the fields inside a bar (§5.6).

Staleness answers "is the newest bar recent". It cannot answer "is the series
whole", and those are different failures with different costs. A stale name is
visibly stale — the status strip says so and the next refresh fixes it. A name
that is current but missing three weeks from the middle of 2024 reports as
perfectly FRESH, and every return, volatility, correlation and base rate
computed across that span is wrong with nothing anywhere to indicate it.

Both holes this module looks for are real and were both invisible:

  * **Missing sessions.** Ingestion appended past the stored maximum date, so
    once a gap was behind the newest bar nothing ever looked at it again.
  * **Missing fields inside a stored bar.** The near-live quote refresh wrote
    close, close_raw and volume only, so the bars it created — which is most
    recent bars, because it runs daily and every few minutes with a tab open —
    had no intraday range. Yang-Zhang volatility silently degrades to
    close-to-close without it, and that number is the stop distance and
    therefore the position size.

Everything here is computed from GROUP BY aggregates, never from the bars
themselves. The whole check costs a few thousand rows against a table with over
a million, which matters because the database meters data transfer and a health
check that is expensive to run is a health check that gets run less often than
it should be.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from ..models import Company, PriceObservation

# A date counts as a trading session only if a reasonable share of the covered
# names have a bar on it. Deriving the calendar from "any name traded" would let
# a single misbehaving symbol — a bad tick, a stray backfill, a listing on a
# foreign calendar — invent sessions that the exchange never held, and every
# other name would then report a gap on a day the market was shut.
SESSION_QUORUM = 0.30

# Below this, a name is too new or too thin to say anything about.
MIN_BARS_TO_JUDGE = 30


def _trading_calendar(session: Session,
                      spans: list[tuple[date, date]]) -> list[date]:
    """Dates the exchange actually traded, derived from the panel itself.

    Deliberately not a holiday table. India's holiday list changes yearly and a
    hardcoded one silently rots; the panel's own consensus is self-maintaining
    and is the same calendar the staleness measure already uses.

    The quorum is measured against the names ALIVE on each date, not against the
    panel's busiest day. That distinction decides how much history the check can
    see at all. This universe grew from 50 names to 500, so a fixed share of the
    peak demands ~150 names on a date in 2019 when only 50 existed — every
    session before the expansion fails the test, the calendar collapses to the
    last twelve months, and gaps in the deep history of exactly the names with
    the longest series become undetectable. Alive-relative, the calendar spans
    the whole panel.
    """
    counts = session.execute(
        select(PriceObservation.obs_date, func.count().label("n"))
        .group_by(PriceObservation.obs_date)
        .order_by(PriceObservation.obs_date)).all()
    if not counts:
        return []
    from ..db import note_rows
    note_rows("coverage.trading_calendar", len(counts))

    import bisect
    firsts = sorted(f for f, _l in spans)
    lasts = sorted(l for _f, l in spans)
    out = []
    for d, n in counts:
        # started on or before d, minus those that had already ended before d
        alive = bisect.bisect_right(firsts, d) - bisect.bisect_left(lasts, d)
        if n >= max(1, int(alive * SESSION_QUORUM)):
            out.append(d)
    return out


def price_coverage(session: Session, members_only: bool = True) -> dict:
    """Per-name completeness of the stored price panel.

    `members_only` scopes to the live index. Departed constituents are retained
    on purpose (§5.1) and their series legitimately stop on the day they left —
    counting that as a gap would report the survivorship-bias correction as a
    data fault, every single day.
    """
    q = (select(
            PriceObservation.company_id,
            func.min(PriceObservation.obs_date),
            func.max(PriceObservation.obs_date),
            func.count().label("bars"),
            # COUNT of a CASE, not COUNT of the column: counting the column
            # directly would skip nulls and make "how many bars have a range"
            # indistinguishable from "how many bars exist".
            func.sum(case((PriceObservation.open_price.is_(None), 0), else_=1)),
            func.sum(case((PriceObservation.volume.is_(None), 0), else_=1)))
         .group_by(PriceObservation.company_id))
    if members_only:
        q = q.join(Company, Company.id == PriceObservation.company_id) \
             .where(Company.is_index_member.is_(True))
    rows = session.execute(q).all()
    from ..db import note_rows
    note_rows("coverage.per_name_aggregate", len(rows))
    if not rows:
        return {"names": 0, "sessions": 0, "names_with_gaps": 0,
                "missing_sessions": 0, "worst": [],
                "ohlc_complete_pct": None, "note": "no price history stored"}

    judged = [r for r in rows if r[3] >= MIN_BARS_TO_JUDGE]
    calendar = _trading_calendar(session, [(r[1], r[2]) for r in judged])
    if not calendar:
        return {"names": 0, "sessions": 0, "names_with_gaps": 0,
                "missing_sessions": 0, "worst": [],
                "ohlc_complete_pct": None,
                "note": "no date carries a quorum of names"}

    tickers = {c.id: c.ticker for c in session.scalars(
        select(Company).where(Company.id.in_([r[0] for r in rows]))).all()}

    import bisect
    names, total_missing, total_bars, total_ohlc = [], 0, 0, 0
    for cid, first, last, bars, with_ohlc, with_vol in judged:
        # Sessions the exchange held while this name was in the panel. Bounded
        # by the name's own first and last bar so a recent listing is not
        # charged for the decade before it existed.
        expected = (bisect.bisect_right(calendar, last)
                    - bisect.bisect_left(calendar, first))
        missing = max(0, expected - bars)
        total_missing += missing
        total_bars += bars
        total_ohlc += int(with_ohlc or 0)
        names.append({
            "ticker": tickers.get(cid, str(cid)),
            "company_id": cid,
            "bars": bars,
            "expected_sessions": expected,
            "missing_sessions": missing,
            "first": str(first), "last": str(last),
            "ohlc_pct": round(100.0 * (with_ohlc or 0) / bars, 1),
            "volume_pct": round(100.0 * (with_vol or 0) / bars, 1),
        })

    gapped = [n for n in names if n["missing_sessions"] > 0]
    # Ranked by what a repair would actually recover: absolute sessions, since
    # one name missing 40 bars corrupts more analysis than ten missing four.
    worst = sorted(names, key=lambda n: (-n["missing_sessions"], n["ohlc_pct"]))[:15]
    return {
        "sessions": len(calendar),
        "names": len(names),
        "names_with_gaps": len(gapped),
        "missing_sessions": total_missing,
        "ohlc_complete_pct": round(100.0 * total_ohlc / total_bars, 1) if total_bars else None,
        "worst": worst,
        "calendar_first": str(calendar[0]),
        "calendar_last": str(calendar[-1]),
    }


def names_needing_repair(session: Session, limit: int = 15,
                         min_ohlc_pct: float = 98.0) -> list[str]:
    """Tickers worth re-pulling, worst first.

    Bounded because repair competes with the rest of the daily pipeline for a
    300-second function, and because it is genuinely never urgent: a hole that
    has been there for a year survives another day. Spreading the work is what
    keeps a repair from costing the irreplaceable captures (§3.4) that cannot be
    backfilled at all.

    Field completeness counts as damage alongside missing sessions. A name with
    every session present but no intraday range on the last month of them is
    exactly the case that reads healthy on every existing measure while the
    volatility estimator that sizes its position quietly falls back.
    """
    cov = price_coverage(session)
    return [n["ticker"] for n in cov["worst"]
            if n["missing_sessions"] > 0 or n["ohlc_pct"] < min_ohlc_pct][:limit]
