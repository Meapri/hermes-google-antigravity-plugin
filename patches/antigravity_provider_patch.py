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

import sys
import logging

logger = logging.getLogger(__name__)

_patched = False


def _patch_providers():
    """Inject google-antigravity into HERMES_OVERLAYS."""
    from hermes_cli.providers import HermesOverlay, HERMES_OVERLAYS

    if "google-antigravity" not in HERMES_OVERLAYS:
        HERMES_OVERLAYS["google-antigravity"] = HermesOverlay(
            transport="openai_chat",
            auth_type="oauth_external",
            base_url_override="cloudcode-pa://antigravity",
        )
        logger.info("[antigravity_patch] injected into HERMES_OVERLAYS")


def _patch_auth_registry():
    """Inject google-antigravity into PROVIDER_REGISTRY and aliases."""
    from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig

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


def _patch_runtime_provider():
    """Inject google-antigravity handling into runtime_provider."""
    import hermes_cli.runtime_provider as rp

    # Patch pool entry resolver
    original_resolve = rp._resolve_runtime_from_pool_entry

    def patched_resolve(*, provider, entry, requested_provider,
                        model_cfg=None, pool=None, target_model=None, **kwargs):
        if provider == "google-antigravity":
            import os
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
    original_main = rp.resolve_runtime_provider

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


def _patch_agent_runtime():
    """Inject GoogleAntigravityClient routing."""
    import agent.agent_runtime_helpers as arh

    original_create = arh._create_new_client

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
        return original_create(agent, client_kwargs, reason, shared)

    arh._create_new_client = patched_create
    logger.info("[antigravity_patch] injected client routing")


def apply():
    """Apply all antigravity provider patches."""
    global _patched
    if _patched:
        return
    _patched = True

    try:
        _patch_providers()
    except Exception as exc:
        logger.debug("patch providers: %s", exc)

    try:
        _patch_auth_registry()
    except Exception as exc:
        logger.debug("patch auth: %s", exc)

    try:
        _patch_runtime_provider()
    except Exception as exc:
        logger.debug("patch runtime_provider: %s", exc)

    try:
        _patch_agent_runtime()
    except Exception as exc:
        logger.debug("patch agent_runtime: %s", exc)

    print("[antigravity_provider_patch] bypass installed", file=sys.stderr, flush=True)
