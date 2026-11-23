"""Workshop segment 2 (minutes 5-15): the smallest useful Strands agent.

One model, one system prompt, two tools. Run it with:
    python main.py first-agent
"""

from __future__ import annotations

from strands import Agent

from agents.model_factory import build_model
from committee.config import Settings
from tools.company_data import get_financial_statements
from tools.financial_calculations import calculate_revenue_growth


def build_first_agent(settings: Settings) -> Agent:
    return Agent(
        name="First Agent",
        model=build_model("specialist", settings),
        system_prompt=(
            "You are a concise financial assistant. Use the tools for every number. "
            "Never calculate figures yourself. Say which tool each number came from."
        ),
        tools=[get_financial_statements, calculate_revenue_growth],
        # Default callback handler streams the answer to the console, so the audience sees it arrive.
    )


def run_first_agent(settings: Settings, question: str = "How fast did NVIDIA's revenue grow in its latest fiscal year, and what was its CAGR?") -> None:
    agent = build_first_agent(settings)
    result = agent(question)
    print("\n\n--- What just happened ---")
    print(f"Model: {settings.specialist_model_id}")
    print(f"Tools called: {list(result.metrics.tool_metrics.keys())}")
    print(f"Tokens (from Bedrock): {result.metrics.accumulated_usage}")
    print(f"Event-loop cycles: {result.metrics.cycle_count}")
