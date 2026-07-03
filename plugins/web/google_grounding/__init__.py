"""Google Search grounding web-search backend (via Google Antigravity).

Registers Hermes' ``web_search`` provider ``google_grounding``. Uses the
Antigravity Cloud Code session with the ``gemini-3.5-flash-high`` model and
Google Search grounding (``tools=[{"google_search": {}}]``) to answer queries
with cited, current sources — no API key, reuses the provider's Google OAuth.

Search-only: grounding returns cited source URLs/snippets, not full page
content, so ``supports_extract`` is False (extraction stays on the configured
extract backend, e.g. firecrawl).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.5-flash-high"


def _client():
    from agent.antigravity_cloudcode import AntigravityClient
    from agent.antigravity_cloudcode_config import Settings
    return AntigravityClient(Settings.from_env())


class GoogleGroundingWebSearchProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "google_grounding"

    @property
    def display_name(self) -> str:
        return "Google Grounding (Antigravity)"

    def is_available(self) -> bool:
        try:
            from agent.antigravity_oauth import resolve_antigravity_runtime_credentials
            return bool(resolve_antigravity_runtime_credentials().get("api_key"))
        except Exception:
            return False

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Empty search query."}
        try:
            r = _client().grounded_search(query=query, model=_MODEL, limit=limit)
        except Exception as exc:
            logger.warning("Google grounding search failed: %s", exc)
            return {"success": False, "error": f"Google grounding search failed: {exc}"}
        web: List[Dict[str, Any]] = []
        for pos, item in enumerate(r.get("results", []), start=1):
            web.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("snippet", ""),
                "position": pos,
            })
        data: Dict[str, Any] = {"web": web}
        answer = (r.get("answer") or "").strip()
        if answer:
            data["answer"] = answer
        queries = r.get("queries") or []
        if queries:
            data["search_queries"] = queries
        return {"success": True, "data": data}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Google Grounding (Antigravity)",
            "badge": "subscription",
            "tag": "Google Search grounding via Antigravity gemini-3.5-flash-high (run: hermes auth add google-antigravity)",
            "env_vars": [],
        }


def register(ctx: Any) -> None:
    ctx.register_web_search_provider(GoogleGroundingWebSearchProvider())
