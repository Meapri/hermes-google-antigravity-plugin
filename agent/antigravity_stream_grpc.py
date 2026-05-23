"""Context window compression utilities for Antigravity REST streaming.

Adds ``contextWindowCompression`` to the inner ``request`` dict sent to
``v1internal:streamGenerateContent``.  This tells the server to apply a
sliding-window compression strategy when the conversation grows past
``triggerTokens``, trimming it back to ``targetTokens``.

The feature originates in the BidiGenerateContent proto but the REST
streaming endpoint *may* honour the same field.  If the server ignores
it, no harm done — the field is silently dropped.  If accepted, it
enables automatic server-side context compression and prevents hard
context-length failures on long sessions.

JSON shape injected into the request body::

    {
        "contextWindowCompression": {
            "triggerTokens": 100000,
            "slidingWindow": {
                "targetTokens": 60000
            }
        }
    }

Proto schema (reconstructed from agy binary)::

    message ContextWindowCompressionConfig {
        optional int32 trigger_tokens = 1;
        oneof compression_mechanism {
            SlidingWindow sliding_window = 2;
        }
    }

    message SlidingWindow {
        optional int32 target_tokens = 1;
    }
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default token budgets per model family
# ---------------------------------------------------------------------------
# (trigger_tokens, target_tokens)
#
# trigger_tokens: when conversation token count reaches this, compression fires
# target_tokens:  what the server trims the conversation down to
#
# Gemini Flash / Pro — 128K context windows
_GEMINI_DEFAULTS: Tuple[int, int] = (100_000, 60_000)
# Claude — 200K context window
_CLAUDE_DEFAULTS: Tuple[int, int] = (160_000, 100_000)
# GPT-OSS — 128K context window
_GPT_OSS_DEFAULTS: Tuple[int, int] = (100_000, 60_000)
# Fallback for unknown models
_FALLBACK_DEFAULTS: Tuple[int, int] = (100_000, 60_000)


def _defaults_for_model(model: str) -> Tuple[int, int]:
    """Return ``(trigger_tokens, target_tokens)`` for the given model name."""
    lower = (model or "").lower()
    if "claude" in lower:
        return _CLAUDE_DEFAULTS
    if "gpt-oss" in lower:
        return _GPT_OSS_DEFAULTS
    # Gemini Flash / Pro / anything else from Google
    return _GEMINI_DEFAULTS


def _is_compression_enabled() -> bool:
    """Check the ``HERMES_ANTIGRAVITY_CONTEXT_COMPRESSION`` env var.

    The REST endpoint currently rejects this field on some accounts/endpoints,
    so it is opt-in only. Values ``1``, ``true``, ``yes``, ``on``, ``enabled``
    enable the experimental server-side compression hint.
    """
    raw = os.getenv("HERMES_ANTIGRAVITY_CONTEXT_COMPRESSION", "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_context_window_compression_config(
    trigger_tokens: int,
    target_tokens: int,
) -> Dict[str, Any]:
    """Build the JSON dict for ``contextWindowCompression``.

    >>> build_context_window_compression_config(100_000, 60_000)
    {'triggerTokens': 100000, 'slidingWindow': {'targetTokens': 60000}}
    """
    return {
        "triggerTokens": int(trigger_tokens),
        "slidingWindow": {
            "targetTokens": int(target_tokens),
        },
    }


def inject_context_compression(
    request_body: Dict[str, Any],
    *,
    trigger_tokens: Optional[int] = None,
    target_tokens: Optional[int] = None,
    model: str = "",
) -> None:
    """Add ``contextWindowCompression`` to *request_body* in-place.

    If *trigger_tokens* or *target_tokens* are ``None``, defaults are chosen
    based on the *model* name (see ``_defaults_for_model``).

    Respects the ``HERMES_ANTIGRAVITY_CONTEXT_COMPRESSION`` env var — when set
    to a falsy value the function is a no-op.

    The function is idempotent: calling it twice with the same parameters
    overwrites with the same values.

    Parameters
    ----------
    request_body:
        The inner ``request`` dict that will be sent inside the Antigravity
        wrapped body.  Modified in-place.
    trigger_tokens:
        When to trigger compression (total token count).
    target_tokens:
        Target size after compression.
    model:
        Model name used to pick defaults when explicit values are not given.
    """
    if not _is_compression_enabled():
        return

    default_trigger, default_target = _defaults_for_model(model)
    effective_trigger = trigger_tokens if trigger_tokens is not None else default_trigger
    effective_target = target_tokens if target_tokens is not None else default_target

    # Sanity: target must be less than trigger
    if effective_target >= effective_trigger:
        logger.warning(
            "Context compression target_tokens (%d) >= trigger_tokens (%d); skipping",
            effective_target,
            effective_trigger,
        )
        return

    request_body["contextWindowCompression"] = build_context_window_compression_config(
        effective_trigger,
        effective_target,
    )
    logger.debug(
        "Injected contextWindowCompression: trigger=%d target=%d (model=%s)",
        effective_trigger,
        effective_target,
        model or "<unknown>",
    )
