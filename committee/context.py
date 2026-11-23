"""Shared context for one committee run.

The orchestrator passes this object to every agent through Strands
``invocation_state``. Tools declared with ``@tool(context=True)`` read it from
``tool_context.invocation_state[SHARED_CONTEXT_KEY]``. This is how the budget,
currency and user-supplied prices reach the tools without the model having to
copy them (and possibly mistype them).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from committee.schemas import InvestmentRequest

if TYPE_CHECKING:
    from observability.events import EventRecorder
    from security.guardrails import GuardrailChecker

SHARED_CONTEXT_KEY = "committee_context"


@dataclass
class SharedContext:
    request: InvestmentRequest
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    recorder: "EventRecorder | None" = None
    guardrail: "GuardrailChecker | None" = None
    sanitize_documents: bool = True
    deadline: float | None = None
    max_calls_per_specialist: int = 2
    specialist_calls: Counter = field(default_factory=Counter)
    specialist_findings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    security_events: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def time_left(self) -> float | None:
        return None if self.deadline is None else self.deadline - time.monotonic()

    def register_specialist_call(self, name: str) -> int:
        with self._lock:
            self.specialist_calls[name] += 1
            return self.specialist_calls[name]

    def add_finding(self, name: str, finding: dict[str, Any]) -> None:
        with self._lock:
            self.specialist_findings.setdefault(name, []).append(finding)

    def add_security_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.security_events.append(event)
        if self.recorder:
            self.recorder.security(event.get("agent", "Security"), event.get("message", ""), details=event)


def get_shared_context(invocation_state: dict[str, Any] | None) -> SharedContext:
    ctx = (invocation_state or {}).get(SHARED_CONTEXT_KEY)
    if not isinstance(ctx, SharedContext):
        raise RuntimeError("Shared committee context is missing from invocation_state.")
    return ctx
