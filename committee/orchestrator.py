"""Runs one Investment Committee session in live (Bedrock) or mock mode."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from agents.prompts import committee_brief
from agents.supervisor import SUPERVISOR_LIMITS, build_supervisor
from committee.config import Settings, get_settings
from committee.context import SHARED_CONTEXT_KEY, SharedContext
from committee.preflight import explain_error
from committee.report import FinalReport, finalize_report, synthesize_report
from committee.schemas import CommitteeReport, InvestmentRequest
from observability.events import SUPERVISOR, EventRecorder, ExecutionEvent
from observability.tracing import configure_logging, estimate_cost, setup_tracing, usage_from_result
from security.guardrails import build_checker

logger = logging.getLogger("committee.orchestrator")


class CommitteeRun(BaseModel):
    run_id: str
    mode: Literal["live", "mock"]
    request: InvestmentRequest
    final: FinalReport | None
    findings: dict[str, list[dict[str, Any]]]
    events: list[ExecutionEvent]
    agent_statuses: dict[str, str]
    usage: list[dict[str, Any]]
    total_tokens: int | None
    total_cost_usd: float | None
    pricing_configured: bool
    security_events: list[dict[str, Any]]
    started_at: str
    duration_s: float
    error: str | None = None


def _totals(ctx: SharedContext, settings: Settings) -> tuple[int | None, float | None]:
    if not ctx.usage:
        return None, None
    tokens = sum(u["tokens"].get("totalTokens", 0) for u in ctx.usage)
    costs = [u["cost_usd"] for u in ctx.usage]
    cost = round(sum(costs), 6) if settings.pricing_configured and all(c is not None for c in costs) else None
    return tokens, cost


def run_committee(
    request: InvestmentRequest,
    *,
    mode: Literal["live", "mock"] = "live",
    settings: Settings | None = None,
    recorder: EventRecorder | None = None,
    sanitize_documents: bool = True,
    mock_delay_s: float = 0.35,
) -> CommitteeRun:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    setup_tracing(settings)

    if mode == "mock":
        from committee.mock_mode import run_mock

        return run_mock(request, settings=settings, recorder=recorder, sanitize_documents=sanitize_documents, delay_s=mock_delay_s)

    ctx = SharedContext(
        request=request,
        guardrail=build_checker(settings),
        sanitize_documents=sanitize_documents,
        deadline=time.monotonic() + settings.run_budget_s,
        max_calls_per_specialist=settings.max_calls_per_specialist,
    )
    if recorder is not None:
        ctx.run_id = recorder.run_id
    ctx.recorder = recorder or EventRecorder(ctx.run_id, simulated=False, log_dir=settings.log_dir)
    rec = ctx.recorder
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.monotonic()
    models = {"supervisor": settings.supervisor_model_id, "specialists": settings.specialist_model_id}

    rec.emit("run_started", SUPERVISOR, f"Live run on Amazon Bedrock ({settings.aws_region})", tool_input=str(models), status="running")
    rec.agent_started(SUPERVISOR, request.question)

    report: CommitteeReport | None = None
    produced_by: Literal["supervisor_agent", "deterministic_fallback"] = "supervisor_agent"
    error: str | None = None
    # Hard wall-clock limit: when it fires, Strands cancels in-flight agent loops.
    watchdog = threading.Timer(settings.run_budget_s, ctx.cancel_signal.set)
    watchdog.daemon = True
    watchdog.start()
    try:
        supervisor = build_supervisor(settings, rec)
        prompt = (f"{committee_brief(request)}\n\nRun the committee workflow for this request and produce the final CommitteeReport.")
        result = supervisor(prompt, invocation_state={SHARED_CONTEXT_KEY: ctx}, structured_output_model=CommitteeReport,
                            limits=SUPERVISOR_LIMITS, cancel_signal=ctx.cancel_signal)
        report = result.structured_output
        if report is None:
            raise RuntimeError(f"Supervisor stopped with '{result.stop_reason}' before returning a valid CommitteeReport")
        usage = usage_from_result(result)
        ctx.add_usage(SUPERVISOR, settings.supervisor_model_id, usage, estimate_cost(settings.supervisor_model_id, usage, settings.pricing))
        rec.agent_completed(SUPERVISOR, duration_ms=(time.monotonic() - t0) * 1000, tokens=usage,
                            summary=f"{report.recommendation} {report.selected_ticker or ''} ({report.confidence} confidence)")
    except Exception as exc:  # noqa: BLE001
        error = explain_error(exc)
        logger.exception("Supervisor failed")
        rec.agent_failed(SUPERVISOR, error, duration_ms=(time.monotonic() - t0) * 1000)
        if ctx.specialist_findings:
            report = synthesize_report(ctx, reason="The supervisor agent failed, so this report was assembled by deterministic rules from the specialist findings.")
            produced_by = "deterministic_fallback"
            rec.emit("info", "Orchestrator", "Deterministic fallback report assembled from specialist findings", status="warning")
    finally:
        watchdog.cancel()
    if ctx.cancel_signal.is_set() and error is None:
        error = f"Run stopped by the {settings.run_budget_s}s time budget."

    final = finalize_report(report, ctx, mode="live", produced_by=produced_by, models=models) if report else None
    tokens, cost = _totals(ctx, settings)
    if final:
        rec.emit("run_completed", SUPERVISOR, f"Run completed: {final.report.recommendation} {final.report.selected_ticker or ''}".strip(),
                 status="ok" if produced_by == "supervisor_agent" else "warning",
                 duration_ms=round((time.monotonic() - t0) * 1000, 1), tokens={"totalTokens": tokens or 0})
    else:
        rec.emit("run_failed", SUPERVISOR, f"Run failed: {error}", status="error")
    events, statuses = rec.snapshot()
    return CommitteeRun(
        run_id=ctx.run_id, mode="live", request=request, final=final, findings=ctx.specialist_findings,
        events=events, agent_statuses=statuses, usage=ctx.usage, total_tokens=tokens, total_cost_usd=cost,
        pricing_configured=settings.pricing_configured, security_events=ctx.security_events,
        started_at=started_at, duration_s=round(time.monotonic() - t0, 2), error=error if not final or produced_by != "supervisor_agent" else None,
    )
