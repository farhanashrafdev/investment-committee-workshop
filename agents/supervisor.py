"""Agent 1: Investment Committee Supervisor.

Specialists are exposed to the supervisor with the Strands "agents as tools"
pattern (custom @tool wrappers, as documented by Strands). The supervisor model
decides when to call each specialist; each call creates a real specialist Agent
that runs its own model + tool loop on Bedrock and returns a validated
SpecialistFinding.

The wrapper adds deterministic controls the model cannot override:
- tickers must be inside the request scope
- each specialist can be called at most MAX_CALLS_PER_SPECIALIST times
- no new delegation starts after the run's time budget is spent
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from strands import Agent, ToolContext, tool
from strands.types.agent import Limits
from strands.types.exceptions import StructuredOutputException

from agents.financial import build_financial_agent
from agents.model_factory import build_model
from agents.prompts import SUPERVISOR_PROMPT, committee_brief
from agents.research import build_research_agent
from agents.risk import build_risk_agent
from committee.config import Settings
from committee.context import SHARED_CONTEXT_KEY, get_shared_context
from committee.schemas import SpecialistFinding
from observability.events import FINANCIAL, RESEARCH, RISK, SUPERVISOR, EventRecorder
from observability.tracing import ToolTimelineHook, estimate_cost, usage_from_result
from security.input_validation import InputValidationError, validate_tickers
from tools.planning_tools import assess_investment_goal
from tools.risk_tools import check_investment_policy

MIN_SECONDS_TO_START_SPECIALIST = 20

# Deterministic loop bounds (Strands Limits): an agent can never spin forever,
# even if a model keeps producing invalid structured output.
SPECIALIST_LIMITS = Limits(turns=12, structured_output_attempts=3)
SUPERVISOR_LIMITS = Limits(turns=16, structured_output_attempts=3)


@dataclass(frozen=True)
class SpecialistSpec:
    tool_name: str
    agent_name: str
    builder: Callable[[Settings, EventRecorder | None], Agent]


SPECIALISTS = [
    SpecialistSpec("market_research_analyst", RESEARCH, build_research_agent),
    SpecialistSpec("financial_analyst", FINANCIAL, build_financial_agent),
    SpecialistSpec("risk_analyst", RISK, build_risk_agent),
]


def _run_specialist(spec: SpecialistSpec, settings: Settings, task: str, tickers: list[str], tool_context: ToolContext) -> dict[str, Any]:
    ctx = get_shared_context(tool_context.invocation_state)
    rec = ctx.recorder
    call_no = ctx.register_specialist_call(spec.tool_name)
    if call_no > ctx.max_calls_per_specialist:
        return {"status": "refused", "specialist": spec.agent_name,
                "reason": f"Call limit reached ({ctx.max_calls_per_specialist}). Use the findings you already have."}
    left = ctx.time_left()
    if left is not None and left < MIN_SECONDS_TO_START_SPECIALIST:
        return {"status": "refused", "specialist": spec.agent_name, "reason": "Run time budget exhausted. Finalize with the findings you have."}
    try:
        scoped = validate_tickers(tickers)
        outside = [t for t in scoped if t not in ctx.request.tickers]
        if outside:
            raise InputValidationError(f"Tickers {outside} are outside this request's scope {ctx.request.tickers}.")
    except InputValidationError as exc:
        return {"status": "error", "specialist": spec.agent_name, "error": str(exc)}

    if rec:
        rec.delegation(spec.agent_name, f"[call {call_no}] {task} | tickers={scoped}")
        rec.agent_started(spec.agent_name, task)
    prompt = f"{committee_brief(ctx.request)}\n\nTASK FROM THE SUPERVISOR:\n{task}\n\nTickers to analyze: {', '.join(scoped)}"
    started = time.monotonic()
    try:
        agent = spec.builder(settings, rec)
        result = agent(prompt, invocation_state={SHARED_CONTEXT_KEY: ctx}, structured_output_model=SpecialistFinding,
                       limits=SPECIALIST_LIMITS, cancel_signal=ctx.cancel_signal)
        finding: SpecialistFinding | None = result.structured_output
        if finding is None:
            raise StructuredOutputException(f"stopped with '{result.stop_reason}' before returning a valid finding")
        usage = usage_from_result(result)
        cost = estimate_cost(settings.specialist_model_id, usage, settings.pricing)
        payload = finding.model_dump()
        ctx.add_finding(spec.agent_name, payload)
        ctx.add_usage(spec.agent_name, settings.specialist_model_id, usage, cost)
        if rec:
            rec.agent_completed(spec.agent_name, duration_ms=(time.monotonic() - started) * 1000, tokens=usage,
                                summary=f"stance={finding.stance}, confidence={finding.confidence}: {finding.summary}")
        return {"status": "ok", "specialist": spec.agent_name, "call": call_no, "finding": payload}
    except StructuredOutputException as exc:
        msg = f"{spec.agent_name} did not return a valid structured finding: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the supervisor and UI
        msg = f"{type(exc).__name__}: {str(exc)[:400]}"
    if rec:
        rec.agent_failed(spec.agent_name, msg, duration_ms=(time.monotonic() - started) * 1000)
    return {"status": "error", "specialist": spec.agent_name, "error": msg}


SPECIALIST_DESCRIPTIONS = {
    "market_research_analyst": "Market Research Analyst agent: business model, competitive advantages, market trends, industry position, third-party documents.",
    "financial_analyst": "Financial Analyst agent: revenue growth, margins, cash flow, valuation and comparisons computed by deterministic Python tools.",
    "risk_analyst": "Risk Analyst agent (independent challenger): company risks, downside scenarios, concentration, currency and cross-border risk.",
}


def build_specialist_tools(settings: Settings) -> list:
    tools = []
    for spec in SPECIALISTS:
        def make(spec: SpecialistSpec = spec):
            @tool(name=spec.tool_name, description=SPECIALIST_DESCRIPTIONS[spec.tool_name], context=True)
            def delegate(task: str, tickers: list[str], tool_context: ToolContext) -> dict[str, Any]:
                """Delegate a focused task to a specialist agent and receive its structured finding.

                Args:
                    task: Specific instructions for the specialist (what to analyze and why).
                    tickers: Tickers to analyze; must be within the committee request.
                """
                return _run_specialist(spec, settings, task, tickers, tool_context)

            return delegate

        tools.append(make())
    return tools


def build_supervisor(settings: Settings, recorder: EventRecorder | None = None) -> Agent:
    return Agent(
        name=SUPERVISOR,
        description="Coordinates the investment committee and produces the final recommendation.",
        model=build_model("supervisor", settings),
        system_prompt=SUPERVISOR_PROMPT,
        tools=[*build_specialist_tools(settings), assess_investment_goal, check_investment_policy],
        hooks=[ToolTimelineHook(recorder, SUPERVISOR)] if recorder else [],
        callback_handler=None,
    )
