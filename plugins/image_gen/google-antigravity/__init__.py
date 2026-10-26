"""Google Antigravity (Cloud Code PA) image-generation backend.

Registers Hermes' ``image_gen`` provider ``google-antigravity``. The actual
generation is delegated to the vendored Cloud Code client
(``agent.antigravity_cloudcode.AntigravityClient.generate_image``) and reuses the
model-provider's Google-account OAuth (``agent.antigravity_oauth``) — no API key.

Model catalog is fetched live from the backend
(``fetch_available_image_models``) with a curated fallback when the live fetch is
unavailable.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    success_response,
)

logger = logging.getLogger(__name__)

# Curated fallback (A) — used only when the live catalog (B) is unavailable.
_CURATED: Dict[str, Dict[str, Any]] = {
    "gemini-3-pro-image": {"display": "Nano Banana Pro (Gemini 3 Pro Image)", "speed": "~15-30s", "strengths": "Highest quality, text rendering"},
    "gemini-3.1-flash-image": {"display": "Nano Banana (Gemini 3.1 Flash Image)", "speed": "~5-10s", "strengths": "Fast, general purpose"},
    "gemini-2.5-flash-image": {"display": "Gemini 2.5 Flash Image", "speed": "~5-10s", "strengths": "Fast, legacy"},
}
_DEFAULT_MODEL = "gemini-3.1-flash-image"
_CATALOG_TTL = 600.0
_catalog_cache: Dict[str, Any] = {"t": 0.0, "models": None}


def _client():
    from agent.antigravity_cloudcode import AntigravityClient
    from agent.antigravity_cloudcode_config import Settings
    return AntigravityClient(Settings.from_env())


def _images_dir() -> Path:
    from hermes_constants import get_hermes_home
    d = get_hermes_home() / "cache" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


class GoogleAntigravityImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "google-antigravity"

    @property
    def display_name(self) -> str:
        return "Google Antigravity"

    def is_available(self) -> bool:
        try:
            from agent.antigravity_oauth import resolve_antigravity_runtime_credentials
            return bool(resolve_antigravity_runtime_credentials().get("api_key"))
        except Exception:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        now = time.time()
        cached = _catalog_cache.get("models")
        if cached and (now - float(_catalog_cache.get("t") or 0.0)) < _CATALOG_TTL:
            return cached
        models: Optional[List[Dict[str, Any]]] = None
        # (B) live catalog from the backend
        try:
            ids = _client().fetch_available_image_models()
            if ids:
                models = [dict(id=i, **_CURATED.get(i, {"display": i})) for i in ids]
        except Exception as exc:
            logger.debug("Antigravity live image-model fetch failed; using curated list: %s", exc)
        # (A) curated fallback
        if not models:
            models = [dict(id=k, **v) for k, v in _CURATED.items()]
        _catalog_cache.update(t=now, models=models)
        return models

    def default_model(self) -> Optional[str]:
        ids = [m.get("id") for m in self.list_models()]
        if _DEFAULT_MODEL in ids:
            return _DEFAULT_MODEL
        return ids[0] if ids else None

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Google Antigravity",
            "badge": "subscription",
            "tag": "Gemini image models via Google-account OAuth (run: hermes auth add google-antigravity)",
            "env_vars": [],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text"], "max_reference_images": 0}

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        model = str(kwargs.get("model") or self.default_model() or _DEFAULT_MODEL)
        if not prompt:
            return error_response(error="Image prompt is empty.", error_type="invalid_request",
                                  provider="google-antigravity", model=model, aspect_ratio=aspect)
        image_size = str(kwargs.get("image_size") or "").strip()
        extra = {"image_size": image_size} if image_size else {}
        try:
            path = _client().generate_image(prompt=prompt, output_dir=_images_dir(),
                                            aspect_ratio=aspect, model=model, **extra)
        except Exception as exc:
            logger.warning("Antigravity image generation failed: %s", exc)
            return error_response(error=f"Antigravity image generation failed: {exc}",
                                  provider="google-antigravity", model=model, prompt=prompt, aspect_ratio=aspect)
        return success_response(image=str(path), model=model, prompt=prompt,
                                aspect_ratio=aspect, provider="google-antigravity", modality="text")


def register(ctx: Any) -> None:
    ctx.register_image_gen_provider(GoogleAntigravityImageGenProvider())
