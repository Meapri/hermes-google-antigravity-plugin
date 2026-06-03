import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_patch_module():
    path = Path(__file__).resolve().parents[2] / "patches" / "antigravity_provider_patch.py"
    spec = importlib.util.spec_from_file_location("test_antigravity_provider_patch", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_auth_add_google_antigravity_runs_browser_oauth(monkeypatch, capsys):
    patch = _load_patch_module()

    auth_mod = ModuleType("hermes_cli.auth")
    auth_mod.PROVIDER_REGISTRY = {}

    class ProviderConfig:
        def __init__(self, id, name, auth_type, inference_base_url=""):
            self.id = id
            self.name = name
            self.auth_type = auth_type
            self.inference_base_url = inference_base_url

    auth_mod.ProviderConfig = ProviderConfig

    calls = []

    class FakePool:
        def __init__(self):
            self._entries = []

        def entries(self):
            return list(self._entries)

        def add_entry(self, entry):
            self._entries.append(entry)

    pool = FakePool()
    auth_commands = ModuleType("hermes_cli.auth_commands")
    auth_commands._OAUTH_CAPABLE_PROVIDERS = set()
    auth_commands.AUTH_TYPE_OAUTH = "oauth"
    auth_commands.SOURCE_MANUAL = "manual"
    auth_commands.uuid = __import__("uuid")
    auth_commands.PooledCredential = lambda **kwargs: SimpleNamespace(**kwargs)
    auth_commands.load_pool = lambda provider: pool
    auth_commands._oauth_default_label = lambda provider, count: f"{provider}-oauth-{count}"

    def original_auth_add(args):
        calls.append(("original", args.provider))

    auth_commands.auth_add_command = original_auth_add

    hermes_cli = ModuleType("hermes_cli")
    hermes_cli.auth = auth_mod
    hermes_cli.auth_commands = auth_commands
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth_commands", auth_commands)

    agent_mod = ModuleType("agent")
    oauth_mod = ModuleType("agent.google_antigravity_oauth")

    def fake_login():
        calls.append(("login", "google-antigravity"))
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at_ms": 1234,
            "email": "user@example.com",
        }

    oauth_mod.run_antigravity_oauth_login_pure = fake_login
    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.google_antigravity_oauth", oauth_mod)

    assert patch._patch_auth_registry()
    assert "google-antigravity" in auth_commands._OAUTH_CAPABLE_PROVIDERS

    auth_commands.auth_add_command(SimpleNamespace(provider="google-antigravity", label=""))

    assert calls == [("login", "google-antigravity")]
    entries = pool.entries()
    assert len(entries) == 1
    assert entries[0].provider == "google-antigravity"
    assert entries[0].auth_type == "oauth"
    assert entries[0].source == "manual:google_antigravity_pkce"
    assert entries[0].access_token == "access"
    assert entries[0].refresh_token == "refresh"
    assert entries[0].base_url == "cloudcode-pa://antigravity"
    assert 'Added google-antigravity OAuth credential #1: "user@example.com"' in capsys.readouterr().out


def test_auth_add_non_antigravity_delegates(monkeypatch):
    patch = _load_patch_module()

    auth_mod = ModuleType("hermes_cli.auth")
    auth_mod.PROVIDER_REGISTRY = {}

    class ProviderConfig:
        def __init__(self, id, name, auth_type, inference_base_url=""):
            self.id = id
            self.name = name
            self.auth_type = auth_type
            self.inference_base_url = inference_base_url

    auth_mod.ProviderConfig = ProviderConfig
    calls = []
    auth_commands = ModuleType("hermes_cli.auth_commands")
    auth_commands._OAUTH_CAPABLE_PROVIDERS = set()

    def original_auth_add(args):
        calls.append(("original", args.provider))

    auth_commands.auth_add_command = original_auth_add
    hermes_cli = ModuleType("hermes_cli")
    hermes_cli.auth = auth_mod
    hermes_cli.auth_commands = auth_commands
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth_commands", auth_commands)

    assert patch._patch_auth_registry()
    auth_commands.auth_add_command(SimpleNamespace(provider="openai-api"))

    assert calls == [("original", "openai-api")]


def test_model_flow_google_antigravity_opens_login_when_auth_missing(monkeypatch, capsys):
    patch = _load_patch_module()

    calls = []
    auth_mod = ModuleType("hermes_cli.auth")
    auth_state = {"ready": False}

    def resolve_credentials():
        if not auth_state["ready"]:
            raise RuntimeError("missing token")
        return {"api_key": "access", "email": "user@example.com"}

    def prompt_model_selection(models, current_model=""):
        calls.append(("prompt", tuple(models), current_model))
        return "gemini-3.5-flash-high"

    def save_model_choice(model):
        calls.append(("save", model))

    def update_config(provider, base_url):
        calls.append(("config", provider, base_url))

    auth_mod.resolve_antigravity_oauth_runtime_credentials = resolve_credentials
    auth_mod._prompt_model_selection = prompt_model_selection
    auth_mod._save_model_choice = save_model_choice
    auth_mod._update_config_for_provider = update_config

    hermes_cli = ModuleType("hermes_cli")
    hermes_cli.auth = auth_mod
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", auth_mod)

    agent_mod = ModuleType("agent")
    oauth_mod = ModuleType("agent.google_antigravity_oauth")

    def fake_login():
        calls.append(("login", "google-antigravity"))
        auth_state["ready"] = True
        return {"access_token": "access", "email": "user@example.com"}

    oauth_mod.run_antigravity_oauth_login_pure = fake_login
    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.google_antigravity_oauth", oauth_mod)

    patch._model_flow_google_antigravity({}, current_model="")

    assert calls[0] == ("login", "google-antigravity")
    assert calls[1][0] == "prompt"
    assert calls[2:] == [
        ("save", "gemini-3.5-flash-high"),
        ("config", "google-antigravity", "cloudcode-pa://antigravity"),
    ]
    out = capsys.readouterr().out
    assert "Opening Google login for google-antigravity" in out
    assert "Authenticated as: user@example.com" in out
