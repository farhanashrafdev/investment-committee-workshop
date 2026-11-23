from __future__ import annotations

import pytest

from security.input_validation import InputValidationError
from tools.planning_tools import assess_return_target, parse_goal_question
from tools.risk_tools import (
    concentration_check,
    currency_exposure,
    downside_scenarios,
    investment_policy_check,
    run_downside_scenarios,
)


def test_downside_scenarios_math():
    r = downside_scenarios("MSFT", budget=100_000, investor_currency="USD", allocation_pct=20)
    first = r["scenarios"][0]
    assert r["position_size"] == 20_000
    assert first["price_move_pct"] == -20
    assert first["loss"] == pytest.approx(4_000)
    assert "with_adverse_fx_move" not in first


def test_downside_adds_fx_overlay_for_pkr_investor_in_us_stock():
    r = downside_scenarios("NVDA", budget=1_000_000, investor_currency="PKR", allocation_pct=10)
    first = r["scenarios"][0]
    assert first["with_adverse_fx_move"]["position_value_after"] == pytest.approx(100_000 * 0.8 * 0.9)


def test_downside_rejects_bad_allocation():
    with pytest.raises(InputValidationError):
        downside_scenarios("MSFT", 100_000, "USD", 150)


def test_downside_tool_uses_budget_from_shared_context(tool_context):
    tc, _ = tool_context(budget=50_000)
    r = run_downside_scenarios("NVDA", 10, tool_context=tc)
    assert r["position_size"] == 5_000


def test_concentration_limits():
    assert concentration_check(10, "conservative")["within_limit"]
    assert not concentration_check(25, "moderate")["within_limit"]


@pytest.mark.parametrize(
    "rec,alloc,conf,tol,exp_rec,exp_alloc",
    [
        ("INVEST", 50, "high", "moderate", "INVEST", 20),
        ("INVEST", 15, "low", "moderate", "HOLD", 0),
        ("HOLD", 10, "high", "moderate", "HOLD", 0),
        ("INVEST", 0, "high", "moderate", "HOLD", 0),
        ("REJECT", 0, "low", "conservative", "REJECT", 0),
        ("INVEST", 10, "medium", "conservative", "INVEST", 10),
    ],
)
def test_policy_gate(rec, alloc, conf, tol, exp_rec, exp_alloc):
    r = investment_policy_check(rec, alloc, conf, tol)
    assert r["adjusted_recommendation"] == exp_rec
    assert r["adjusted_allocation_pct"] == exp_alloc


def test_policy_rejects_unknown_recommendation():
    with pytest.raises(InputValidationError):
        investment_policy_check("STRONG BUY", 100, "high", "aggressive")


def test_currency_exposure_notes_for_pakistani_investor_buying_us_stock():
    r = currency_exposure("NVDA", "PKR", "PK")
    assert r["currency_mismatch"]
    assert any("State Bank of Pakistan" in n for n in r["notes"])


def test_no_mismatch_for_local_investor_in_psx():
    r = currency_exposure("LUCK", "PKR", "PK")
    assert not r["currency_mismatch"]


@pytest.mark.parametrize(
    "text,amount,currency",
    [
        ("I have 5 lakh rupees and want 30% in 2 years", 500_000, "PKR"),
        ("PKR 2 crore, 18 months, 20 percent", 20_000_000, "PKR"),
        ("Mere paas 10 lac hain, 25% chahiye 3 saal mein", 1_000_000, "PKR"),
        ("I have $10,000 and want to double my money in 4 years", 10_000, "USD"),
        ("I have 50000 dollars for international stocks", 50_000, "USD"),
        ("Rs. 750,000 for local stocks", 750_000, "PKR"),
    ],
)
def test_goal_parser_amounts(text, amount, currency):
    p = parse_goal_question(text)
    assert p["amount"] == amount
    assert p["currency"] == currency


def test_goal_parser_double_converts_to_annual_rate():
    p = parse_goal_question("I want to double $10,000 in 4 years")
    assert p["target_return_pct"] == pytest.approx((2 ** 0.25 - 1) * 100, abs=0.01)
    assert p["horizon_years"] == 4


def test_goal_parser_does_not_treat_us_pronoun_as_market():
    assert "US" not in parse_goal_question("Tell us what to do with 5 lakh rupees")["markets"]


def test_return_target_bands():
    low = assess_return_target(100_000, "USD", 3, 5)
    wild = assess_return_target(500_000, "PKR", 60, 2)
    assert low["band"] == "low_risk_possible"
    assert wild["band"] == "speculative"
    assert wild["target_ending_value"] == pytest.approx(500_000 * 1.6 ** 2)


def test_return_target_validation():
    with pytest.raises(InputValidationError):
        assess_return_target(100, "USD", 150, 2)
