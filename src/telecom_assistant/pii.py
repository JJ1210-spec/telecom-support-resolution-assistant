"""PII redaction applied before any LLM call, embedding call, trace or log write.

Regex recognizers cover the identifiers that show up in telecom complaints (email, phone/MSISDN,
account / IMEI / ICCID numbers, card numbers, Indian PAN/Aadhaar-style IDs, IP and MAC addresses).
The raw complaint is stored only in the system-of-record ticket row, visible to the owner and agents.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?!\w)")),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,5}(?!\d)")),
    ("ACCOUNT", re.compile(r"\b(?:acc(?:oun)?t|a/c|customer id|cust id|ban)\s*(?:no\.?|number|#|:)?\s*[A-Z0-9-]{5,}\b",
                           re.IGNORECASE)),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("MAC", re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")),
]


def redact(text: str) -> tuple[str, list[str]]:
    """Return redacted text and the list of entity types found (never the values)."""
    found: list[str] = []
    result = text
    for label, pattern in _PATTERNS:
        def _sub(match: re.Match[str], label: str = label) -> str:
            digits = sum(ch.isdigit() for ch in match.group(0))
            if label == "PHONE" and digits < 8:
                return match.group(0)  # "2 days", "8 pm" etc. are not phone numbers
            found.append(label)
            return f"[{label}]"

        result = pattern.sub(_sub, result)
    return result, sorted(set(found))


def redact_text(text: str) -> str:
    return redact(text)[0]
