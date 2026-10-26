from __future__ import annotations

import asyncio
import json
import base64
import logging
import mimetypes
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import httpx

from agent.antigravity_cloudcode_config import Settings

log = logging.getLogger(__name__)

ENDPOINT = "https://cloudcode-pa.googleapis.com"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ANTIGRAVITY_VERSION = "2.0.1"
GEMINI_CLI_USER_AGENT = "google-api-nodejs-client/9.15.1 (gzip)"
GEMINI_X_GOOG_API_CLIENT = "gl-node/24.0.0"
GROUNDING_HINT = (
    "Google Search grounding is enabled for every reply. Use grounded search for "
    "current facts and ALWAYS prefer the LATEST information as of today. Do NOT rely "
    "on your prior/training knowledge, which may be outdated: newer product "
    "generations, versions, models, prices, and releases may exist that you are "
    "unaware of. If search results show something newer than you expected, trust the "
    "search results. Verify claims against evidence, separate confirmed facts from "
    "inference, and include short source URLs only when they help the reply."
)
GROUNDING_RETRY_HINT = (
    "\n\n[Google Search grounding 재확인]\n"
    "이 요청은 최신/외부 사실 확인이 필요하지만 이전 응답에서 검색 근거 메타데이터가 확인되지 않았다. "
    "반드시 Google Search grounding을 사용해 현재 사실을 확인한 뒤 답해. "
    "검색 근거가 충분하지 않으면 가격, 결과, 점수, 순위, 일정, 출시 여부, 재고, 법/정책 같은 세부 사실을 단정하지 말고 "
    "확인되지 않았다고 부드럽게 말해."
)
GROUNDING_REQUIRED_RE = re.compile(r"\[최신/외부 사실 검증 필요\]|최신/외부 사실 검증 필요")
GROUNDING_METADATA_KEYS = {
    "groundingMetadata",
    "grounding_metadata",
    "groundingChunks",
    "grounding_chunks",
    "groundingSupports",
    "grounding_supports",
    "webSearchQueries",
    "web_search_queries",
    "searchEntryPoint",
    "search_entry_point",
    "retrievalMetadata",
    "retrieval_metadata",
    "citationMetadata",
    "citation_metadata",
}
MODEL_ALIASES = {
    # Gemini 3.5 Flash tiers (as reported by fetchAvailableModels)
    "gemini-3.5-flash": "gemini-3.5-flash-low",
    "gemini-3.5-flash-high": "gemini-3-flash-agent",
    "gemini-3.5-flash-medium": "gemini-3.5-flash-low",
    "gemini-3.5-flash-low": "gemini-3.5-flash-extra-low",
    # Claude aliases
    "claude-opus-4.6": "claude-opus-4-6-thinking",
    "claude-opus-4-6": "claude-opus-4-6-thinking",
    "claude-4.6-opus": "claude-opus-4-6-thinking",
    "claude-4-6-opus": "claude-opus-4-6-thinking",
    "claude-opus-4.6-thinking": "claude-opus-4-6-thinking",
    # Gemini Pro aliases
    "gemini-3.1-pro-high": "gemini-pro-agent",
    "gemini-3.1-pro": "gemini-3.1-pro-low",
}
IMAGE_MIME_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp"}
IMAGE_ASPECT_RATIOS = {"landscape": "16:9", "square": "1:1", "portrait": "9:16"}

# ---------------------------------------------------------------------------
# HTTP retry helpers
# ---------------------------------------------------------------------------
_MAX_RETRIES = 3
_RETRYABLE_CODES = {429, 500, 502, 503, 504}

# Dynamic Model Downgrade Fallback: when gemini-3-flash-agent returns
# MODEL_CAPACITY_EXHAUSTED (503), try cheaper tiers in order.
CAPACITY_FALLBACK_CHAIN: dict[str, list[str]] = {
    "gemini-3-flash-agent": ["gemini-3.5-flash-low", "gemini-3.5-flash-extra-low", "gemini-pro-agent"],
    "gemini-3.5-flash-low": ["gemini-3.5-flash-extra-low", "gemini-pro-agent"],
    "gemini-3.5-flash-extra-low": ["gemini-pro-agent"],
}


def _is_capacity_exhausted(exc: Exception) -> bool:
    """Return True when the server refused with 503 MODEL_CAPACITY_EXHAUSTED."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return False
    if getattr(resp, "status_code", None) != 503:
        return False
    try:
        return "MODEL_CAPACITY_EXHAUSTED" in resp.text
    except Exception:
        return False


def _retry_http(func, *, max_retries: int = _MAX_RETRIES):
    """Call *func()* up to max_retries+1 times on transient HTTP errors with exponential backoff.

    MODEL_CAPACITY_EXHAUSTED (503) is re-raised immediately — retrying the same
    model on a capacity-exhausted backend wastes quota; callers handle it via
    CAPACITY_FALLBACK_CHAIN instead.
    """
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            return func()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code not in _RETRYABLE_CODES or attempt == max_retries:
                raise
            if _is_capacity_exhausted(exc):
                raise  # let the caller switch to a fallback model
            log.warning(
                "Upstream HTTP %d — retry %d/%d in %.1fs: %s",
                code, attempt + 1, max_retries, delay, exc.request.url,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


@dataclass(frozen=True)
class AntigravityCredentials:
    access_token: str
    refresh_token: str
    expires_ms: int
    project_id: str = ""

    @property
    def expired(self) -> bool:
        return self.expires_ms > 0 and self.expires_ms < int(time.time() * 1000) + 60_000


class AntigravityClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._project_id = settings.antigravity_project_id
        self._pool: httpx.Client | None = None
        self._async_pool: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.Client:
        """Persistent, connection-reusing HTTP client (keep-alive to cloudcode-pa).

        httpx.Client is thread-safe, so concurrent generate_raw() calls — run via
        the proxy's asyncio.to_thread pool, e.g. parallel web_search — safely share
        one connection pool, saving a fresh TLS handshake (~0.3-0.5s) per request.
        """
        if self._pool is None:
            self._pool = httpx.Client(
                timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._pool

    @property
    def _async_http(self) -> httpx.AsyncClient:
        """Async HTTP client for real-time SSE streaming (event-loop-bound)."""
        if self._async_pool is None:
            self._async_pool = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._async_pool

    def complete(self, *, system: str, prompt: str, memories: list[str], model: str = "", grounding: bool = True) -> str:
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        requested_model = model.strip() or self.settings.model
        resolved = MODEL_ALIASES.get(requested_model, requested_model)
        inner = self._build_gemini_request(system=system, prompt=prompt, memories=memories, grounding=grounding)
        base_wrapped = {
            "project": project_id,
            "request": inner,
            "requestType": "agent",
            "userAgent": "antigravity",
        }
        headers = self._antigravity_headers(creds.access_token)
        _timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        def _do_post(body):
            r = self._http.post(_url, json=body, headers=headers, timeout=_timeout)
            r.raise_for_status()
            return r.json()

        fallback_models = [resolved] + CAPACITY_FALLBACK_CHAIN.get(resolved, [])
        data: dict[str, Any] | None = None
        successful_model = resolved
        for attempt_model in fallback_models:
            _wrapped = {**base_wrapped, "model": attempt_model, "requestId": "agent-" + str(uuid.uuid4())}
            try:
                data = _retry_http(lambda b=_wrapped: _do_post(b))
                successful_model = attempt_model
                break
            except httpx.HTTPStatusError as exc:
                if _is_capacity_exhausted(exc) and attempt_model is not fallback_models[-1]:
                    log.warning(
                        "MODEL_CAPACITY_EXHAUSTED on %s — downgrading to next fallback model",
                        attempt_model,
                    )
                    continue
                raise
        assert data is not None
        text = self._extract_text(data)
        if grounding and self._needs_grounding_retry(system=system, prompt=prompt, response=data):
            log.info("grounding metadata missing for current-fact reply; retrying with stricter grounding prompt")
            retry_inner = self._build_gemini_request(
                system=system + GROUNDING_RETRY_HINT,
                prompt=prompt,
                memories=memories,
                grounding=True,
            )
            retry_inner["generationConfig"]["temperature"] = 0.35
            retry_inner["generationConfig"]["topP"] = 0.8
            retry_wrapped = {**base_wrapped, "model": successful_model, "request": retry_inner, "requestId": "agent-grounding-retry-" + str(uuid.uuid4())}
            retry_data = _retry_http(lambda b=retry_wrapped: _do_post(b))
            retry_text = self._extract_text(retry_data)
            if retry_text:
                return retry_text
        return text

    def generate_raw(self, *, request: dict[str, Any], model: str = "") -> dict[str, Any]:
        """Post a fully-formed Gemini `request` (systemInstruction / contents /
        tools / generationConfig) and return the raw response dict. Lets callers
        do function calling: they build contents with functionCall/functionResponse
        parts and read functionCall parts back out of the response."""
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        requested_model = (model or "").strip() or self.settings.model
        resolved = MODEL_ALIASES.get(requested_model, requested_model)
        base_wrapped = {
            "project": project_id,
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
        }
        headers = self._antigravity_headers(creds.access_token)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        fallback_models = [resolved] + CAPACITY_FALLBACK_CHAIN.get(resolved, [])
        for attempt_model in fallback_models:
            _wrapped = {**base_wrapped, "model": attempt_model, "requestId": "agent-" + str(uuid.uuid4())}
            try:
                def _do(b=_wrapped):
                    r = self._http.post(_url, json=b, headers=headers)
                    r.raise_for_status()
                    return r.json()
                return _retry_http(_do)
            except httpx.HTTPStatusError as exc:
                try:
                    log.error("Antigravity generate_raw HTTPStatusError body: %s", exc.response.text)
                except Exception:
                    pass
                if _is_capacity_exhausted(exc) and attempt_model is not fallback_models[-1]:
                    log.warning(
                        "MODEL_CAPACITY_EXHAUSTED on %s (generate_raw) — downgrading to next fallback model",
                        attempt_model,
                    )
                    continue
                raise
        raise RuntimeError("All fallback models exhausted (generate_raw)")

    def generate_raw_stream(
        self,
        *,
        request: dict[str, Any],
        model: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        """POST to v1internal:streamGenerateContent and yield raw chunk dicts (sync).

        Raises httpx.HTTPStatusError if upstream returns non-2xx — callers should
        catch that and fall back to generate_raw() if streaming is unsupported.
        Each yielded dict has the same shape as a non-streaming candidate response.
        """
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        requested_model = (model or "").strip() or self.settings.model
        resolved = MODEL_ALIASES.get(requested_model, requested_model)
        wrapped = {
            "project": project_id,
            "model": resolved,
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "agent-stream-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        try:
            with self._http.stream(
                "POST",
                f"{ENDPOINT}/v1internal:streamGenerateContent",
                json=wrapped,
                headers=headers,
                timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    line = line.strip()
                    if not line or line in ("[", "]", ","):
                        continue
                    line = line.lstrip(",")
                    text = line.removeprefix("data:").strip()
                    if not text or text == "[DONE]":
                        continue
                    try:
                        yield json.loads(text)
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPStatusError as exc:
            try:
                log.error("Antigravity generate_raw_stream HTTPStatusError body: %s", exc.response.text)
            except Exception:
                pass
            raise

    async def generate_raw_stream_async(
        self,
        *,
        request: dict[str, Any],
        model: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Async streaming version of generate_raw_stream using httpx.AsyncClient.

        Yields parsed response chunks as they arrive from upstream; each chunk has
        the same structure as a non-streaming candidate response. Raises
        httpx.HTTPStatusError on non-2xx — callers fall back to complete().
        """
        creds = await asyncio.to_thread(self._valid_credentials)
        project_id = self._project_id or creds.project_id
        if not project_id:
            project_id = await asyncio.to_thread(self._discover_project, creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        requested_model = (model or "").strip() or self.settings.model
        resolved = MODEL_ALIASES.get(requested_model, requested_model)
        wrapped = {
            "project": project_id,
            "model": resolved,
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "agent-astream-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        try:
            async with self._async_http.stream(
                "POST",
                f"{ENDPOINT}/v1internal:streamGenerateContent",
                json=wrapped,
                headers=headers,
                timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line in ("[", "]", ","):
                        continue
                    line = line.lstrip(",")
                    text = line.removeprefix("data:").strip()
                    if not text or text == "[DONE]":
                        continue
                    try:
                        yield json.loads(text)
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPStatusError as exc:
            try:
                body = await exc.response.aread()
                log.error("Antigravity generate_raw_stream_async HTTPStatusError body: %s", body.decode("utf-8", errors="replace"))
            except Exception:
                pass
            raise

    def generate_image(
        self,
        *,
        prompt: str,
        output_dir: Path,
        aspect_ratio: str = "square",
        image_size: str = "1K",
        model: str = "",
    ) -> Path:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("image prompt is empty")
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        request = self._build_image_request(prompt=prompt, aspect_ratio=aspect_ratio, image_size=image_size)
        wrapped = {
            "project": project_id,
            "model": model or self.settings.image_model,
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "image-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        _img_timeout = httpx.Timeout(connect=15.0, read=240.0, write=30.0, pool=30.0)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        def _do_img():
            r = self._http.post(_url, json=wrapped, headers=headers, timeout=_img_timeout)
            r.raise_for_status()
            return r.json()

        data = _retry_http(_do_img)
        image_data, kind, extension = self._extract_image_result(data)
        if not image_data:
            raise RuntimeError("Antigravity response contained no image bytes or image URL.")
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"antigravity_generated_{int(time.time())}_{uuid.uuid4().hex[:8]}.{extension or 'png'}"
        if kind == "url":
            dl = self._http.get(image_data, timeout=60.0)
            dl.raise_for_status()
            path.write_bytes(dl.content)
        else:
            if image_data.startswith("data:image/"):
                header, _, image_data = image_data.partition(",")
                mime = header.split(";", 1)[0].removeprefix("data:").lower()
                path = path.with_suffix("." + IMAGE_MIME_EXTENSIONS.get(mime, extension or "png"))
            path.write_bytes(base64.b64decode(image_data))
        return path

    def fetch_available_image_models(self) -> list[str]:
        """Live image-model catalog from the backend (v1internal:fetchAvailableModels).
        Returns image model IDs, or [] on any failure so callers can fall back."""
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            return []
        headers = self._antigravity_headers(creds.access_token)
        body = {"project": project_id, "requestId": "images-" + str(uuid.uuid4())}
        r = self._http.post(f"{ENDPOINT}/v1internal:fetchAvailableModels", json=body, headers=headers, timeout=30.0)
        r.raise_for_status()
        payload = r.json()
        ids = payload.get("imageGenerationModelIds") or payload.get("image_generation_model_ids") or []
        return [str(x) for x in ids] if isinstance(ids, list) else []

    def describe_image(self, *, image_path: Path, prompt: str = "") -> str:
        return self.describe_media(
            media_path=image_path,
            mime_type=mimetypes.guess_type(str(image_path))[0] or "image/jpeg",
            prompt=prompt
            or (
                "Analyze this shared image in Korean. "
                "Describe visible UI, logos, people, objects, places, situations, and readable text as accurately as possible. "
                "Summarize the screen structure and key contents in 4 to 8 sentences. "
                "Do not guess; clearly separate visible facts from uncertain parts."
            ),
            max_bytes=8 * 1024 * 1024,
            model="gemini-3.5-flash-high",
        )

    def describe_media(self, *, media_path: Path, mime_type: str, prompt: str, max_bytes: int = 20 * 1024 * 1024, model: str | None = None) -> str:
        if not media_path.exists():
            raise FileNotFoundError(str(media_path))
        raw = media_path.read_bytes()
        if len(raw) > max_bytes:
            raise RuntimeError(f"media file is too large for inline analysis: {len(raw)} bytes")
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        media_b64 = base64.b64encode(raw).decode("ascii")
        request = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime_type, "data": media_b64}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "maxOutputTokens": 1200,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
            ],
        }
        model_name = model or self.settings.model
        wrapped = {
            "project": project_id,
            "model": MODEL_ALIASES.get(model_name, model_name),
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "vision-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        _med_timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        def _do_med():
            r = self._http.post(_url, json=wrapped, headers=headers, timeout=_med_timeout)
            r.raise_for_status()
            return r.json()

        data = _retry_http(_do_med)
        return self._extract_text(data).strip()

    def analyze_youtube_url(self, *, url: str, prompt: str) -> str:
        url = url.strip()
        if not url:
            raise ValueError("YouTube URL is empty.")
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        request = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"fileData": {"fileUri": url}},
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "maxOutputTokens": 1800,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }
        wrapped = {
            "project": project_id,
            "model": MODEL_ALIASES.get(self.settings.model, self.settings.model),
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "youtube-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        _yt_timeout = httpx.Timeout(connect=15.0, read=240.0, write=30.0, pool=30.0)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        def _do_yt():
            r = self._http.post(_url, json=wrapped, headers=headers, timeout=_yt_timeout)
            r.raise_for_status()
            return r.json()

        data = _retry_http(_do_yt)
        return self._extract_text(data).strip()

    def generate_youtube_raw(self, *, request: dict[str, Any], model: str = "") -> dict[str, Any]:
        """Post a fully-formed Gemini `request` containing a YouTube fileData part
        through the same v1internal special wrapper that `analyze_youtube_url`
        uses (requestType="agent", userAgent="antigravity"), returning the raw
        response dict. This is the only path cloudcode-pa accepts for YouTube
        fileData URIs; the standard generate_raw path rejects them. The caller
        is responsible for building `request` (contents / generationConfig)."""
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        requested_model = (model or "").strip() or self.settings.model
        wrapped = {
            "project": project_id,
            "model": MODEL_ALIASES.get(requested_model, requested_model),
            "request": request,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "youtube-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        _yt_timeout = httpx.Timeout(connect=15.0, read=240.0, write=30.0, pool=30.0)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        def _do_yt():
            r = self._http.post(_url, json=wrapped, headers=headers, timeout=_yt_timeout)
            r.raise_for_status()
            return r.json()

        return _retry_http(_do_yt)

    def analyze_web_url(self, *, url: str, prompt: str) -> str:
        url = url.strip()
        if not url:
            raise ValueError("Web URL is empty.")
        creds = self._valid_credentials()
        project_id = self._project_id or creds.project_id or self._discover_project(creds.access_token)
        if not project_id:
            raise RuntimeError("Could not resolve Antigravity project id.")
        system = (
            "You are Antigravity Proxy's internal web link analyzer. "
            "Use the directly fetched page text together with Google Search grounding, "
            "then answer concisely in Korean while separating confirmed facts from inference."
        )
        inner = self._build_gemini_request(system=system, prompt=prompt, memories=[])
        inner["generationConfig"]["temperature"] = 0.25
        inner["generationConfig"]["maxOutputTokens"] = 1600
        wrapped = {
            "project": project_id,
            "model": MODEL_ALIASES.get(self.settings.model, self.settings.model),
            "request": inner,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": "web-url-" + str(uuid.uuid4()),
        }
        headers = self._antigravity_headers(creds.access_token)
        _web_timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0)
        _url = f"{ENDPOINT}/v1internal:generateContent"

        def _do_web():
            r = self._http.post(_url, json=wrapped, headers=headers, timeout=_web_timeout)
            r.raise_for_status()
            return r.json()

        data = _retry_http(_do_web)
        return self._extract_text(data).strip()

    def _load_credentials(self, path: Path) -> AntigravityCredentials:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AntigravityCredentials(
            access_token=str(data.get("access") or data.get("access_token") or ""),
            refresh_token=str(data.get("refresh") or data.get("refresh_token") or ""),
            expires_ms=int(data.get("expires") or data.get("expires_at_ms") or 0),
            project_id=str(data.get("project_id") or data.get("cloudaicompanion_project") or ""),
        )

    def _valid_credentials(self) -> AntigravityCredentials:
        creds = self._load_credentials(self.settings.antigravity_auth_file)
        if not creds.expired:
            return creds
        return self._refresh_credentials(creds)

    def _refresh_credentials(self, creds: AntigravityCredentials) -> AntigravityCredentials:
        if not creds.refresh_token:
            raise RuntimeError("Antigravity refresh token is missing.")
        client = self._load_oauth_client()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        }
        response = self._http.post(TOKEN_ENDPOINT, data=payload, timeout=30.0)
        try:
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError:
            cli_creds = self._refresh_credentials_via_cli(creds)
            if cli_creds:
                return cli_creds
            raise
        access = str(data.get("access_token") or "").strip()
        if not access:
            raise RuntimeError("Antigravity refresh response did not include access token.")
        refresh = str(data.get("refresh_token") or "").strip() or creds.refresh_token
        expires_in = int(data.get("expires_in") or 3600)
        fresh = AntigravityCredentials(
            access_token=access,
            refresh_token=refresh,
            expires_ms=int((time.time() + max(60, expires_in)) * 1000),
            project_id=creds.project_id,
        )
        self._save_credentials(fresh)
        return fresh

    def _refresh_credentials_via_cli(self, previous: AntigravityCredentials) -> AntigravityCredentials | None:
        # Non-interactive guard: the agy CLI may open a browser or prompt for
        # OAuth consent. In a background/systemd environment (no TTY) that would
        # block forever. Skip the CLI path entirely in those cases.
        if not sys.stdin.isatty():
            log.warning(
                "Non-TTY environment detected; skipping interactive CLI OAuth refresh "
                "to prevent blocking. Re-run 'agy auth' from an interactive terminal "
                "to renew credentials, then restart the service."
            )
            return None
        cli = self.settings.antigravity_cli_path
        if not cli.exists():
            return None
        try:
            subprocess.run(
                [str(cli), "--prompt", "OK", "--print-timeout", "30s"],
                capture_output=True,
                text=True,
                timeout=75,
                check=False,
            )
        except Exception:
            return None
        fresh = self._load_cli_credentials(previous)
        if not fresh or fresh.expired:
            return None
        self._save_credentials(fresh)
        return fresh

    def _load_cli_credentials(self, previous: AntigravityCredentials) -> AntigravityCredentials | None:
        path = self.settings.antigravity_cli_token_file
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        token = data.get("token") if isinstance(data.get("token"), dict) else data
        access = str(token.get("access_token") or token.get("access") or "").strip()
        refresh = str(token.get("refresh_token") or token.get("refresh") or previous.refresh_token or "").strip()
        expires = _expiry_to_ms(token.get("expiry") or token.get("expires_at") or token.get("expires") or 0)
        if not access:
            return None
        return AntigravityCredentials(
            access_token=access,
            refresh_token=refresh,
            expires_ms=expires,
            project_id=previous.project_id,
        )

    def _load_oauth_client(self) -> dict[str, str]:
        import os

        env_id = os.getenv("GOOGLE_ANTIGRAVITY_CLIENT_ID", "").strip()
        env_secret = os.getenv("GOOGLE_ANTIGRAVITY_CLIENT_SECRET", "").strip()
        if env_id and env_secret:
            return {"client_id": env_id, "client_secret": env_secret}
        data = json.loads(self.settings.antigravity_client_file.read_text(encoding="utf-8"))
        client_id = str(data.get("client_id") or "").strip()
        client_secret = str(data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise RuntimeError("Antigravity OAuth client id/secret is missing.")
        return {"client_id": client_id, "client_secret": client_secret}

    def _save_credentials(self, creds: AntigravityCredentials) -> None:
        path = self.settings.antigravity_auth_file
        existing: dict[str, Any] = {}
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        existing.update(
            {
                "access": creds.access_token,
                "refresh": creds.refresh_token,
                "expires": creds.expires_ms,
            }
        )
        if creds.project_id:
            existing["project_id"] = creds.project_id
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _discover_project(self, access_token: str) -> str:
        headers = self._gemini_cli_headers(access_token)
        payload: dict[str, Any] = {
            "metadata": {
                "duetProject": self.settings.antigravity_project_id,
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        if self.settings.antigravity_project_id:
            payload["cloudaicompanionProject"] = self.settings.antigravity_project_id
        response = self._http.post(
            f"{ENDPOINT}/v1internal:loadCodeAssist",
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        project = (
            data.get("cloudaicompanionProject")
            or data.get("cloudaicompanion_project")
            or data.get("project")
            or data.get("projectId")
            or ""
        )
        self._project_id = str(project)
        return self._project_id

    def _gemini_cli_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"{GEMINI_CLI_USER_AGENT} model/{self.settings.model}",
            "X-Goog-Api-Client": GEMINI_X_GOOG_API_CLIENT,
            "x-activity-request-id": str(uuid.uuid4()),
        }

    def _antigravity_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Antigravity/{ANTIGRAVITY_VERSION} Chrome/138.0.7204.235 "
                "Electron/37.3.1 Safari/537.36"
            ),
            "X-Goog-Api-Client": f"antigravity-cli/{ANTIGRAVITY_VERSION}",
            "x-activity-request-id": str(uuid.uuid4()),
        }

    def _build_gemini_request(self, *, system: str, prompt: str, memories: list[str], grounding: bool = True) -> dict[str, Any]:
        memory_text = "\n".join(f"- {m}" for m in memories if m.strip())
        full_system = system
        if memory_text:
            full_system += (
                "\n\n[대화 기억]\n"
                "아래 기억은 현재 질문과 관련될 때만 참고용이다. 질문과 무관하면 무시하고, "
                "사용자가 묻지 않은 과거 화제를 먼저 꺼내지 마라.\n" + memory_text
            )
        if grounding:
            _today = datetime.now().astimezone().strftime("%Y-%m-%d")
            full_system += chr(10) + chr(10) + GROUNDING_HINT + chr(10) + (
                f"Today is {_today}; prefer the latest info as of today and trust "
                "fresh search results over prior assumptions."
            )
        request: dict[str, Any] = {
            "systemInstruction": {"role": "system", "parts": [{"text": full_system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                # Accuracy-first: lower sampling reduces off-topic drift and
                # factual variance while keeping the warm tone.
                "temperature": 0.5,
                "topP": 0.88,
                "maxOutputTokens": 4096,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
            ],
        }
        if grounding:
            request["tools"] = [{"google_search": {}}]
        return request

    def _build_image_request(self, *, prompt: str, aspect_ratio: str, image_size: str) -> dict[str, Any]:
        ratio = IMAGE_ASPECT_RATIOS.get(aspect_ratio, IMAGE_ASPECT_RATIOS["square"])
        config: dict[str, Any] = {"aspectRatio": ratio}
        if image_size:
            config["imageSize"] = image_size
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": config,
            },
        }

    def grounded_search(self, *, query: str, model: str = "", limit: int = 5) -> dict[str, Any]:
        """Google-Search-grounded query via generateContent. Returns
        {"answer": str, "queries": [str], "results": [{"title","url","snippet"}]}."""
        query = (query or "").strip()
        if not query:
            return {"answer": "", "queries": [], "results": []}
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        system = (
            f"You are a real-time web search assistant. Today is {today}. "
            "ALWAYS use Google Search to answer; never rely on your own prior/training "
            "knowledge, which is outdated. Products, versions, models, prices, rankings "
            "and releases change over time and newer generations may exist that you are "
            "unaware of. Search for the CURRENT, LATEST state as of today, prefer recently "
            "published sources, and check release dates. If results show a newer "
            "version/model/product than you expected, TRUST the search results over your "
            "assumptions. For latest/newest/current/comparison questions, explicitly "
            "search for the most recent generation and list what exists now. Cite sources."
        )
        request = self._build_gemini_request(system=system, prompt=query, memories=[], grounding=True)
        data = self.generate_raw(request=request, model=model or self.settings.model)
        return {
            "answer": self._extract_text(data),
            "queries": self._extract_search_queries(data),
            "results": self._extract_grounding_results(data, limit=limit),
        }

    def _grounding_metadata(self, data: dict[str, Any]) -> dict[str, Any]:
        inner = data.get("response") if isinstance(data.get("response"), dict) else data
        candidates = inner.get("candidates") if isinstance(inner, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return {}
        for key in ("groundingMetadata", "grounding_metadata"):
            md = candidates[0].get(key)
            if isinstance(md, dict):
                return md
        return {}

    def _extract_search_queries(self, data: dict[str, Any]) -> list[str]:
        md = self._grounding_metadata(data)
        for key in ("webSearchQueries", "web_search_queries"):
            q = md.get(key)
            if isinstance(q, list):
                return [str(x) for x in q if x]
        return []

    def _extract_grounding_results(self, data: dict[str, Any], *, limit: int = 5) -> list[dict[str, str]]:
        md = self._grounding_metadata(data)
        chunks = None
        for key in ("groundingChunks", "grounding_chunks", "chunks"):
            if isinstance(md.get(key), list):
                chunks = md[key]
                break
        if not chunks:
            return []
        snippets: dict[int, list[str]] = {}
        for key in ("groundingSupports", "grounding_supports", "supports"):
            supports = md.get(key)
            if isinstance(supports, list):
                for sup in supports:
                    if not isinstance(sup, dict):
                        continue
                    seg = ((sup.get("segment") or {}).get("text")) or sup.get("text") or ""
                    idxs = sup.get("groundingChunkIndices") or sup.get("grounding_chunk_indices") or []
                    for i in idxs:
                        if seg and isinstance(i, int):
                            snippets.setdefault(i, []).append(str(seg).strip())
                break
        results: list[dict[str, str]] = []
        for i, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            web = chunk.get("web") if isinstance(chunk.get("web"), dict) else {}
            url = web.get("uri") or web.get("url") or chunk.get("uri") or chunk.get("url") or ""
            title = web.get("title") or chunk.get("title") or ""
            if not url:
                continue
            snip = " ".join(snippets.get(i, []))[:500]
            results.append({"title": str(title) or str(url), "url": str(url), "snippet": snip})
            if len(results) >= limit:
                break
        return results

    def _extract_text(self, data: dict[str, Any]) -> str:
        inner = data.get("response") if isinstance(data.get("response"), dict) else data
        candidates = inner.get("candidates") if isinstance(inner, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("text")]
        return "\n".join(texts).strip()

    def _needs_grounding_retry(self, *, system: str, prompt: str, response: dict[str, Any]) -> bool:
        text = f"{system}\n{prompt}"
        return bool(GROUNDING_REQUIRED_RE.search(text)) and not self._has_grounding_metadata(response)

    def _has_grounding_metadata(self, value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in GROUNDING_METADATA_KEYS and self._metadata_has_content(child):
                    return True
                if self._has_grounding_metadata(child):
                    return True
        elif isinstance(value, list):
            return any(self._has_grounding_metadata(child) for child in value)
        return False

    def _metadata_has_content(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (int, float, bool)):
            return bool(value)
        if isinstance(value, dict):
            return any(self._metadata_has_content(child) for child in value.values())
        if isinstance(value, list):
            return any(self._metadata_has_content(child) for child in value)
        return True

    def _extract_image_result(self, payload: Any) -> tuple[str | None, str, str]:
        for item in self._iter_values(payload):
            if not isinstance(item, dict):
                continue
            inline = item.get("inlineData") or item.get("inline_data")
            if isinstance(inline, dict):
                data = inline.get("data") or inline.get("b64Json") or inline.get("b64_json")
                if isinstance(data, str) and data.strip():
                    mime = str(inline.get("mimeType") or inline.get("mime_type") or "").lower()
                    return data.strip(), "b64", IMAGE_MIME_EXTENSIONS.get(mime, "png")
            for key in ("imageUrl", "image_url", "url", "uri"):
                value = item.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value, "url", "png"
            for key in ("imageBase64", "image_b64", "b64Json", "b64_json", "base64", "data", "result"):
                value = item.get(key)
                if isinstance(value, str) and self._looks_like_base64_image(value):
                    return value.strip(), "b64", self._image_extension_from_b64(value) or "png"
        return None, "", "png"

    def _iter_values(self, value: Any):
        yield value
        if isinstance(value, dict):
            for child in value.values():
                yield from self._iter_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._iter_values(child)

    def _looks_like_base64_image(self, value: str) -> bool:
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

    def _image_extension_from_b64(self, value: str) -> str:
        text = value.strip()
        if text.startswith("data:image/"):
            header, _, _ = text.partition(",")
            mime = header.split(";", 1)[0].removeprefix("data:").lower()
            return IMAGE_MIME_EXTENSIONS.get(mime, "png")
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


def _expiry_to_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        numeric = int(value)
        return numeric if numeric > 10_000_000_000 else numeric * 1000
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return _expiry_to_ms(int(text))
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0
