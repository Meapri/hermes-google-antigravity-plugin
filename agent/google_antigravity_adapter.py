"""OpenAI-compatible facade for Google Antigravity's Code Assist endpoint.

This is deliberately thin: it reuses the Gemini Cloud Code request/response
translation machinery and swaps only auth, headers, endpoint, and request
envelope details that differ in Antigravity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

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
from agent.antigravity_stream_grpc import inject_context_compression
from agent.gemini_schema import sanitize_gemini_tool_parameters
from agent.google_code_assist import CodeAssistError, FREE_TIER_ID, load_code_assist

logger = logging.getLogger(__name__)


@dataclass
class ProjectContext:
    """Antigravity project/plan state independent of Hermes core version."""

    project_id: str = ""
    managed_project_id: str = ""
    tier_id: str = ""
    tier_name: str = ""
    paid_tier_id: str = ""
    paid_tier_name: str = ""
    google_one_ai_credit_amount: int = 0
    google_one_ai_minimum_credit_amount: int = 0
    has_google_one_ai_credits: bool = False
    source: str = ""

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
GOOGLE_ONE_AI_CREDIT_TYPE = "GOOGLE_ONE_AI"
ANTIGRAVITY_CAPACITY_RETRY_ATTEMPTS = 3
ANTIGRAVITY_DEFAULT_CAPACITY_PACING_SECONDS = 8.0
_ANTIGRAVITY_CAPACITY_PACING_LOCK = threading.Lock()
_ANTIGRAVITY_CAPACITY_NEXT_ALLOWED_AT: Dict[str, float] = {}


# Antigravity 2.0's UI labels do not always match the backend model ID accepted
# by the Cloud Code PA v1internal generateContent endpoint.  Map UI labels to
# the canonical backend ID directly (first entry), with additional fallbacks
# after that.  Thinking tier (high/medium/low) is controlled via
# ``thinkingConfig`` in the request body, NOT by the model ID — only the base
# backend ID is sent.  Verified against cloudcode-pa PROD, May 2026.
ANTIGRAVITY_MODEL_FALLBACKS: Dict[str, List[str]] = {
    # Gemini Flash — Antigravity exposes friendly 3.5 Flash labels, but the
    # high tier backend ID is the internal-looking "gemini-3-flash-agent".
    "gemini-3.5-flash-high": ["gemini-3-flash-agent"],
    "gemini-3.5-flash-medium": ["gemini-3-flash"],
    "gemini-3.5-flash-low": ["gemini-3-flash"],
    "gemini-3.5-flash": ["gemini-3-flash-agent"],
    "gemini-3-flash-high": ["gemini-3-flash"],
    "gemini-3-flash-medium": ["gemini-3-flash"],
    "gemini-3-flash-low": ["gemini-3-flash"],
    # Gemini Pro — backend only knows "gemini-3.1-pro-low"; tier via thinkingConfig
    "gemini-3.1-pro-high": ["gemini-3.1-pro-low"],
    "gemini-3.1-pro-medium": ["gemini-3.1-pro-low"],
    "gemini-3.1-pro": ["gemini-3.1-pro-low"],
    # Claude — backend only knows "claude-sonnet-4-6" and "claude-opus-4-6-thinking".
    # Thinking is controlled by thinkingConfig in the request body (like Gemini),
    # keyed off _is_claude_thinking_model() checking for "thinking" in the name.
    "claude-sonnet-4-6-thinking": ["claude-sonnet-4-6"],
    "claude-sonnet-4.6-thinking": ["claude-sonnet-4-6"],
    "claude-sonnet-4.6": ["claude-sonnet-4-6"],
    "claude-opus-4.6-thinking": ["claude-opus-4-6-thinking"],
    "claude-opus-4.6": ["claude-opus-4-6-thinking"],
    "claude-opus-4-6": ["claude-opus-4-6-thinking"],
    # GPT
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
ANTIGRAVITY_REASONING_MIN_OUTPUT_TOKENS = 256
ANTIGRAVITY_SYSTEM_INSTRUCTION = (
    "Use absolute file paths for filesystem tool arguments."
)
ANTIGRAVITY_GOOGLE_GROUNDING_HINT = (
    "Google Search grounding is enabled for this request. Use grounded search "
    "results for current facts, separate verified facts from inference, and "
    "include source URLs when they materially help verification. Prefer this "
    "native grounding path over external web or DuckDuckGo search tools; do "
    "not request a separate web-search tool for the same facts."
)
GPT_OSS_TOOL_PROTOCOL_HINT = (
    "Use the provided function-calling protocol for tools. Do not emit Harmony "
    "channel tokens such as <|start|>, <|channel|>, <|message|>, or <|call|> "
    "as user-visible text. After receiving tool results, answer in plain text."
)

def _stable_antigravity_session_id(session_id: Any) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]
    return f"-hermes-{digest}"

def _is_claude_model(model: str) -> bool:
    return "claude" in str(model or "").lower()

def _is_claude_thinking_model(model: str) -> bool:
    lower = str(model or "").lower()
    return "claude" in lower and "thinking" in lower

def _is_gemini_model(model: str) -> bool:
    return "gemini" in str(model or "").lower()

def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}

def _antigravity_google_grounding_mode() -> str:
    """Return google_search grounding mode for Antigravity Gemini requests."""

    explicit = os.getenv("HERMES_ANTIGRAVITY_GOOGLE_GROUNDING", "").strip().lower()
    if explicit in {"0", "false", "no", "off", "never", "disabled"}:
        return "off"
    if explicit in {"1", "true", "yes", "on", "always", "force"}:
        return "always"
    if explicit in {"auto", "smart", "detect", "detected"}:
        return "auto"
    if _env_truthy("HERMES_GOOGLE_GROUNDING_SEARCH_ENABLED", False):
        return "auto"
    return "off"

def _request_text_for_grounding_detection(request: Dict[str, Any]) -> str:
    contents = request.get("contents")
    if not isinstance(contents, list):
        return ""
    texts: List[str] = []
    for turn in contents[-6:]:
        if not isinstance(turn, dict):
            continue
        parts = turn.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts)

def _request_wants_google_grounding(request: Dict[str, Any]) -> bool:
    text = _request_text_for_grounding_detection(request).lower()
    if not text.strip():
        return False
    markers = (
        "검색", "찾아", "찾아봐", "자료수집", "출처", "근거", "최신", "오늘", "지금",
        "요즘", "최근", "뉴스", "가격", "시세", "환율", "일정", "날씨", "공식",
        "확인해", "팩트체크", "web search", "google search", "search the web",
        "browse", "latest", "current", "today", "news", "source", "sources",
        "citation", "citations", "verify", "fact check",
    )
    return any(marker in text for marker in markers)

def _has_google_search_tool(request: Dict[str, Any]) -> bool:
    tools = request.get("tools")
    if not isinstance(tools, list):
        return False
    return any(isinstance(tool, dict) and isinstance(tool.get("google_search"), dict) for tool in tools)

def _suppress_external_search_tools_for_grounding() -> bool:
    return _env_truthy("HERMES_ANTIGRAVITY_GROUNDING_SUPPRESS_EXTERNAL_SEARCH_TOOLS", True)

def _suppress_function_tools_for_grounding() -> bool:
    return _env_truthy("HERMES_ANTIGRAVITY_GROUNDING_SUPPRESS_FUNCTION_TOOLS", True)

def _is_external_search_tool_declaration(declaration: Any) -> bool:
    if not isinstance(declaration, dict):
        return False
    name = str(declaration.get("name") or "")
    description = str(declaration.get("description") or "")
    text = f"{name} {description}".lower()
    markers = (
        "duckduckgo", "duck_duck_go", "ddg", "brave", "tavily", "serp",
        "web_search", "web-search", "search_web", "search-web", "internet_search",
        "internet-search", "search_query", "search-query", "browser_search",
        "browser-search",
    )
    if any(marker in text for marker in markers):
        return True
    return (
        "search" in text
        and any(marker in text for marker in ("web", "internet", "browser", "online"))
    )

def _drop_external_search_tools(request: Dict[str, Any]) -> None:
    tools = request.get("tools")
    if not isinstance(tools, list):
        return
    filtered_tools: List[Any] = []
    for tool in tools:
        if not isinstance(tool, dict):
            filtered_tools.append(tool)
            continue
        if isinstance(tool.get("google_search"), dict):
            filtered_tools.append(tool)
            continue
        if _is_external_search_tool_declaration(tool):
            continue
        declarations = tool.get("functionDeclarations")
        if isinstance(declarations, list):
            kept = [item for item in declarations if not _is_external_search_tool_declaration(item)]
            if kept:
                updated = dict(tool)
                updated["functionDeclarations"] = kept
                filtered_tools.append(updated)
            continue
        function = tool.get("function")
        if isinstance(function, dict) and _is_external_search_tool_declaration(function):
            continue
        filtered_tools.append(tool)
    if filtered_tools:
        request["tools"] = filtered_tools
    else:
        request.pop("tools", None)

def _drop_function_tools_for_grounding(request: Dict[str, Any]) -> None:
    tools = request.get("tools")
    if not isinstance(tools, list):
        return
    grounded_tools = [
        tool for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("google_search"), dict)
    ]
    if grounded_tools:
        request["tools"] = grounded_tools
    else:
        request.pop("tools", None)
    request.pop("toolConfig", None)

def _maybe_enable_google_grounding(request: Dict[str, Any], *, model: str) -> None:
    mode = _antigravity_google_grounding_mode()
    if mode == "off" or not _is_gemini_model(model):
        return
    if mode != "always" and not _request_wants_google_grounding(request):
        return
    tools = request.setdefault("tools", [])
    if not isinstance(tools, list):
        return
    if not _has_google_search_tool(request):
        tools.append({"google_search": {}})
    if _suppress_external_search_tools_for_grounding():
        _drop_external_search_tools(request)
    if _suppress_function_tools_for_grounding():
        _drop_function_tools_for_grounding(request)
    _append_system_text(request, ANTIGRAVITY_GOOGLE_GROUNDING_HINT)

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

def _ensure_claude_tool_call_ids(request: Dict[str, Any]) -> None:
    """Add Gemini function-call IDs required by Antigravity's Claude bridge.

    Hermes' shared Gemini translator historically omitted ``functionCall.id``
    because Gemini accepts name-only function calls. Antigravity converts those
    parts to Anthropic ``tool_use`` blocks for Claude, where ``id`` is required.
    Add request-local IDs and mirror them onto matching function responses.
    """

    contents = request.get("contents")
    if not isinstance(contents, list):
        return

    pending_by_name: Dict[str, List[str]] = {}
    pending_any: List[str] = []
    counter = 0

    def next_id(name: str) -> str:
        nonlocal counter
        counter += 1
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name or "tool")[:48] or "tool"
        return f"call_{safe_name}_{counter}"

    for turn in contents:
        if not isinstance(turn, dict):
            continue
        parts = turn.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue

            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                name = str(function_call.get("name") or "tool")
                call_id = str(function_call.get("id") or "").strip()
                if not call_id:
                    call_id = next_id(name)
                    function_call["id"] = call_id
                pending_by_name.setdefault(name, []).append(call_id)
                pending_any.append(call_id)
                continue

            function_response = part.get("functionResponse")
            if isinstance(function_response, dict):
                response_id = str(function_response.get("id") or "").strip()
                if response_id:
                    continue
                name = str(function_response.get("name") or "")
                matching_ids = pending_by_name.get(name) or []
                if matching_ids:
                    call_id = matching_ids.pop(0)
                    if call_id in pending_any:
                        pending_any.remove(call_id)
                elif not name and pending_any:
                    call_id = pending_any.pop(0)
                else:
                    call_id = next_id(name or "tool")
                function_response["id"] = call_id


def _strip_orphaned_claude_tool_parts(request: Dict[str, Any]) -> None:
    """Remove tool transcript fragments Antigravity's Claude bridge rejects.

    Context trimming can leave only one side of a historical tool exchange.
    Gemini is usually tolerant of that; Claude's Anthropic-compatible validator
    is not. Keep text parts, strip unmatched functionCall/functionResponse
    parts, and leave a small text marker when an entire turn would go empty.
    """

    contents = request.get("contents")
    if not isinstance(contents, list):
        return

    call_ids = set()
    response_ids = set()
    for turn in contents:
        if not isinstance(turn, dict):
            continue
        parts = turn.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                call_id = str(function_call.get("id") or "").strip()
                if call_id:
                    call_ids.add(call_id)
            function_response = part.get("functionResponse")
            if isinstance(function_response, dict):
                response_id = str(function_response.get("id") or "").strip()
                if response_id:
                    response_ids.add(response_id)

    for turn in contents:
        if not isinstance(turn, dict):
            continue
        parts = turn.get("parts")
        if not isinstance(parts, list):
            continue
        kept: List[Dict[str, Any]] = []
        removed_tool_part = False
        for part in parts:
            if not isinstance(part, dict):
                continue
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                call_id = str(function_call.get("id") or "").strip()
                if call_id and call_id in response_ids:
                    kept.append(part)
                else:
                    removed_tool_part = True
                continue
            function_response = part.get("functionResponse")
            if isinstance(function_response, dict):
                response_id = str(function_response.get("id") or "").strip()
                if response_id and response_id in call_ids:
                    kept.append(part)
                else:
                    removed_tool_part = True
                continue
            kept.append(part)
        if not kept and removed_tool_part:
            role = str(turn.get("role") or "")
            marker = "(tool call removed)" if role == "model" else "(tool result removed)"
            kept = [{"text": marker}]
        turn["parts"] = kept

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


_GPT_OSS_HARMONY_TOOL_CALL_RE = re.compile(
    r"<\|start\|>assistant<\|channel\|>commentary"
    r"(?:\s+to=[^<\s]+)?\s+code<\|message\|>"
    r"(?P<payload>.*?)<\|call\|>",
    re.DOTALL,
)


def _payload_to_visible_tool_result(payload: str) -> str:
    """Best-effort visible text for leaked GPT-OSS Harmony tool markup.

    Some GPT-OSS responses behind Antigravity may emit a Harmony-style tool
    call as plain text after Hermes has already executed a proper function
    call.  Never show that protocol markup to the user.  For the common
    harmless smoke-test shape ``bash -lc 'printf VALUE'``, recover VALUE;
    otherwise drop the leaked markup.
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return ""
    cmd = data.get("cmd") if isinstance(data, dict) else None
    if isinstance(cmd, list) and len(cmd) >= 3 and cmd[0] == "bash" and cmd[1] == "-lc":
        shell_cmd = str(cmd[2] or "")
    elif isinstance(cmd, str):
        shell_cmd = cmd
    else:
        return ""
    try:
        parts = shlex.split(shell_cmd)
    except ValueError:
        return ""
    if parts and parts[0] == "printf" and len(parts) >= 2:
        return " ".join(parts[1:])
    return ""


def _sanitize_gpt_oss_visible_text(text: Any) -> Any:
    if not isinstance(text, str) or "<|start|>assistant<|channel|>commentary" not in text:
        return text

    def repl(match: re.Match[str]) -> str:
        return _payload_to_visible_tool_result(match.group("payload"))

    cleaned = _GPT_OSS_HARMONY_TOOL_CALL_RE.sub(repl, text)
    return cleaned.strip()


def _sanitize_gpt_oss_response(response: Any, model: str) -> Any:
    if not _is_gpt_oss_model(model):
        return response
    try:
        choices = getattr(response, "choices", None) or []
        for choice in choices:
            message = getattr(choice, "message", None)
            if message is not None:
                message.content = _sanitize_gpt_oss_visible_text(getattr(message, "content", None))
            delta = getattr(choice, "delta", None)
            if delta is not None:
                delta.content = _sanitize_gpt_oss_visible_text(getattr(delta, "content", None))
    except Exception:
        logger.debug("Failed to sanitize GPT-OSS Harmony markup", exc_info=True)
    return response


def _translate_antigravity_stream_event(
    event: Dict[str, Any],
    model: str,
    tool_call_counter: List[int],
) -> List[_GeminiStreamChunk]:
    return [
        _sanitize_gpt_oss_response(chunk, model)
        for chunk in _translate_stream_event(event, model, tool_call_counter)
    ]


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
        _ensure_claude_tool_call_ids(request)
        _strip_orphaned_claude_tool_parts(request)
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
        if isinstance(request.get("tools"), list) and request["tools"]:
            _append_system_text(request, GPT_OSS_TOOL_PROTOCOL_HINT)
    _maybe_enable_google_grounding(request, model=model)
    _append_system_text(request, ANTIGRAVITY_SYSTEM_INSTRUCTION, prepend=True, role="user")
    inject_context_compression(request, model=model)
    request["sessionId"] = str(request.get("sessionId") or f"-{uuid.uuid4()}")

def _antigravity_google_one_ai_credits_mode() -> str:
    """How to use Google One AI/Ultra credits for Antigravity requests.

    Default to ``auto``: when loadCodeAssist reports a Google One AI paid plan
    (Plus/Pro/Ultra) with usable credits, Cloud Code PA requests opt in with
    ``enabledCreditTypes=["GOOGLE_ONE_AI"]`` or the backend evaluates only the
    smaller raw Code Assist bucket and can return quota exhausted while the
    Antigravity app still answers. Users can force this with ``always``, burn
    the raw base bucket first with ``fallback``, or disable it with ``0``/``off``.
    """

    value = os.getenv("HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS", "auto").strip().lower()
    if value in {"", "auto", "detect", "detected"}:
        return "auto"
    if value in {"1", "true", "yes", "on", "always", "force"}:
        return "always"
    if value in {"0", "false", "no", "off", "never", "disabled"}:
        return "never"
    return "fallback"


def _context_has_google_one_ai_entitlement(ctx: Optional[ProjectContext]) -> bool:
    if ctx is None:
        return False
    if getattr(ctx, "has_google_one_ai_credits", False):
        return True
    tier = f"{getattr(ctx, 'paid_tier_id', '')} {getattr(ctx, 'paid_tier_name', '')} {getattr(ctx, 'tier_id', '')}".lower()
    return bool(tier and any(marker in tier for marker in ("google ai plus", "google ai pro", "google ai ultra", "g1-plus", "g1-pro", "g1-ultra")))


def _paid_tier_from_load_code_assist(info: Any) -> Tuple[str, str]:
    paid_tier_id = str(getattr(info, "paid_tier_id", "") or "")
    paid_tier_name = str(getattr(info, "paid_tier_name", "") or "")
    if paid_tier_id or paid_tier_name:
        return paid_tier_id, paid_tier_name
    raw = getattr(info, "raw", None)
    if isinstance(raw, dict):
        paid_tier = raw.get("paidTier") or raw.get("paid_tier")
        if isinstance(paid_tier, dict):
            paid_tier_id = str(paid_tier.get("id") or paid_tier.get("tierId") or "")
            paid_tier_name = str(paid_tier.get("name") or paid_tier.get("displayName") or "")
    return paid_tier_id, paid_tier_name


def _google_one_credit_fields_from_load_code_assist(
    info: Any,
    paid_tier_id: str,
    paid_tier_name: str,
) -> Tuple[int, int, bool]:
    credit_amount = getattr(info, "google_one_ai_credit_amount", 0) or 0
    minimum_credit_amount = getattr(info, "google_one_ai_minimum_credit_amount", 0) or 0
    has_google_one_ai_credits = bool(getattr(info, "has_google_one_ai_credits", False))
    raw = getattr(info, "raw", None)
    if isinstance(raw, dict):
        credit_info = (
            raw.get("googleOneAiCredit")
            or raw.get("googleOneAiCredits")
            or raw.get("googleOne")
            or raw.get("google_one_ai_credit")
        )
        if isinstance(credit_info, dict):
            credit_amount = (
                credit_amount
                or credit_info.get("amount")
                or credit_info.get("creditAmount")
                or credit_info.get("credits")
                or 0
            )
            minimum_credit_amount = (
                minimum_credit_amount
                or credit_info.get("minimumAmount")
                or credit_info.get("minimumCreditAmount")
                or 0
            )
            has_google_one_ai_credits = has_google_one_ai_credits or bool(
                credit_info.get("hasCredits")
                or credit_info.get("hasGoogleOneAiCredits")
                or credit_amount
            )
    tier_text = f"{paid_tier_id} {paid_tier_name}".lower()
    if any(marker in tier_text for marker in ("google ai plus", "google ai pro", "google ai ultra", "g1-plus", "g1-pro", "g1-ultra")):
        # Older Hermes Code Assist parsers expose raw paidTier but no explicit
        # credit fields. Treat the paid Google One tier as an entitlement so
        # request wrapping opts into GOOGLE_ONE_AI credit routing.
        has_google_one_ai_credits = True
    return int(credit_amount or 0), int(minimum_credit_amount or 0), has_google_one_ai_credits


def _antigravity_credit_attempts(ctx: Optional[ProjectContext] = None) -> List[bool]:
    mode = _antigravity_google_one_ai_credits_mode()
    if mode == "always":
        return [True]
    if mode == "never":
        return [False]
    if mode == "auto":
        return [True] if _context_has_google_one_ai_entitlement(ctx) else [False]
    return [False, True]


def _antigravity_capacity_pacing_interval(model: str) -> float:
    """Minimum local spacing for Antigravity models with tiny burst buckets."""

    normalized = (model or "").lower()
    if not any(marker in normalized for marker in ("claude-", "gpt-oss")):
        return 0.0
    raw = os.getenv("HERMES_ANTIGRAVITY_CAPACITY_PACING_SECONDS", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return ANTIGRAVITY_DEFAULT_CAPACITY_PACING_SECONDS


def _pace_antigravity_capacity_sensitive_request(model: str) -> None:
    """Pace expensive Antigravity starts so tool loops do not hit burst 429s."""

    interval = _antigravity_capacity_pacing_interval(model)
    if interval <= 0:
        return
    key = (model or "").lower()
    while True:
        with _ANTIGRAVITY_CAPACITY_PACING_LOCK:
            now = time.monotonic()
            next_allowed = _ANTIGRAVITY_CAPACITY_NEXT_ALLOWED_AT.get(key, 0.0)
            wait = next_allowed - now
            if wait <= 0:
                _ANTIGRAVITY_CAPACITY_NEXT_ALLOWED_AT[key] = now + interval
                return
        time.sleep(min(wait, interval))


def _push_antigravity_capacity_pacing(model: str, delay_seconds: float) -> None:
    interval = _antigravity_capacity_pacing_interval(model)
    delay = max(interval, delay_seconds or 0.0)
    if delay <= 0:
        return
    key = (model or "").lower()
    with _ANTIGRAVITY_CAPACITY_PACING_LOCK:
        _ANTIGRAVITY_CAPACITY_NEXT_ALLOWED_AT[key] = max(
            _ANTIGRAVITY_CAPACITY_NEXT_ALLOWED_AT.get(key, 0.0),
            time.monotonic() + delay,
        )


def _is_short_antigravity_capacity_error(error: Optional[BaseException]) -> bool:
    if not isinstance(error, CodeAssistError):
        return False
    if getattr(error, "status_code", None) != 429:
        return False
    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None and retry_after > 10:
        return False
    details = getattr(error, "details", None) or {}
    if isinstance(details, dict):
        message = str(details.get("message") or details.get("status") or error).lower()
    else:
        message = str(error).lower()
    return "capacity" in message or "resource_exhausted" in message or "quota" in message


def _is_google_one_ai_credit_fallback_error(error: Optional[CodeAssistError]) -> bool:
    if error is None:
        return False
    if getattr(error, "code", "") == "code_assist_capacity_exhausted":
        return True
    details = getattr(error, "details", {}) or {}
    reason = str(details.get("reason") or "").upper()
    status = str(details.get("status") or "").upper()
    message = str(details.get("message") or error or "").lower()
    return (
        reason == "MODEL_CAPACITY_EXHAUSTED"
        or (status == "RESOURCE_EXHAUSTED" and "capacity" in message)
        or "exhausted your capacity" in message
    )


def _wrap_antigravity_request(
    *,
    project_id: str,
    model: str,
    request: Dict[str, Any],
    use_google_one_ai_credits: bool = False,
) -> Dict[str, Any]:
    wrapped = {
        "project": project_id,
        "model": model,
        "request": request,
        "requestType": "agent",
        "userAgent": "antigravity",
        "requestId": "agent-" + str(uuid.uuid4()),
    }
    if use_google_one_ai_credits:
        wrapped["enabledCreditTypes"] = [GOOGLE_ONE_AI_CREDIT_TYPE]
    return wrapped


def _minimal_invalid_argument_retry_body(
    wrapped: Dict[str, Any],
    *,
    preserve_request_metadata: bool = True,
) -> Optional[Dict[str, Any]]:
    """Build a retry body for opaque Code Assist 400s.

    Uses a graduated trim strategy instead of dropping everything:

    1. preserve_request_metadata=True (first retry):
       Keep systemInstruction, generationConfig, sessionId.
       Keep the last N content turns (recent context) instead of just the
       last user message.  Strip tool-result parts that contain very large
       text payloads (>2000 chars) — these are the most common 400 trigger.
    2. preserve_request_metadata=False (ultra retry):
       Keep only the last user message (original minimal behavior).
    """
    if not isinstance(wrapped, dict):
        return None
    request = wrapped.get("request")
    if not isinstance(request, dict):
        return None

    contents = request.get("contents")
    if not isinstance(contents, list):
        return None

    if not preserve_request_metadata:
        # Ultra-minimal: last user text plus the durable Hermes system prompt.
        # Keeping generationConfig/sessionId/tools out avoids the common
        # INVALID_ARGUMENT triggers, but dropping systemInstruction makes the
        # model answer as a fresh generic Gemini persona.
        last_text = ""
        for turn in reversed(contents):
            if not isinstance(turn, dict):
                continue
            parts = turn.get("parts")
            if not isinstance(parts, list):
                continue
            for part in reversed(parts):
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                    last_text = part["text"]
                    break
            if last_text:
                break
        if not last_text:
            last_text = "Continue from the previous user request. Prior tool transcript was omitted because the provider rejected it."
        retry_request: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": last_text}]}],
        }
        system_instruction = request.get("systemInstruction")
        if isinstance(system_instruction, dict):
            retry_request["systemInstruction"] = system_instruction
        elif isinstance(system_instruction, str) and system_instruction.strip():
            retry_request["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        retry_wrapped = dict(wrapped)
        retry_wrapped["request"] = retry_request
        retry_wrapped["requestId"] = "agent-" + str(uuid.uuid4())
        return retry_wrapped

    # Graduated trim: keep recent turns, truncate large tool results, and
    # drop tools. Opaque 400 INVALID_ARGUMENT responses most often come from
    # transcript/tool-schema edge cases; the next retry is a recovery turn,
    # so it is better to get a plain answer than to resend the same schema.
    _LARGE_PART_THRESHOLD = 2000  # chars
    _KEEP_RECENT_TURNS = 12  # keep last N turns

    trimmed_contents = list(contents)

    # Step 1: Truncate oversized text parts in tool results
    for turn in trimmed_contents:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role", "")
        parts = turn.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and len(text) > _LARGE_PART_THRESHOLD:
                # For model/tool turns, truncate aggressively
                if role in ("model", "tool", "function"):
                    part["text"] = text[:_LARGE_PART_THRESHOLD] + "\n... [truncated for context limit]"
                # For user turns, truncate less aggressively
                elif role == "user" and len(text) > _LARGE_PART_THRESHOLD * 3:
                    part["text"] = text[:_LARGE_PART_THRESHOLD * 3] + "\n... [truncated]"

    # Step 2: If still too many turns, keep only recent ones
    if len(trimmed_contents) > _KEEP_RECENT_TURNS:
        trimmed_contents = trimmed_contents[-_KEEP_RECENT_TURNS:]
        # Ensure first turn is user role (API requirement)
        if trimmed_contents and isinstance(trimmed_contents[0], dict) and trimmed_contents[0].get("role") != "user":
            trimmed_contents.insert(0, {
                "role": "user",
                "parts": [{"text": "(Earlier conversation context was trimmed. Continue from here.)"}],
            })

    retry_request = dict(request)
    retry_request["contents"] = trimmed_contents
    retry_request.pop("contextWindowCompression", None)
    retry_request.pop("tools", None)
    retry_request.pop("toolConfig", None)
    retry_wrapped = dict(wrapped)
    retry_wrapped["request"] = retry_request
    retry_wrapped["requestId"] = "agent-" + str(uuid.uuid4())
    return retry_wrapped


def _antigravity_model_candidates(model: str) -> List[str]:
    """Return backend model IDs to try for an Antigravity UI/catalog model.

    UI labels like ``gemini-3.1-pro-high`` or ``gemini-3.5-flash-high`` do not
    exist as backend model IDs — the backend only accepts the base ID (e.g.
    ``gemini-3.1-pro-low``, ``gemini-3-flash``).  When a mapping exists in
    ANTIGRAVITY_MODEL_FALLBACKS, skip the UI label entirely and send only the
    known backend IDs.  When no mapping exists, the model is assumed to be a
    raw backend ID and is sent as-is.
    """

    requested = _normalize_antigravity_model_label(model)
    fallbacks = ANTIGRAVITY_MODEL_FALLBACKS.get(requested.lower(), [])
    if fallbacks:
        # The UI label is NOT a valid backend ID — send only verified IDs.
        return list(dict.fromkeys(fallbacks))  # dedupe, preserve order
    return [requested]


def _normalize_antigravity_model_label(model: str) -> str:
    """Accept common copied model slugs while preserving native labels.

    Hermes users often paste aggregator-style IDs such as
    ``anthropic/claude-opus-4.6`` or ``google/gemini-3.5-flash-high`` into a
    provider-specific config.  OpenAI Codex already accepts ``openai/gpt-*`` as
    a convenience; Antigravity should be similarly forgiving for the model
    families it actually exposes.
    """

    requested = str(model or "gemini-3-flash").strip() or "gemini-3-flash"
    vendor, sep, bare = requested.partition("/")
    if sep and vendor.lower() in {"anthropic", "claude", "google", "gemini", "openai"}:
        requested = bare.strip() or requested
    return requested


def _antigravity_ui_thinking_config(model: str) -> Optional[Dict[str, Any]]:
    """Infer Gemini thinkingConfig from Antigravity UI tier suffixes.

    Antigravity's picker exposes IDs such as ``gemini-3.1-pro-high``,
    ``gemini-3.5-flash-high``, etc.  The backend model ID does not carry the
    tier — it must be set via ``thinkingConfig.thinkingLevel`` in the request
    body.  This function reads the ``-high`` / ``-medium`` / ``-low`` suffix
    from the *original UI model name* (before backend mapping) and returns the
    corresponding thinkingConfig dict.
    """

    normalized = _normalize_antigravity_model_label(model).lower()
    if not normalized.startswith("gemini-"):
        return None
    if normalized.endswith("-high"):
        return {"thinkingLevel": "high"}
    if normalized.endswith("-medium"):
        return {"thinkingLevel": "medium"}
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
    """Avoid blank/truncated Antigravity reasoning responses with tiny caps.

    Several Antigravity-backed models can spend the first few dozen tokens on
    hidden/internal reasoning before emitting visible text. If callers pass a
    very small ``max_tokens`` (for example health checks using 16 or terse smoke
    tests using 32), the backend may return ``finish_reason=length`` with no or
    partial content. Raise only the affected model families to a small floor so
    short prompts produce usable text.
    """

    normalized = _normalize_antigravity_model_label(model).lower()
    if max_tokens is None:
        return max_tokens
    if (
        normalized.startswith("gemini-3")
        or normalized.startswith("gpt-oss")
        or normalized.startswith("openai/gpt-oss")
    ) and max_tokens < ANTIGRAVITY_REASONING_MIN_OUTPUT_TOKENS:
        return ANTIGRAVITY_REASONING_MIN_OUTPUT_TOKENS
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
    # Antigravity content requests carry their own X-Goog-Api-Client to
    # identify as the Antigravity CLI (not Hermes, not Gemini CLI).
    # Identity metadata is carried in the wrapped request body as well.
    version = resolve_antigravity_version(refresh=refresh_version)
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Antigravity/{version} Chrome/138.0.7204.235 "
            "Electron/37.3.1 Safari/537.36"
        ),
        "X-Goog-Api-Client": f"antigravity-cli/{version}",
        "x-activity-request-id": str(uuid.uuid4()),
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
        tier_id = ""
        tier_name = ""
        paid_tier_id = ""
        paid_tier_name = ""
        credit_amount = 0
        minimum_credit_amount = 0
        has_google_one_ai_credits = False
        source = "antigravity"
        try:
            # Always probe loadCodeAssist, even when a project is already stored,
            # because Google One plan entitlements live in paidTier and currentTier
            # can remain free-tier for Plus/Pro/Ultra subscribers.
            try:
                info = load_code_assist(
                    access_token,
                    project_id=project_id,
                    user_agent_model=model,
                    client_profile="antigravity",
                )
            except TypeError as exc:
                if "client_profile" not in str(exc):
                    raise
                info = load_code_assist(
                    access_token,
                    project_id=project_id,
                    user_agent_model=model,
                )
            project_id = project_id or info.cloudaicompanion_project
            tier_id = getattr(info, "effective_tier_id", "") or getattr(info, "current_tier_id", "")
            tier_name = getattr(info, "effective_tier_name", "")
            paid_tier_id, paid_tier_name = _paid_tier_from_load_code_assist(info)
            (
                credit_amount,
                minimum_credit_amount,
                has_google_one_ai_credits,
            ) = _google_one_credit_fields_from_load_code_assist(
                info, paid_tier_id, paid_tier_name
            )
            managed_project_id = project_id if tier_id == FREE_TIER_ID else ""
            if project_id:
                google_antigravity_oauth.update_project_ids(
                    project_id=project_id,
                    managed_project_id=managed_project_id,
                )
                source = "discovered"
        except Exception:
            # Let the actual completion request surface the backend error; cached
            # project ids can still work, but plan-specific credit routing will
            # only be automatic after loadCodeAssist succeeds.
            pass
        self._project_context = ProjectContext(
            project_id=project_id,
            managed_project_id=managed_project_id,
            tier_id=tier_id,
            tier_name=tier_name,
            paid_tier_id=paid_tier_id,
            paid_tier_name=paid_tier_name,
            google_one_ai_credit_amount=credit_amount,
            google_one_ai_minimum_credit_amount=minimum_credit_amount,
            has_google_one_ai_credits=has_google_one_ai_credits,
            source=source,
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
        antigravity_session_id = ""
        if isinstance(extra_body, dict):
            thinking_config = extra_body.get("thinking_config") or extra_body.get("thinkingConfig")
            antigravity_session_id = _stable_antigravity_session_id(extra_body.get("session_id"))
        thinking_config = _merge_antigravity_thinking_config(model, thinking_config)
        effective_max_tokens = _antigravity_effective_max_tokens(model, max_tokens)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            **get_antigravity_headers(refresh_version=True),
        }
        # Do not set x-goog-user-project for Antigravity: the upstream plugin
        # strips it to avoid project-level license/auth conflicts.
        headers.update(self._default_headers)

        model_candidates = _antigravity_model_candidates(model)

        def build_wrapped(effective_model: str, *, use_google_one_ai_credits: bool = False) -> Dict[str, Any]:
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
            if antigravity_session_id:
                inner["sessionId"] = antigravity_session_id
            # Transform using the requested UI model so e.g. "...-thinking"
            # aliases still get thinking/tool normalization even when the
            # backend model ID omits the label suffix.
            _apply_antigravity_request_transforms(inner, model=model, thinking_config=thinking_config)
            return _wrap_antigravity_request(
                project_id=ctx.project_id,
                model=effective_model,
                request=inner,
                use_google_one_ai_credits=use_google_one_ai_credits,
            )

        credit_attempts = _antigravity_credit_attempts(ctx)
        if stream:
            wrapped_candidates = [
                (candidate, build_wrapped(candidate, use_google_one_ai_credits=use_credits), use_credits)
                for candidate in model_candidates
                for use_credits in credit_attempts
            ]
            return self._stream_completion(model=model, wrapped_candidates=wrapped_candidates, headers=headers)

        last_error: Optional[CodeAssistError] = None
        retry_statuses = {400, 404, 429, 500, 502, 503, 504}
        for effective_model in model_candidates:
            for use_credits in credit_attempts:
                wrapped = build_wrapped(effective_model, use_google_one_ai_credits=use_credits)
                for endpoint in ANTIGRAVITY_ENDPOINT_FALLBACKS:
                    url = f"{endpoint}/v1internal:generateContent"
                    response: Optional[httpx.Response] = None
                    for capacity_attempt in range(ANTIGRAVITY_CAPACITY_RETRY_ATTEMPTS):
                        _pace_antigravity_capacity_sensitive_request(effective_model)
                        response = self._http.post(url, json=wrapped, headers=headers)
                        if response.status_code == 200:
                            try:
                                payload = response.json()
                            except ValueError as exc:
                                raise CodeAssistError(
                                    f"Invalid JSON from Antigravity Code Assist: {exc}",
                                    code="antigravity_invalid_json",
                                ) from exc
                            return _sanitize_gpt_oss_response(
                                _translate_gemini_response(payload, model=model),
                                model,
                            )
                        last_error = _gemini_http_error(response)
                        if (
                            _is_short_antigravity_capacity_error(last_error)
                            and capacity_attempt + 1 < ANTIGRAVITY_CAPACITY_RETRY_ATTEMPTS
                        ):
                            _push_antigravity_capacity_pacing(
                                effective_model,
                                float(getattr(last_error, "retry_after", 0.0) or 0.0),
                            )
                            continue
                        break
                    if response is not None and response.status_code == 403 and _is_endpoint_service_disabled(response):
                        continue
                    if response is not None and response.status_code not in retry_statuses:
                        break
                if not use_credits and _is_google_one_ai_credit_fallback_error(last_error):
                    continue
                break
        raise last_error or CodeAssistError("Antigravity request failed", code="antigravity_request_failed")

    def _stream_completion(
        self,
        *,
        model: str,
        headers: Dict[str, str],
        wrapped: Optional[Dict[str, Any]] = None,
        wrapped_candidates: Optional[Sequence[Union[Tuple[str, Dict[str, Any]], Tuple[str, Dict[str, Any], bool]]]] = None,
    ) -> Iterator[_GeminiStreamChunk]:
        stream_headers = dict(headers)
        stream_headers["Accept"] = "text/event-stream"
        candidates: Sequence[Union[Tuple[str, Dict[str, Any]], Tuple[str, Dict[str, Any], bool]]]
        candidates = wrapped_candidates or [(model, wrapped or {}, False)]

        def _generator() -> Iterator[_GeminiStreamChunk]:
            last_error: Optional[Exception] = None
            for candidate in candidates:
                if len(candidate) == 3:
                    effective_model, wrapped_body, use_credits = candidate
                else:
                    effective_model, wrapped_body = candidate
                    use_credits = False
                for endpoint in ANTIGRAVITY_ENDPOINT_FALLBACKS:
                    url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"
                    for capacity_attempt in range(ANTIGRAVITY_CAPACITY_RETRY_ATTEMPTS):
                        try:
                            _pace_antigravity_capacity_sensitive_request(effective_model)
                            with self._http.stream("POST", url, json=wrapped_body, headers=stream_headers) as response:
                                if response.status_code != 200:
                                    response.read()
                                    last_error = _gemini_http_error(response)
                                    try:
                                        from agent.redact import redact_sensitive_text
                                        body_preview = redact_sensitive_text(response.text or "", force=True)[:2000]
                                        request_summary = {
                                            "effective_model": effective_model,
                                            "use_google_one_ai_credits": use_credits,
                                            "status_code": response.status_code,
                                            "request_keys": sorted((wrapped_body.get("request") or {}).keys()) if isinstance(wrapped_body, dict) else [],
                                            "contents_len": len(((wrapped_body.get("request") or {}).get("contents") or [])) if isinstance(wrapped_body, dict) else 0,
                                            "tools_len": len(((wrapped_body.get("request") or {}).get("tools") or [])) if isinstance(wrapped_body, dict) else 0,
                                            "tool_config": (wrapped_body.get("request") or {}).get("toolConfig") if isinstance(wrapped_body, dict) else None,
                                        }
                                        logger.warning(
                                            "Antigravity stream HTTP %s diagnostics: error=%s request=%s",
                                            response.status_code,
                                            body_preview,
                                            request_summary,
                                        )
                                    except Exception:
                                        logger.debug("Antigravity stream diagnostics failed", exc_info=True)
                                    if (
                                        response.status_code == 400
                                        and isinstance(last_error, CodeAssistError)
                                        and str(getattr(last_error, "details", {}).get("status", "")).upper() == "INVALID_ARGUMENT"
                                    ):
                                        retry_body = _minimal_invalid_argument_retry_body(wrapped_body)
                                        if retry_body is not None:
                                            logger.warning(
                                                "Retrying Antigravity stream after generic INVALID_ARGUMENT with minimal transcript"
                                            )
                                            with self._http.stream("POST", url, json=retry_body, headers=stream_headers) as retry_response:
                                                if retry_response.status_code == 200:
                                                    tool_call_counter = [0]
                                                    for event in _iter_sse_events(retry_response):
                                                        for chunk in _translate_antigravity_stream_event(event, model, tool_call_counter):
                                                            yield chunk
                                                    return
                                                retry_response.read()
                                                last_error = _gemini_http_error(retry_response)
                                                if (
                                                    retry_response.status_code == 400
                                                    and isinstance(last_error, CodeAssistError)
                                                    and str(getattr(last_error, "details", {}).get("status", "")).upper() == "INVALID_ARGUMENT"
                                                ):
                                                    ultra_retry_body = _minimal_invalid_argument_retry_body(
                                                        wrapped_body,
                                                        preserve_request_metadata=False,
                                                    )
                                                    if ultra_retry_body is not None:
                                                        logger.warning(
                                                            "Retrying Antigravity stream after metadata-preserving INVALID_ARGUMENT fallback failed"
                                                        )
                                                        with self._http.stream("POST", url, json=ultra_retry_body, headers=stream_headers) as ultra_retry_response:
                                                            if ultra_retry_response.status_code == 200:
                                                                tool_call_counter = [0]
                                                                for event in _iter_sse_events(ultra_retry_response):
                                                                    for chunk in _translate_antigravity_stream_event(event, model, tool_call_counter):
                                                                        yield chunk
                                                                return
                                                            ultra_retry_response.read()
                                                            last_error = _gemini_http_error(ultra_retry_response)
                                    if response.status_code == 403 and _is_endpoint_service_disabled(response):
                                        break
                                    if response.status_code not in {400, 404, 429, 500, 502, 503, 504}:
                                        raise last_error
                                    if (
                                        _is_short_antigravity_capacity_error(last_error)
                                        and capacity_attempt + 1 < ANTIGRAVITY_CAPACITY_RETRY_ATTEMPTS
                                    ):
                                        _push_antigravity_capacity_pacing(
                                            effective_model,
                                            float(getattr(last_error, "retry_after", 0.0) or 0.0),
                                        )
                                        continue
                                    if use_credits or _is_google_one_ai_credit_fallback_error(last_error):
                                        break
                                    # 404/400 → fall through to next
                                    # candidate instead of raising
                                    # immediately (mirrors non-stream
                                    # fallback logic).
                                    break
                                tool_call_counter: List[int] = [0]
                                for event in _iter_sse_events(response):
                                    for chunk in _translate_antigravity_stream_event(event, model, tool_call_counter):
                                        yield chunk
                                return
                        except httpx.HTTPError as exc:
                            last_error = CodeAssistError(
                                f"Antigravity streaming request failed for {effective_model}: {exc}",
                                code="antigravity_stream_error",
                            )
                            break
            if last_error:
                raise last_error

        return _generator()
