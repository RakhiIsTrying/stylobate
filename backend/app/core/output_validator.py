# backend/app/core/output_validator.py
import re
from typing import Any

from pydantic import BaseModel

_REQUIRED_TYPES = ("recommendation", "disclaimer", "done")
_ALLOWED_SIGNALS = {"tactical_buy", "accumulate", "hold", "reduce"}
_NUMERIC_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|x|bps|bn|m|tn)?\b", re.IGNORECASE)


class ValidationResult(BaseModel):
    ok: bool
    issues: list[str]


def _has_uncited_numbers(section: dict[str, Any]) -> bool:
    md = section.get("markdown", "")
    citations = section.get("citations", []) or []
    if not _NUMERIC_RE.search(md):
        return False
    return len(citations) == 0


def validate(deltas: list[dict[str, Any]]) -> ValidationResult:
    issues: list[str] = []
    types = [d.get("type", "") for d in deltas]

    for required in _REQUIRED_TYPES:
        if required not in types:
            issues.append(f"missing required delta: {required}")

    for d in deltas:
        t = d.get("type")
        if t == "recommendation":
            sig = d.get("signal")
            if sig not in _ALLOWED_SIGNALS:
                issues.append(f"recommendation signal '{sig}' not in {sorted(_ALLOWED_SIGNALS)}")
            psr = d.get("position_size_range") or []
            if len(psr) != 2:
                issues.append("recommendation position_size_range must have exactly 2 numbers")
        elif t == "section":
            if _has_uncited_numbers(d):
                issues.append(
                    f"section '{d.get('title')}' has numeric claims but no citations"
                )

    return ValidationResult(ok=not issues, issues=issues)
