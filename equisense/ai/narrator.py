"""AI orchestration layer (PROJECT_DRAFT §13, §16.2).

This layer contains no financial logic. It constructs grounded context from
the deterministic engines' outputs, calls the LLM, and validates that the
response introduces no ungrounded numbers (§13.4). Every response returns the
exact context that was supplied, so the UI can show it (§19.1 layer 3).

Degrades gracefully: with no Anthropic credentials configured, endpoints
return the structured facts with `available: false` instead of failing.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .grounding import validate

MODEL = "claude-opus-4-8"

_NARRATOR_SYSTEM = """You are the narration layer of EquiSense, a personal equity-analysis \
workspace for the Indian market. You explain numbers that a deterministic \
computation engine has already produced. Hard rules, no exceptions:

1. Only reference figures present in the supplied JSON context. Never state a \
number you were not given. If a figure you'd want is missing, say it is \
unavailable rather than estimating it.
2. No forecasts, no price targets, no buy/sell/hold recommendations, and no \
probability-of-return language. If the data invites a "what will happen" \
framing, reframe it as "what the current numbers imply is priced in".
3. Write like a sell-side analyst explaining to a sharp colleague: plain \
language, cause-and-effect, trends over snapshots. Cite the actual figures.
4. Flag data gaps and caveats explicitly (e.g. Z-score calibration caveats \
supplied in the context).
5. Keep it to 3–5 tight paragraphs unless asked otherwise."""

_THESIS_SYSTEM = """You draft structured investment-thesis skeletons inside EquiSense. The user \
will edit and own the thesis — you produce a rigorous starting point, never a \
recommendation. Hard rules:

1. Only reference figures present in the supplied JSON context.
2. Every assumption must be FALSIFIABLE — a specific, observable claim with a \
number or date where possible ("occupancy stays above 65%"), never a vibe \
("the company will do well").
3. Every invalidation trigger must be checkable against future filings or \
disclosures.
4. No price targets or return forecasts. The thesis statement describes the \
business/economics claim, not a stock-price outcome.
5. Do not present the draft as advice; it is raw material for the user's own \
judgment."""

_THESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "statement": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "invalidation_triggers": {"type": "array", "items": {"type": "string"}},
        "sizing_considerations": {"type": "string"},
        "suggested_review_months": {"type": "integer"},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["statement", "assumptions", "invalidation_triggers",
                 "sizing_considerations", "suggested_review_months", "data_gaps"],
    "additionalProperties": False,
}


def _client():
    import anthropic
    return anthropic.Anthropic()


def _unavailable(context: Any, reason: str) -> dict:
    return {"available": False, "reason": reason, "text": None,
            "grounding": None, "context": context}


def _call(system: str, user_content: str, output_schema: Optional[dict] = None) -> str:
    kwargs: dict = dict(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    if output_schema is not None:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": output_schema}
    with _client().messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Model declined the request (stop_reason=refusal).")
    return next(b.text for b in msg.content if b.type == "text")


def _grounded_call(system: str, context: Any, instruction: str,
                   output_schema: Optional[dict] = None) -> dict:
    """One call + grounding validation; one corrective retry on violation
    (§29.2 — automated, not a manual spot check)."""
    ctx_json = json.dumps(context, indent=1, default=str)
    prompt = f"<context>\n{ctx_json}\n</context>\n\n{instruction}"
    try:
        text = _call(system, prompt, output_schema)
        report = validate(text, context)
        if not report["grounded"]:
            retry_prompt = (prompt + "\n\nYour previous draft contained numbers not "
                            f"present in the context: {report['violations']}. Rewrite it "
                            "using ONLY figures from the context.")
            text = _call(system, retry_prompt, output_schema)
            report = validate(text, context)
        return {"available": True, "text": text, "grounding": report,
                "context": context}
    except Exception as e:  # auth errors, network, refusal — degrade, don't 500
        return _unavailable(context, f"{type(e).__name__}: {e}")


def narrate_statements(context: dict) -> dict:
    """Financial statement narrative interpretation (§13.3 row 1)."""
    return _grounded_call(
        _NARRATOR_SYSTEM, context,
        "Explain this company's financial trajectory and what the computed "
        "metrics imply about the quality of the business. Address: growth and "
        "margin trend, cash-flow quality vs reported profit, balance-sheet "
        "risk (Z-score with its caveat), and what growth rate the market is "
        "currently pricing in versus the company's own history.")


def narrate_portfolio(context: dict) -> dict:
    """Portfolio diagnostic narrative (§13.3 row 5) — no trade recommendations."""
    return _grounded_call(
        _NARRATOR_SYSTEM, context,
        "Review this portfolio's composition like an analyst reviewing a "
        "colleague's book: performance (XIRR), the four concentration axes "
        "(position, sector, cap band, quality tier), and where the actual "
        "book conflicts with the investor profile's stated limits. Describe "
        "the diagnostic facts; do NOT recommend specific trades.")


def draft_thesis(context: dict, user_angle: str) -> dict:
    """Thesis generation assistant (§13.3 row 2). Returns structured JSON the
    user edits — the thesis remains the user's reasoning."""
    result = _grounded_call(
        _THESIS_SYSTEM, context,
        "Draft a structured thesis skeleton for this company. The user's own "
        f"angle, in their words: {user_angle!r}. Build on their angle; do not "
        "replace it.",
        output_schema=_THESIS_SCHEMA)
    if result["available"] and result["text"]:
        try:
            result["draft"] = json.loads(result["text"])
        except json.JSONDecodeError:
            result["draft"] = None
    return result
