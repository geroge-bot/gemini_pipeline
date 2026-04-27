import json
import shutil
from pathlib import Path

from pipeline.interfaces import PipelineContext
from pipeline.models import PipelineConfig, PipelineResult
from pipeline.modules.two_image_labeler import (
    TwoImageLabelingModule,
    parse_labeling_json,
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


def test_parse_labeling_json_strips_markdown_fence() -> None:
    payload = {
        "input_image": {"camera_angle": 90},
        "output_image": {"aesthetic_score": 4},
    }

    parsed = parse_labeling_json("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")

    assert parsed == payload


def test_two_image_labeling_module_writes_labels(monkeypatch) -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_two_image_labeler"
    if scratch.exists():
        shutil.rmtree(scratch)

    context = _make_context(scratch)
    expected = {
        "input_image": {
            "camera_angle": 45,
            "dish_category": "中餐",
            "shooting_scene": "餐厅",
            "lighting": "自然光",
            "color_temperature": "中色温",
            "dish_count": "1",
        },
        "output_image": {
            "camera_angle": 60,
            "shot_size": "近景",
            "camera_angle_method": "斜拍",
            "composition_plating": "中心构图",
            "interaction": "无互动",
            "new_tableware": "筷子",
            "aesthetic_score": 4,
        },
    }
    calls = {}

    class FakeClient:
        def __init__(self, api_key, base_url):
            calls["api_key"] = api_key
            calls["base_url"] = base_url

        def generate_with_messages(self, messages, model):
            calls["messages"] = messages
            calls["model"] = model
            return json.dumps(expected, ensure_ascii=False)

    monkeypatch.setattr("pipeline.modules.two_image_labeler.GeminiAPIClient", FakeClient)
    monkeypatch.setattr(
        "pipeline.modules.two_image_labeler.image_to_base64",
        lambda path: "orig-b64" if Path(path).name == "original.jpg" else "gen-b64",
    )

    try:
        result_context = TwoImageLabelingModule(model="vision-model").process(context)

        result = result_context.results[0]
        assert result.labels == expected
        assert calls["api_key"] == "test-key"
        assert calls["base_url"] == "https://example.invalid/v1"
        assert calls["model"] == "vision-model"
        user_content = calls["messages"][1]["content"]
        assert user_content[0]["text"].startswith("对两张图进行打标")
        assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,orig-b64"
        assert user_content[2]["image_url"]["url"] == "data:image/jpeg;base64,gen-b64"

        saved = json.loads((scratch / "generated.json").read_text(encoding="utf-8"))
        assert saved["labels"] == expected
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_two_image_labeling_module_skips_missing_generated_image() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_two_image_labeler_skip"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        context = _make_context(scratch)
        context.results[0].generated_image_path = None

        TwoImageLabelingModule().process(context)

        assert context.results[0].labels is None
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
