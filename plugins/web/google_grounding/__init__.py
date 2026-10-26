"""Google Search grounding web-search backend on the native Antigravity provider.

Registers Hermes' ``web_search`` provider ``google_grounding``. A Gemini model on
Google Cloud Code PA is called with ``tools=[{"google_search": {}}]`` and the
grounding metadata (chunks + supports) becomes the standard Hermes rows.

Auth and transport come from the ``google-antigravity`` model-provider plugin in
this repository (``hermes auth add google-antigravity``): Hermes owns the pooled
OAuth token and its refresh. No API key, proxy, or Hermes core patch.

Config (optional, ``config.yaml``)::

    web:
      search_backend: google_grounding
      google_grounding:
        model: gemini-3.8-flash-low   # any Gemini id from the Antigravity catalog
        timeout: 90
        resolve_urls: true            # turn Google redirect links into the cited page URLs

Search-only: grounding returns cited URLs and support snippets, not page bodies,
so extraction stays on the configured extract backend.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from plugins.web._common import (
    SEARCH_LIMIT_CAP, BaseWebSearchProvider, run_search, search_ok, setup_schema, title_hit,
)

logger = logging.getLogger(__name__)

PROVIDER = "google-antigravity"
DEFAULT_MODEL = "gemini-3.8-flash-low"
DEFAULT_TIMEOUT = 90.0
_SNIPPET_CHARS = 500

_SYSTEM = (
    "You are a real-time web search assistant. Today is {today}. ALWAYS use Google Search; "
    "never answer from prior knowledge, which is outdated. Versions, models, prices and releases "
    "change and newer ones may exist that you do not know. Search for the current state as of "
    "today, prefer recently published sources, and trust search results over your assumptions. "
    "Answer briefly and cite sources."
)


def _web_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg: Any = load_config()
        for key in ("web", "google_grounding"):
            cfg = cfg.get(key) if isinstance(cfg, dict) else None
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:  # config is optional
        logger.debug("Could not load web.google_grounding config: %s", exc)
        return {}


def _client(timeout: float):
    """Build the provider plugin's own client with Hermes-resolved (auto-refreshed) credentials."""
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from providers import get_provider_profile

    profile = get_provider_profile(PROVIDER)
    if profile is None:
        raise ValueError("google-antigravity model provider plugin is not installed "
                         "(plugins/model-providers/google-antigravity).")
    runtime = resolve_runtime_provider(requested=PROVIDER)
    api_key = str(runtime.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("No Google Antigravity credentials. Run `hermes auth add google-antigravity`.")
    return profile.create_client(api_key=api_key, base_url=runtime.get("base_url"), timeout=timeout)


def _candidate(data: Dict[str, Any]) -> Dict[str, Any]:
    inner = data.get("response") if isinstance(data.get("response"), dict) else data
    cands = inner.get("candidates") if isinstance(inner, dict) else None
    return cands[0] if isinstance(cands, list) and cands and isinstance(cands[0], dict) else {}


def _metadata(cand: Dict[str, Any]) -> Dict[str, Any]:
    md = cand.get("groundingMetadata") or cand.get("grounding_metadata")
    return md if isinstance(md, dict) else {}


def _rows(md: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    chunks = md.get("groundingChunks") or md.get("grounding_chunks") or []
    snippets: Dict[int, List[str]] = {}
    for sup in md.get("groundingSupports") or md.get("grounding_supports") or []:
        if not isinstance(sup, dict):
            continue
        text = str((sup.get("segment") or {}).get("text") or "").strip()
        for i in sup.get("groundingChunkIndices") or sup.get("grounding_chunk_indices") or []:
            if text and isinstance(i, int):
                snippets.setdefault(i, []).append(text)
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for i, chunk in enumerate(chunks):
        web = chunk.get("web") if isinstance(chunk, dict) and isinstance(chunk.get("web"), dict) else {}
        url = str(web.get("uri") or web.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        title = str(web.get("title") or web.get("domain") or url)
        rows.append(title_hit(title, url, " ".join(snippets.get(i, []))[:_SNIPPET_CHARS], len(rows) + 1))
        if len(rows) >= limit:
            break
    return rows


_REDIRECT_PREFIX = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def _resolve_one(url: str) -> str:
    """Grounding links are opaque Google redirects; read the Location header (no body download)."""
    if not url.startswith(_REDIRECT_PREFIX):
        return url
    try:
        import httpx
        resp = httpx.head(url, follow_redirects=False, timeout=5.0)
        location = resp.headers.get("location") or ""
        return location if location.startswith(("http://", "https://")) else url
    except Exception:
        return url


def _resolve_urls(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace redirect URLs with the cited page URL, in parallel; drop duplicates after resolution."""
    if not rows:
        return rows
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, len(rows))) as pool:
        resolved = list(pool.map(_resolve_one, [r["url"] for r in rows]))
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row, url in zip(rows, resolved):
        if url in seen:
            continue
        seen.add(url)
        out.append(title_hit(row["title"], url, row["description"], len(out) + 1))
    return out


class GoogleGroundingWebSearchProvider(BaseWebSearchProvider):
    """Trust model: URLs come from Google's grounding metadata (redirect links that resolve to
    the cited page); descriptions are the model's supported answer segments."""

    NAME = "google_grounding"
    DISPLAY_NAME = "Google Search Grounding (Antigravity)"

    def is_available(self) -> bool:
        """Cheap: a pooled google-antigravity credential exists. No network, no refresh."""
        try:
            from hermes_cli.auth import read_credential_pool
            return any(isinstance(e, dict) and (e.get("access_token") or e.get("refresh_token"))
                       for e in read_credential_pool(PROVIDER))
        except Exception:
            return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        def body() -> Dict[str, Any]:
            q = (query or "").strip()
            if not q:
                raise ValueError("Empty search query.")
            try:
                n = max(1, min(int(limit), SEARCH_LIMIT_CAP))
            except (TypeError, ValueError):
                n = 5
            cfg = _web_config()
            model = str(cfg.get("model") or "").strip() or DEFAULT_MODEL
            try:
                timeout = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
            except (TypeError, ValueError):
                timeout = DEFAULT_TIMEOUT
            request = {
                "systemInstruction": {"role": "system", "parts": [{"text": _SYSTEM.format(
                    today=datetime.now().astimezone().strftime("%Y-%m-%d"))}]},
                "contents": [{"role": "user", "parts": [{"text": q}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0.2},
            }
            logger.info("Google grounding search: '%s' (limit=%d, model=%s)", q, n, model)
            client = _client(timeout)
            try:
                data = client._generate(f"{client.base_url}/models/{model}:{client.GENERATE_METHOD}",
                                        request, timeout)
            finally:
                client.close()
            cand = _candidate(data)
            md = _metadata(cand)
            result = search_ok(_resolve_urls(_rows(md, n)) if cfg.get("resolve_urls", True) else _rows(md, n))
            parts = (cand.get("content") or {}).get("parts") or []
            answer = "\n".join(str(p.get("text")) for p in parts
                               if isinstance(p, dict) and p.get("text") and not p.get("thought")).strip()
            if answer:
                result["data"]["answer"] = answer
            queries = md.get("webSearchQueries") or md.get("web_search_queries")
            if isinstance(queries, list) and queries:
                result["data"]["search_queries"] = [str(x) for x in queries if x]
            return result

        return run_search("Google grounding", logger, body)

    def get_setup_schema(self) -> Dict[str, Any]:
        return setup_schema(
            self.DISPLAY_NAME, "subscription",
            "Google Search grounding through your Antigravity subscription — run `hermes auth add google-antigravity`.",
        )


def register(ctx: Any) -> None:
    ctx.register_web_search_provider(GoogleGroundingWebSearchProvider())
