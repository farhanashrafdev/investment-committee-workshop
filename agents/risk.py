"""Agent 4: Risk Analyst, the committee's independent challenger."""

from __future__ import annotations

from strands import Agent

from agents.model_factory import build_model
from agents.prompts import RISK_PROMPT
from committee.config import Settings
from observability.events import RISK, EventRecorder
from observability.tracing import ToolTimelineHook
from tools.company_data import get_third_party_documents
from tools.risk_tools import assess_currency_risk, check_concentration, get_company_risks, run_downside_scenarios

RISK_TOOLS = [get_company_risks, run_downside_scenarios, check_concentration, assess_currency_risk, get_third_party_documents]


def build_risk_agent(settings: Settings, recorder: EventRecorder | None = None) -> Agent:
    return Agent(
        name=RISK,
        description="Challenges optimistic assumptions: risks, stress scenarios, concentration and currency exposure.",
        model=build_model("specialist", settings),
        system_prompt=RISK_PROMPT,
        tools=RISK_TOOLS,
        hooks=[ToolTimelineHook(recorder, RISK)] if recorder else [],
        callback_handler=None,
    )
