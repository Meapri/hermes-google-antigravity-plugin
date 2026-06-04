from __future__ import annotations

import base64
import importlib
from pathlib import Path

import pytest


ag_image = importlib.import_module("plugins.image_gen.google-antigravity")

_PNG_HEX = (
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44"
    "ae426082"
)


def _b64_png() -> str:
    return base64.b64encode(bytes.fromhex(_PNG_HEX)).decode()


def _b64_jpg_header() -> str:
    return base64.b64encode(b"\xff\xd8\xff\xe0" + b"JFIF\x00" + (b"\x00" * 64)).decode()


@pytest.fixture(autouse=True)
def _tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def provider():
    return ag_image.GoogleAntigravityImageGenProvider()


class TestMetadata:
    def test_name(self, provider):
        assert provider.name == "google-antigravity"

    def test_display_name(self, provider):
        assert provider.display_name == "Google Antigravity"

    def test_default_model(self, provider):
        assert provider.default_model() == "gemini-3.1-flash-image"

    def test_list_models_exposes_antigravity_image_models(self, provider, monkeypatch):
        monkeypatch.setattr(ag_image, "_available_model_catalog", lambda access_token=None: dict(ag_image._MODELS))

        ids = [m["id"] for m in provider.list_models()]
        assert ids == [
            "gemini-3.1-flash-image",
            "gemini-2.5-flash-image",
        ]

    def test_list_models_uses_runtime_image_catalog(self, provider, monkeypatch):
        monkeypatch.setattr(
            ag_image,
            "_available_model_catalog",
            lambda access_token=None: {
                "gemini-3.1-flash-image": {
                    "display": "Gemini 3.1 Flash Image",
                    "speed": "~8-20s",
                    "strengths": "Runtime catalog image model",
                    "quotaInfo": {"remainingFraction": 1},
                }
            },
        )

        models = provider.list_models()

        assert [m["id"] for m in models] == ["gemini-3.1-flash-image"]
        assert models[0]["quota"] == {"remainingFraction": 1}

    def test_setup_schema_requires_no_image_api_key(self, provider):
        schema = provider.get_setup_schema()
        assert schema["env_vars"] == []
        assert schema["badge"] == "oauth"


class TestRequestShape:
    def test_build_image_request_uses_response_modalities_and_aspect_ratio(self):
        request = ag_image._build_image_request(
            prompt="draw a small lychee-shaped robot",
            aspect_ratio="portrait",
        )

        assert request["contents"][0]["role"] == "user"
        assert request["contents"][0]["parts"][0]["text"] == "draw a small lychee-shaped robot"
        assert request["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
        assert request["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"

    def test_build_image_request_accepts_image_size(self):
        request = ag_image._build_image_request(
            prompt="draw a small lychee-shaped robot",
            aspect_ratio="square",
            image_size="2K",
        )

        assert request["generationConfig"]["imageConfig"] == {
            "aspectRatio": "1:1",
            "imageSize": "2K",
        }

    def test_image_size_normalization_accepts_pixel_aliases(self):
        assert ag_image._resolve_image_size("2048") == "2K"
        assert ag_image._resolve_image_size("4096px") == "4K"
        assert ag_image._resolve_image_size("0.5K") == "512"
        assert ag_image._resolve_image_size("weird") == ""

    def test_model_normalization_accepts_google_prefix_and_dash_aliases(self):
        assert ag_image._normalize_model("google/gemini-2-5-flash-image") == "gemini-2.5-flash-image"
        assert ag_image._normalize_model("gemini/gemini-3-1-flash-image") == "gemini-3.1-flash-image"

    def test_explicit_supported_model_is_not_masked_by_empty_env(self, monkeypatch):
        monkeypatch.delenv("HERMES_ANTIGRAVITY_IMAGE_MODEL", raising=False)

        model_id, _meta = ag_image._resolve_model("gemini-2.5-flash-image")

        assert model_id == "gemini-2.5-flash-image"


class TestResponseParsing:
    def test_extracts_inline_data_image(self):
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": _b64_png(),
                                }
                            }
                        ]
                    }
                }
            ]
        }

        data, kind, extension = ag_image._extract_image_result(payload)

        assert data == _b64_png()
        assert kind == "b64"
        assert extension == "png"

    def test_extracts_data_url_image(self):
        data_url = f"data:image/png;base64,{_b64_png()}"
        payload = {"generatedImages": [{"result": data_url}]}

        data, kind, extension = ag_image._extract_image_result(payload)

        assert data == data_url
        assert kind == "b64"
        assert extension == "png"

    def test_bare_base64_extension_is_inferred_from_image_header(self):
        payload = {"generatedImages": [{"result": _b64_jpg_header()}]}

        data, kind, extension = ag_image._extract_image_result(payload)

        assert data == _b64_jpg_header()
        assert kind == "b64"
        assert extension == "jpg"


class TestGenerate:
    def test_returns_invalid_argument_for_empty_prompt(self, provider):
        result = provider.generate("   ")

        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_routes_pro_image_alias_when_antigravity_catalog_exposes_it(self, provider, monkeypatch):
        from agent import google_antigravity_oauth

        monkeypatch.setattr(google_antigravity_oauth, "get_valid_access_token", lambda: "token")
        monkeypatch.setattr(
            ag_image,
            "_available_model_catalog",
            lambda access_token=None: {
                "gemini-3-pro-image": {
                    "display": "Gemini 3 Pro Image",
                    "speed": "~20-90s",
                    "strengths": "Runtime Pro image model",
                    "quotaInfo": {},
                }
            },
        )

        def fake_submit_antigravity_image_request(**kwargs):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": _b64_png(),
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

        monkeypatch.setattr(ag_image, "_submit_antigravity_image_request", fake_submit_antigravity_image_request)

        result = provider.generate("draw a card", model="nano-banana-pro")

        assert result["success"] is True
        assert result["model"] == "gemini-3-pro-image"
        assert result["backend"] == "antigravity-code-assist"
        assert result["request_model"] == "gemini-3-pro-image"

    def test_routes_resolution_to_antigravity_request(self, provider, monkeypatch):
        from agent import google_antigravity_oauth

        monkeypatch.setattr(google_antigravity_oauth, "get_valid_access_token", lambda: "token")
        seen = {}

        def fake_submit_antigravity_image_request(**kwargs):
            seen.update(kwargs)
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": _b64_png(),
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

        monkeypatch.setattr(ag_image, "_submit_antigravity_image_request", fake_submit_antigravity_image_request)

        result = provider.generate("draw a card", model="gemini-3.1-flash-image", resolution="2048")

        assert result["success"] is True
        assert result["model"] == "gemini-3.1-flash-image"
        assert result["image_size"] == "2K"
        assert seen["model"] == "gemini-3.1-flash-image"
        assert seen["request"]["generationConfig"]["imageConfig"]["imageSize"] == "2K"

    def test_returns_invalid_model_when_pro_is_not_in_antigravity_catalog(self, provider, monkeypatch):
        from agent import google_antigravity_oauth

        monkeypatch.setattr(google_antigravity_oauth, "get_valid_access_token", lambda: "token")
        monkeypatch.setattr(
            ag_image,
            "_available_model_catalog",
            lambda access_token=None: {
                "gemini-3.1-flash-image": {
                    "display": "Gemini 3.1 Flash Image",
                    "speed": "~8-20s",
                    "strengths": "Runtime catalog image model",
                    "quotaInfo": {},
                }
            },
        )

        result = provider.generate("draw a card", model="nano-banana-pro")

        assert result["success"] is False
        assert result["error_type"] == "invalid_model"
        assert result["model"] == "gemini-3-pro-image"
        assert "imageGenerationModelIds" in result["error"]

    def test_generate_saves_inline_image(self, provider, monkeypatch, tmp_path):
        from agent import google_antigravity_oauth

        monkeypatch.setattr(google_antigravity_oauth, "get_valid_access_token", lambda: "token")
        seen = {}

        def fake_submit_antigravity_image_request(**kwargs):
            seen.update(kwargs)
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": _b64_png(),
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

        monkeypatch.setattr(
            ag_image,
            "_submit_antigravity_image_request",
            fake_submit_antigravity_image_request,
        )

        result = provider.generate("draw a tiny terminal window", aspect_ratio="square", image_size="2K")

        assert result["success"] is True
        assert result["provider"] == "google-antigravity"
        assert result["model"] == "gemini-3.1-flash-image"
        assert result["aspect_ratio"] == "square"
        assert result["backend"] == "antigravity-code-assist"
        assert result["image_size"] == "2K"
        assert seen["request"]["generationConfig"]["imageConfig"]["imageSize"] == "2K"

        saved = Path(result["image"])
        assert saved.exists()
        assert saved.parent == tmp_path / "cache" / "images"
        assert saved.name.startswith("google_antigravity_gemini-3.1-flash-image_")
