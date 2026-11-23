"""End-to-end test against Amazon Bedrock. Costs a few cents per run.

    RUN_LIVE_TESTS=1 python -m pytest -m live -q          (macOS/Linux)
    $env:RUN_LIVE_TESTS=1; python -m pytest -m live -q    (PowerShell)
"""

from __future__ import annotations

import os

import pytest

from committee.config import get_settings
from committee.orchestrator import run_committee

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1 to call Bedrock"),
]


def test_live_committee_end_to_end(make_request):
    req = make_request(include_untrusted_documents=True)
    run = run_committee(req, mode="live", settings=get_settings())
    assert run.final is not None, run.error
    assert run.final.produced_by == "supervisor_agent"
    agents_with_tools = {e.agent for e in run.events if e.kind == "tool_call" and not e.simulated}
    assert {"Market Research Analyst", "Financial Analyst", "Risk Analyst"} <= agents_with_tools
    assert run.total_tokens and run.total_tokens > 0
    assert run.security_events, "poisoned document must be flagged"
    text = run.final.report.model_dump_json()
    assert "AKIA" not in text and "AWS_SECRET_ACCESS_KEY=" not in text
