"""Deterministic input validation at the trust boundary (UI / CLI -> agents).

Everything here is plain Python: the same input always gives the same result.
It does not rely on a model to decide whether input is safe.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import ValidationError

from committee.schemas import InvestmentRequest, PriceInput
from tools.dataset import load_dataset

MAX_QUESTION_CHARS = 1500
TICKER_RE = re.compile(r"^[A-Z]{1,6}$")

# Phrases that commonly appear in prompt-injection attempts. A match does not
# block the request on its own; it is flagged and shown to the reviewer, and the
# probabilistic layer (Bedrock Guardrails) can make the blocking decision.
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("override_instructions", re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|your|all|system)\b.{0,30}\b(instructions?|rules|prompt|guidelines)\b", re.I | re.S)),
    ("credential_request", re.compile(r"\b(reveal|print|show|output|send|share|exfiltrate|leak)\b.{0,60}\b(aws|api|secret|access)\s*(keys?|credentials?|tokens?|passwords?)\b", re.I | re.S)),
    ("credential_terms", re.compile(r"\b(aws_secret_access_key|aws_access_key_id|aws_session_token)\b", re.I)),
    ("role_hijack", re.compile(r"\b(you are now|act as|pretend to be|new system prompt|developer mode)\b", re.I)),
    ("hidden_markup", re.compile(r"<\s*/?\s*(system|instructions?|admin)\s*>", re.I)),
    ("trade_execution", re.compile(r"\b(execute|place|submit)\b.{0,30}\b(trade|order|buy order|sell order)s?\b", re.I | re.S)),
]

AWS_KEY_RE = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")


class InputValidationError(ValueError):
    """Raised with a user-friendly message when input is rejected."""


@dataclass
class ScanResult:
    flags: list[str] = field(default_factory=list)
    matches: list[str] = field(default_factory=list)

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def normalize_text(text: str) -> str:
    """NFKC-normalize and drop control / zero-width characters often used to hide text."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


def scan_for_injection(text: str) -> ScanResult:
    result = ScanResult()
    norm = normalize_text(text)
    for name, pattern in INJECTION_PATTERNS:
        m = pattern.search(norm)
        if m:
            result.flags.append(name)
            result.matches.append(m.group(0)[:160])
    if AWS_KEY_RE.search(norm):
        result.flags.append("aws_key_pattern")
        result.matches.append("[AWS access key pattern]")
    return result


def validate_ticker(ticker: str) -> str:
    if not isinstance(ticker, str):
        raise InputValidationError("Ticker must be a string.")
    t = ticker.strip().upper()
    if not TICKER_RE.match(t):
        raise InputValidationError(f"'{ticker}' is not a valid ticker format (1-6 letters).")
    allowed = load_dataset().tickers
    if t not in allowed:
        raise InputValidationError(f"Ticker '{t}' is not in the workshop dataset. Allowed: {', '.join(allowed)}.")
    return t


def validate_tickers(tickers: list[str], max_count: int = 3) -> list[str]:
    if not tickers:
        raise InputValidationError("Select at least one company.")
    if len(tickers) > max_count:
        raise InputValidationError(f"Select at most {max_count} companies per analysis.")
    out: list[str] = []
    for t in tickers:
        v = validate_ticker(t)
        if v not in out:
            out.append(v)
    return out


def validate_question(question: str) -> tuple[str, ScanResult]:
    if not question or not question.strip():
        raise InputValidationError("Enter an investment question.")
    q = normalize_text(question).strip()
    if len(q) > MAX_QUESTION_CHARS:
        raise InputValidationError(f"Question is too long ({len(q)} characters, max {MAX_QUESTION_CHARS}).")
    if AWS_KEY_RE.search(q):
        raise InputValidationError("The question appears to contain an AWS access key. Remove credentials and try again.")
    return q, scan_for_injection(q)


def build_request(
    *,
    tickers: list[str],
    budget: float,
    currency: str,
    risk_tolerance: str,
    horizon_years: int,
    question: str,
    target_return_pct: float | None = None,
    investor_country: str = "PK",
    price_inputs: dict[str, dict] | None = None,
    include_untrusted_documents: bool = False,
) -> tuple[InvestmentRequest, ScanResult]:
    """Validate raw UI/CLI input and return a typed request plus injection scan."""
    clean_tickers = validate_tickers(tickers)
    clean_q, scan = validate_question(question)
    prices: dict[str, PriceInput] = {}
    for tk, p in (price_inputs or {}).items():
        if not p or not p.get("price"):
            continue
        tk = validate_ticker(tk)
        try:
            prices[tk] = PriceInput(price=float(p["price"]), as_of=str(p.get("as_of", "")).strip(), source=str(p.get("source", "")).strip())
        except (ValidationError, ValueError) as exc:
            raise InputValidationError(f"Invalid price input for {tk}: give a positive price, its date and its source.") from exc
    try:
        req = InvestmentRequest(
            tickers=clean_tickers,
            budget=float(budget),
            currency=currency,  # type: ignore[arg-type]
            risk_tolerance=risk_tolerance,  # type: ignore[arg-type]
            horizon_years=int(horizon_years),
            question=clean_q,
            target_return_pct=target_return_pct,
            investor_country=investor_country,  # type: ignore[arg-type]
            price_inputs=prices,
            include_untrusted_documents=include_untrusted_documents,
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        field_name = ".".join(str(x) for x in first["loc"])
        raise InputValidationError(f"Invalid value for {field_name}: {first['msg']}") from exc
    return req, scan
