"""Runtime settings, loaded from environment variables (and an optional .env file).

No credentials live here. boto3 resolves AWS credentials through its standard
chain (aws login, SSO, environment, instance role).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=False)

# Amazon Nova models (first-party AWS models, no AWS Marketplace subscription needed).
# Verified as invocable in the workshop account on 2026-11-23.
DEFAULT_SUPERVISOR_MODEL = "us.amazon.nova-pro-v1:0"
DEFAULT_SPECIALIST_MODEL = "global.amazon.nova-2-lite-v1:0"


@dataclass(frozen=True)
class ModelPrice:
    """USD price per 1,000 tokens. Only used when explicitly configured."""

    input_per_1k: float
    output_per_1k: float


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


def _pricing() -> dict[str, ModelPrice]:
    raw = os.getenv("MODEL_PRICING_JSON", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    return {
        model_id: ModelPrice(float(p["input_per_1k"]), float(p["output_per_1k"]))
        for model_id, p in parsed.items()
    }


@dataclass(frozen=True)
class Settings:
    aws_region: str = "us-east-1"
    supervisor_model_id: str = DEFAULT_SUPERVISOR_MODEL
    specialist_model_id: str = DEFAULT_SPECIALIST_MODEL
    supervisor_max_tokens: int = 4000
    specialist_max_tokens: int = 2500
    connect_timeout_s: int = 10
    read_timeout_s: int = 120
    max_retries: int = 4
    run_budget_s: int = 300
    max_calls_per_specialist: int = 2
    guardrail_id: str | None = None
    guardrail_version: str = "DRAFT"
    attach_guardrail_to_models: bool = False
    pricing: dict[str, ModelPrice] = field(default_factory=dict)
    log_level: str = "INFO"
    console_tracing: bool = False
    otlp_endpoint: str | None = None
    audit_dir: Path = ROOT_DIR / "audit"
    log_dir: Path = ROOT_DIR / "logs"

    @property
    def pricing_configured(self) -> bool:
        return bool(self.pricing)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        aws_region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1",
        supervisor_model_id=os.getenv("SUPERVISOR_MODEL_ID", DEFAULT_SUPERVISOR_MODEL),
        specialist_model_id=os.getenv("SPECIALIST_MODEL_ID", DEFAULT_SPECIALIST_MODEL),
        supervisor_max_tokens=_int("SUPERVISOR_MAX_TOKENS", 4000),
        specialist_max_tokens=_int("SPECIALIST_MAX_TOKENS", 2500),
        connect_timeout_s=_int("BEDROCK_CONNECT_TIMEOUT_S", 10),
        read_timeout_s=_int("BEDROCK_READ_TIMEOUT_S", 120),
        max_retries=_int("BEDROCK_MAX_RETRIES", 4),
        run_budget_s=_int("RUN_BUDGET_S", 300),
        max_calls_per_specialist=_int("MAX_CALLS_PER_SPECIALIST", 2),
        guardrail_id=(os.getenv("GUARDRAIL_ID") or "").strip() or None,
        guardrail_version=os.getenv("GUARDRAIL_VERSION", "DRAFT"),
        attach_guardrail_to_models=_bool("ATTACH_GUARDRAIL_TO_MODELS"),
        pricing=_pricing(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        console_tracing=_bool("STRANDS_CONSOLE_TRACING"),
        otlp_endpoint=(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip() or None,
        audit_dir=Path(os.getenv("AUDIT_DIR") or ROOT_DIR / "audit"),
        log_dir=Path(os.getenv("LOG_DIR") or ROOT_DIR / "logs"),
    )
