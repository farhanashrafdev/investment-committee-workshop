"""Agent 2: Market Research Analyst."""

from __future__ import annotations

from strands import Agent

from agents.model_factory import build_model
from agents.prompts import RESEARCH_PROMPT
from committee.config import Settings
from observability.events import RESEARCH, EventRecorder
from observability.tracing import ToolTimelineHook
from tools.company_data import get_company_profile, get_third_party_documents, list_available_companies

RESEARCH_TOOLS = [get_company_profile, get_third_party_documents, list_available_companies]


def build_research_agent(settings: Settings, recorder: EventRecorder | None = None) -> Agent:
    return Agent(
        name=RESEARCH,
        description="Analyzes business models, competitive advantages, market trends and third-party sources.",
        model=build_model("specialist", settings),
        system_prompt=RESEARCH_PROMPT,
        tools=RESEARCH_TOOLS,
        hooks=[ToolTimelineHook(recorder, RESEARCH)] if recorder else [],
        callback_handler=None,  # only the supervisor prints; specialists stay quiet
    )
