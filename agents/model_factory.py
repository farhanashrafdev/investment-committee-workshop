"""Builds Strands BedrockModel instances with explicit timeouts and retries."""

from __future__ import annotations

from typing import Literal

from botocore.config import Config as BotocoreConfig
from strands.models import BedrockModel

from committee.config import Settings


def build_model(role: Literal["supervisor", "specialist"], settings: Settings) -> BedrockModel:
    model_id = settings.supervisor_model_id if role == "supervisor" else settings.specialist_model_id
    client_config = BotocoreConfig(
        connect_timeout=settings.connect_timeout_s,
        read_timeout=settings.read_timeout_s,
        # "adaptive" retry mode backs off on throttling (common in shared workshop accounts)
        retries={"max_attempts": settings.max_retries, "mode": "adaptive"},
    )
    kwargs: dict = dict(
        model_id=model_id,
        region_name=settings.aws_region,
        max_tokens=settings.supervisor_max_tokens if role == "supervisor" else settings.specialist_max_tokens,
        temperature=0.0,  # repeatable demos; analysis should not be creative
        boto_client_config=client_config,
    )
    if settings.guardrail_id and settings.attach_guardrail_to_models:
        kwargs.update(
            guardrail_id=settings.guardrail_id,
            guardrail_version=settings.guardrail_version,
            guardrail_trace="enabled",
            guardrail_latest_message=True,
        )
    return BedrockModel(**kwargs)
