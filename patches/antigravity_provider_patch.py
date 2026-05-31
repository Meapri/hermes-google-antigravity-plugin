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

__version__ = "2.0.0"

import inspect
import logging
import sys

logger = logging.getLogger(__name__)

_patched = False
_patch_results: dict[str, bool] = {}

ANTIGRAVITY_MODEL_IDS = [
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
        import hermes_cli.providers as providers_mod
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
    label_overrides = getattr(providers_mod, "_LABEL_OVERRIDES", None)
    if isinstance(label_overrides, dict):
        label_overrides["google-antigravity"] = "Google Antigravity (OAuth)"
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

    pool_resolver = getattr(rp, "_resolve_runtime_from_pool_entry", None)
    main_resolver = getattr(rp, "resolve_runtime_provider", None)

    # 1. Patch pool entry resolver (only patch if it is available)
    if pool_resolver and callable(pool_resolver):
        if _verify_signature(pool_resolver, ["provider", "entry", "requested_provider"]):
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
            logger.info("[antigravity_patch] patched pool entry resolver")
    else:
        logger.info("[antigravity_patch] _resolve_runtime_from_pool_entry missing — skipping optional patch")

    # 2. Patch resolve_runtime_provider to handle google-antigravity
    if not callable(main_resolver):
        logger.warning(
            "[antigravity_patch] resolve_runtime_provider missing"
        )
        return False

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
        original_create = new_client_fn
        def patched_create(agent, client_kwargs, reason, shared):
            if agent.provider == "google-antigravity" or str(client_kwargs.get("base_url", "")).startswith("cloudcode-pa://antigravity"):
                from agent.google_antigravity_adapter import GoogleAntigravityClient
                safe_kwargs = {
                    k: v for k, v in client_kwargs.items()
                    if k in {"api_key", "base_url", "default_headers", "project_id", "timeout"}
                }
                client = GoogleAntigravityClient(**safe_kwargs)
                logger.info(
                    "Google Antigravity client created (%s, shared=%s)",
                    reason,
                    shared,
                )
                return client
            return original_create(agent, client_kwargs, reason=reason, shared=shared)
        arh.create_openai_client = patched_create
        logger.info("[antigravity_patch] agent_runtime: patched create_openai_client")
        return True

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

    def patched_create_old(agent, client_kwargs, reason, shared):
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

    arh._create_new_client = patched_create_old
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
    AG_MODELS = list(ANTIGRAVITY_MODEL_IDS)

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
        logger.info("[antigravity_patch] injected into CANONICAL_PROVIDERS")
    labels[_slug] = "Google Antigravity (OAuth)"

    # Add curated model list
    if not provider_models.get(_slug):
        provider_models[_slug] = list(ANTIGRAVITY_MODEL_IDS)
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


def _patch_auxiliary_client() -> bool:
    """Inject google-antigravity support into resolve_provider_client.

    Returns False if the auxiliary client module is unavailable or incompatible.
    """
    try:
        import agent.auxiliary_client as ac
    except ImportError:
        logger.warning("[antigravity_patch] auxiliary_client module unavailable")
        return False

    original_resolve = getattr(ac, "resolve_provider_client", None)
    if not callable(original_resolve):
        logger.warning("[antigravity_patch] resolve_provider_client missing")
        return False

    # Async proxy classes for GoogleAntigravityClient to handle async_mode
    # safely without unsupported protocol errors (e.g. cloudcode-pa://).
    import asyncio

    class AsyncCompletionsProxy:
        def __init__(self, sync_client):
            self._sync_client = sync_client

        async def create(self, **kwargs):
            return await asyncio.to_thread(self._sync_client.chat.completions.create, **kwargs)

    class AsyncChatProxy:
        def __init__(self, sync_client):
            self.completions = AsyncCompletionsProxy(sync_client)

    class AsyncGoogleAntigravityClientProxy:
        def __init__(self, sync_client):
            self._sync_client = sync_client
            self.chat = AsyncChatProxy(sync_client)
            self.api_key = sync_client.api_key
            self.base_url = sync_client.base_url

        async def close(self):
            await asyncio.to_thread(self._sync_client.close)

    def patched_resolve(provider, model=None, async_mode=False, **kwargs):
        raw_provider = (provider or "").strip().lower()
        provider_normalized = raw_provider
        normalize_aux_provider = getattr(ac, "_normalize_aux_provider", None)
        if callable(normalize_aux_provider):
            try:
                provider_normalized = normalize_aux_provider(provider)
            except Exception:
                provider_normalized = raw_provider
        if provider_normalized in {
            "google-antigravity",
            "antigravity",
            "antigravity-oauth",
        }:
            from hermes_cli.auth import resolve_antigravity_oauth_runtime_credentials
            from agent.google_antigravity_adapter import GoogleAntigravityClient
            try:
                creds = resolve_antigravity_oauth_runtime_credentials()
                client = GoogleAntigravityClient(
                    api_key=creds.get("api_key", ""),
                    base_url=creds.get("base_url", ""),
                    project_id=creds.get("project_id", ""),
                    timeout=kwargs.get("timeout", 120),
                )
                final_model = model or "gemini-3.5-flash-high"
                if async_mode:
                    async_client = AsyncGoogleAntigravityClientProxy(client)
                    return async_client, final_model
                return client, final_model
            except Exception as exc:
                logger.warning("[antigravity_patch] resolve_provider_client failed for google-antigravity: %s", exc)
                return None, None
        return original_resolve(provider, model=model, async_mode=async_mode, **kwargs)

    ac.resolve_provider_client = patched_resolve
    logger.info("[antigravity_patch] injected auxiliary_client google-antigravity resolver")
    return True


def _patch_model_switch_picker() -> bool:
    """Inject google-antigravity into the /model picker provider list.

    Root cause this fixes: ``list_authenticated_providers`` (used by the
    gateway ``/model`` picker via ``list_picker_providers``) decides whether
    to surface a provider by probing for credentials. For an
    ``oauth_external`` provider it checks env-var API keys, the auth store
    ``providers`` dict, and the credential pool — none of which know about
    Antigravity's standalone OAuth file. Only ``anthropic`` has a hard-coded
    external-file fallback. So Antigravity's credentials resolve fine at
    request time, but the picker never lists it.

    We wrap ``list_authenticated_providers`` so that, when the Antigravity
    OAuth token resolves successfully and no row is already present, we inject
    a row using the curated model list registered by ``_patch_models_module``.
    ``list_picker_providers`` calls this same function by module-global name,
    so patching here fixes the picker too.

    Returns False only if the model_switch API is genuinely incompatible.
    """
    import sys as _sys
    # Don't force-import model_switch here. When apply() runs on the
    # hermes_cli.providers hook, model_switch is usually mid-import (it does
    # ``from hermes_cli.providers import get_label``), so list_authenticated_providers
    # isn't defined yet AND force-importing would re-enter a partially
    # initialized module. Defer to the dedicated model_switch import hook,
    # which fires _patch_model_switch_picker() only after the module body
    # has fully executed.
    if "hermes_cli.model_switch" not in _sys.modules:
        return True  # deferred — handled by the model_switch import hook
    try:
        import hermes_cli.model_switch as ms
    except ImportError:
        logger.warning("[antigravity_patch] model_switch module unavailable")
        return False

    original = getattr(ms, "list_authenticated_providers", None)
    if not callable(original):
        logger.warning(
            "[antigravity_patch] list_authenticated_providers missing "
            "— /model picker injection unavailable"
        )
        return False

    if getattr(ms, "_antigravity_picker_injected", False):
        return True  # already done, not a failure

    def _patched_list_authenticated_providers(*args, **kwargs):
        results = original(*args, **kwargs)
        try:
            if not isinstance(results, list):
                return results
            slugs = {str(r.get("slug", "")).lower() for r in results}
            if "google-antigravity" in slugs:
                return results

            # Confirm credentials actually resolve before advertising.
            from hermes_cli.auth import (
                resolve_antigravity_oauth_runtime_credentials,
            )
            creds = resolve_antigravity_oauth_runtime_credentials()
            if not (creds and creds.get("api_key")):
                return results

            # Resolve current_provider + max_models from args/kwargs to mark
            # the row as current and honor the picker's model cap.
            current_provider = ""
            if args:
                current_provider = args[0]
            current_provider = kwargs.get("current_provider", current_provider)
            max_models = kwargs.get("max_models", 8)
            if len(args) >= 5:
                max_models = args[4]

            try:
                from hermes_cli.models import _PROVIDER_MODELS
                model_ids = list(
                    _PROVIDER_MODELS.get("google-antigravity", [])
                    or ANTIGRAVITY_MODEL_IDS
                )
            except Exception:
                model_ids = list(ANTIGRAVITY_MODEL_IDS)

            try:
                from hermes_cli.providers import get_label
                name = get_label("google-antigravity") or "Google Antigravity (OAuth)"
            except Exception:
                name = "Google Antigravity (OAuth)"

            cur = str(current_provider or "").strip().lower()
            results.insert(0, {
                "slug": "google-antigravity",
                "name": name,
                "is_current": cur in (
                    "google-antigravity", "antigravity", "antigravity-oauth",
                ),
                "is_user_defined": False,
                "models": model_ids[:max_models],
                "total_models": len(model_ids),
                "source": "hermes",
            })
        except Exception as exc:
            logger.debug(
                "[antigravity_patch] picker injection skipped: %s", exc
            )
        return results

    ms.list_authenticated_providers = _patched_list_authenticated_providers
    ms._antigravity_picker_injected = True
    logger.info(
        "[antigravity_patch] injected list_authenticated_providers picker row"
    )
    return True


def _antigravity_model_entries() -> list[dict[str, str]]:
    def _label(mid: str) -> str:
        replacements = {
            "gpt": "GPT",
            "oss": "OSS",
            "claude": "Claude",
            "sonnet": "Sonnet",
            "opus": "Opus",
            "thinking": "Thinking",
            "gemini": "Gemini",
            "flash": "Flash",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
            "pro": "Pro",
        }
        parts = []
        for part in str(mid).split("-"):
            parts.append(replacements.get(part.lower(), part))
        return " ".join(parts)

    return [{"id": mid, "label": _label(mid)} for mid in ANTIGRAVITY_MODEL_IDS]


def _patch_webui_config() -> bool:
    """Expose google-antigravity in hermes-webui's /api/models catalog.

    The standalone WebUI on port 8787 builds its picker from api.config, not
    from the gateway's model_switch payload. Registering the provider in the
    Hermes CLI is therefore not enough for the first /api/models response.
    """
    try:
        import api.config as webui_config
    except ImportError:
        return True  # Not running hermes-webui; nothing to patch.

    display = getattr(webui_config, "_PROVIDER_DISPLAY", None)
    models = getattr(webui_config, "_PROVIDER_MODELS", None)
    aliases = getattr(webui_config, "_PROVIDER_ALIASES", None)
    if isinstance(display, dict):
        display["google-antigravity"] = "Google Antigravity (OAuth)"
        display["antigravity"] = "Google Antigravity (OAuth)"
    if isinstance(models, dict) and not models.get("google-antigravity"):
        models["google-antigravity"] = _antigravity_model_entries()
    if isinstance(aliases, dict):
        aliases["antigravity"] = "google-antigravity"
        aliases["antigravity-oauth"] = "google-antigravity"
        aliases["google-antigravity"] = "google-antigravity"

    original = getattr(webui_config, "get_available_models", None)
    if not callable(original):
        return isinstance(display, dict) and isinstance(models, dict)
    if getattr(webui_config, "_antigravity_get_available_models_patched", False):
        return True

    def _patched_get_available_models(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            if not isinstance(result, dict):
                return result
            groups = result.setdefault("groups", [])
            if any(g.get("provider_id") == "google-antigravity" for g in groups):
                return result

            from hermes_cli.auth import resolve_antigravity_oauth_runtime_credentials

            creds = resolve_antigravity_oauth_runtime_credentials()
            if not (creds and creds.get("api_key")):
                return result

            groups.insert(0, {
                "provider": "Google Antigravity (OAuth)",
                "provider_id": "google-antigravity",
                "models": _antigravity_model_entries(),
            })
        except Exception as exc:
            logger.debug("[antigravity_patch] WebUI model injection skipped: %s", exc)
        return result

    webui_config.get_available_models = _patched_get_available_models
    webui_config._antigravity_get_available_models_patched = True

    invalidate = getattr(webui_config, "invalidate_models_cache", None)
    if callable(invalidate):
        try:
            invalidate()
        except Exception:
            logger.debug("[antigravity_patch] WebUI model cache invalidation failed")
    logger.info("[antigravity_patch] injected hermes-webui /api/models row")
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
        ("model_switch_picker", _patch_model_switch_picker),
        ("auxiliary_client", _patch_auxiliary_client),
        ("webui_config", _patch_webui_config),
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
    logger.debug(status)

    return _patch_results
