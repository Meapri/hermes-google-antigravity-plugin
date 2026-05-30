"""
Antigravity Provider Patch for Hermes Agent.

This module monkey-patches Hermes core modules at runtime to register
``google-antigravity`` as a first-class provider.  No source files are
modified — all injections happen through import hooks.

To use: install via sitecustomize.py or import directly:
    import antigravity_provider_patch
    antigravity_provider_patch.apply()
"""
from __future__ import annotations

import inspect
import sys
import logging

logger = logging.getLogger(__name__)

_patched = False
_patch_results: dict[str, bool] = {}


def _verify_signature(fn, expected_params: list[str]) -> bool:
    """Return True if *fn* is callable and has all *expected_params*."""
    if not callable(fn):
        return False
    try:
        sig = inspect.signature(fn)
        return all(p in sig.parameters for p in expected_params)
    except (TypeError, ValueError):
        return False


def _patch_providers() -> bool:
    """Inject google-antigravity into HERMES_OVERLAYS.

    Returns False if the Hermes providers API is incompatible.
    """
    try:
        from hermes_cli.providers import HermesOverlay, HERMES_OVERLAYS
    except ImportError:
        logger.warning("[antigravity_patch] providers module unavailable")
        return False

    if not isinstance(HERMES_OVERLAYS, dict):
        return False

    # Verify HermesOverlay constructor accepts the fields we use
    try:
        sig = inspect.signature(HermesOverlay)
        required = {"transport", "auth_type"}
        if not required.issubset(sig.parameters):
            logger.warning(
                "[antigravity_patch] HermesOverlay signature changed "
                "(expected %s, got %s)", required, set(sig.parameters)
            )
            return False
    except (TypeError, ValueError):
        return False

    if "google-antigravity" not in HERMES_OVERLAYS:
        HERMES_OVERLAYS["google-antigravity"] = HermesOverlay(
            transport="openai_chat",
            auth_type="oauth_external",
            base_url_override="cloudcode-pa://antigravity",
        )
        logger.info("[antigravity_patch] injected into HERMES_OVERLAYS")
    return True


def _patch_auth_registry() -> bool:
    """Inject google-antigravity into PROVIDER_REGISTRY and aliases.

    Returns False if the Hermes auth API is incompatible.
    """
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig
    except ImportError:
        logger.warning("[antigravity_patch] auth module unavailable")
        return False

    if not isinstance(PROVIDER_REGISTRY, dict):
        return False

    # Verify ProviderConfig fields
    try:
        sig = inspect.signature(ProviderConfig)
        required = {"id", "name", "auth_type"}
        if not required.issubset(sig.parameters):
            logger.warning(
                "[antigravity_patch] ProviderConfig signature changed "
                "(expected %s, got %s)", required, set(sig.parameters)
            )
            return False
    except (TypeError, ValueError):
        return False

    if "google-antigravity" not in PROVIDER_REGISTRY:
        PROVIDER_REGISTRY["google-antigravity"] = ProviderConfig(
            id="google-antigravity",
            name="Google Antigravity (OAuth)",
            auth_type="oauth_external",
            inference_base_url="cloudcode-pa://antigravity",
        )
        logger.info("[antigravity_patch] injected into PROVIDER_REGISTRY")

    # Add resolve function
    import hermes_cli.auth as auth_mod

    if not hasattr(auth_mod, "resolve_antigravity_oauth_runtime_credentials"):
        def _resolve_antigravity_oauth_runtime_credentials(
            *, force_refresh: bool = False
        ):
            from agent.google_antigravity_oauth import (
                _credentials_path,
                get_valid_access_token,
                load_credentials,
            )
            from hermes_cli.auth import AuthError as _AuthError

            try:
                access_token = get_valid_access_token(force_refresh=force_refresh)
            except Exception as exc:
                raise _AuthError(
                    str(exc),
                    provider="google-antigravity",
                    code="antigravity_oauth_token_error",
                ) from exc

            creds = load_credentials()
            return {
                "provider": "google-antigravity",
                "base_url": "cloudcode-pa://antigravity",
                "api_key": access_token,
                "source": "antigravity-oauth",
                "expires_at_ms": (creds.expires_ms if creds else None),
                "auth_file": str(_credentials_path()),
                "email": (creds.email if creds else "") or "",
                "project_id": (creds.project_id if creds else "") or "",
            }

        auth_mod.resolve_antigravity_oauth_runtime_credentials = (
            _resolve_antigravity_oauth_runtime_credentials
        )
        logger.info("[antigravity_patch] injected credential resolver")

    # Extend _OAUTH_CAPABLE_PROVIDERS in auth_commands
    try:
        import hermes_cli.auth_commands as ac
        if hasattr(ac, "_OAUTH_CAPABLE_PROVIDERS"):
            ac._OAUTH_CAPABLE_PROVIDERS.add("google-antigravity")
    except Exception:
        pass

    return True


def _patch_runtime_provider() -> bool:
    """Inject google-antigravity handling into runtime_provider.

    Returns False if the Hermes runtime_provider API is incompatible.
    """
    try:
        import hermes_cli.runtime_provider as rp
    except ImportError:
        logger.warning("[antigravity_patch] runtime_provider module unavailable")
        return False

    # Verify target functions exist with expected parameters
    pool_resolver = getattr(rp, "_resolve_runtime_from_pool_entry", None)
    main_resolver = getattr(rp, "resolve_runtime_provider", None)

    if not callable(pool_resolver):
        logger.warning(
            "[antigravity_patch] _resolve_runtime_from_pool_entry missing"
        )
        return False
    if not callable(main_resolver):
        logger.warning(
            "[antigravity_patch] resolve_runtime_provider missing"
        )
        return False

    # Verify pool resolver accepts keyword args
    if not _verify_signature(
        pool_resolver, ["provider", "entry", "requested_provider"]
    ):
        logger.warning(
            "[antigravity_patch] _resolve_runtime_from_pool_entry signature "
            "changed — skipping"
        )
        return False

    # Patch pool entry resolver
    original_resolve = pool_resolver

    def patched_resolve(*, provider, entry, requested_provider,
                        model_cfg=None, pool=None, target_model=None, **kwargs):
        if provider == "google-antigravity":
            from hermes_cli.runtime_provider import _get_model_config
            model_cfg = model_cfg or _get_model_config()
            base_url = (
                getattr(entry, "runtime_base_url", None)
                or getattr(entry, "base_url", None)
                or ""
            ).rstrip("/")
            api_key = (
                getattr(entry, "runtime_api_key", None)
                or getattr(entry, "access_token", "")
            )
            return {
                "provider": "google-antigravity",
                "api_mode": "chat_completions",
                "base_url": base_url or "cloudcode-pa://antigravity",
                "api_key": api_key,
                "source": "credential-pool",
                "expires_at_ms": getattr(entry, "access_token_expires_at_ms", None),
                "requested_provider": requested_provider or "google-antigravity",
            }
        return original_resolve(
            provider=provider, entry=entry, requested_provider=requested_provider,
            model_cfg=model_cfg, pool=pool, target_model=target_model, **kwargs
        )

    rp._resolve_runtime_from_pool_entry = patched_resolve

    # Patch resolve_runtime_provider to handle google-antigravity
    original_main = main_resolver

    def patched_main(*, requested=None, explicit_api_key=None,
                     explicit_base_url=None, target_model=None, **kwargs):
        from hermes_cli.auth import resolve_provider as _resolve_provider
        from hermes_cli.runtime_provider import AuthError as _AuthError

        provider = _resolve_provider(
            requested, explicit_api_key=explicit_api_key,
            explicit_base_url=explicit_base_url,
        )
        if provider == "google-antigravity":
            try:
                from hermes_cli.auth import \
                    resolve_antigravity_oauth_runtime_credentials
                creds = resolve_antigravity_oauth_runtime_credentials()
                return {
                    "provider": "google-antigravity",
                    "api_mode": "chat_completions",
                    "base_url": creds.get("base_url", ""),
                    "api_key": creds.get("api_key", ""),
                    "source": creds.get("source", "antigravity-oauth"),
                    "expires_at_ms": creds.get("expires_at_ms"),
                    "email": creds.get("email", ""),
                    "project_id": creds.get("project_id", ""),
                    "requested_provider": requested,
                }
            except _AuthError:
                if requested not in (None, "auto"):
                    raise
        return original_main(
            requested=requested, explicit_api_key=explicit_api_key,
            explicit_base_url=explicit_base_url, target_model=target_model,
            **kwargs
        )

    rp.resolve_runtime_provider = patched_main
    logger.info("[antigravity_patch] injected runtime_provider handlers")
    return True


def _patch_agent_runtime() -> bool:
    """Inject GoogleAntigravityClient routing for older Hermes versions.

    Returns True if the patch was applied OR if Hermes already handles
    google-antigravity natively (newer versions).  Returns False only if
    the API is genuinely incompatible.
    """
    try:
        import agent.agent_runtime_helpers as arh
    except ImportError:
        logger.warning("[antigravity_patch] agent_runtime_helpers unavailable")
        return False

    # ── Check new API first (Hermes >= 0.11): google-antigravity is built in ──
    new_client_fn = getattr(arh, "create_openai_client", None)
    if callable(new_client_fn):
        # Verify it already handles google-antigravity by inspecting source
        try:
            src = inspect.getsource(new_client_fn)
            if "google-antigravity" in src:
                logger.info(
                    "[antigravity_patch] agent_runtime: already handled "
                    "natively (create_openai_client)"
                )
                return True
        except (OSError, TypeError):
            pass  # can't inspect; assume it's handled

    # ── Fall back to old API (_create_new_client) ─────────────────────
    old_client_fn = getattr(arh, "_create_new_client", None)
    if not callable(old_client_fn):
        logger.info(
            "[antigravity_patch] agent_runtime: no injectable client "
            "factory found (Hermes API may have changed)"
        )
        return False

    if not _verify_signature(old_client_fn, ["agent", "client_kwargs"]):
        logger.warning(
            "[antigravity_patch] _create_new_client signature changed — skipping"
        )
        return False

    def patched_create(agent, client_kwargs, reason, shared):
        if agent.provider == "google-antigravity":
            from agent.google_antigravity_adapter import GoogleAntigravityClient
            safe = {k: v for k, v in client_kwargs.items()
                    if k in ("api_key", "base_url", "default_headers",
                             "project_id", "timeout")}
            client = GoogleAntigravityClient(**safe)
            logger.info(
                "Google Antigravity client created (%s, shared=%s)",
                reason, shared,
            )
            return client
        return old_client_fn(agent, client_kwargs, reason, shared)

    arh._create_new_client = patched_create
    logger.info("[antigravity_patch] injected client routing (legacy API)")
    return True


def _model_flow_google_antigravity(_config, current_model=""):
    """Google Antigravity OAuth provider model picker flow.

    Uses the agy CLI token for auth — no API key needed.
    Shows the curated model list and saves the selection.
    """
    from hermes_cli.auth import (
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
    )

    # Verify credentials resolve
    try:
        from hermes_cli.auth import resolve_antigravity_oauth_runtime_credentials
        creds = resolve_antigravity_oauth_runtime_credentials()
        email = creds.get("email", "")
        if email:
            print(f"  Authenticated as: {email}")
    except Exception as exc:
        print(f"  Auth check failed: {exc}")
        print("  Run: hermes auth add google-antigravity")
        return

    # Curated model list (same as plugin supported models)
    AG_MODELS = [
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-medium",
        "claude-sonnet-4-6",
        "claude-sonnet-4-6-thinking",
        "claude-opus-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b",
        "gpt-oss-120b-medium",
    ]

    default = current_model or (AG_MODELS[0] if AG_MODELS else "gemini-3.5-flash-high")
    selected = _prompt_model_selection(AG_MODELS, current_model=default)
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider(
            "google-antigravity", "cloudcode-pa://antigravity"
        )
        print(f"Default model set to: {selected} (via Google Antigravity)")
    else:
        print("No change.")


def _patch_models_module() -> bool:
    """Inject google-antigravity into CANONICAL_PROVIDERS and related lookups.

    Returns False if the Hermes models API is incompatible.
    """
    try:
        import hermes_cli.models as models_mod
    except ImportError:
        logger.warning("[antigravity_patch] models module unavailable")
        return False

    # Verify key attributes exist
    CANONICAL_PROVIDERS = getattr(models_mod, "CANONICAL_PROVIDERS", None)
    ProviderEntry = getattr(models_mod, "ProviderEntry", None)
    labels = getattr(models_mod, "_PROVIDER_LABELS", None)
    provider_models = getattr(models_mod, "_PROVIDER_MODELS", None)

    if not isinstance(CANONICAL_PROVIDERS, list):
        logger.warning(
            "[antigravity_patch] CANONICAL_PROVIDERS missing or not a list"
        )
        return False
    if not isinstance(labels, dict):
        logger.warning("[antigravity_patch] _PROVIDER_LABELS missing")
        return False
    if not isinstance(provider_models, dict):
        logger.warning("[antigravity_patch] _PROVIDER_MODELS missing")
        return False

    # Verify ProviderEntry fields (slug, label, tui_desc)
    try:
        sig = inspect.signature(ProviderEntry)
        required = {"slug", "label"}
        if not required.issubset(sig.parameters):
            logger.warning(
                "[antigravity_patch] ProviderEntry signature changed "
                "(expected %s, got %s)", required, set(sig.parameters)
            )
            return False
    except (TypeError, ValueError):
        pass  # NamedTuple inspection can fail; proceed anyway

    _slug = "google-antigravity"
    if _slug not in {p.slug for p in CANONICAL_PROVIDERS}:
        CANONICAL_PROVIDERS.append(ProviderEntry(
            _slug,
            "Google Antigravity (OAuth)",
            "Google Antigravity (Gemini/Claude/GPT via agy CLI OAuth — "
            "no API key needed)",
        ))
        labels[_slug] = "Google Antigravity (OAuth)"
        logger.info("[antigravity_patch] injected into CANONICAL_PROVIDERS")

    # Add curated model list
    if _slug not in provider_models:
        provider_models[_slug] = [
            "gemini-3.5-flash-high",
            "gemini-3.5-flash-medium",
            "gemini-3.5-flash-low",
            "gemini-3.1-pro-high",
            "gemini-3.1-pro-medium",
            "claude-sonnet-4-6",
            "claude-sonnet-4-6-thinking",
            "claude-opus-4-6",
            "claude-opus-4-6-thinking",
            "gpt-oss-120b",
            "gpt-oss-120b-medium",
        ]
        logger.info("[antigravity_patch] injected model list")
    return True


def _patch_model_picker() -> bool:
    """Inject google-antigravity dispatch into select_provider_and_model.

    Uses robust attribute-level monkey-patching with signature verification
    — no source-code manipulation, no exec(), no string matching.

    Returns False if the Hermes main module API is incompatible.
    """
    try:
        import hermes_cli.main as main_mod
    except ImportError:
        logger.warning("[antigravity_patch] main module unavailable")
        return False

    if getattr(main_mod, "_antigravity_picker_patched", False):
        return True  # already done, not a failure

    # ── Verify target functions exist and have compatible signatures ──
    is_profile = getattr(main_mod, "_is_profile_api_key_provider", None)
    api_key_flow = getattr(main_mod, "_model_flow_api_key_provider", None)

    if not callable(is_profile):
        logger.warning(
            "[antigravity_patch] _is_profile_api_key_provider missing "
            "— TUI picker dispatch unavailable"
        )
        return False
    if not callable(api_key_flow):
        logger.warning(
            "[antigravity_patch] _model_flow_api_key_provider missing "
            "— TUI picker dispatch unavailable"
        )
        return False

    if not _verify_signature(is_profile, ["provider_id"]):
        logger.warning(
            "[antigravity_patch] _is_profile_api_key_provider signature "
            "changed — TUI picker dispatch unavailable"
        )
        return False
    if not _verify_signature(api_key_flow, ["config", "provider_id"]):
        logger.warning(
            "[antigravity_patch] _model_flow_api_key_provider signature "
            "changed — TUI picker dispatch unavailable"
        )
        return False

    # ── 1. Extend _is_profile_api_key_provider ──────────────────────
    _original_is_profile = is_profile

    def _patched_is_profile(provider_id: str) -> bool:
        if provider_id == "google-antigravity":
            return True
        return _original_is_profile(provider_id)

    main_mod._is_profile_api_key_provider = _patched_is_profile

    # ── 2. Wrap _model_flow_api_key_provider ────────────────────────
    main_mod._model_flow_google_antigravity = _model_flow_google_antigravity
    _original_api_key_flow = api_key_flow

    def _patched_api_key_flow(config, provider_id, current_model=""):
        if provider_id == "google-antigravity":
            return _model_flow_google_antigravity(config, current_model)
        return _original_api_key_flow(config, provider_id, current_model)

    main_mod._model_flow_api_key_provider = _patched_api_key_flow
    main_mod._antigravity_picker_patched = True
    logger.info("[antigravity_patch] injected model picker dispatch (safe mode)")
    return True


def apply() -> dict[str, bool]:
    """Apply all antigravity provider patches.

    Returns a dict mapping each patch name to a boolean indicating success.
    Callers can inspect ``_patch_results`` after the call.
    """
    global _patched, _patch_results
    if _patched:
        return _patch_results
    _patched = True
    _patch_results = {}

    patches = [
        ("providers", _patch_providers),
        ("auth_registry", _patch_auth_registry),
        ("runtime_provider", _patch_runtime_provider),
        ("agent_runtime", _patch_agent_runtime),
        ("models_module", _patch_models_module),
        ("model_picker", _patch_model_picker),
    ]

    for name, fn in patches:
        try:
            ok = fn()
            _patch_results[name] = ok
        except Exception as exc:
            logger.warning(
                "[antigravity_patch] %s raised %s: %s",
                name, type(exc).__name__, exc,
            )
            _patch_results[name] = False

    succeeded = sum(1 for v in _patch_results.values() if v)
    failed = [k for k, v in _patch_results.items() if not v]
    total = len(_patch_results)

    status = (
        f"[antigravity_provider_patch] {succeeded}/{total} patches applied"
    )
    if failed:
        status += f" (failed: {', '.join(failed)})"
    print(status, file=sys.stderr, flush=True)

    return _patch_results
