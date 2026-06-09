import shutil
from pathlib import Path

from pipeline.interfaces import PipelineContext
from pipeline.models import PipelineConfig, PipelineResult
from pipeline.modules.generation import GenerationModule


def test_generation_module_logs_saved_image_path(monkeypatch) -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_generation_logging"
    if scratch.exists():
        shutil.rmtree(scratch)

    saved_events = []

    class FakeClient:
        def __init__(self, api_key, api_key_image, base_url):
            self.last_call_id = None

        def generate_image(self, prompt, ref_image_base64, model):
            self.last_call_id = "call-image-1"
            return "image-b64"

    monkeypatch.setattr("pipeline.modules.generation.GeminiAPIClient", FakeClient)
    monkeypatch.setattr("pipeline.modules.generation.extract_base64_from_response", lambda text: "b64")
    def fake_base64_to_image(b64, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"jpg")
        return True

    monkeypatch.setattr("pipeline.modules.generation.base64_to_image", fake_base64_to_image)
    monkeypatch.setattr(
        "pipeline.modules.generation.log_result_saved",
        lambda **kwargs: saved_events.append(kwargs),
    )

    try:
        original = scratch / "input" / "shop" / "food.jpg"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"orig")
        config = PipelineConfig(
            input_directory=str(scratch / "input"),
            output_directory=str(scratch / "output"),
            api_key="key",
            api_key_image="image-key",
            api_base_url="https://example.invalid/v1",
            random_seed=42,
        )
        context = PipelineContext(config=config, original_image_path=str(original))
        context.original_image_base64 = "orig-b64"
        context.results.append(
            PipelineResult(
                theme="scheme",
                original_image_path=str(original),
                original_plan="plan",
                analysis_prompt_used="prompt",
                mode="C",
            )
        )

        GenerationModule(prompt_template="{suggestion}").process(context)

        generated_path = context.results[0].generated_image_path
        assert generated_path
        assert Path(generated_path).exists()
        assert saved_events == [
            {
                "call_id": "call-image-1",
                "result_path": generated_path,
                "result_kind": "image",
            }
        ]
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
