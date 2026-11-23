"""Agent 3: Financial Analyst. All arithmetic happens in tools/financial_calculations.py."""

from __future__ import annotations

from strands import Agent

from agents.model_factory import build_model
from agents.prompts import FINANCIAL_PROMPT
from committee.config import Settings
from observability.events import FINANCIAL, EventRecorder
from observability.tracing import ToolTimelineHook
from tools.company_data import get_financial_statements
from tools.financial_calculations import (
    calculate_profit_margin,
    calculate_revenue_growth,
    calculate_valuation_ratios,
    compare_companies,
)

FINANCIAL_TOOLS = [
    get_financial_statements,
    calculate_revenue_growth,
    calculate_profit_margin,
    calculate_valuation_ratios,
    compare_companies,
]


def build_financial_agent(settings: Settings, recorder: EventRecorder | None = None) -> Agent:
    return Agent(
        name=FINANCIAL,
        description="Analyzes revenue, growth, margins, cash flow and valuation with deterministic Python tools.",
        model=build_model("specialist", settings),
        system_prompt=FINANCIAL_PROMPT,
        tools=FINANCIAL_TOOLS,
        hooks=[ToolTimelineHook(recorder, FINANCIAL)] if recorder else [],
        callback_handler=None,
    )
