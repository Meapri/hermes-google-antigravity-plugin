from importlib import util
from pathlib import Path


def _load_profile_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "model-providers"
        / "google-antigravity"
        / "__init__.py"
    )
    spec = util.spec_from_file_location("_test_google_antigravity_profile", path)
    module = util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_antigravity_profile_forwards_stable_session_id():
    module = _load_profile_module()

    body = module.google_antigravity.build_extra_body(
        session_id="session-123",
        model="claude-opus-4-6-thinking",
    )

    assert body["session_id"] == "session-123"


def test_antigravity_profile_keeps_gemini_pro_thinking_tiers_with_session_id():
    module = _load_profile_module()

    body = module.google_antigravity.build_extra_body(
        session_id="session-123",
        model="gemini-3.1-pro-high",
    )

    assert body == {
        "session_id": "session-123",
        "thinking_config": {"thinkingLevel": "high"},
    }


def test_antigravity_profile_accepts_google_prefixed_gemini_tiers():
    module = _load_profile_module()

    body = module.google_antigravity.build_extra_body(
        session_id="session-123",
        model="google/gemini-3.1-pro-low",
    )

    assert body == {
        "session_id": "session-123",
        "thinking_config": {"thinkingLevel": "low"},
    }
