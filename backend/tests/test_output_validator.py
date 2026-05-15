# backend/tests/test_output_validator.py
from typing import Any


def _good_deltas() -> list[dict[str, Any]]:
    return [
        {"type": "quick_take", "signal": "tactical_buy", "qualifier": "OK"},
        {"type": "stock_card", "ticker": "AAPL", "name": "Apple Inc.",
         "market": "US", "currency": "USD", "stats": {"P/E": "29"}},
        {"type": "section", "title": "Thesis", "markdown": "Good thesis.",
         "citations": []},
        {"type": "section", "title": "Fundamentals",
         "markdown": "Net margin 25.5% [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}]},
        {"type": "section", "title": "Risks", "markdown": "Some risk.",
         "citations": []},
        {"type": "recommendation", "signal": "tactical_buy",
         "position_size_range": [2, 4], "entry_zone": "440-455",
         "stop": "385", "target_12mo_base": "540"},
        {"type": "disclaimer", "text": "Educational analysis…"},
        {"type": "done"},
    ]


def test_validator_accepts_well_formed_deltas() -> None:
    from app.core.output_validator import validate
    r = validate(_good_deltas())
    assert r.ok is True
    assert r.issues == []


def test_validator_flags_missing_disclaimer() -> None:
    from app.core.output_validator import validate
    deltas = _good_deltas()
    deltas = [d for d in deltas if d["type"] != "disclaimer"]
    r = validate(deltas)
    assert r.ok is False
    assert any("disclaimer" in i.lower() for i in r.issues)


def test_validator_flags_missing_recommendation() -> None:
    from app.core.output_validator import validate
    deltas = [d for d in _good_deltas() if d["type"] != "recommendation"]
    r = validate(deltas)
    assert r.ok is False
    assert any("recommendation" in i.lower() for i in r.issues)


def test_validator_flags_section_with_numbers_no_citations() -> None:
    from app.core.output_validator import validate
    deltas = _good_deltas()
    # Replace the Fundamentals section with one that has numbers but no citations
    for i, d in enumerate(deltas):
        if d.get("type") == "section" and d.get("title") == "Fundamentals":
            deltas[i] = {"type": "section", "title": "Fundamentals",
                         "markdown": "Net margin 25.5%.", "citations": []}
            break
    r = validate(deltas)
    assert r.ok is False
    assert any("citation" in i.lower() for i in r.issues)


def test_validator_flags_invalid_signal() -> None:
    from app.core.output_validator import validate
    deltas = _good_deltas()
    for i, d in enumerate(deltas):
        if d.get("type") == "recommendation":
            deltas[i] = {**d, "signal": "definitely_buy"}
            break
    r = validate(deltas)
    assert r.ok is False
    assert any("signal" in i.lower() for i in r.issues)
