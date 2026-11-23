"""Financial calculations are checked against hand-computed values from the source filings."""

from __future__ import annotations

import pytest

from committee.schemas import PriceInput
from tools.dataset import load_dataset
from tools.financial_calculations import (
    calculate_valuation_ratios,
    compare,
    profit_margins,
    revenue_growth,
    valuation_ratios,
)


def test_nvda_revenue_growth_matches_hand_calculation():
    # NVIDIA 10-K: revenue FY2025 = 130,497M, FY2026 = 215,938M; FY2024 = 60,922M
    g = revenue_growth("NVDA")
    assert g["latest_yoy_growth_pct"] == pytest.approx((215_938 / 130_497 - 1) * 100, abs=0.01)
    assert g["cagr_pct"] == pytest.approx(((215_938 / 60_922) ** 0.5 - 1) * 100, abs=0.01)
    assert g["years"][0]["yoy_growth_pct"] is None


def test_amzn_free_cash_flow_is_ocf_minus_capex():
    m = profit_margins("AMZN")["latest"]
    # Amazon FY2025: OCF 139,514M, purchases of property and equipment 131,819M
    assert m["free_cash_flow"] == pytest.approx(139_514e6 - 131_819e6)
    assert m["operating_margin_pct"] == pytest.approx(79_975 / 716_924 * 100, abs=0.01)
    assert m["net_margin_pct"] == pytest.approx(77_670 / 716_924 * 100, abs=0.01)


def test_msft_margins():
    m = profit_margins("MSFT")["latest"]
    assert m["operating_margin_pct"] == pytest.approx(155_237 / 331_839 * 100, abs=0.01)
    assert m["capex_to_revenue_pct"] == pytest.approx(115_948 / 331_839 * 100, abs=0.01)


def test_psx_company_reports_unavailable_metrics_instead_of_guessing():
    m = profit_margins("SYS")
    assert m["latest"]["operating_margin_pct"] is None
    assert m["latest"]["free_cash_flow"] is None
    assert "operating_margin_pct" in m["unavailable_metrics"]
    assert m["latest"]["net_margin_pct"] == pytest.approx(8_019_014 / 44_230_607 * 100, abs=0.01)


def test_valuation_without_price_is_insufficient_data():
    v = valuation_ratios("NVDA")
    assert v["status"] == "insufficient_data"
    assert "share_price" in v["missing"]


def test_valuation_with_user_price():
    price = PriceInput(price=150.0, as_of="2026-11-20", source="test fixture")
    v = valuation_ratios("NVDA", price)
    shares = load_dataset().company("NVDA").shares_outstanding.value
    assert v["status"] == "ok"
    assert v["market_cap"] == pytest.approx(150.0 * shares)
    assert v["pe_on_latest_fy_eps"] == pytest.approx(150.0 / 4.90, abs=0.01)
    assert v["price_origin"] == "user_supplied"


def test_psx_valuation_uses_dataset_snapshot():
    v = valuation_ratios("OGDC")
    c = load_dataset().company("OGDC")
    assert v["status"] == "ok"
    assert v["price_origin"] == "dataset_snapshot"
    assert v["pe_on_latest_fy_eps"] == pytest.approx(c.market_snapshot.price / c.latest.diluted_eps, abs=0.01)
    assert v["exchange_reported_pe_ttm"] == c.market_snapshot.pe_ttm


def test_compare_converts_pkr_to_usd_with_dataset_rate():
    result = compare(["NVDA", "SYS"])
    fx = load_dataset().market_context.fx.rate
    sys_row = next(r for r in result["companies"] if r["ticker"] == "SYS")
    assert sys_row["revenue_usd"] == pytest.approx(sys_row["revenue_native"] / fx, rel=1e-6)
    assert result["fx_used"]["rate"] == fx


def test_compare_rejects_more_than_three():
    with pytest.raises(ValueError):
        compare(["NVDA", "MSFT", "AMZN", "SYS"])


def test_valuation_tool_reads_price_from_shared_context(tool_context):
    tc, _ = tool_context(price_inputs={"MSFT": {"price": 400, "as_of": "2026-11-20", "source": "test"}}, tickers=["MSFT"])
    v = calculate_valuation_ratios("MSFT", tool_context=tc)
    assert v["status"] == "ok"
    assert v["pe_on_latest_fy_eps"] == pytest.approx(400 / 17.95, abs=0.01)
