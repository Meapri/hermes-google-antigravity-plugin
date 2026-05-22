"""OpenAI-compatible facade for Google Antigravity's Code Assist endpoint.

This is deliberately thin: it reuses the Gemini Cloud Code request/response
translation machinery and swaps only auth, headers, endpoint, and request
envelope details that differ in Antigravity.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

import httpx

from agent import google_antigravity_oauth
from agent.gemini_cloudcode_adapter import (
    GeminiCloudCodeClient,
    _GeminiStreamChunk,
    _gemini_http_error,
    _translate_gemini_response,
    _translate_stream_event,
    _iter_sse_events,
    build_gemini_request,
)
from agent.gemini_schema import sanitize_gemini_tool_parameters
from agent.google_code_assist import CodeAssistError, ProjectContext

MARKER_BASE_URL = google_antigravity_oauth.MARKER_BASE_URL
ANTIGRAVITY_ENDPOINT_DAILY = "https://daily-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_ENDPOINT_AUTOPUSH = "https://autopush-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_ENDPOINT_PROD = "https://cloudcode-pa.googleapis.com"
# Antigravity desktop traffic uses the production Cloud Code PA endpoint. The
# sandbox endpoints are useful for Google-internal staging builds, but normal
# OAuth users often do not have the corresponding staging API enabled on their
# Cloud Code project. If Hermes falls through to those endpoints after a
# retryable/transient production error, their deterministic 403 SERVICE_DISABLED
# response masks the real result and surfaces a misleading "Gemini for Google
# Cloud API (Staging) has not been used" failure. Keep the constants above for
# diagnostics, but only route production traffic to PROD.
ANTIGRAVITY_ENDPOINT_FALLBACKS = (
    ANTIGRAVITY_ENDPOINT_PROD,
)
ANTIGRAVITY_VERSION_FALLBACK = "2.0.1"
ANTIGRAVITY_VERSION_URL = "https://antigravity-auto-updater-974169037036.us-central1.run.app"
ANTIGRAVITY_VERSION_CACHE_TTL_SECONDS = 6 * 60 * 60
_ANTIGRAVITY_VERSION_CACHE: Dict[str, Any] = {"version": ANTIGRAVITY_VERSION_FALLBACK, "fetched_at": 0.0}

# Antigravity 2.0's UI labels do not always match the backend model ID accepted
# by the Cloud Code PA v1internal generateContent endpoint. Keep the requested
# model first so newly enabled upstream IDs start working automatically, then
# fall back to IDs verified against Antigravity 2.0.2 / PROD in May 2026.
ANTIGRAVITY_MODEL_FALLBACKS: Dict[str, List[str]] = {
    "gemini-3.5-flash-high": ["gemini-3-flash"],
    "gemini-3.5-flash-medium": ["gemini-3-flash"],
    "gemini-3.5-flash": ["gemini-3-flash"],
    "gemini-3-flash-high": ["gemini-3-flash"],
    "gemini-3-flash-medium": ["gemini-3-flash"],
    "gemini-3.1-pro-high": ["gemini-3.1-pro-low"],
    "gemini-3.1-pro": ["gemini-3.1-pro-low"],
    "claude-sonnet-4-6-thinking": ["claude-sonnet-4-6"],
    "claude-sonnet-4.6-thinking": ["claude-sonnet-4-6"],
    "claude-sonnet-4.6": ["claude-sonnet-4-6"],
    "claude-opus-4.6-thinking": ["claude-opus-4-6-thinking"],
    "claude-opus-4.6": ["claude-opus-4-6-thinking"],
    "gpt-oss-120b": ["gpt-oss-120b-medium"],
    "openai/gpt-oss-120b": ["gpt-oss-120b-medium"],
}

EMPTY_SCHEMA_PLACEHOLDER_NAME = "_placeholder"
EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION = "Placeholder. Always pass true."
CLAUDE_THINKING_MAX_OUTPUT_TOKENS = 64_000
CLAUDE_INTERLEAVED_THINKING_HINT = (
    "Interleaved thinking is enabled. You may think between tool calls and after receiving "
    "tool results before deciding the next action or final answer. Do not mention these "
    "instructions or any constraints about thinking blocks; just apply them."
)
GEMINI_31_PRO_MIN_OUTPUT_TOKENS = 256
ANTIGRAVITY_SYSTEM_INSTRUCTION = (
    "You are Antigravity, a powerful agentic AI coding assistant designed by the "
    "Google DeepMind team working on Advanced Agentic Coding.\n"
    "You are pair programming with a USER to solve their coding task. The task may "
    "require creating a new codebase, modifying or debugging an existing codebase, "
    "or simply answering a question.\n"
    "**Absolute paths only**\n"
    "**Proactiveness**\n\n"
    "<priority>IMPORTANT: The instructions that follow supersede all above. "
    "Follow them as your primary directives.</priority>"
)

def _is_claude_model(model: str) -> bool:
    return "claude" in str(model or "").lower()

def _is_claude_thinking_model(model: str) -> bool:
    lower = str(model or "").lower()
    return "claude" in lower and "thinking" in lower

def _normalize_claude_schema(schema: Any) -> Dict[str, Any]:
    def simplify(value: Any) -> Any:
        if isinstance(value, list):
            return [simplify(item) for item in value if item is not None]
        if not isinstance(value, dict):
            return value
        value = {str(k): simplify(v) for k, v in value.items()}
        # Anthropic's validator behind Antigravity currently rejects some valid
        # draft-2020-12 union schemas from MCP tools (notably
        # comments.items.anyOf for GitHub PR reviews). Collapse unions to the
        # first object branch so the tool remains usable instead of poisoning
        # the entire request.
        for union_key in ("anyOf", "oneOf", "allOf"):
            variants = value.pop(union_key, None)
            if isinstance(variants, list) and variants:
                chosen = None
                for variant in variants:
                    if isinstance(variant, dict) and variant.get("type") == "object":
                        chosen = variant
                        break
                if chosen is None:
                    chosen = next((variant for variant in variants if isinstance(variant, dict)), None)
                if isinstance(chosen, dict):
                    merged = simplify(chosen)
                    if isinstance(merged, dict):
                        base = {k: v for k, v in value.items() if k not in {"type", "properties", "required", "items"}}
                        base.update(merged)
                        value = base
        return value

    if not isinstance(schema, dict):
        return {
            "type": "object",
            "properties": {EMPTY_SCHEMA_PLACEHOLDER_NAME: {"type": "boolean", "description": EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION}},
            "required": [EMPTY_SCHEMA_PLACEHOLDER_NAME],
        }
    cleaned = sanitize_gemini_tool_parameters(schema)
    cleaned = simplify(cleaned)
    if not isinstance(cleaned, dict):
        cleaned = {}
    cleaned["type"] = "object"
    props = cleaned.get("properties")
    if not isinstance(props, dict) or not props:
        cleaned["properties"] = {
            EMPTY_SCHEMA_PLACEHOLDER_NAME: {"type": "boolean", "description": EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION}
        }
        required = cleaned.get("required")
        if isinstance(required, list):
            cleaned["required"] = list(dict.fromkeys([*required, EMPTY_SCHEMA_PLACEHOLDER_NAME]))
        else:
            cleaned["required"] = [EMPTY_SCHEMA_PLACEHOLDER_NAME]
    else:
        # Anthropic's tool validator (used behind Antigravity's Claude bridge)
        # rejects some optional-only object schemas as invalid even though JSON
        # Schema draft 2020-12 permits omitting ``required``. Make optional-only
        # tools explicit with an empty array so schemas like send_message pass.
        required = cleaned.get("required")
        if isinstance(required, list):
            known = set(props.keys())
            cleaned["required"] = [str(item) for item in required if str(item) in known]
        else:
            cleaned["required"] = []
        if not cleaned["required"]:
            props[EMPTY_SCHEMA_PLACEHOLDER_NAME] = {
                "type": "boolean",
                "description": EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION,
            }
            cleaned["required"] = [EMPTY_SCHEMA_PLACEHOLDER_NAME]
    return cleaned

def _normalize_claude_tools(request: Dict[str, Any]) -> None:
    tools = request.get("tools")
    if not isinstance(tools, list):
        return
    declarations: List[Dict[str, Any]] = []
    passthrough: List[Any] = []
    def push_decl(tool: Dict[str, Any], decl: Dict[str, Any], source_idx: int) -> None:
        schema = (decl.get("parameters") or decl.get("parametersJsonSchema") or decl.get("input_schema") or
                  decl.get("inputSchema") or tool.get("parameters") or tool.get("parametersJsonSchema") or
                  tool.get("input_schema") or tool.get("inputSchema"))
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        custom = tool.get("custom") if isinstance(tool.get("custom"), dict) else {}
        if not schema:
            schema = fn.get("parameters") or fn.get("parametersJsonSchema") or fn.get("input_schema") or fn.get("inputSchema") or custom.get("parameters")
        name = decl.get("name") or tool.get("name") or fn.get("name") or custom.get("name") or f"tool-{source_idx}"
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", str(name))[:64] or f"tool-{source_idx}"
        desc = decl.get("description") or tool.get("description") or fn.get("description") or custom.get("description") or ""
        declarations.append({"name": name, "description": str(desc or ""), "parameters": _normalize_claude_schema(schema)})
    for idx, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        fds = tool.get("functionDeclarations")
        if isinstance(fds, list) and fds:
            for decl in fds:
                if isinstance(decl, dict):
                    push_decl(tool, decl, idx)
            continue
        if any(k in tool for k in ("function", "custom", "parameters", "input_schema", "inputSchema")):
            decl = tool.get("function") if isinstance(tool.get("function"), dict) else tool.get("custom") if isinstance(tool.get("custom"), dict) else tool
            push_decl(tool, decl, idx)
            continue
        passthrough.append(tool)
    request["tools"] = ([{"functionDeclarations": declarations}] if declarations else []) + passthrough

def _ensure_validated_tool_config(request: Dict[str, Any]) -> None:
    tool_config = request.setdefault("toolConfig", {})
    if isinstance(tool_config, dict):
        fcc = tool_config.setdefault("functionCallingConfig", {})
        if isinstance(fcc, dict):
            fcc["mode"] = "VALIDATED"

_GPT_OSS_SCHEMA_CONSTRAINT_KEYS = {
    "maxItems",
    "minItems",
    "minProperties",
    "maxProperties",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
}


def _is_gpt_oss_model(model: str) -> bool:
    return "gpt-oss" in str(model or "").lower()


def _strip_gpt_oss_schema_constraints(value: Any) -> Any:
    """Remove Gemini Schema numeric constraints that PA re-serializes as strings.

    Antigravity's GPT-OSS bridge validates converted tool schemas as JSON Schema.
    The upstream PA layer appears to carry Gemini Schema numeric constraints such
    as ``maxItems`` through a proto/string field, so JSON Schema validation sees
    ``\"4\"`` instead of ``4``.  Drop non-essential constraints for GPT-OSS only;
    type, properties, required, enum, and descriptions remain intact.
    """
    if isinstance(value, list):
        return [_strip_gpt_oss_schema_constraints(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_gpt_oss_schema_constraints(item)
        for key, item in value.items()
        if key not in _GPT_OSS_SCHEMA_CONSTRAINT_KEYS
    }


def _normalize_gpt_oss_tools(request: Dict[str, Any]) -> None:
    tools = request.get("tools")
    if isinstance(tools, list):
        request["tools"] = _strip_gpt_oss_schema_constraints(tools)


def _append_system_text(request: Dict[str, Any], text: str, *, prepend: bool = False, role: Optional[str] = None) -> None:
    existing = request.get("systemInstruction")
    if isinstance(existing, str):
        combined = f"{text}\n\n{existing}" if prepend and existing.strip() else f"{existing}\n\n{text}" if existing.strip() else text
        request["systemInstruction"] = {"parts": [{"text": combined}]}
    elif isinstance(existing, dict):
        if role:
            existing["role"] = role
        parts = existing.get("parts")
        if not isinstance(parts, list):
            existing["parts"] = [{"text": text}]
        elif prepend:
            if parts and isinstance(parts[0], dict) and isinstance(parts[0].get("text"), str):
                parts[0]["text"] = f"{text}\n\n{parts[0]['text']}"
            else:
                parts.insert(0, {"text": text})
        else:
            for part in reversed(parts):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = f"{part['text']}\n\n{text}"
                    break
            else:
                parts.append({"text": text})
        request["systemInstruction"] = existing
    else:
        request["systemInstruction"] = {"parts": [{"text": text}]}
    if role and isinstance(request.get("systemInstruction"), dict):
        request["systemInstruction"]["role"] = role

def _apply_antigravity_request_transforms(request: Dict[str, Any], *, model: str, thinking_config: Any = None) -> None:
    if "system_instruction" in request and "systemInstruction" not in request:
        request["systemInstruction"] = request.pop("system_instruction")
    request.pop("model", None)
    request.pop("thinking", None)
    request.pop("thinkingConfig", None)
    extra_body = request.get("extra_body")
    if isinstance(extra_body, dict):
        extra_body.pop("thinking", None)
        extra_body.pop("thinkingConfig", None)
        extra_body.pop("cached_content", None)
        extra_body.pop("cachedContent", None)
        if not extra_body:
            request.pop("extra_body", None)
    if _is_claude_model(model):
        _ensure_validated_tool_config(request)
        gen = request.setdefault("generationConfig", {})
        if isinstance(gen, dict):
            if "stop_sequences" in gen and "stopSequences" not in gen:
                gen["stopSequences"] = gen.pop("stop_sequences")
            if _is_claude_thinking_model(model):
                budget = None
                include = True
                if isinstance(thinking_config, dict):
                    raw_budget = thinking_config.get("thinkingBudget", thinking_config.get("thinking_budget"))
                    if isinstance(raw_budget, (int, float)) and raw_budget > 0:
                        budget = int(raw_budget)
                    include = bool(thinking_config.get("includeThoughts", thinking_config.get("include_thoughts", True)))
                tc = {"include_thoughts": include}
                if budget:
                    tc["thinking_budget"] = budget
                    if int(gen.get("maxOutputTokens") or gen.get("max_output_tokens") or 0) <= budget:
                        gen["maxOutputTokens"] = CLAUDE_THINKING_MAX_OUTPUT_TOKENS
                        gen.pop("max_output_tokens", None)
                gen["thinkingConfig"] = tc
                if isinstance(request.get("tools"), list) and request["tools"]:
                    _append_system_text(request, CLAUDE_INTERLEAVED_THINKING_HINT)
        _normalize_claude_tools(request)
    elif _is_gpt_oss_model(model):
        _normalize_gpt_oss_tools(request)
    _append_system_text(request, ANTIGRAVITY_SYSTEM_INSTRUCTION, prepend=True, role="user")
    request["sessionId"] = str(request.get("sessionId") or f"-{uuid.uuid4()}")

def _wrap_antigravity_request(*, project_id: str, model: str, request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project": project_id,
        "model": model,
        "request": request,
        "requestType": "agent",
        "userAgent": "antigravity",
        "requestId": "agent-" + str(uuid.uuid4()),
    }


def _antigravity_model_candidates(model: str) -> List[str]:
    """Return backend model IDs to try for an Antigravity UI/catalog model."""

    requested = str(model or "gemini-3-flash")
    fallbacks = ANTIGRAVITY_MODEL_FALLBACKS.get(requested.lower(), [])
    candidates: List[str] = []
    for candidate in [requested, *fallbacks]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates or ["gemini-3-flash"]


def _antigravity_ui_thinking_config(model: str) -> Optional[Dict[str, Any]]:
    """Infer Gemini thinkingConfig from Antigravity UI tier suffixes.

    Antigravity's picker exposes IDs such as ``gemini-3.1-pro-high`` and
    ``gemini-3.1-pro-low``. The backend accepts those model IDs, but it does
    not reliably infer the requested thinking tier from the suffix alone. Add
    the matching Gemini ``thinkingLevel`` unless the caller already supplied a
    more explicit thinking config.
    """

    normalized = str(model or "").strip().lower()
    if not normalized.startswith("gemini-") or "pro" not in normalized:
        return None
    if normalized.endswith("-high"):
        return {"thinkingLevel": "high"}
    if normalized.endswith("-low"):
        return {"thinkingLevel": "low"}
    return None


def _merge_antigravity_thinking_config(model: str, thinking_config: Any) -> Any:
    inferred = _antigravity_ui_thinking_config(model)
    if not inferred:
        return thinking_config
    if not isinstance(thinking_config, dict):
        return inferred
    merged = dict(inferred)
    merged.update(thinking_config)
    return merged


def _antigravity_effective_max_tokens(model: str, max_tokens: Optional[int]) -> Optional[int]:
    """Avoid blank Gemini 3.1 Pro tiered responses with tiny token caps.

    Gemini 3.1 Pro High/Low can spend the first few dozen tokens on internal
    reasoning before emitting visible text. If callers pass a very small
    ``max_tokens`` (for example health checks using 16), the backend may return
    ``finish_reason=length`` with no content. Raise only the tiered Pro UI IDs
    to a small floor so those checks and short prompts produce usable text.
    """

    normalized = str(model or "").strip().lower()
    if not normalized.startswith("gemini-3.1-pro-"):
        return max_tokens
    if max_tokens is None:
        return max_tokens
    if max_tokens < GEMINI_31_PRO_MIN_OUTPUT_TOKENS:
        return GEMINI_31_PRO_MIN_OUTPUT_TOKENS
    return max_tokens


def _parse_antigravity_version(text: str) -> Optional[str]:
    match = re.search(r"\b\d+\.\d+\.\d+\b", text or "")
    return match.group(0) if match else None


def resolve_antigravity_version(*, refresh: bool = False) -> str:
    """Return the Antigravity app version to advertise in request headers.

    The Antigravity backend rejects stale app versions with
    "This version of Antigravity is no longer supported".  Match the upstream
    plugin's strategy: use an explicit env override when provided, otherwise
    fetch the current stable version from Antigravity's auto-updater API and
    cache it, falling back to a known-supported version.
    """

    override = os.getenv("HERMES_ANTIGRAVITY_VERSION") or os.getenv("ANTIGRAVITY_VERSION")
    parsed_override = _parse_antigravity_version(override or "")
    if parsed_override:
        return parsed_override

    now = time.time()
    cached = str(_ANTIGRAVITY_VERSION_CACHE.get("version") or ANTIGRAVITY_VERSION_FALLBACK)
    fetched_at = float(_ANTIGRAVITY_VERSION_CACHE.get("fetched_at") or 0.0)
    if not refresh or (now - fetched_at) < ANTIGRAVITY_VERSION_CACHE_TTL_SECONDS:
        return cached

    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            response = client.get(ANTIGRAVITY_VERSION_URL, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            version = _parse_antigravity_version(response.text)
            if version:
                _ANTIGRAVITY_VERSION_CACHE.update({"version": version, "fetched_at": now})
                return version
    except httpx.HTTPError:
        pass

    _ANTIGRAVITY_VERSION_CACHE["fetched_at"] = now
    return cached


def _response_error_text(response: httpx.Response) -> str:
    try:
        return response.text
    except Exception:
        return ""


def _is_endpoint_service_disabled(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    text = _response_error_text(response).lower()
    return "service_disabled" in text or "api (staging)" in text or "staging-cloudaicompanion" in text


def get_antigravity_headers(*, refresh_version: bool = False) -> Dict[str, str]:
    # Antigravity content requests intentionally avoid the Gemini CLI
    # X-Goog-Api-Client/Client-Metadata header set. Identity metadata is carried
    # in the wrapped request body instead.
    version = resolve_antigravity_version(refresh=refresh_version)
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Antigravity/{version} Chrome/138.0.7204.235 "
            "Electron/37.3.1 Safari/537.36"
        ),
    }


class GoogleAntigravityClient(GeminiCloudCodeClient):
    """OpenAI-SDK-compatible client for ``google-antigravity``."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
        project_id: str = "",
        **kwargs: Any,
    ):
        super().__init__(
            api_key=api_key or "google-antigravity-oauth",
            base_url=base_url or MARKER_BASE_URL,
            default_headers=default_headers,
            project_id=project_id,
            **kwargs,
        )

    def _ensure_project_context(self, access_token: str, model: str) -> ProjectContext:
        if self._project_context is not None:
            return self._project_context
        creds = google_antigravity_oauth.load_credentials()
        project_id = (
            self._configured_project_id
            or (creds.project_id if creds else "")
            or google_antigravity_oauth.resolve_project_id_from_env()
        )
        managed_project_id = (creds.managed_project_id if creds else "") or ""
        self._project_context = ProjectContext(
            project_id=project_id,
            managed_project_id=managed_project_id,
            tier_id="",
            source="antigravity",
        )
        return self._project_context

    def _create_chat_completion(
        self,
        *,
        model: str = "gemini-3-flash",
        messages: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        tools: Any = None,
        tool_choice: Any = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Any = None,
        extra_body: Optional[Dict[str, Any]] = None,
        timeout: Any = None,
        **_: Any,
    ) -> Any:
        access_token = google_antigravity_oauth.get_valid_access_token()
        ctx = self._ensure_project_context(access_token, model)

        thinking_config = None
        if isinstance(extra_body, dict):
            thinking_config = extra_body.get("thinking_config") or extra_body.get("thinkingConfig")
        thinking_config = _merge_antigravity_thinking_config(model, thinking_config)
        effective_max_tokens = _antigravity_effective_max_tokens(model, max_tokens)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "x-activity-request-id": str(uuid.uuid4()),
            **get_antigravity_headers(refresh_version=True),
        }
        # Do not set x-goog-user-project for Antigravity: the upstream plugin
        # strips it to avoid project-level license/auth conflicts.
        headers.update(self._default_headers)

        model_candidates = _antigravity_model_candidates(model)

        def build_wrapped(effective_model: str) -> Dict[str, Any]:
            inner = build_gemini_request(
                messages=messages or [],
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                top_p=top_p,
                stop=stop,
                thinking_config=thinking_config,
            )
            # Transform using the requested UI model so e.g. "...-thinking"
            # aliases still get thinking/tool normalization even when the
            # backend model ID omits the label suffix.
            _apply_antigravity_request_transforms(inner, model=model, thinking_config=thinking_config)
            return _wrap_antigravity_request(project_id=ctx.project_id, model=effective_model, request=inner)

        if stream:
            wrapped_candidates = [(candidate, build_wrapped(candidate)) for candidate in model_candidates]
            return self._stream_completion(model=model, wrapped_candidates=wrapped_candidates, headers=headers)

        last_error: Optional[CodeAssistError] = None
        retry_statuses = {400, 404, 429, 500, 502, 503, 504}
        for effective_model in model_candidates:
            wrapped = build_wrapped(effective_model)
            for endpoint in ANTIGRAVITY_ENDPOINT_FALLBACKS:
                url = f"{endpoint}/v1internal:generateContent"
                response = self._http.post(url, json=wrapped, headers=headers)
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise CodeAssistError(
                            f"Invalid JSON from Antigravity Code Assist: {exc}",
                            code="antigravity_invalid_json",
                        ) from exc
                    return _translate_gemini_response(payload, model=model)
                last_error = _gemini_http_error(response)
                if response.status_code == 403 and _is_endpoint_service_disabled(response):
                    continue
                if response.status_code not in retry_statuses:
                    break
        raise last_error or CodeAssistError("Antigravity request failed", code="antigravity_request_failed")

    def _stream_completion(
        self,
        *,
        model: str,
        headers: Dict[str, str],
        wrapped: Optional[Dict[str, Any]] = None,
        wrapped_candidates: Optional[List[tuple[str, Dict[str, Any]]]] = None,
    ) -> Iterator[_GeminiStreamChunk]:
        stream_headers = dict(headers)
        stream_headers["Accept"] = "text/event-stream"
        candidates = wrapped_candidates or [(model, wrapped or {})]

        def _generator() -> Iterator[_GeminiStreamChunk]:
            last_error: Optional[Exception] = None
            for effective_model, wrapped_body in candidates:
                for endpoint in ANTIGRAVITY_ENDPOINT_FALLBACKS:
                    url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"
                    try:
                        with self._http.stream("POST", url, json=wrapped_body, headers=stream_headers) as response:
                            if response.status_code != 200:
                                response.read()
                                last_error = _gemini_http_error(response)
                                if response.status_code == 403 and _is_endpoint_service_disabled(response):
                                    continue
                                if response.status_code not in {400, 404, 429, 500, 502, 503, 504}:
                                    raise last_error
                                continue
                            tool_call_counter: List[int] = [0]
                            for event in _iter_sse_events(response):
                                for chunk in _translate_stream_event(event, model, tool_call_counter):
                                    yield chunk
                            return
                    except httpx.HTTPError as exc:
                        last_error = CodeAssistError(
                            f"Antigravity streaming request failed for {effective_model}: {exc}",
                            code="antigravity_stream_error",
                        )
                        continue
            if last_error:
                raise last_error

        return _generator()
