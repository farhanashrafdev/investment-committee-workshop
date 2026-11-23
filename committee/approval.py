"""Human-in-the-loop review. Recording a decision is the only thing approval does.

There is deliberately no broker integration and no trade function anywhere in
this codebase. Approval appends one line to audit/decisions.jsonl.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from committee.config import get_settings
from committee.report import FinalReport
from committee.schemas import ReviewDecision
from security.input_validation import normalize_text
from security.output_checks import redact_secrets

_lock = threading.Lock()


def record_decision(final: FinalReport, decision: str, reviewer: str, comment: str = "", audit_dir: Path | None = None) -> ReviewDecision:
    comment_clean, _ = redact_secrets(normalize_text(comment or "").strip())
    record = ReviewDecision(
        run_id=final.run_id,
        decision=decision,  # type: ignore[arg-type]
        reviewer=normalize_text(reviewer).strip()[:80] or "anonymous",
        comment=comment_clean[:2000],
        recommendation=final.report.recommendation,
        selected_ticker=final.report.selected_ticker,
        allocation_amount=final.allocation_amount,
        currency=final.currency,  # type: ignore[arg-type]
    )
    base = audit_dir or get_settings().audit_dir
    base.mkdir(parents=True, exist_ok=True)
    with _lock, (base / "decisions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**record.model_dump(), "mode": final.mode}) + "\n")
    return record
