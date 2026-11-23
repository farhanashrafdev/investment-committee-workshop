"""The dataset must be internally consistent and fully sourced."""

from __future__ import annotations

import json

from tools.dataset import DATA_DIR, load_dataset, load_policy


def test_dataset_loads_and_every_source_id_resolves():
    ds = load_dataset()  # Dataset validator raises on unknown source ids
    assert {"NVDA", "MSFT", "AMZN", "SYS", "OGDC", "LUCK"} <= set(ds.tickers)


def test_every_source_has_url_and_retrieval_date():
    for s in load_dataset().sources.values():
        assert s.url.startswith("https://")
        assert len(s.retrieved) == 10


def test_currencies_by_market():
    for c in load_dataset().companies:
        assert c.currency == ("USD" if c.market == "US" else "PKR")


def test_revenue_positive_and_years_ascending():
    for c in load_dataset().companies:
        labels = [f.fiscal_year for f in c.financials]
        assert labels == sorted(labels)
        assert all(f.revenue and f.revenue > 0 for f in c.financials)


def test_us_figures_match_sec_10k_spot_checks():
    ds = load_dataset()
    assert ds.company("NVDA").latest.revenue == 215_938_000_000
    assert ds.company("MSFT").latest.net_income == 133_749_000_000
    assert ds.company("AMZN").latest.operating_cash_flow == 139_514_000_000


def test_workshop_summaries_contain_no_figures():
    """Qualitative workshop text must not introduce numbers that are not in the sourced data."""
    import re

    for c in load_dataset().companies:
        for item in [*c.competitive_advantages, *c.risks]:
            if item.kind == "workshop_summary":
                text = re.sub(r"\b10-K\b", "", item.text)
                assert not re.search(r"\d", text), f"{c.ticker}: {item.text}"


def test_untrusted_documents_are_marked_synthetic():
    index = json.loads((DATA_DIR / "documents" / "index.json").read_text(encoding="utf-8"))
    for d in index["documents"]:
        assert d["synthetic"] is True
        text = (DATA_DIR / "documents" / d["file"]).read_text(encoding="utf-8")
        assert text.startswith("SYNTHETIC WORKSHOP TEST DOCUMENT")


def test_policy_limits_increase_with_risk_tolerance():
    p = load_policy().max_single_position_pct
    assert p["conservative"] < p["moderate"] < p["aggressive"]
