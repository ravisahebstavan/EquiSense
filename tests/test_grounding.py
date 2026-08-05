"""AI-output grounding validation (§15): the automated test that the AI
layer can never state a number it wasn't given."""
from equisense.ai.grounding import collect_context_numbers, validate

CONTEXT = {
    "company": "Apollo Hospitals",
    "period": "FY2025",
    "metrics": [
        {"key": "roe", "value": 19.29, "formula": "Net income 135.0 / Total equity 700.0"},
        {"key": "effective_tax_rate", "value": 0.25},
    ],
}


def test_collects_numbers_from_nested_structures_and_strings():
    nums = collect_context_numbers(CONTEXT)
    assert 19.29 in nums
    assert 135.0 in nums      # embedded in a formula string
    assert 2025.0 in nums     # embedded in "FY2025"


def test_grounded_text_passes():
    r = validate("ROE in FY2025 was 19.3%, on net income of 135 crore.", CONTEXT)
    assert r["grounded"], r["violations"]


def test_percentage_fraction_equivalence():
    r = validate("The effective tax rate is 25%.", CONTEXT)
    assert r["grounded"], r["violations"]


def test_fabricated_number_is_caught():
    r = validate("ROE was 19.3% and revenue grew 42.7% last year.", CONTEXT)
    assert not r["grounded"]
    assert "42.7" in r["violations"]


def test_rounding_tolerance():
    # 19.29 stated as 19.3 (one decimal) is a legitimate rounding, 19.9 is not
    assert validate("ROE was 19.3%.", CONTEXT)["grounded"]
    assert not validate("ROE was 19.9%.", CONTEXT)["grounded"]


def test_small_ordinals_are_free():
    r = validate("Two of the 9 signals improved; 3 deteriorated.", CONTEXT)
    assert r["grounded"], r["violations"]
