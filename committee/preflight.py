"""Preflight checks: Python, AWS credentials, Bedrock model access, guardrail.

Credentials are never printed. The account ID is masked to its last 4 digits.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from committee.config import Settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def explain_error(exc: BaseException) -> str:
    """Turn common AWS/Bedrock failures into an actionable sentence."""
    text = str(exc)
    if isinstance(exc, NoCredentialsError) or "Unable to locate credentials" in text:
        return "No AWS credentials found. Run `aws login` (or `aws configure sso`) and retry, or use Mock mode."
    if "botocore[crt]" in text:
        return "Credentials from `aws login` need the CRT extra: pip install \"botocore[crt]\"."
    if "INVALID_PAYMENT_INSTRUMENT" in text:
        return "Bedrock refused the model because the AWS account has no valid payment method for its Marketplace subscription. Add one in Billing, wait 5 minutes, or use an Amazon Nova model."
    if "is not available for this account" in text or "don't have access to the model" in text:
        return "This model is not enabled for your account. Pick another model ID in .env (see workshop/troubleshooting.md)."
    if "on-demand throughput isn" in text:
        return "This model needs an inference profile ID (prefix us., eu. or global.)."
    if "provided model identifier is invalid" in text:
        return "Invalid model ID for this Region. Check SUPERVISOR_MODEL_ID / SPECIALIST_MODEL_ID and AWS_REGION."
    if "ThrottlingException" in text or "Too many requests" in text:
        return "Bedrock is throttling requests. Wait a minute, reduce parallel runs, or switch to Mock mode for the demo."
    if "ExpiredToken" in text or "expired" in text.lower() and "token" in text.lower():
        return "Your AWS session expired. Run `aws login` again."
    if "AccessDenied" in text:
        return "Access denied by IAM. Attach the policy in iam/bedrock-workshop-policy.json to your user or role."
    if "Read timeout" in text or "ReadTimeout" in text:
        return "Bedrock did not respond in time. Retry, or raise BEDROCK_READ_TIMEOUT_S."
    if "Could not connect" in text or "EndpointConnectionError" in text:
        return "Cannot reach AWS. Check your network or proxy."
    return text[:300]


def _client(service: str, settings: Settings):
    return boto3.client(service, region_name=settings.aws_region,
                        config=BotocoreConfig(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2, "mode": "standard"}))


def check_python() -> Check:
    ok = sys.version_info >= (3, 11)
    return Check("Python >= 3.11", ok, sys.version.split()[0], "" if ok else "Install Python 3.11 or newer.")


def check_credentials(settings: Settings) -> Check:
    try:
        ident = _client("sts", settings).get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        return Check("AWS credentials", False, "not available", explain_error(exc))
    arn = ident["Arn"]
    principal = "root user" if arn.endswith(":root") else arn.split(":")[-1].split("/")[0]
    detail = f"account ****{ident['Account'][-4:]}, principal type: {principal}, region {settings.aws_region}"
    hint = "You are signed in as the account root user. Use an IAM user or role with iam/bedrock-workshop-policy.json instead." if principal == "root user" else ""
    return Check("AWS credentials", True, detail, hint)


def check_model(settings: Settings, model_id: str, label: str) -> Check:
    try:
        resp = _client("bedrock-runtime", settings).converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with the single word OK."}]}],
            inferenceConfig={"maxTokens": 5, "temperature": 0},
        )
        u = resp.get("usage", {})
        return Check(f"Bedrock model ({label})", True, f"{model_id} responded ({u.get('inputTokens', 0)} in / {u.get('outputTokens', 0)} out tokens)")
    except (ClientError, BotoCoreError, NoCredentialsError) as exc:
        return Check(f"Bedrock model ({label})", False, model_id, explain_error(exc))


def check_guardrail(settings: Settings) -> Check:
    if not settings.guardrail_id:
        return Check("Bedrock guardrail", True, "not configured (optional)", "Run `python main.py create-guardrail` to enable the probabilistic layer.")
    try:
        g = _client("bedrock", settings).get_guardrail(guardrailIdentifier=settings.guardrail_id, guardrailVersion=settings.guardrail_version)
        return Check("Bedrock guardrail", g.get("status") == "READY", f"{g['name']} v{g['version']} status {g.get('status')}")
    except (ClientError, BotoCoreError) as exc:
        return Check("Bedrock guardrail", False, settings.guardrail_id, explain_error(exc))


def run_preflight(settings: Settings, *, invoke_models: bool = True) -> list[Check]:
    checks = [check_python(), check_credentials(settings)]
    if checks[-1].ok and invoke_models:
        checks.append(check_model(settings, settings.supervisor_model_id, "supervisor"))
        if settings.specialist_model_id != settings.supervisor_model_id:
            checks.append(check_model(settings, settings.specialist_model_id, "specialists"))
    if checks[1].ok:
        checks.append(check_guardrail(settings))
    return checks
