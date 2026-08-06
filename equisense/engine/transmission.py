"""Macro → sector → security transmission chains.

The question this answers is the one a human analyst actually asks: *crude just
fell 27% — so what, for this book?* Every other engine here reasons about a
security in isolation. This one reasons about the PATH a macro move takes to
reach a price, one link at a time, and refuses to assert any link it cannot
measure.

The discipline that makes it honest
-----------------------------------
Each link carries three separate things, and they are never blended:

  MECHANISM   why the link should exist — economics, stated in advance, with
              its expected sign. This is a PRIOR. It is not evidence, and it
              never contributes a number.
  MEASUREMENT what this platform's own data says: OLS beta of the follower's
              returns to the driver's, with a confidence interval, R², and n.
  VERDICT     whether the measurement CONFIRMS the mechanism, CONTRADICTS it,
              or is too weak to say either way.

A chain of plausible-sounding mechanisms is a story, and stories are exactly
how a research system talks itself into a position. So a link whose beta is
indistinguishable from zero is reported as *not detectable* and propagates
NOTHING downstream — it does not quietly pass its prior along. A link whose
beta has the wrong sign is reported as CONTRADICTED and is the most valuable
output here, because it is the one that says the intuition is wrong in this
market.

R² is carried the whole way for a reason: a beta of -0.4 that explains 2% of a
name's variance is a real relationship that will still be swamped by everything
else on any given day. Reporting the beta without it invites reading a rounding
error as a thesis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .crossasset import beta_to, correlation_with_ci, lead_lag

# A link needs enough overlapping observations to say anything. Below this the
# honest answer is "underpowered", not a number with a wide interval attached.
MIN_LINK_OBS = 60

# |beta| below this is treated as economically empty even when statistically
# significant: on a daily-returns basis it cannot move a position's outcome.
NEGLIGIBLE_BETA = 0.05


@dataclass(frozen=True)
class Channel:
    """One economic mechanism, declared before any data is looked at."""
    driver: str                 # macro symbol
    driver_label: str
    sector: str                 # platform sector taxonomy, or "*" for the market
    expected_sign: int          # +1 driver up helps, -1 driver up hurts
    mechanism: str              # the causal story, in one sentence
    source: str                 # where the mechanism comes from


# Transmission channels for the Indian market. Deliberately conservative: only
# mechanisms with a direct, well-understood accounting path from the macro
# variable to the income statement. Second-order stories ("crude down helps
# consumption, which helps retail") are omitted, because at that distance the
# sign depends on assumptions the platform cannot check.
CHANNELS: tuple[Channel, ...] = (
    # ------------------------------------------------------------- crude oil
    Channel("BZ=F", "Brent crude", "Energy", +1,
            "Upstream producers realise the crude price directly, so a higher "
            "barrel lifts revenue per unit produced with costs largely fixed.",
            "Accounting identity for an upstream producer's realisation."),
    Channel("BZ=F", "Brent crude", "Materials", -1,
            "Petrochemical and paint inputs are crude derivatives; a higher "
            "barrel raises cost of goods before any price pass-through.",
            "Input-cost channel; pass-through lags are well documented in "
            "Indian paints and chemicals."),
    Channel("BZ=F", "Brent crude", "Consumer Discretionary", -1,
            "Tyres, autos and travel carry crude-linked input or fuel costs, "
            "and higher pump prices compete with discretionary spend.",
            "Input-cost and disposable-income channel."),
    Channel("BZ=F", "Brent crude", "*", -1,
            "India imports the large majority of its crude, so a higher barrel "
            "widens the current-account deficit and imports inflation.",
            "Terms-of-trade channel for a net oil importer."),
    # ------------------------------------------------------------- the rupee
    Channel("INR=X", "USD/INR (up = rupee weaker)", "Information Technology", +1,
            "IT services bill in dollars and pay costs in rupees, so a weaker "
            "rupee expands realised margin on the same contract.",
            "Export-realisation channel; standard in Indian IT guidance."),
    Channel("INR=X", "USD/INR (up = rupee weaker)", "Healthcare", +1,
            "Formulation exporters realise dollar revenue against a largely "
            "rupee cost base.",
            "Export-realisation channel."),
    Channel("INR=X", "USD/INR (up = rupee weaker)", "Energy", -1,
            "Crude is invoiced in dollars, so a weaker rupee raises the "
            "landed input cost for refiners and marketers.",
            "Imported-input channel."),
    Channel("INR=X", "USD/INR (up = rupee weaker)", "*", -1,
            "A weakening rupee usually accompanies foreign outflows and raises "
            "imported inflation, tightening financial conditions.",
            "Capital-flow and imported-inflation channel."),
    # ------------------------------------------------------------ volatility
    Channel("^INDIAVIX", "India VIX", "*", -1,
            "Implied volatility rises as risk appetite falls; positions are "
            "cut into higher expected variance regardless of fundamentals.",
            "Risk-appetite channel; VIX is a price of insurance, not a "
            "forecast of direction."),
    Channel("^INDIAVIX", "India VIX", "Financials", -1,
            "Leveraged balance sheets de-rate fastest when the market's price "
            "of risk rises.",
            "Risk-appetite channel, amplified by leverage."),
    # -------------------------------------------------------- global signals
    Channel("^GSPC", "S&P 500", "*", +1,
            "Global risk appetite drives foreign portfolio flows, which are "
            "the marginal buyer of Indian large caps.",
            "FPI-flow channel."),
    Channel("^NSEBANK", "NIFTY Bank", "Financials", +1,
            "The banking index IS the sector's own aggregate; it is the "
            "transmission point for credit conditions into the wider market.",
            "Definitional for the sector; credit-conditions channel beyond it."),
    # ------------------------------------------------------------------ gold
    Channel("GC=F", "Gold", "*", -1,
            "Gold bids when real rates fall or safety is sought; in an Indian "
            "context it also competes directly with equities for household "
            "savings.",
            "Safe-haven and household-allocation channel."),
)


def channels_for(driver: str) -> list[Channel]:
    return [c for c in CHANNELS if c.driver == driver]


def drivers() -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for c in CHANNELS:
        seen.setdefault(c.driver, c.driver_label)
    return sorted(seen.items())


def _verdict(measured_beta: Optional[float], expected_sign: int,
             significant: bool, n: int) -> tuple[str, str]:
    """CONFIRMED / CONTRADICTED / NOT DETECTABLE, and why."""
    if measured_beta is None or n < MIN_LINK_OBS:
        return ("underpowered",
                f"only {n} overlapping observations — below the {MIN_LINK_OBS} "
                "needed to distinguish this link from noise")
    if not significant:
        return ("not_detectable",
                "the confidence interval spans zero: this mechanism is not "
                "measurable in this data, so the chain stops here rather than "
                "passing an assumption downstream")
    if abs(measured_beta) < NEGLIGIBLE_BETA:
        return ("negligible",
                f"beta {measured_beta:+.3f} is statistically real but too small "
                "to move a position's outcome")
    if (measured_beta > 0) == (expected_sign > 0):
        return ("confirmed",
                "measured sensitivity agrees with the stated mechanism")
    return ("contradicted",
            "measured sensitivity has the OPPOSITE sign to the mechanism — in "
            "this market, over this window, the intuition does not hold")


def measure_link(follower: Sequence[float], driver: Sequence[float],
                 expected_sign: int, label: str) -> dict:
    """One link: the measurement and the verdict on it."""
    b = beta_to(follower, driver)
    c = correlation_with_ci(follower, driver)
    n = int(b.get("observations") or c.get("n") or 0)
    beta = b.get("beta") if b.get("computable") else None
    significant = bool(c.get("significant"))
    verdict, why = _verdict(beta, expected_sign, significant, n)
    return {
        "label": label,
        "beta": beta,
        "r_squared": b.get("r_squared"),
        "correlation": c.get("r"),
        "ci95": c.get("ci95"),
        "p_value": c.get("p_value"),
        "observations": n,
        "expected_sign": expected_sign,
        "verdict": verdict,
        "why": why,
        # A beta is only worth acting on to the extent the driver explains the
        # follower at all, so the two travel together everywhere.
        "explains_pct": (None if b.get("r_squared") is None
                         else round(b["r_squared"] * 100, 1)),
    }


def implied_move(beta: Optional[float], driver_move_pct: Optional[float],
                 r_squared: Optional[float]) -> Optional[dict]:
    """Translate a measured beta and an OBSERVED driver move into a direction.

    Explicitly not a forecast and not a price target: it is the arithmetic of
    the measured sensitivity applied to a move that has already happened, which
    is a statement about exposure carried, not about what happens next.
    """
    if beta is None or driver_move_pct is None:
        return None
    est = beta * driver_move_pct
    return {
        "driver_move_pct": round(driver_move_pct, 2),
        "implied_pct": round(est, 2),
        "direction": "up" if est > 0 else "down" if est < 0 else "flat",
        "confidence": ("low" if not r_squared or r_squared < 0.05
                       else "moderate" if r_squared < 0.20 else "higher"),
        "caveat": ("Arithmetic of a measured sensitivity applied to a move that "
                   "has ALREADY occurred — the exposure this position carried, "
                   "not a prediction of the next move. The driver explains "
                   f"{'an unknown share of' if r_squared is None else f'{r_squared * 100:.0f}% of'}"
                   " this follower's variance; the rest is everything else."),
    }


def build_chain(driver: str, driver_returns: Sequence[float],
                driver_move_pct: Optional[float],
                market_returns: Sequence[float],
                sector_returns: dict[str, Sequence[float]],
                name_returns: Optional[dict[str, Sequence[float]]] = None,
                name_sector: Optional[dict[str, str]] = None) -> dict:
    """The full macro → market → sector → security chain for one driver.

    Returns every link with its own measurement and verdict. Links that are not
    detectable are RETAINED in the output rather than dropped: "the mechanism
    everyone believes does not show up here" is a finding, and hiding it would
    leave the chain looking cleaner than the evidence is.
    """
    chans = channels_for(driver)
    if not chans:
        return {"available": False, "reason": f"no declared channel for {driver}"}
    label = chans[0].driver_label

    market_links, sector_links = [], []
    for ch in chans:
        if ch.sector == "*":
            link = measure_link(market_returns, driver_returns,
                                ch.expected_sign, "market (NIFTY)")
            link.update(mechanism=ch.mechanism, source=ch.source, scope="market")
            link["implied"] = implied_move(link["beta"], driver_move_pct,
                                           link.get("r_squared"))
            market_links.append(link)
            continue
        series = sector_returns.get(ch.sector)
        if not series:
            continue
        link = measure_link(series, driver_returns, ch.expected_sign, ch.sector)
        link.update(mechanism=ch.mechanism, source=ch.source, scope="sector",
                    sector=ch.sector)
        link["implied"] = implied_move(link["beta"], driver_move_pct,
                                       link.get("r_squared"))
        sector_links.append(link)

    # Security leg: only for names whose SECTOR link was actually established.
    # Measuring a name against a driver whose sector channel is not detectable
    # would be fishing — with 50 names something always looks significant.
    live_sectors = {l["sector"] for l in sector_links if l["verdict"] == "confirmed"}
    name_links = []
    for tkr, series in (name_returns or {}).items():
        sec = (name_sector or {}).get(tkr)
        if sec not in live_sectors:
            continue
        exp = next((c.expected_sign for c in chans if c.sector == sec), 0)
        link = measure_link(series, driver_returns, exp, tkr)
        link.update(scope="security", ticker=tkr, sector=sec,
                    mechanism=f"Inherits the {sec} channel; measured on this "
                              "name to see whether it amplifies or damps it.")
        link["implied"] = implied_move(link["beta"], driver_move_pct,
                                       link.get("r_squared"))
        name_links.append(link)
    name_links.sort(key=lambda l: -(abs(l["beta"] or 0)))

    lead = lead_lag(driver_returns, market_returns)
    confirmed = [l for l in sector_links + market_links if l["verdict"] == "confirmed"]
    contradicted = [l for l in sector_links + market_links if l["verdict"] == "contradicted"]
    return {
        "available": True,
        "driver": driver,
        "driver_label": label,
        "driver_move_pct": driver_move_pct,
        "market_links": market_links,
        "sector_links": sector_links,
        "security_links": name_links[:12],
        "lead_lag": lead,
        "summary": {
            "channels_declared": len(chans),
            "confirmed": len(confirmed),
            "contradicted": len(contradicted),
            "not_detectable": len([l for l in sector_links + market_links
                                   if l["verdict"] in ("not_detectable", "negligible",
                                                       "underpowered")]),
        },
        "reading": (
            "Each link states its mechanism BEFORE the measurement, so a link "
            "that fails is visible as a failed prediction about the world rather "
            "than quietly omitted. Links that are not detectable stop the chain: "
            "they pass nothing downstream. A contradicted link is the most "
            "informative result here — it is the one saying the standard "
            "intuition does not hold in this market over this window."),
    }
