from __future__ import annotations

from types import SimpleNamespace

import pytest

from committee.context import SHARED_CONTEXT_KEY, SharedContext
from security.input_validation import build_request


@pytest.fixture
def make_request():
    def _make(**overrides):
        args = dict(
            tickers=["NVDA"],
            budget=100_000,
            currency="USD",
            risk_tolerance="moderate",
            horizon_years=5,
            question="Should we invest, hold, or reject NVIDIA?",
        )
        args.update(overrides)
        req, _ = build_request(**args)
        return req

    return _make


@pytest.fixture
def tool_context(make_request):
    """A stand-in for strands.ToolContext carrying the shared committee context."""

    def _make(**overrides):
        ctx = SharedContext(request=make_request(**overrides))
        return SimpleNamespace(invocation_state={SHARED_CONTEXT_KEY: ctx}, agent=SimpleNamespace(name="Test Agent")), ctx

    return _make
