import json
from types import SimpleNamespace


def test_openai_compatible_client_logs_usage(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKEN_USAGE_LOG_DIR", str(tmp_path))

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="reply"))],
                usage=SimpleNamespace(
                    prompt_tokens=11,
                    completion_tokens=4,
                    total_tokens=15,
                    model_dump=lambda: {
                        "prompt_tokens": 11,
                        "completion_tokens": 4,
                        "total_tokens": 15,
                    },
                ),
            )

    class FakeOpenAI:
        def __init__(self, api_key, base_url, timeout):
            self.base_url = base_url
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("pipeline.utils.api_client.openai.OpenAI", FakeOpenAI)

    from pipeline.utils.api_client import GeminiAPIClient

    client = GeminiAPIClient(
        api_key="key",
        base_url="https://example.invalid/v1",
        service_name="az_text",
    )
    assert client.generate_text("hello", model="vision-model") == "reply"

    records = [
        json.loads(line)
        for line in next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["event"] == "api_call"
    assert records[0]["service_format"] == "openai"
    assert records[0]["service_name"] == "az_text"
    assert records[0]["operation"] == "text_dialogue"
    assert records[0]["model"] == "vision-model"
    assert records[0]["token_usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "total_tokens": 15,
    }
    assert records[0]["raw_usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "total_tokens": 15,
    }
    assert client.last_call_id == records[0]["call_id"]


def test_gemini_native_client_logs_usage_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKEN_USAGE_LOG_DIR", str(tmp_path))

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": "native reply"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 21,
                    "candidatesTokenCount": 6,
                    "totalTokenCount": 27,
                },
            }

    monkeypatch.setattr(
        "pipeline.utils.gemini_native_client.requests.post",
        lambda *args, **kwargs: FakeResponse(),
    )

    from pipeline.utils.gemini_native_client import GeminiNativeAPIClient

    client = GeminiNativeAPIClient(
        api_key="key",
        base_url="https://generativelanguage.googleapis.com/v1beta/models/model-x:generateContent",
        service_name="gemini_text",
    )
    assert client.generate_text("hello") == "native reply"

    records = [
        json.loads(line)
        for line in next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["service_format"] == "gemini_native"
    assert records[0]["service_name"] == "gemini_text"
    assert records[0]["operation"] == "text_dialogue"
    assert records[0]["model"] == "model-x"
    assert records[0]["token_usage"] == {
        "prompt_tokens": 21,
        "completion_tokens": 6,
        "total_tokens": 27,
    }
    assert records[0]["raw_usage"] == {
        "promptTokenCount": 21,
        "candidatesTokenCount": 6,
        "totalTokenCount": 27,
    }
    assert client.last_call_id == records[0]["call_id"]


def test_client_factory_passes_service_name_to_created_clients(monkeypatch) -> None:
    from pipeline.utils.client_factory import _create_client_from_config
    from pipeline.utils.service_manager import ServiceConfig

    captured = {}

    class FakeClient:
        def __init__(self, api_key, base_url, service_name=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["service_name"] = service_name

    monkeypatch.setattr("pipeline.utils.client_factory.GeminiAPIClient", FakeClient)

    _create_client_from_config(
        ServiceConfig(
            name="az_text",
            service_type="openai",
            api_key="key",
            base_url="https://example.invalid/v1",
            model="model",
        )
    )

    assert captured == {
        "api_key": "key",
        "base_url": "https://example.invalid/v1",
        "service_name": "az_text",
    }


def test_service_manager_matches_service_name_with_model_tie_breaker(monkeypatch) -> None:
    from pipeline.utils.service_manager import ServiceConfig, ServiceManager

    monkeypatch.setattr(
        ServiceManager,
        "get_all_services",
        lambda: {
            "az_text": ServiceConfig(
                name="az_text",
                service_type="openai",
                api_key="shared-key",
                base_url="https://az.example/v1",
                model="gemini-3.1-pro-preview",
            ),
            "az_image": ServiceConfig(
                name="az_image",
                service_type="openai",
                api_key="shared-key",
                base_url="https://az.example/v1",
                model="gemini-3-pro-image-preview",
            ),
        },
    )

    assert (
        ServiceManager.find_matching_service_name(
            "openai",
            "shared-key",
            "https://az.example/v1/",
            "gemini-3-pro-image-preview",
        )
        == "az_image"
    )
