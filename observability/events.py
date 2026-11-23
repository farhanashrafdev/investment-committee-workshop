"""Execution events shown in the UI timeline and written to logs/run-<id>.jsonl.

Events are recorded from real Strands hook callbacks (live mode) or from the
scripted mock runner. Mock events carry ``simulated=True`` and never include
token counts, so simulated runs cannot be mistaken for Bedrock invocations.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from security.output_checks import redact_secrets

logger = logging.getLogger("committee.events")

SUPERVISOR = "Investment Committee Supervisor"
RESEARCH = "Market Research Analyst"
FINANCIAL = "Financial Analyst"
RISK = "Risk Analyst"
AGENT_NAMES = [SUPERVISOR, RESEARCH, FINANCIAL, RISK]

AgentStatus = Literal["Waiting", "Running", "Completed", "Failed"]
EventKind = Literal[
    "run_started", "run_completed", "run_failed", "delegation", "agent_started", "agent_completed",
    "agent_failed", "tool_call", "tool_result", "structured_output", "security", "policy", "usage", "info",
]

PREVIEW_CHARS = 600


def _preview(value: Any, limit: int = PREVIEW_CHARS) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text, _ = redact_secrets(text)
    return text if len(text) <= limit else text[:limit] + " ..."


class ExecutionEvent(BaseModel):
    seq: int
    ts: str
    elapsed_s: float
    run_id: str
    kind: EventKind
    agent: str
    message: str
    tool_name: str | None = None
    tool_input: str | None = None
    output_preview: str | None = None
    status: Literal["ok", "error", "running", "warning", "blocked"] | None = None
    duration_ms: float | None = None
    tokens: dict[str, int] | None = None
    simulated: bool = False


class EventRecorder:
    """Thread-safe event store. Specialists may run concurrently."""

    def __init__(self, run_id: str, *, simulated: bool = False, log_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.simulated = simulated
        self._events: list[ExecutionEvent] = []
        self._statuses: dict[str, AgentStatus] = {name: "Waiting" for name in AGENT_NAMES}
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._tool_starts: dict[str, float] = {}
        self._log_path: Path | None = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / f"run-{run_id}.jsonl"

    # -- core ---------------------------------------------------------------
    def emit(self, kind: EventKind, agent: str, message: str, **fields: Any) -> ExecutionEvent:
        with self._lock:
            ev = ExecutionEvent(
                seq=len(self._events) + 1,
                ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                elapsed_s=round(time.monotonic() - self._t0, 2),
                run_id=self.run_id,
                kind=kind,
                agent=agent,
                message=message,
                simulated=self.simulated,
                **fields,
            )
            self._events.append(ev)
            if self._log_path:
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(ev.model_dump_json() + "\n")
        logger.info(ev.message, extra={"event": ev.model_dump(exclude={"output_preview", "tool_input"})})
        return ev

    def set_status(self, agent: str, status: AgentStatus) -> None:
        with self._lock:
            self._statuses[agent] = status

    def snapshot(self) -> tuple[list[ExecutionEvent], dict[str, AgentStatus]]:
        with self._lock:
            return list(self._events), dict(self._statuses)

    # -- helpers -------------------------------------------------------------
    def tool_started(self, agent: str, tool_name: str, tool_input: Any, tool_use_id: str | None = None, kind: EventKind = "tool_call") -> None:
        if tool_use_id:
            with self._lock:
                self._tool_starts[tool_use_id] = time.monotonic()
        self.emit(kind, agent, f"{agent} called {tool_name}", tool_name=tool_name, tool_input=_preview(tool_input, 300), status="running")

    def tool_finished(self, agent: str, tool_name: str, *, status: str, output: Any, tool_use_id: str | None = None,
                      duration_ms: float | None = None) -> None:
        if duration_ms is None and tool_use_id:
            with self._lock:
                start = self._tool_starts.pop(tool_use_id, None)
            if start is not None:
                duration_ms = (time.monotonic() - start) * 1000
        ok = status == "success" or status == "ok"
        self.emit("tool_result", agent, f"{tool_name} {'succeeded' if ok else 'failed'}", tool_name=tool_name,
                  output_preview=_preview(output), status="ok" if ok else "error",
                  duration_ms=round(duration_ms, 1) if duration_ms is not None else None)

    def delegation(self, to_agent: str, task: str) -> None:
        self.emit("delegation", SUPERVISOR, f"Supervisor delegated to {to_agent}", tool_input=_preview(task, 400), status="running")

    def agent_started(self, agent: str, task: str = "") -> None:
        self.set_status(agent, "Running")
        self.emit("agent_started", agent, f"{agent} started", tool_input=_preview(task, 400) if task else None, status="running")

    def agent_completed(self, agent: str, *, duration_ms: float, tokens: dict[str, int] | None = None, summary: str = "") -> None:
        self.set_status(agent, "Completed")
        self.emit("agent_completed", agent, f"{agent} completed", duration_ms=round(duration_ms, 1), tokens=tokens,
                  output_preview=_preview(summary, 400), status="ok")

    def agent_failed(self, agent: str, error: str, duration_ms: float | None = None) -> None:
        self.set_status(agent, "Failed")
        self.emit("agent_failed", agent, f"{agent} failed: {error[:200]}", status="error",
                  duration_ms=round(duration_ms, 1) if duration_ms is not None else None)

    def security(self, agent: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.emit("security", agent, message, output_preview=_preview(details, 800), status="warning")

    def policy(self, message: str, details: dict[str, Any], status: str = "ok") -> None:
        self.emit("policy", "Policy Engine (deterministic)", message, output_preview=_preview(details, 800), status=status)  # type: ignore[arg-type]
