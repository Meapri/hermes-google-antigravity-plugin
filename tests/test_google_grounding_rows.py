"""Offline tests for the google_grounding web provider row mapping."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERMES = Path.home() / ".hermes" / "hermes-agent"
if not (HERMES / "plugins" / "web" / "_common.py").exists():
    pytest.skip("Hermes checkout with plugins/web/_common.py required", allow_module_level=True)
sys.path.insert(0, str(HERMES))
_spec = importlib.util.spec_from_file_location(
    "google_grounding_under_test", Path(__file__).parents[1] / "plugins/web/google_grounding/__init__.py")
gg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gg)

MD = {
    "webSearchQueries": ["q1"],
    "groundingChunks": [
        {"web": {"uri": "https://a.example/1", "title": "a.example"}},
        {"web": {"uri": "https://a.example/1", "title": "dup"}},
        {"web": {"uri": "https://b.example/2", "title": "b.example"}},
        {"retrievedContext": {}},
    ],
    "groundingSupports": [
        {"segment": {"text": "alpha"}, "groundingChunkIndices": [0]},
        {"segment": {"text": "beta"}, "groundingChunkIndices": [0, 2]},
    ],
}


def test_rows_shape_dedupe_and_snippets():
    rows = gg._rows(MD, 5)
    assert [r["url"] for r in rows] == ["https://a.example/1", "https://b.example/2"]
    assert rows[0] == {"title": "a.example", "url": "https://a.example/1", "description": "alpha beta", "position": 1}
    assert rows[1]["description"] == "beta" and rows[1]["position"] == 2


def test_rows_limit():
    assert len(gg._rows(MD, 1)) == 1


def test_resolve_keeps_non_redirect_urls():
    assert gg._resolve_one("https://a.example/1") == "https://a.example/1"


def test_resolve_dedupes_after_resolution(monkeypatch):
    monkeypatch.setattr(gg, "_resolve_one", lambda u: "https://same.example/")
    rows = [gg.title_hit("t", "x1", "d", 1), gg.title_hit("t", "x2", "d", 2)]
    assert [r["position"] for r in gg._resolve_urls(rows)] == [1]


def test_empty_query_fails_cleanly():
    assert gg.GoogleGroundingWebSearchProvider().search("  ") == {"success": False, "error": "Empty search query."}
