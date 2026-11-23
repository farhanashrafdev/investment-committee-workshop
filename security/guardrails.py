"""Amazon Bedrock Guardrails integration (probabilistic layer).

The workshop guardrail uses two policies:
- PROMPT_ATTACK content filter on inputs (detects jailbreak / injection attempts)
- Sensitive-information filter that blocks AWS access keys, secret keys and passwords

We call the standalone ApplyGuardrail API on untrusted documents and free-text
questions, so the check runs at a known point regardless of which model is used.
Guardrails are a classifier: they reduce risk but do not guarantee that every
prompt injection is caught. The deterministic controls (no credential tools,
no trade tools, tool allowlists, policy gate, output redaction) still apply.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import BotoCoreError, ClientError

from committee.config import Settings

logger = logging.getLogger("committee.guardrails")

GUARDRAIL_NAME = "investment-committee-workshop"


@dataclass
class GuardrailVerdict:
    evaluated: bool
    action: str = "NOT_EVALUATED"
    reasons: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def intervened(self) -> bool:
        return self.action == "GUARDRAIL_INTERVENED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reasons(assessments: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for a in assessments or []:
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("action") not in (None, "NONE"):
                out.append(f"content filter {f.get('type')} (confidence {f.get('confidence')}) -> {f.get('action')}")
        for e in a.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            if e.get("action") not in (None, "NONE"):
                out.append(f"sensitive info {e.get('type')} -> {e.get('action')}")
        for t in a.get("topicPolicy", {}).get("topics", []):
            if t.get("action") not in (None, "NONE"):
                out.append(f"denied topic {t.get('name')} -> {t.get('action')}")
    return out


class GuardrailChecker:
    def __init__(self, settings: Settings) -> None:
        self.guardrail_id = settings.guardrail_id
        self.version = settings.guardrail_version
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            config=BotocoreConfig(connect_timeout=5, read_timeout=20, retries={"max_attempts": 3, "mode": "standard"}),
        )

    def check(self, text: str, source: str = "INPUT") -> GuardrailVerdict:
        if not self.guardrail_id:
            return GuardrailVerdict(evaluated=False, reasons=["Guardrail not configured (set GUARDRAIL_ID)"])
        try:
            resp = self._client.apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.version,
                source=source,
                content=[{"text": {"text": text[:20_000]}}],
            )
            return GuardrailVerdict(evaluated=True, action=resp.get("action", "NONE"), reasons=_reasons(resp.get("assessments", [])))
        except (ClientError, BotoCoreError) as exc:
            logger.warning("ApplyGuardrail failed: %s", exc)
            return GuardrailVerdict(evaluated=False, action="ERROR", error=str(exc)[:300])


def build_checker(settings: Settings) -> GuardrailChecker | None:
    return GuardrailChecker(settings) if settings.guardrail_id else None


def create_workshop_guardrail(region: str) -> dict[str, str]:
    """Create (or reuse) the workshop guardrail and return its id and version."""
    client = boto3.client("bedrock", region_name=region)
    for g in client.list_guardrails(maxResults=100).get("guardrails", []):
        if g["name"] == GUARDRAIL_NAME:
            return {"guardrail_id": g["id"], "version": "DRAFT", "status": g.get("status", ""), "created": "false"}
    resp = client.create_guardrail(
        name=GUARDRAIL_NAME,
        description="Investment Committee workshop: prompt-attack detection and credential blocking.",
        contentPolicyConfig={
            "filtersConfig": [
                {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
            ]
        },
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
                {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
                {"type": "PASSWORD", "action": "BLOCK"},
            ]
        },
        blockedInputMessaging="This input was blocked by the Investment Committee guardrail.",
        blockedOutputsMessaging="This response was blocked by the Investment Committee guardrail.",
        tags=[{"key": "project", "value": "investment-committee-workshop"}],
    )
    return {"guardrail_id": resp["guardrailId"], "version": resp["version"], "status": "CREATING", "created": "true"}
