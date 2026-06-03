"""Quota/status report helpers for the Google Antigravity provider."""

from __future__ import annotations

from typing import Any, Iterable, List


def _bar(fraction: float, width: int = 20) -> str:
    pct = max(0.0, min(1.0, float(fraction or 0.0)))
    filled = int(round(pct * width))
    return "#" * filled + "-" * (width - filled)


def _pct(fraction: float) -> str:
    pct = max(0.0, min(1.0, float(fraction or 0.0)))
    return f"{int(round(pct * 100)):3d}%"


def _paid_tier(raw: dict[str, Any]) -> dict[str, Any]:
    paid = raw.get("paidTier") if isinstance(raw, dict) else {}
    return paid if isinstance(paid, dict) else {}


def _is_auth_error(exc: Exception) -> bool:
    text = str(exc)
    return (
        "401" in text
        or "UNAUTHENTICATED" in text
        or "invalid authentication credentials" in text
    )


def _append_rest_buckets(lines: List[str], buckets: Iterable[Any]) -> None:
    rows = sorted(
        list(buckets),
        key=lambda b: (
            str(getattr(b, "model_id", "") or ""),
            str(getattr(b, "token_type", "") or ""),
        ),
    )
    if not rows:
        lines.append("  Base REST quota: no buckets reported")
        return
    lines.append("")
    lines.append("Base request quota (REST retrieveUserQuota):")
    for bucket in rows:
        model = str(getattr(bucket, "model_id", "") or "(unknown)")
        token_type = str(getattr(bucket, "token_type", "") or "REQUESTS")
        remaining = float(getattr(bucket, "remaining_fraction", 0.0) or 0.0)
        reset = str(getattr(bucket, "reset_time_iso", "") or "")
        suffix = f" reset {reset}" if reset else ""
        lines.append(f"  {model:34s} [{token_type:8s}] {_bar(remaining)} {_pct(remaining)}{suffix}")


def _append_grpc_buckets(lines: List[str], buckets: Iterable[Any] | None) -> None:
    if buckets is None:
        lines.append("")
        lines.append("Antigravity extended/base quota (gRPC FetchQuotaStatus): unavailable")
        lines.append("  Reason: grpcio missing or Google rejected the token/scope for this endpoint.")
        return
    rows = sorted(
        list(buckets),
        key=lambda b: (
            str(getattr(b, "model", "") or ""),
            int(getattr(b, "quota_type", 0) or 0),
        ),
    )
    if not rows:
        lines.append("")
        lines.append("Antigravity extended/base quota (gRPC FetchQuotaStatus): no buckets reported")
        return
    lines.append("")
    lines.append("Antigravity extended/base quota (gRPC FetchQuotaStatus):")
    for bucket in rows:
        model = str(getattr(bucket, "model", "") or "(unknown)")
        quota_type = str(getattr(bucket, "quota_type_name", "") or getattr(bucket, "quota_type", ""))
        remaining = float(getattr(bucket, "remaining_fraction", 0.0) or 0.0)
        reset = str(getattr(bucket, "reset_time", "") or "")
        suffix = f" reset {reset}" if reset else ""
        lines.append(f"  {model:34s} [{quota_type:12s}] {_bar(remaining)} {_pct(remaining)}{suffix}")


def build_antigravity_quota_report(*, include_grpc: bool = True) -> str:
    from agent import google_antigravity_oauth
    from agent.google_code_assist import load_code_assist, retrieve_user_quota
    from agent.google_antigravity_adapter import (
        GoogleAntigravityClient,
        _antigravity_credit_attempts,
        _antigravity_google_one_ai_credits_mode,
    )

    creds = google_antigravity_oauth.load_credentials()
    if not creds or not getattr(creds, "access_token", ""):
        return "Antigravity OAuth token not found. Run `hermes auth add google-antigravity`."

    try:
        access_token = google_antigravity_oauth.get_valid_access_token()
    except Exception:
        access_token = creds.access_token

    model = "gemini-3.5-flash-high"
    project_id = getattr(creds, "project_id", "") or ""
    try:
        info = load_code_assist(
            access_token,
            project_id=project_id,
            user_agent_model=model,
        )
    except Exception as exc:
        if not _is_auth_error(exc):
            raise
        access_token = google_antigravity_oauth.get_valid_access_token(force_refresh=True)
        info = load_code_assist(
            access_token,
            project_id=project_id,
            user_agent_model=model,
        )
    raw = getattr(info, "raw", {}) or {}
    paid = _paid_tier(raw)

    client = GoogleAntigravityClient(api_key=access_token, model=model)
    ctx = client._ensure_project_context(access_token, model)
    credit_mode = _antigravity_google_one_ai_credits_mode()
    credit_attempts = _antigravity_credit_attempts(ctx)

    lines: List[str] = []
    lines.append("Google Antigravity quota/status")
    lines.append(f"  currentTier: {getattr(info, 'current_tier_id', '') or '(unknown)'}")
    lines.append(f"  paidTier: {paid.get('id', '') or ctx.paid_tier_id or '(none)'}")
    if paid.get("name") or ctx.paid_tier_name:
        lines.append(f"  paidTierName: {paid.get('name', '') or ctx.paid_tier_name}")
    lines.append(f"  creditRoutingMode: {credit_mode}")
    lines.append(f"  creditAttempts: {credit_attempts}")
    lines.append(
        "  note: paidTier is the Plus/Pro/Ultra plan entitlement; "
        "base quota and GOOGLE_ONE_AI routing are shown separately because "
        "overage/credit consumption is not exposed by this quota API."
    )

    try:
        try:
            rest_buckets = retrieve_user_quota(
                access_token,
                project_id=getattr(info, "cloudaicompanion_project", "") or project_id,
                user_agent_model=model,
            )
        except Exception as exc:
            if not _is_auth_error(exc):
                raise
            access_token = google_antigravity_oauth.get_valid_access_token(force_refresh=True)
            rest_buckets = retrieve_user_quota(
                access_token,
                project_id=getattr(info, "cloudaicompanion_project", "") or project_id,
                user_agent_model=model,
            )
        _append_rest_buckets(lines, rest_buckets)
    except Exception as exc:
        lines.append("")
        lines.append(f"Base REST quota lookup failed: {exc}")

    if include_grpc:
        try:
            from agent.antigravity_quota_grpc import fetch_quota_status

            grpc_buckets = fetch_quota_status(access_token, timeout=10.0)
        except Exception:
            grpc_buckets = None
        _append_grpc_buckets(lines, grpc_buckets)

    return "\n".join(lines)
