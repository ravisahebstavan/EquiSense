"""Distribution boundary — what may leave the single-user context, and how.

WHY THIS EXISTS
---------------
EquiSense produces verdicts (`long_candidate`), position sizes, stop distances
and predicted excess-return magnitudes. For the person who built it and runs it
on their own capital, that is decision support. The moment the same output is
shown to ANOTHER person in exchange for consideration, its regulatory character
changes: in India, distributing research reports or recommendations for
consideration engages the SEBI (Research Analysts) Regulations, 2014, and
providing personalised investment advice for a fee engages the SEBI (Investment
Advisers) Regulations, 2013. Both require registration.

PROJECT_DRAFT §7.1 already drew this line for order execution. This module draws
it for OUTPUT, which is the boundary that actually gets crossed by accident —
sharing a dossier link is one click, and nothing in the codebase previously
distinguished a private dossier from a distributed research note.

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is a mechanical classifier and labeller: it says which fields constitute a
recommendation, redacts them for non-personal distribution modes, and stamps the
required disclosure. It makes the boundary explicit and hard to cross silently.

It is NOT legal advice, and it cannot make an unregistered distribution lawful.
If output is distributed for consideration, registration is the answer, not
redaction. The honest use of PERSONAL mode is: keep it personal.

A SEPARATE, NON-REGULATORY POINT
--------------------------------
The platform's own research currently finds NO demonstrated edge in its price
signals: 3 of 45 base-rate cells survive multiplicity control, the backtest's
Deflated Sharpe is 0.88 (indistinguishable from the best of 8 lucky trials), and
no signal passes its Information Coefficient t-test. Distributing signals from a
system whose own measurements say they do not work is an integrity problem
before it is a compliance one, and `edge_disclosure()` states that plainly so it
travels with anything that does go out.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# --- distribution modes ------------------------------------------------------
PERSONAL = "personal"        # single user, own capital. Full output.
EDUCATIONAL = "educational"  # methodology shown, recommendations stripped
PUBLIC_DEMO = "public_demo"  # portfolio/demo. Recommendations stripped, no sizing.

MODES = (PERSONAL, EDUCATIONAL, PUBLIC_DEMO)

# Fields that constitute a RECOMMENDATION rather than a measurement. A verdict
# plus a size plus a stop is an actionable trade instruction in substance,
# whatever it is called in the UI.
RECOMMENDATION_FIELDS = {
    "verdict", "conviction_band", "net_score", "net_z",
    "sizing", "recommended_shares", "recommended_value", "stop_distance_pct",
    "risk_at_stop", "binding_constraint", "predicted_excess_pct",
    "stated_probability", "claim", "candidates", "tradable",
}

# Fields that are measurement or methodology — these are what makes the system
# interesting and they carry no recommendation character.
MEASUREMENT_FIELDS = {
    "metrics", "evidence", "base_rate", "coverage", "confidence", "dispersion",
    "regime", "epistemics", "formula", "inputs", "caveat", "caveats",
    "n_eff", "icc", "design_effect", "t_stat", "p_value", "q_value",
    "minimum_detectable_ic", "mean_ic", "admissibility_reason",
    "multiplicity_verdict", "method", "methodology",
}

EDGE_DISCLOSURE = (
    "This system's own research has NOT established a deployable edge. Of 45 "
    "pre-registered study cells, 3 survive multiple-testing control; the "
    "strategy backtest's Deflated Sharpe is 0.88, meaning it is not "
    "distinguishable from the best of several lucky trials; and no price signal "
    "passes its Information Coefficient t-test on the tested universe. Nothing "
    "here should be relied on as a source of returns."
)

REGULATORY_NOTICE = (
    "Not investment advice and not a research report. This output is generated "
    "by a personal analytical tool for its own operator's use. It is not "
    "produced by a SEBI-registered Research Analyst or Investment Adviser, and "
    "it is not offered to any person in exchange for consideration. Distributing "
    "recommendations for consideration in India requires registration under the "
    "SEBI (Research Analysts) Regulations, 2014 or the SEBI (Investment "
    "Advisers) Regulations, 2013 as applicable."
)


def current_mode() -> str:
    """Distribution mode from EQUISENSE_DISTRIBUTION, defaulting to PERSONAL.

    Defaults to the PERMISSIVE mode deliberately: the tool's normal state is a
    single operator on their own capital, and silently degrading that would be
    the wrong failure. The mode must be raised explicitly when output starts
    reaching other people, which is a decision a human should make consciously.
    """
    mode = (os.environ.get("EQUISENSE_DISTRIBUTION") or PERSONAL).strip().lower()
    return mode if mode in MODES else PERSONAL


def classify(payload: Any, path: str = "") -> dict:
    """Walk a payload and report which fields carry recommendation character.

    Used to audit what an endpoint actually emits, rather than assuming. A
    field's NAME is the classifier here, which is crude but auditable — the
    alternative, guessing from values, fails silently.
    """
    found_rec: list[str] = []
    found_meas: list[str] = []

    def walk(obj: Any, p: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                here = f"{p}.{k}" if p else k
                if k in RECOMMENDATION_FIELDS:
                    found_rec.append(here)
                elif k in MEASUREMENT_FIELDS:
                    found_meas.append(here)
                walk(v, here)
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj[:50]):
                walk(v, f"{p}[{i}]")

    walk(payload, path)
    return {"recommendation_fields": sorted(set(found_rec)),
            "measurement_fields": sorted(set(found_meas)),
            "carries_recommendation": bool(found_rec)}


def apply_boundary(payload: Any, mode: Optional[str] = None) -> Any:
    """Return the payload as it may be presented in the given mode.

    PERSONAL     unchanged.
    EDUCATIONAL  recommendation fields replaced with a stated redaction; every
                 measurement, formula and caveat is preserved, because the
                 methodology is the part worth showing and carries no
                 recommendation character.
    PUBLIC_DEMO  as EDUCATIONAL, and sizing is removed outright rather than
                 redacted, since a position size is actionable even when the
                 direction is hidden.
    """
    mode = mode or current_mode()
    if mode == PERSONAL:
        return payload
    strip = mode == PUBLIC_DEMO

    def scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in RECOMMENDATION_FIELDS:
                    if strip and k in ("sizing", "candidates"):
                        continue
                    out[k] = f"[redacted — {mode} mode: recommendation withheld]"
                else:
                    out[k] = scrub(v)
            return out
        if isinstance(obj, list):
            return [scrub(v) for v in obj]
        return obj

    scrubbed = scrub(payload)
    if isinstance(scrubbed, dict):
        scrubbed["_distribution"] = {
            "mode": mode,
            "regulatory_notice": REGULATORY_NOTICE,
            "edge_disclosure": EDGE_DISCLOSURE,
            "what_was_removed": ("verdict, conviction, scores and position sizing "
                                 "— the fields that constitute a recommendation"),
            "what_remains": ("every measurement, formula, input and caveat: the "
                             "methodology, which is the part that carries no "
                             "recommendation character"),
        }
    return scrubbed


def edge_disclosure() -> dict:
    """The honest state of the platform's own evidence, for any output that
    leaves the personal context."""
    return {
        "has_demonstrated_edge": False,
        "statement": EDGE_DISCLOSURE,
        "evidence": {
            "base_rate_cells_surviving_multiplicity": "3 of 45",
            "backtest_deflated_sharpe": 0.88,
            "signals_passing_ic_t_test": 0,
            "note": ("The IC nulls are partly a POWER result — at ~55 names the "
                     "minimum detectable IC is 0.067 and every measured value "
                     "fell below it. Absence of evidence here is not yet "
                     "evidence of absence, and saying so is part of the "
                     "disclosure."),
        },
    }


def monetisation_surface() -> dict:
    """What can and cannot be sold from this system without registration.

    Kept in code rather than a document so it stays attached to the thing it
    describes. NOT legal advice — the boundary is drawn conservatively and a
    lawyer should confirm any commercial use.
    """
    return {
        "requires_sebi_registration": [
            {"activity": "selling signals, verdicts or 'buy/sell' calls",
             "regime": "SEBI (Research Analysts) Regulations, 2014",
             "why": "distributing research recommendations for consideration"},
            {"activity": "personalised portfolio advice for a fee",
             "regime": "SEBI (Investment Advisers) Regulations, 2013",
             "why": "advice tailored to a specific person's circumstances"},
            {"activity": "managing another person's money",
             "regime": "SEBI (Portfolio Managers) Regulations, 2020",
             "why": "discretionary management of client funds"},
        ],
        "no_registration_surface": [
            {"activity": "open-sourcing the statistical and data engines",
             "why": ("a library that computes cluster-robust standard errors or "
                     "parses an exchange bhavcopy makes no recommendation"),
             "assets": ["research/stats.py", "research/ic.py",
                        "ingestion/nse_archive.py", "engine/derivatives.py"]},
            {"activity": "software licensed for users to run on their own data",
             "why": ("selling a tool is not distributing research — but this gets "
                     "murky fast if the tool ships recommendations, so the "
                     "distribution mode matters")},
            {"activity": "writing about the methodology",
             "why": "education about technique, not advice about securities"},
            {"activity": "career capital — demonstrating the work itself",
             "why": ("the original design document names this explicitly, and it "
                     "is the highest-expected-value path given that the strongest "
                     "asset here is the measurement infrastructure rather than "
                     "any signal")},
        ],
        "the_uncomfortable_part": (
            "The most saleable-looking output — signals — is the one the "
            "platform's own research says does not work. The genuinely rare "
            "asset is the machinery that established that: cluster-robust "
            "inference, FDR and Harvey-Liu-Zhu control, Deflated Sharpe, and IC "
            "with an explicit detection limit. Almost nothing in retail finance "
            "does this, and none of it requires registration to distribute."),
        "disclaimer": ("Conservative reading, not legal advice. Confirm with a "
                       "lawyer before any commercial use."),
    }
