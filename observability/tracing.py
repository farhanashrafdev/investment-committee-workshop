"""Observability: Strands hooks for the execution timeline, optional OpenTelemetry
tracing, structured logging, and token/cost accounting.

Token counts come from Strands ``AgentResult.metrics.accumulated_usage``, which
is what Bedrock returned. Cost is only estimated when MODEL_PRICING_JSON is set;
otherwise the UI shows "pricing not configured" instead of a guess.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from committee.config import ModelPrice, Settings
from observability.events import EventRecorder

SPECIALIST_TOOL_NAMES = {"market_research_analyst", "financial_analyst", "risk_analyst"}
STRUCTURED_OUTPUT_TOOLS = {"SpecialistFinding", "CommitteeReport"}

_tracing_initialized = False


class ToolTimelineHook(HookProvider):
    """Records every tool call an agent makes: name, inputs, status and duration."""

    def __init__(self, recorder: EventRecorder, agent_label: str) -> None:
        self.recorder = recorder
        self.agent_label = agent_label

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _kind(self, name: str) -> str:
        if name in STRUCTURED_OUTPUT_TOOLS:
            return "structured_output"
        return "tool_call"

    def _before(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name in SPECIALIST_TOOL_NAMES:
            return  # the delegation wrapper records richer events itself
        if name in STRUCTURED_OUTPUT_TOOLS:
            self.recorder.emit("structured_output", self.agent_label, f"{self.agent_label} submitted structured {name}", tool_name=name, status="running")
            return
        self.recorder.tool_started(self.agent_label, name, event.tool_use.get("input"), event.tool_use.get("toolUseId"))

    def _after(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name in SPECIALIST_TOOL_NAMES:
            return
        if name in STRUCTURED_OUTPUT_TOOLS:
            result = event.result or {}
            if result.get("status") == "error":
                text = (result.get("content") or [{}])[0].get("text", "")
                self.recorder.emit("structured_output", self.agent_label, f"{name} failed schema validation; the model is asked to fix it",
                                   tool_name=name, output_preview=text[:300], status="warning")
            return
        result = event.result or {}
        content = result.get("content") or []
        output: Any = None
        if content:
            first = content[0]
            output = first.get("json") if "json" in first else first.get("text")
        if event.exception is not None and output is None:
            output = f"{type(event.exception).__name__}: {event.exception}"
        self.recorder.tool_finished(
            self.agent_label, name, status=str(result.get("status", "error")), output=output,
            tool_use_id=event.tool_use.get("toolUseId"),
        )


def usage_from_result(result: Any) -> dict[str, int]:
    """Extract Bedrock-reported token usage from a Strands AgentResult."""
    usage = dict(getattr(result.metrics, "accumulated_usage", {}) or {})
    metrics = dict(getattr(result.metrics, "accumulated_metrics", {}) or {})
    out = {
        "inputTokens": int(usage.get("inputTokens", 0)),
        "outputTokens": int(usage.get("outputTokens", 0)),
        "totalTokens": int(usage.get("totalTokens", 0)),
    }
    if "latencyMs" in metrics:
        out["latencyMs"] = int(metrics["latencyMs"])
    return out


def estimate_cost(model_id: str, usage: dict[str, int], pricing: dict[str, ModelPrice]) -> float | None:
    price = pricing.get(model_id)
    if price is None:
        return None
    return round(usage.get("inputTokens", 0) / 1000 * price.input_per_1k + usage.get("outputTokens", 0) / 1000 * price.output_per_1k, 6)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event  # type: ignore[attr-defined]
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("committee")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def setup_tracing(settings: Settings) -> str:
    """Enable Strands OpenTelemetry export when configured. Returns a status string."""
    global _tracing_initialized
    if _tracing_initialized:
        return "already initialized"
    if not settings.otlp_endpoint and not settings.console_tracing:
        return "disabled (set OTEL_EXPORTER_OTLP_ENDPOINT or STRANDS_CONSOLE_TRACING=true)"
    from strands.telemetry import StrandsTelemetry

    telemetry = StrandsTelemetry()
    parts = []
    if settings.otlp_endpoint:
        telemetry.setup_otlp_exporter()
        parts.append(f"OTLP -> {settings.otlp_endpoint}")
    if settings.console_tracing:
        telemetry.setup_console_exporter()
        parts.append("console")
    _tracing_initialized = True
    return "enabled: " + ", ".join(parts)
