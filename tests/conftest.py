"""Shared helpers for the suite."""
from collections.abc import Iterable

from app.rules import SEVERITY_ORDER


def blocking(findings: Iterable[dict[str, object]], threshold: str = "medium") -> list[str]:
    """Rule IDs at or above `threshold` — what a CI gate would actually fail on."""
    return [
        str(f["rule"])
        for f in findings
        if SEVERITY_ORDER[str(f["severity"])] >= SEVERITY_ORDER[threshold]
    ]
