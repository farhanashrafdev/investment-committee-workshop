"""Deterministic output checks applied to everything the agents produce.

Even if a model were tricked into echoing a secret, these regular expressions
redact anything that looks like an AWS key or credential assignment before it
is displayed, logged or written to the audit trail. The agents also have no
tool that can read credentials, so there is nothing for them to reveal.
"""

from __future__ import annotations

import re

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("aws_secret_assignment", re.compile(r"(?i)(aws_secret_access_key|aws_session_token)\s*[:=]\s*\S+")),
    ("generic_secret_assignment", re.compile(r"(?i)\b(secret|password|api[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_\-]{12,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
]


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, list_of_pattern_names_found)."""
    found: list[str] = []
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            found.append(name)
            text = pattern.sub(f"[REDACTED:{name}]", text)
    return text, found
