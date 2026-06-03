from types import SimpleNamespace

from agent import antigravity_quota_report as quota_report
from agent import google_antigravity_adapter as ag
from agent import google_antigravity_oauth
from agent import google_code_assist


def test_quota_report_separates_paid_plan_base_quota_and_credit_routing(monkeypatch):
    monkeypatch.setattr(
        google_antigravity_oauth,
        "load_credentials",
        lambda: SimpleNamespace(access_token="token", project_id="credential-project"),
    )
    monkeypatch.setattr(
        google_antigravity_oauth,
        "get_valid_access_token",
        lambda **kwargs: "token",
    )
    monkeypatch.setattr(
        google_code_assist,
        "load_code_assist",
        lambda *args, **kwargs: SimpleNamespace(
            current_tier_id="standard-tier",
            cloudaicompanion_project="quota-project",
            raw={
                "paidTier": {
                    "id": "g1-ultra-tier",
                    "name": "Gemini Code Assist in Google One AI Ultra",
                },
            },
        ),
    )
    monkeypatch.setattr(
        google_code_assist,
        "retrieve_user_quota",
        lambda *args, **kwargs: [
            SimpleNamespace(
                model_id="gemini-3-flash-agent",
                token_type="REQUESTS",
                remaining_fraction=0.42,
                reset_time_iso="2026-06-04T12:00:00Z",
            )
        ],
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def _ensure_project_context(self, *args, **kwargs):
            return ag.ProjectContext(
                tier_id="standard-tier",
                paid_tier_id="g1-ultra-tier",
                paid_tier_name="Gemini Code Assist in Google One AI Ultra",
                has_google_one_ai_credits=True,
            )

    monkeypatch.setattr(ag, "GoogleAntigravityClient", FakeClient)
    monkeypatch.setattr(ag, "_antigravity_google_one_ai_credits_mode", lambda: "auto")
    monkeypatch.setattr(ag, "_antigravity_credit_attempts", lambda ctx=None: [True])

    report = quota_report.build_antigravity_quota_report(include_grpc=False)

    assert "paidTier: g1-ultra-tier" in report
    assert "paidTierName: Gemini Code Assist in Google One AI Ultra" in report
    assert "creditRoutingMode: auto" in report
    assert "creditAttempts: [True]" in report
    assert "Base request quota (REST retrieveUserQuota):" in report
    assert "gemini-3-flash-agent" in report
    assert "42%" in report
    assert "paidTier is the Plus/Pro/Ultra plan entitlement" in report
    assert "base quota and GOOGLE_ONE_AI routing are shown separately" in report


def test_quota_report_handles_missing_token(monkeypatch):
    monkeypatch.setattr(google_antigravity_oauth, "load_credentials", lambda: None)

    report = quota_report.build_antigravity_quota_report(include_grpc=False)

    assert "Antigravity OAuth token not found" in report
