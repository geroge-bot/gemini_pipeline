import json
import shutil
from pathlib import Path

from pipeline.interfaces import PipelineContext
from pipeline.models import PipelineConfig, PipelineResult
from pipeline.modules.user_guide_generator import (
    UserGuideGeneratorModule,
    parse_user_guide_json,
)


def _make_context(scratch: Path) -> PipelineContext:
    original = scratch / "original.jpg"
    generated = scratch / "generated.jpg"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"orig")
    generated.write_bytes(b"gen")

    config = PipelineConfig(
        input_directory=str(scratch),
        output_directory=str(scratch),
        api_key="test-key",
        api_base_url="https://example.invalid/v1",
    )
    context = PipelineContext(config=config, original_image_path=str(original))
    context.results.append(
        PipelineResult(
            theme="scheme",
            original_image_path=str(original),
            generated_image_path=str(generated),
            original_plan="plan",
            analysis_prompt_used="prompt",
            mode="C",
        )
    )
    return context


def test_parse_user_guide_json_strips_markdown_fence() -> None:
    payload = {
        "场景描述": "日式寿司",
        "整体引导": "将寿司排成斜线，配菜点缀边缘，俯拍突出层次",
        "摆盘描述": "把寿司错落排成斜线，姜片和芥末放在盘边留白",
    }

    parsed = parse_user_guide_json("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")

    assert parsed == payload


def test_parse_user_guide_json_keeps_only_latest_compose_fields() -> None:
    payload = {
        "场景描述": "日式寿司",
        "整体引导": "将寿司排成斜线，配菜点缀边缘，俯拍突出层次",
        "整体描述": "旧字段不应保留",
        "摆盘描述": "把寿司错落排成斜线，姜片和芥末放在盘边留白",
        "用户引导语": "旧字段不应保留",
    }

    parsed = parse_user_guide_json(json.dumps(payload, ensure_ascii=False))

    assert parsed == {
        "场景描述": "日式寿司",
        "整体引导": "将寿司排成斜线，配菜点缀边缘，俯拍突出层次",
        "摆盘描述": "把寿司错落排成斜线，姜片和芥末放在盘边留白",
    }


def test_user_guide_generator_module_writes_guide(monkeypatch) -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_user_guide_generator"
    if scratch.exists():
        shutil.rmtree(scratch)

    context = _make_context(scratch)
    expected = {
        "场景描述": "法式甜点",
        "整体引导": "把蛋糕移到窗边，浆果围边点缀，45度俯拍",
        "摆盘描述": "蛋糕居中放置，浆果沿盘边半圈排列，留出前景空白",
    }
    calls = {}
    saved_events = []

    class FakeClient:
        def __init__(self, api_key, base_url):
            calls["api_key"] = api_key
            calls["base_url"] = base_url
            self.last_call_id = None

        def generate_with_messages(self, messages, model):
            calls["messages"] = messages
            calls["model"] = model
            self.last_call_id = "call-guide-1"
            return json.dumps(expected, ensure_ascii=False)

    monkeypatch.setattr("pipeline.modules.user_guide_generator.GeminiAPIClient", FakeClient)
    monkeypatch.setattr(
        "pipeline.modules.user_guide_generator.log_result_saved",
        lambda **kwargs: saved_events.append(kwargs),
    )
    monkeypatch.setattr(
        "pipeline.modules.user_guide_generator.image_to_base64",
        lambda path: "orig-b64" if Path(path).name == "original.jpg" else "gen-b64",
    )

    try:
        result_context = UserGuideGeneratorModule(model="vision-model").process(context)

        result = result_context.results[0]
        assert result.description["user_guide"] == expected
        assert calls["api_key"] == "test-key"
        assert calls["base_url"] == "https://example.invalid/v1"
        assert calls["model"] == "vision-model"
        user_content = calls["messages"][1]["content"]
        assert user_content[0]["text"].startswith("你是一位深耕美食摄影")
        assert "场景描述" in user_content[0]["text"]
        assert "整体引导" in user_content[0]["text"]
        assert "摆盘描述" in user_content[0]["text"]
        assert "用户引导语" not in user_content[0]["text"]
        assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,orig-b64"
        assert user_content[2]["image_url"]["url"] == "data:image/jpeg;base64,gen-b64"

        saved = json.loads((scratch / "generated.json").read_text(encoding="utf-8"))
        assert saved["description"]["user_guide"] == expected
        assert saved_events == [
            {
                "call_id": "call-guide-1",
                "result_path": str(scratch / "generated.json"),
                "result_kind": "json",
            }
        ]
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_user_guide_generator_skips_existing_guide(monkeypatch) -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_user_guide_generator_skip"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        context = _make_context(scratch)
        context.results[0].description = {
            "user_guide": {
                "场景描述": "已有场景",
                "整体引导": "已有整体引导",
                "摆盘描述": "已有摆盘描述",
            }
        }

        def fail_if_called(*args, **kwargs):
            raise AssertionError("client should not be called")

        monkeypatch.setattr("pipeline.modules.user_guide_generator.GeminiAPIClient", fail_if_called)

        UserGuideGeneratorModule().process(context)

        assert context.results[0].description["user_guide"]["场景描述"] == "已有场景"
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
