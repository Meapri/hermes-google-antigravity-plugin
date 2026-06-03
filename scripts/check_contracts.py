#!/usr/bin/env python3
"""Hermes Antigravity installation and compatibility checks."""

from __future__ import annotations

import argparse
import filecmp
import importlib
import os
import site
import sys
from pathlib import Path


HOOK_NEEDLES = {
    "hermes_cli.auth": "hermes_cli.auth",
    "hermes_cli.auth_commands": "hermes_cli.auth_commands",
    "hermes_cli.providers": "hermes_cli.providers",
    "hermes_cli.commands": "hermes_cli.commands",
    "cli": '"cli"',
    "agent.auxiliary_client": "agent.auxiliary_client",
    "hermes_cli.runtime_provider": "hermes_cli.runtime_provider",
    "hermes_cli.main": "hermes_cli.main",
    "hermes_cli.model_switch": "hermes_cli.model_switch",
    "api.config": "api.config",
}

CRITICAL_PATCHES = {
    "providers",
    "auth_registry",
    "commands",
    "runtime_provider",
    "auxiliary_client",
}

NONCRITICAL_PATCHES = {
    "agent_runtime",
    "models_module",
    "model_picker",
    "model_switch_picker",
    "cli_agyquota",
    "webui_config",
}


def color(enabled: bool, code: str, text: str) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


class Reporter:
    def __init__(self, *, color_enabled: bool) -> None:
        self.color_enabled = color_enabled
        self.failed = False
        self.warnings = 0

    def ok(self, message: str) -> None:
        print(color(self.color_enabled, "0;32", f"[OK] {message}"))

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(color(self.color_enabled, "1;33", f"[WARN] {message}"))

    def fail(self, message: str) -> None:
        self.failed = True
        print(color(self.color_enabled, "0;31", f"[FAIL] {message}"))


def find_venv(hermes_agent_dir: Path) -> Path | None:
    env_venv = os.environ.get("HERMES_VENV")
    if env_venv:
        path = Path(env_venv).expanduser()
        if path.is_dir():
            return path
    for name in ("venv", ".venv"):
        path = hermes_agent_dir / name
        if path.is_dir():
            return path
    return None


def sitecustomize_path(venv_dir: Path) -> Path:
    candidates = sorted(venv_dir.glob("lib/python*/site-packages/sitecustomize.py"))
    if candidates:
        return candidates[0]
    return Path(site.getsitepackages()[0]) / "sitecustomize.py"


def compare_file(reporter: Reporter, installed: Path, repo: Path, *, required: bool = True) -> None:
    if not installed.exists():
        if required:
            reporter.fail(f"missing installed file: {installed}")
        else:
            reporter.warn(f"missing optional installed file: {installed}")
        return
    if not repo.exists():
        reporter.fail(f"missing repo source file: {repo}")
        return
    if filecmp.cmp(installed, repo, shallow=False):
        reporter.ok(f"{installed.name} matches repo")
    else:
        reporter.fail(f"{installed} differs from repo source {repo}")


def check_files(reporter: Reporter, repo_root: Path, hermes_home: Path, hermes_agent_dir: Path) -> None:
    pairs = [
        (hermes_home / "patches/antigravity_provider_patch.py", repo_root / "patches/antigravity_provider_patch.py"),
        (hermes_agent_dir / "agent/google_antigravity_adapter.py", repo_root / "agent/google_antigravity_adapter.py"),
        (hermes_agent_dir / "agent/google_antigravity_oauth.py", repo_root / "agent/google_antigravity_oauth.py"),
        (hermes_agent_dir / "agent/antigravity_quota_grpc.py", repo_root / "agent/antigravity_quota_grpc.py"),
        (hermes_agent_dir / "agent/antigravity_quota_report.py", repo_root / "agent/antigravity_quota_report.py"),
        (hermes_agent_dir / "agent/antigravity_stream_grpc.py", repo_root / "agent/antigravity_stream_grpc.py"),
        (
            hermes_home / "plugins/model-providers/google-antigravity/__init__.py",
            repo_root / "plugins/model-providers/google-antigravity/__init__.py",
        ),
        (
            hermes_home / "plugins/model-providers/google-antigravity/plugin.yaml",
            repo_root / "plugins/model-providers/google-antigravity/plugin.yaml",
        ),
    ]
    for installed, repo in pairs:
        compare_file(reporter, installed, repo)


def check_sitecustomize(reporter: Reporter, repo_root: Path, venv_dir: Path) -> None:
    installed = sitecustomize_path(venv_dir)
    repo = repo_root / "scripts/sitecustomize_hook.py"
    compare_file(reporter, installed, repo)
    if not installed.exists():
        return
    text = installed.read_text(encoding="utf-8", errors="replace")
    if "# hermes-antigravity managed" not in text:
        reporter.fail("sitecustomize.py is missing hermes-antigravity marker")
    else:
        reporter.ok("sitecustomize.py has hermes-antigravity marker")
    for label, needle in HOOK_NEEDLES.items():
        if needle in text:
            reporter.ok(f"sitecustomize hook present: {label}")
        else:
            reporter.fail(f"sitecustomize hook missing: {label}")


def check_stale_antigravity_guidance(reporter: Reporter, hermes_home: Path) -> None:
    installed_patch = hermes_home / "patches/antigravity_provider_patch.py"
    if not installed_patch.exists():
        return
    text = installed_patch.read_text(encoding="utf-8", errors="replace")
    stale_needles = [
        "Do not run `hermes auth add google-antigravity`",
        "this provider reuses the agy token.",
    ]
    if any(needle in text for needle in stale_needles):
        reporter.fail(
            "installed patch still contains stale agy-only auth guidance"
        )
    else:
        reporter.ok("installed patch has Google login auth guidance")


def check_import_contracts(
    reporter: Reporter,
    hermes_home: Path,
    hermes_agent_dir: Path,
) -> None:
    patches_dir = hermes_home / "patches"
    for path in (patches_dir, hermes_agent_dir):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    try:
        patch = importlib.import_module("antigravity_provider_patch")
    except Exception as exc:  # pragma: no cover - exercised by shell checks
        reporter.fail(f"cannot import antigravity_provider_patch: {type(exc).__name__}: {exc}")
        return

    apply = getattr(patch, "apply", None)
    if not callable(apply):
        reporter.fail("antigravity_provider_patch.apply is missing")
        return

    try:
        results = apply()
    except Exception as exc:
        reporter.fail(f"antigravity_provider_patch.apply failed: {type(exc).__name__}: {exc}")
        return

    if not isinstance(results, dict):
        reporter.fail("antigravity_provider_patch.apply did not return a result dict")
        return

    for name in sorted(CRITICAL_PATCHES):
        if results.get(name) is True:
            reporter.ok(f"critical patch applied: {name}")
        else:
            reporter.fail(f"critical patch failed: {name}")

    for name in sorted(NONCRITICAL_PATCHES):
        if results.get(name) is True:
            reporter.ok(f"noncritical patch applied: {name}")
        else:
            reporter.warn(f"noncritical patch skipped: {name}")

    try:
        auth = importlib.import_module("hermes_cli.auth")
        registry = getattr(auth, "PROVIDER_REGISTRY", {})
        if "google-antigravity" in registry:
            reporter.ok("google-antigravity registered in PROVIDER_REGISTRY")
        else:
            reporter.fail("google-antigravity missing from PROVIDER_REGISTRY")
        if callable(getattr(auth, "get_auth_status", None)):
            reporter.ok("hermes_cli.auth.get_auth_status is callable")
        else:
            reporter.warn("hermes_cli.auth.get_auth_status is unavailable")
    except Exception as exc:
        reporter.fail(f"cannot inspect hermes_cli.auth: {type(exc).__name__}: {exc}")

    try:
        runtime = importlib.import_module("hermes_cli.runtime_provider")
        if getattr(runtime, "_antigravity_runtime_patched", False):
            reporter.ok("runtime provider resolver is patched")
        else:
            reporter.fail("runtime provider resolver is not patched")
    except Exception as exc:
        reporter.fail(f"cannot inspect hermes_cli.runtime_provider: {type(exc).__name__}: {exc}")

    try:
        commands = importlib.import_module("hermes_cli.commands")
        resolve = getattr(commands, "resolve_command", None)
        if callable(resolve) and getattr(resolve("agyquota"), "name", "") == "agyquota":
            reporter.ok("/agyquota command metadata is registered")
        else:
            reporter.fail("/agyquota command metadata is not registered")
    except Exception as exc:
        reporter.fail(f"cannot inspect hermes_cli.commands: {type(exc).__name__}: {exc}")

    try:
        auth_commands = importlib.import_module("hermes_cli.auth_commands")
        capable = getattr(auth_commands, "_OAUTH_CAPABLE_PROVIDERS", set())
        if "google-antigravity" in capable:
            reporter.ok("google-antigravity is OAuth-capable in auth add")
        else:
            reporter.fail("google-antigravity missing from auth add OAuth-capable providers")
        if getattr(auth_commands, "_antigravity_auth_add_patched", False):
            reporter.ok("hermes auth add google-antigravity is patched")
        else:
            reporter.fail("hermes auth add google-antigravity is not patched")
    except Exception as exc:
        reporter.fail(f"cannot inspect hermes_cli.auth_commands: {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", "~/.hermes"))
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    hermes_home = Path(args.hermes_home).expanduser().resolve()
    hermes_agent_dir = Path(os.environ.get("HERMES_AGENT_DIR", hermes_home / "hermes-agent")).expanduser().resolve()
    reporter = Reporter(color_enabled=not args.no_color and sys.stdout.isatty())

    if not hermes_agent_dir.is_dir():
        reporter.fail(f"hermes-agent not found: {hermes_agent_dir}")
        return 1
    reporter.ok(f"hermes-agent found: {hermes_agent_dir}")

    venv_dir = find_venv(hermes_agent_dir)
    if venv_dir is None:
        reporter.fail("Hermes virtualenv not found")
        return 1
    reporter.ok(f"Hermes virtualenv found: {venv_dir}")

    check_files(reporter, repo_root, hermes_home, hermes_agent_dir)
    check_sitecustomize(reporter, repo_root, venv_dir)
    check_stale_antigravity_guidance(reporter, hermes_home)
    check_import_contracts(reporter, hermes_home, hermes_agent_dir)

    if reporter.failed:
        print("\nRepair with: ./scripts/repair.sh")
        return 1
    if reporter.warnings:
        print(f"\nContract check passed with {reporter.warnings} warning(s).")
        return 0
    print("\nContract check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
