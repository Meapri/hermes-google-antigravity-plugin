"""Google Antigravity OAuth image generation backend.

This provider registers Hermes' ``image_gen`` backend named
``google-antigravity``. It only uses the Antigravity OAuth and Code Assist
transport installed by this repository, so image generation stays inside the
Antigravity provider path.
"""

from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)


_MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-3.1-flash-image": {
        "display": "Nano Banana (Gemini 3.1 Flash Image)",
        "speed": "~8-20s",
        "strengths": "Antigravity OAuth image generation",
    },
    "gemini-2.5-flash-image": {
        "display": "Gemini 2.5 Flash Image",
        "speed": "~8-20s",
        "strengths": "Compatibility fallback for older image routing",
    },
}

_MODEL_ALIASES = {
    "nano-banana-pro": "gemini-3-pro-image",
    "nano-banana-pro-2": "gemini-3-pro-image",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
    "gemini-3.1-pro-image": "gemini-3-pro-image",
    "gemini-3-pro-image-generation": "gemini-3-pro-image",
    "nano-banana": "gemini-3.1-flash-image",
}

_PRO_IMAGE_MODELS = {
    "gemini-3-pro-image",
    "gemini-3.1-pro-image",
    "gemini-3-pro-image-preview",
    "gemini-3-pro-image-generation",
    "nano-banana-pro",
    "nano-banana-pro-2",
}

DEFAULT_MODEL = "gemini-3.1-flash-image"
_MODEL_CACHE_TTL_SECONDS = 300.0
_AVAILABLE_MODELS_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "models": None}

_ASPECT_RATIOS = {
    "landscape": "16:9",
    "square": "1:1",
    "portrait": "9:16",
}

_IMAGE_SIZES = {"512", "1K", "2K", "4K"}
_IMAGE_SIZE_ALIASES = {
    "0.5K": "512",
    "512PX": "512",
    "1024": "1K",
    "1024PX": "1K",
    "2048": "2K",
    "2048PX": "2K",
    "4096": "4K",
    "4096PX": "4K",
}

_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


def _load_image_gen_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _normalize_model(model: Any) -> str:
    value = str(model or "").strip()
    if not value:
        return DEFAULT_MODEL
    vendor, sep, bare = value.partition("/")
    if sep and vendor.lower() in {"google", "gemini"}:
        value = bare.strip() or value
    value = value.replace("gemini-2-5-", "gemini-2.5-")
    value = value.replace("gemini-3-1-", "gemini-3.1-")
    return _MODEL_ALIASES.get(value, value)


def _resolve_model(explicit: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    env_override = os.environ.get("HERMES_ANTIGRAVITY_IMAGE_MODEL")
    cfg = _load_image_gen_config()
    provider_cfg = cfg.get("google-antigravity") if isinstance(cfg.get("google-antigravity"), dict) else {}
    for candidate in (
        explicit,
        env_override,
        provider_cfg.get("model") if isinstance(provider_cfg, dict) else None,
        cfg.get("model"),
    ):
        if candidate is None or str(candidate).strip() == "":
            continue
        model_id = _normalize_model(candidate)
        if model_id in _MODELS or model_id in {"gemini-3-pro-image"}:
            return model_id, _MODELS.get(model_id, {"display": model_id})
    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _model_catalog_from_ids(model_ids: Iterable[str], model_details: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    details = model_details if isinstance(model_details, dict) else {}
    for raw_id in model_ids:
        model_id = _normalize_model(raw_id)
        if not model_id:
            continue
        known = _MODELS.get(model_id, {})
        detail = details.get(model_id) if isinstance(details.get(model_id), dict) else {}
        display = str(detail.get("displayName") or known.get("display") or model_id)
        quota = detail.get("quotaInfo") if isinstance(detail.get("quotaInfo"), dict) else {}
        catalog[model_id] = {
            "display": display,
            "speed": known.get("speed", "~8-30s"),
            "strengths": known.get("strengths", "Antigravity catalog image generation model"),
            "quotaInfo": quota,
        }
    return catalog


def _fetch_available_image_models(access_token: str, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    now = time.time()
    cached = _AVAILABLE_MODELS_CACHE.get("models")
    fetched_at = float(_AVAILABLE_MODELS_CACHE.get("fetched_at") or 0.0)
    if not refresh and isinstance(cached, dict) and now - fetched_at < _MODEL_CACHE_TTL_SECONDS:
        return cached

    from agent import google_antigravity_oauth
    from agent.google_antigravity_adapter import (
        ANTIGRAVITY_ENDPOINT_FALLBACKS,
        GoogleAntigravityClient,
        _gemini_http_error,
        get_antigravity_headers,
    )

    client = GoogleAntigravityClient(api_key=access_token, model=DEFAULT_MODEL)
    ctx = client._ensure_project_context(access_token, DEFAULT_MODEL)
    project_id = getattr(ctx, "project_id", "") or google_antigravity_oauth.resolve_project_id_from_env()
    if not project_id:
        return {}

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        **get_antigravity_headers(refresh_version=True),
    }
    body = {
        "project": project_id,
        "requestId": "agent-" + str(uuid.uuid4()),
    }

    last_error: Optional[BaseException] = None
    for endpoint in ANTIGRAVITY_ENDPOINT_FALLBACKS:
        response = client._http.post(f"{endpoint}/v1internal:fetchAvailableModels", json=body, headers=headers)
        if response.status_code == 200:
            payload = response.json()
            ids = payload.get("imageGenerationModelIds") or payload.get("image_generation_model_ids") or []
            if not isinstance(ids, list):
                ids = []
            catalog = _model_catalog_from_ids((str(item) for item in ids), payload.get("models"))
            _AVAILABLE_MODELS_CACHE.update({"fetched_at": now, "models": catalog})
            return catalog
        last_error = _gemini_http_error(response)
    logger.debug("Could not fetch Antigravity image model catalog: %s", last_error)
    return {}


def _available_model_catalog(access_token: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    if not access_token:
        try:
            from agent import google_antigravity_oauth

            access_token = google_antigravity_oauth.get_valid_access_token()
        except Exception:
            return dict(_MODELS)
    try:
        fetched = _fetch_available_image_models(access_token)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Falling back to static Antigravity image catalog: %s", exc)
        return dict(_MODELS)
    return fetched or dict(_MODELS)


def _resolve_image_size(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    if not text:
        return ""
    return _IMAGE_SIZE_ALIASES.get(text, text if text in _IMAGE_SIZES else "")


def _build_image_request(*, prompt: str, aspect_ratio: str, image_size: str = "") -> Dict[str, Any]:
    image_config = {
        "aspectRatio": _ASPECT_RATIOS.get(aspect_ratio, _ASPECT_RATIOS[DEFAULT_ASPECT_RATIO]),
    }
    if image_size:
        image_config["imageSize"] = image_size

    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_config,
        },
    }


def _submit_antigravity_image_request(
    *,
    access_token: str,
    model: str,
    request: Dict[str, Any],
) -> Dict[str, Any]:
    from agent import google_antigravity_oauth
    from agent.google_antigravity_adapter import (
        ANTIGRAVITY_ENDPOINT_FALLBACKS,
        GoogleAntigravityClient,
        _antigravity_credit_attempts,
        _gemini_http_error,
        _wrap_antigravity_request,
        get_antigravity_headers,
    )

    client = GoogleAntigravityClient(api_key=access_token, model=model)
    ctx = client._ensure_project_context(access_token, model)
    project_id = getattr(ctx, "project_id", "") or google_antigravity_oauth.resolve_project_id_from_env()
    if not project_id:
        raise RuntimeError("Could not resolve Google Antigravity Code Assist project id")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        **get_antigravity_headers(refresh_version=True),
    }

    last_error: Optional[BaseException] = None
    for use_credits in _antigravity_credit_attempts(ctx):
        wrapped = _wrap_antigravity_request(
            project_id=project_id,
            model=model,
            request=request,
            use_google_one_ai_credits=use_credits,
        )
        for endpoint in ANTIGRAVITY_ENDPOINT_FALLBACKS:
            url = f"{endpoint}/v1internal:generateContent"
            response = client._http.post(url, json=wrapped, headers=headers)
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise RuntimeError(f"Invalid JSON from Antigravity image endpoint: {exc}") from exc
            last_error = _gemini_http_error(response)
            if response.status_code not in {400, 404, 429, 500, 502, 503, 504}:
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("Antigravity image request failed before an HTTP response was returned")


def _iter_values(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_values(child)


def _extract_image_result(payload: Any) -> Tuple[Optional[str], str, str]:
    """Return ``(data, kind, extension)`` from an Antigravity image response."""

    for item in _iter_values(payload):
        if not isinstance(item, dict):
            continue

        inline = item.get("inlineData") or item.get("inline_data")
        if isinstance(inline, dict):
            data = inline.get("data") or inline.get("b64Json") or inline.get("b64_json")
            if isinstance(data, str) and data.strip():
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "").lower()
                extension = _MIME_EXTENSIONS.get(mime) or _image_extension_from_b64(data) or "png"
                return data.strip(), "b64", extension

        for key in ("imageUrl", "image_url", "url", "uri"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value, "url", "png"

        for key in (
            "imageBase64",
            "image_b64",
            "b64Json",
            "b64_json",
            "base64",
            "data",
            "result",
        ):
            value = item.get(key)
            if isinstance(value, str) and _looks_like_base64_image(value):
                return value.strip(), "b64", _image_extension_from_b64(value) or "png"

    return None, "", "png"


def _looks_like_base64_image(value: str) -> bool:
    text = value.strip()
    if len(text) < 32:
        return False
    if text.startswith("data:image/"):
        return True
    try:
        raw = base64.b64decode(text[:128] + "==", validate=False)
    except Exception:
        return False
    return raw.startswith((b"\x89PNG", b"\xff\xd8\xff", b"RIFF"))


def _image_extension_from_b64(value: str) -> str:
    text = value.strip()
    if text.startswith("data:image/"):
        _header, _sep, text = text.partition(",")
    try:
        raw = base64.b64decode(text[:256] + "==", validate=False)
    except Exception:
        return ""
    if raw.startswith(b"\x89PNG"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
        return "webp"
    return ""


def _strip_data_url(value: str) -> Tuple[str, str]:
    text = value.strip()
    if not text.startswith("data:image/"):
        return text, _image_extension_from_b64(text) or "png"
    header, _, data = text.partition(",")
    mime = header.split(";", 1)[0].removeprefix("data:").lower()
    return data.strip(), _MIME_EXTENSIONS.get(mime, "png")


class GoogleAntigravityImageGenProvider(ImageGenProvider):
    """Antigravity OAuth image generation backend."""

    @property
    def name(self) -> str:
        return "google-antigravity"

    @property
    def display_name(self) -> str:
        return "Google Antigravity"

    def is_available(self) -> bool:
        try:
            from agent import google_antigravity_oauth

            return bool(google_antigravity_oauth.load_credentials())
        except Exception:  # noqa: BLE001
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        catalog = _available_model_catalog()
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": "Antigravity plan quota",
                **({"quota": meta["quotaInfo"]} if meta.get("quotaInfo") else {}),
            }
            for model_id, meta in catalog.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Google Antigravity",
            "badge": "oauth",
            "tag": "Image generation through the existing Antigravity OAuth session",
            "env_vars": [],
            "post_setup_hint": (
                "Sign in with `hermes auth add google-antigravity` if no "
                "Antigravity OAuth session is available."
            ),
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        explicit_model = kwargs.get("model")
        model_id, _meta = _resolve_model(explicit_model)
        requested_model = _normalize_model(explicit_model) if explicit_model else ""
        image_size = _resolve_image_size(
            kwargs.get("image_size")
            or kwargs.get("resolution")
            or kwargs.get("size")
            or os.environ.get("HERMES_ANTIGRAVITY_IMAGE_SIZE")
        )

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider=self.name,
                model=model_id,
                aspect_ratio=aspect,
            )

        try:
            from agent import google_antigravity_oauth

            access_token = google_antigravity_oauth.get_valid_access_token()
        except Exception as exc:  # noqa: BLE001
            return error_response(
                error=f"No usable Google Antigravity OAuth credentials available: {exc}",
                error_type="auth_required",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        catalog = _available_model_catalog(access_token)
        if requested_model and requested_model not in catalog:
            suffix = ""
            if explicit_model and str(explicit_model).strip() in _PRO_IMAGE_MODELS:
                suffix = (
                    " This account's Antigravity fetchAvailableModels.imageGenerationModelIds "
                    "does not expose a Pro image model yet, so the provider cannot call it "
                    "through Antigravity."
                )
            return error_response(
                error=(
                    f"Unsupported Google Antigravity image model '{requested_model}'. "
                    f"Available image models: {', '.join(catalog) or 'none'}.{suffix}"
                ),
                error_type="invalid_model",
                provider=self.name,
                model=requested_model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        if requested_model and requested_model in catalog:
            model_id = requested_model
        if model_id not in catalog and catalog:
            model_id = next(iter(catalog))

        request = _build_image_request(prompt=prompt, aspect_ratio=aspect, image_size=image_size)
        try:
            payload = _submit_antigravity_image_request(
                access_token=access_token,
                model=model_id,
                request=request,
            )
        except httpx.HTTPStatusError as exc:
            return error_response(
                error=f"Antigravity image generation failed ({exc.response.status_code}): {exc.response.text[:500]}",
                error_type="api_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Antigravity image generation failed", exc_info=True)
            return error_response(
                error=f"Antigravity image generation failed: {exc}",
                error_type="api_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        data, kind, extension = _extract_image_result(payload)
        if not data:
            return error_response(
                error="Antigravity response contained no image bytes or image URL",
                error_type="empty_response",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            if kind == "url":
                saved_path = save_url_image(data, prefix=f"google_antigravity_{model_id}")
            else:
                b64, data_url_extension = _strip_data_url(data)
                saved_path = save_b64_image(
                    b64,
                    prefix=f"google_antigravity_{model_id}",
                    extension=data_url_extension or extension,
                )
        except Exception as exc:  # noqa: BLE001
            return error_response(
                error=f"Could not save Antigravity image to cache: {exc}",
                error_type="io_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=str(saved_path),
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=self.name,
            extra={
                "backend": "antigravity-code-assist",
                "request_model": model_id,
                **({"image_size": image_size} if image_size else {}),
            },
        )


def register(ctx) -> None:
    ctx.register_image_gen_provider(GoogleAntigravityImageGenProvider())
