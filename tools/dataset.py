"""Typed access to the local, dated workshop dataset in ``data/``.

The dataset is validated with Pydantic when it is loaded, so a malformed or
hand-edited JSON file fails fast with a clear error instead of producing wrong
numbers in the middle of a live demo.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Sourced(BaseModel):
    text: str
    kind: Literal["filing_excerpt", "workshop_summary", "exchange_profile", "data_point"]
    source_id: str


class Risk(Sourced):
    category: str


class FinancialYear(BaseModel):
    fiscal_year: str
    period_start: str | None = None
    period_end: str | None = None
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    operating_cash_flow: float | None = None
    capital_expenditures: float | None = None
    diluted_eps: float | None = None
    source_id: str


class SharesOutstanding(BaseModel):
    value: float = Field(gt=0)
    as_of: str
    source_id: str
    notes: str | None = None


class MarketSnapshot(BaseModel):
    price: float = Field(gt=0)
    currency: str
    as_of: str
    market_cap: float | None = None
    pe_ttm: float | None = None
    free_float_pct: float | None = None
    source_id: str
    notes: str | None = None


class Company(BaseModel):
    ticker: str
    name: str
    market: Literal["US", "PK"]
    exchange: str
    currency: Literal["USD", "PKR"]
    sector: str
    fiscal_year_end: str
    business_description: Sourced
    financials: list[FinancialYear] = Field(min_length=2)
    shares_outstanding: SharesOutstanding | None = None
    market_snapshot: MarketSnapshot | None = None
    competitive_advantages: list[Sourced]
    risks: list[Risk]
    data_gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _years_sorted(self) -> "Company":
        labels = [f.fiscal_year for f in self.financials]
        if labels != sorted(labels):
            raise ValueError(f"{self.ticker}: financial years must be sorted ascending")
        return self

    @property
    def latest(self) -> FinancialYear:
        return self.financials[-1]


class Rate(BaseModel):
    value: float
    as_of: str
    source_id: str


class FxRate(BaseModel):
    pair: Literal["USD/PKR"]
    rate: float = Field(gt=0)
    description: str
    as_of: str
    source_id: str


class MarketContext(BaseModel):
    fx: FxRate
    rates: dict[str, Rate]


class Source(BaseModel):
    id: str
    publisher: str
    title: str
    url: str
    retrieved: str
    notes: str | None = None

    model_config = {"extra": "allow"}


class Dataset(BaseModel):
    dataset_version: str
    built_at_utc: str
    disclaimer: str
    companies: list[Company]
    market_context: MarketContext
    sources: dict[str, Source] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sources_resolve(self) -> "Dataset":
        if not self.sources:
            return self
        missing: set[str] = set()
        for c in self.companies:
            ids = {c.business_description.source_id, *(f.source_id for f in c.financials)}
            ids |= {a.source_id for a in c.competitive_advantages} | {r.source_id for r in c.risks}
            if c.market_snapshot:
                ids.add(c.market_snapshot.source_id)
            missing |= ids - self.sources.keys()
        missing |= {r.source_id for r in self.market_context.rates.values()} - self.sources.keys()
        if self.market_context.fx.source_id not in self.sources:
            missing.add(self.market_context.fx.source_id)
        if missing:
            raise ValueError(f"Dataset references unknown source ids: {sorted(missing)}")
        return self

    def company(self, ticker: str) -> Company:
        for c in self.companies:
            if c.ticker == ticker:
                return c
        raise KeyError(ticker)

    @property
    def tickers(self) -> list[str]:
        return [c.ticker for c in self.companies]


class InvestmentPolicy(BaseModel):
    """The committee's own rules. These are workshop policy choices, not market facts."""

    max_single_position_pct: dict[str, float]
    min_confidence_to_invest: Literal["low", "medium", "high"]
    target_return_bands_pp_over_risk_free: dict[str, float]
    stress_scenarios_pct: list[float]
    fx_shock_pct: float
    notes: str


@lru_cache(maxsize=1)
def load_dataset(data_dir: Path | None = None) -> Dataset:
    base = data_dir or DATA_DIR
    raw = json.loads((base / "companies.json").read_text(encoding="utf-8"))
    src = json.loads((base / "sources.json").read_text(encoding="utf-8"))
    raw["sources"] = {s["id"]: s for s in src["sources"]}
    return Dataset.model_validate(raw)


@lru_cache(maxsize=1)
def load_policy(data_dir: Path | None = None) -> InvestmentPolicy:
    base = data_dir or DATA_DIR
    return InvestmentPolicy.model_validate_json((base / "investment_policy.json").read_text(encoding="utf-8"))
