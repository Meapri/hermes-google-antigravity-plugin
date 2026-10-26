"""Antigravity native client shim: OpenAI-style .chat.completions.create backed by
Cloud Code PA (v1internal, Google OAuth). Reuses gemini_native_adapter conversion +
the vendored cloudcode-pa client, so the whole chat_completions path works unchanged."""
from __future__ import annotations
from typing import Any, Optional
from agent.gemini_native_adapter import build_gemini_request, translate_gemini_response

_ANTIGRAVITY_HOSTS = ("cloudcode-pa.googleapis.com",)

def is_antigravity_base_url(base_url: Optional[str]) -> bool:
    return bool(base_url) and any(h in base_url for h in _ANTIGRAVITY_HOSTS)

def _ag_client():
    from agent.antigravity_cloudcode import AntigravityClient
    from agent.antigravity_cloudcode_config import Settings
    return AntigravityClient(Settings.from_env())

class _Completions:
    def __init__(self, o): self._o = o
    def create(self, **kw):
        model = kw.get("model", "")
        req = build_gemini_request(messages=kw.get("messages") or [], tools=kw.get("tools"),
            tool_choice=kw.get("tool_choice"), temperature=kw.get("temperature"),
            max_tokens=kw.get("max_tokens") or kw.get("max_completion_tokens"),
            top_p=kw.get("top_p"), stop=kw.get("stop"), thinking_config=kw.get("reasoning_config"))
        raw = self._o._ag.generate_raw(request=req, model=model)
        data = raw.get("response", raw) if isinstance(raw, dict) else {}
        return translate_gemini_response(data if isinstance(data, dict) else {}, model)

class _Chat:
    def __init__(self, o): self.completions = _Completions(o)

class AntigravityChatClient:
    def __init__(self, api_key: str = "", base_url: str = "", **kwargs: Any):
        self.api_key, self.base_url = api_key, base_url
        self._ag = _ag_client(); self.chat = _Chat(self)
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
