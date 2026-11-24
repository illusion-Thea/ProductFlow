import io

import httpx
import pytest
from openai import APIError
from PIL import Image

from productflow_backend.application.contracts import ProductInput
from productflow_backend.infrastructure.provider_config import ResolvedImageProviderConfig, ResolvedTextProviderConfig


@pytest.mark.parametrize("adapter", ["text", "responses", "images_generate", "images_edit", "gemini"])
@pytest.mark.parametrize("failure", [429, 503, "disconnect", "timeout", "invalid_200"])
def test_real_sdk_with_fake_http_sends_exactly_once(configured_env, monkeypatch, adapter, failure):
    """Exercise the installed SDK retry loops without opening any socket."""
    calls = []

    def transport(request):
        calls.append(request)
        if failure == "disconnect":
            raise httpx.RemoteProtocolError("fixture disconnected", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if failure == "invalid_200":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "fixture-response",
                    "output": [],
                    "output_text": "bad structure",
                    "usage": {"input_tokens": 7, "output_tokens": 1, "total_tokens": 8},
                    "candidates": [],
                    "data": [],
                },
            )
        return httpx.Response(failure, request=request, json={"error": {"message": "fixture refusal", "code": failure}})

    http = httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=False, trust_env=False)
    for module in ("text.openai_provider", "image.responses_provider", "image.images_provider"):
        monkeypatch.setattr(f"productflow_backend.infrastructure.{module}.single_request_http_client", lambda: http)
    if adapter == "text":
        from productflow_backend.infrastructure.text.openai_provider import OpenAITextProvider

        provider = OpenAITextProvider(
            ResolvedTextProviderConfig(
                "openai",
                "fixture-text",
                "fixture-text",
                api_key="fixture-not-a-key",
                base_url="https://fake.invalid/v1",
            )
        )
        def invoke():
            return provider.generate_brief(
                ProductInput(name="fixture", category=None, price=None, source_note=None, image_path="")
            )
    elif adapter == "gemini":
        from google import genai

        from productflow_backend.infrastructure.image.gemini_provider import GoogleGeminiImageClient

        original = genai.Client

        def fake_http_client(**kwargs):
            options = kwargs["http_options"]
            assert options.retry_options.attempts == 1
            assert options.client_args["follow_redirects"] is False
            options.client_args["transport"] = httpx.MockTransport(transport)
            return original(**kwargs)

        monkeypatch.setattr("productflow_backend.infrastructure.image.gemini_provider.genai.Client", fake_http_client)
        provider = GoogleGeminiImageClient(
            ResolvedImageProviderConfig("google_gemini_image", "gemini-2.5-flash-image", api_key="fixture-not-a-key")
        )
        def invoke():
            return provider.generate_image(prompt="fixture", size="1024x1024")
    elif adapter == "responses":
        from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

        provider = OpenAIResponsesImageClient(
            ResolvedImageProviderConfig(
                "openai_responses", "fixture-image", api_key="fixture-not-a-key", base_url="https://fake.invalid/v1"
            )
        )
        def invoke():
            return provider.generate_image(prompt="fixture", size="1024x1024")
    else:
        from productflow_backend.infrastructure.image.images_provider import OpenAIImagesClient

        provider = OpenAIImagesClient(
            ResolvedImageProviderConfig(
                "openai_images", "gpt-image-1", api_key="fixture-not-a-key", base_url="https://fake.invalid/v1"
            )
        )
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="PNG")
        invoke = (
            (lambda: provider.generate(prompt="fixture", size="1024x1024"))
            if adapter == "images_generate"
            else (lambda: provider.edit(image=buffer.getvalue(), prompt="fixture", size="1024x1024"))
        )
    with pytest.raises((APIError, RuntimeError, ValueError)):
        invoke()
    assert len(calls) == 1, (adapter, failure, len(calls))
    assert calls[0].method == "POST"
    if adapter == "text" and failure == "invalid_200":
        assert provider.last_response_metadata["request_id"] == "fixture-response"
        assert provider.last_response_metadata["usage"]["input_tokens"] == 7
    http.close()
