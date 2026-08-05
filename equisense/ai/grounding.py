"""Grounding validation (§13.2, §13.2).

The load-bearing guarantee behind the AI layer: the LLM never originates a
number. This module checks it programmatically — every numeric token in an
AI output must be traceable to the structured context that was supplied,
within rounding tolerance. This runs on every AI response, not as a spot
check.
"""
from __future__ import annotations

import re
from typing import Any

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")

# Small counts/ordinals ("3 of the 5 signals", "two of nine") and single
# digits are conversational, not financial claims.
_FREE_INT_LIMIT = 12


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def collect_context_numbers(obj: Any, acc: set[float] | None = None) -> set[float]:
    """Walk any JSON-able structure and collect every number, including numbers
    embedded in strings (formulas, periods like 'FY2025')."""
    if acc is None:
        acc = set()
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, str):
        for tok in _NUM_RE.findall(obj):
            v = _to_float(tok)
            if v is not None:
                acc.add(v)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            collect_context_numbers(k, acc)
            collect_context_numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_context_numbers(v, acc)
    return acc


def _expand(values: set[float]) -> set[float]:
    """Add sign and scale variants so '0.25' in context grounds '25%' in prose.

    TIGHTENED (Wave S): the previous version added v, −v, v×100 AND v÷100 for
    EVERY context value unconditionally, quadrupling the accepted set with
    numbers the engine never produced. Verified consequence: with an ordinary
    dossier context, the fabricated figures "1352" and "0.184" both validated as
    grounded — 1352 because 13.52 was in context, 0.184 because 18.4 was. That
    is a hole in the one guarantee the whole AI layer rests on.

    The expansion is now directional and bounded by what the conversion can
    plausibly mean:
      * a value in [−1, 1] may legitimately be stated as a percentage → ×100;
      * a value in [−100, 100] may legitimately be stated as a fraction → ÷100;
      * nothing else is rescaled, so a large figure like 13.52 no longer
        silently licenses 1352.
    """
    out: set[float] = set()
    for v in values:
        out.add(v)
        out.add(-v)
        if abs(v) <= 1.0:            # fraction → percent
            out.add(v * 100)
            out.add(-v * 100)
        if abs(v) <= 100.0:          # percent → fraction
            out.add(v / 100)
            out.add(-v / 100)
    return out


def _decimals(token: str) -> int:
    if "." in token:
        return len(token.split(".")[1])
    return 0


def _matches(n: float, token: str, context: set[float]) -> bool:
    tol = 0.51 * 10 ** (-_decimals(token))
    return any(abs(n - c) <= tol for c in context)


def validate(text: str, context: Any) -> dict:
    """Check every number in `text` against the supplied context.

    Returns {"grounded": bool, "checked": int, "violations": [str, ...]}.
    """
    ctx = _expand(collect_context_numbers(context))
    violations: list[str] = []
    checked = 0
    for tok in _NUM_RE.findall(text):
        n = _to_float(tok)
        if n is None:
            continue
        checked += 1
        if n == int(n) and abs(n) <= _FREE_INT_LIMIT:
            continue
        if not _matches(n, tok, ctx):
            violations.append(tok)
    return {"grounded": not violations, "checked": checked,
            "violations": sorted(set(violations))}
