"""Orchestration tests that do not call Bedrock: mock mode, delegation guards, report post-processing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.supervisor import SpecialistSpec, _run_specialist
from committee.approval import record_decision
from committee.config import Settings
from committee.context import SHARED_CONTEXT_KEY, SharedContext
from committee.orchestrator import run_committee
from committee.preflight import explain_error
from committee.report import finalize_report, generate_investment_report
from committee.schemas import CommitteeReport, SpecialistFinding
from observability.events import FINANCIAL, RISK, EventRecorder
from security.output_checks import redact_secrets

SETTINGS = Settings()


def _report(**kw) -> CommitteeReport:
    base = dict(recommendation="INVEST", selected_ticker="NVDA", proposed_allocation_pct=15, executive_summary="s",
                bull_case=["b"], bear_case=["r"], financial_assessment="f", key_risks=["k"], confidence="high",
                confidence_explanation="c")
    base.update(kw)
    return CommitteeReport(**base)


@pytest.fixture(autouse=True)
def no_bedrock(monkeypatch):
    """Fail loudly if anything in these tests tries to reach AWS."""
    import boto3

    def boom(*a, **k):
        raise AssertionError("Bedrock/AWS must not be called in offline tests")

    monkeypatch.setattr(boto3, "client", boom)


def test_mock_run_is_labeled_and_complete(make_request, tmp_path):
    req = make_request(include_untrusted_documents=True)
    run = run_committee(req, mode="mock", settings=Settings(log_dir=tmp_path), mock_delay_s=0)
    assert run.mode == "mock" and run.final is not None
    assert run.final.produced_by == "mock_script"
    assert all(e.simulated for e in run.events)
    assert run.total_tokens is None and run.total_cost_usd is None
    assert set(run.agent_statuses.values()) == {"Completed"}
    assert run.security_events, "poisoned document should be flagged in mock mode too"
    md = generate_investment_report(run.final)
    assert "MOCK MODE" in md and "No trade is executed" in md


def test_mock_run_covers_pakistan_companies(make_request, tmp_path):
    req = make_request(tickers=["SYS", "OGDC", "LUCK"], currency="PKR", budget=5_000_000)
    run = run_committee(req, mode="mock", settings=Settings(log_dir=tmp_path), mock_delay_s=0)
    assert run.final.report.selected_ticker in {"SYS", "OGDC", "LUCK", None}
    tools = {e.tool_name for e in run.events if e.kind == "tool_call"}
    assert {"calculate_revenue_growth", "run_downside_scenarios", "compare_companies"} <= tools


def test_policy_caps_allocation_and_records_adjustment(make_request):
    ctx = SharedContext(request=make_request(risk_tolerance="conservative"))
    final = finalize_report(_report(proposed_allocation_pct=60), ctx, mode="live", produced_by="supervisor_agent")
    assert final.report.proposed_allocation_pct == 10
    assert final.allocation_amount == 10_000
    assert final.original_allocation_pct == 60
    assert final.adjustments


def test_out_of_scope_ticker_is_corrected(make_request):
    ctx = SharedContext(request=make_request(tickers=["NVDA", "MSFT"]))
    final = finalize_report(_report(selected_ticker="TSLA"), ctx, mode="live", produced_by="supervisor_agent")
    assert final.report.recommendation == "HOLD"
    assert final.report.selected_ticker is None


def test_secrets_are_redacted_from_report(make_request):
    ctx = SharedContext(request=make_request())
    final = finalize_report(_report(executive_summary="key AKIAABCDEFGHIJKLMNOP aws_secret_access_key=abc123"), ctx,
                            mode="live", produced_by="supervisor_agent")
    assert "AKIA" not in final.report.executive_summary
    assert "aws_secret_access_key=" not in final.report.executive_summary
    assert final.redactions


def test_security_events_always_appear_in_report(make_request):
    ctx = SharedContext(request=make_request())
    ctx.add_security_event({"doc_id": "nvda-blog-001", "deterministic_flags": ["credential_request"]})
    final = finalize_report(_report(security_observations=[]), ctx, mode="live", produced_by="supervisor_agent")
    assert any("nvda-blog-001" in s for s in final.report.security_observations)


def test_lenient_schema_accepts_null_lists():
    f = SpecialistFinding(stance="neutral", summary="s", key_points=None, concerns=None, missing_information="one gap", confidence="low")
    assert f.concerns == [] and f.key_points == [] and f.missing_information == ["one gap"]


def _fake_spec(behaviour):
    class FakeAgent:
        def __call__(self, prompt, **kwargs):
            if behaviour == "fail":
                raise RuntimeError("model exploded")
            finding = SpecialistFinding(stance="positive", summary="ok", key_points=[], confidence="high")
            return SimpleNamespace(structured_output=finding, stop_reason="end_turn",
                                   metrics=SimpleNamespace(accumulated_usage={"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, accumulated_metrics={"latencyMs": 100}))

    return SpecialistSpec("financial_analyst", FINANCIAL, lambda s, r: FakeAgent())


def _tc(ctx):
    return SimpleNamespace(invocation_state={SHARED_CONTEXT_KEY: ctx}, agent=SimpleNamespace(name="supervisor"))


def test_delegation_success_records_usage_and_status(make_request):
    ctx = SharedContext(request=make_request(), recorder=EventRecorder("t"))
    out = _run_specialist(_fake_spec("ok"), SETTINGS, "analyze", ["NVDA"], _tc(ctx))
    assert out["status"] == "ok"
    assert ctx.usage[0]["tokens"]["totalTokens"] == 15
    assert ctx.recorder.snapshot()[1][FINANCIAL] == "Completed"


def test_delegation_failure_is_contained(make_request):
    ctx = SharedContext(request=make_request(), recorder=EventRecorder("t"))
    out = _run_specialist(_fake_spec("fail"), SETTINGS, "analyze", ["NVDA"], _tc(ctx))
    assert out["status"] == "error" and "model exploded" in out["error"]
    assert ctx.recorder.snapshot()[1][FINANCIAL] == "Failed"


def test_delegation_call_limit(make_request):
    ctx = SharedContext(request=make_request(), max_calls_per_specialist=1)
    spec = _fake_spec("ok")
    assert _run_specialist(spec, SETTINGS, "a", ["NVDA"], _tc(ctx))["status"] == "ok"
    assert _run_specialist(spec, SETTINGS, "b", ["NVDA"], _tc(ctx))["status"] == "refused"


def test_delegation_rejects_tickers_outside_request(make_request):
    ctx = SharedContext(request=make_request(tickers=["NVDA"]))
    out = _run_specialist(_fake_spec("ok"), SETTINGS, "a", ["MSFT"], _tc(ctx))
    assert out["status"] == "error" and "outside" in out["error"]


def test_approval_records_decision_and_never_trades(make_request, tmp_path):
    ctx = SharedContext(request=make_request())
    final = finalize_report(_report(), ctx, mode="mock", produced_by="mock_script")
    d = record_decision(final, "APPROVED", "Farhan", "looks fine AKIAABCDEFGHIJKLMNOP", audit_dir=tmp_path)
    assert d.trade_executed is False
    line = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    assert '"trade_executed": false' in line and "AKIA" not in line


def test_redact_secrets_leaves_normal_text():
    text = "NVIDIA revenue grew 65.47% to $215.9B (source https://www.sec.gov/Archives/edgar/data/1045810/x.htm)"
    assert redact_secrets(text) == (text, [])


@pytest.mark.parametrize("msg,expect", [
    ("Model access is denied due to INVALID_PAYMENT_INSTRUMENT", "payment method"),
    ("Unable to locate credentials", "aws login"),
    ("ThrottlingException: Too many requests", "throttling"),
])
def test_explain_error(msg, expect):
    assert expect in explain_error(RuntimeError(msg)).lower()


def test_recorder_writes_jsonl(tmp_path):
    rec = EventRecorder("abc", log_dir=tmp_path)
    rec.agent_started(RISK, "task")
    rec.agent_completed(RISK, duration_ms=5)
    assert (tmp_path / "run-abc.jsonl").read_text(encoding="utf-8").count("\n") == 2
