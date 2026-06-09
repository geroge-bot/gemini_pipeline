import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.utils.api_usage_logger import (
    log_api_call,
    log_result_saved,
    summarize_usage,
)


def test_api_call_and_result_saved_are_appended_to_daily_jsonl(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKEN_USAGE_LOG_DIR", str(tmp_path))
    ts = datetime(2026, 5, 12, 10, 30, 0, tzinfo=timezone.utc)

    call_id = log_api_call(
        timestamp=ts,
        service_format="openai",
        operation="vl_dialogue",
        model="gemini-3.1-pro-preview",
        token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        raw_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        result_preview="ok",
    )
    log_result_saved(
        call_id=call_id,
        timestamp=ts,
        result_path=r"D:\out\generated.json",
        result_kind="json",
    )

    log_file = tmp_path / "2026-05-12.jsonl"
    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]

    assert [record["event"] for record in records] == ["api_call", "result_saved"]
    assert records[0]["call_id"] == call_id
    assert records[0]["service_format"] == "openai"
    assert records[0]["operation"] == "vl_dialogue"
    assert records[0]["raw_usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert records[1]["call_id"] == call_id
    assert records[1]["result_path"] == r"D:\out\generated.json"


def test_summarize_usage_filters_by_time_and_groups_by_hour(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKEN_USAGE_LOG_DIR", str(tmp_path))

    log_api_call(
        timestamp=datetime(2026, 5, 12, 10, 5, tzinfo=timezone.utc),
        service_format="openai",
        service_name="az_text",
        operation="vl_dialogue",
        model="model-a",
        token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        raw_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    log_api_call(
        timestamp=datetime(2026, 5, 12, 10, 55, tzinfo=timezone.utc),
        service_format="gemini_native",
        service_name="gemini_image",
        operation="image_generation",
        model="model-b",
        token_usage={"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
        raw_usage={"promptTokenCount": 20, "candidatesTokenCount": 7, "totalTokenCount": 27},
    )
    log_api_call(
        timestamp=datetime(2026, 5, 12, 11, 0, tzinfo=timezone.utc),
        service_format="openai",
        service_name="az_text",
        operation="text_dialogue",
        model="model-a",
        token_usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        raw_usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    )

    summary = summarize_usage(
        start=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 12, 11, 0, tzinfo=timezone.utc),
        group_by="hour",
    )

    assert summary == {
        "2026-05-12T10:00:00+00:00": {
            "call_count": 2,
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "total_tokens": 42,
            "by_operation": {
                "vl_dialogue": {"call_count": 1, "total_tokens": 15},
                "image_generation": {"call_count": 1, "total_tokens": 27},
            },
            "by_model": {
                "model-a": {"call_count": 1, "total_tokens": 15},
                "model-b": {"call_count": 1, "total_tokens": 27},
            },
            "by_service": {
                "az_text": {"call_count": 1, "total_tokens": 15},
                "gemini_image": {"call_count": 1, "total_tokens": 27},
            },
        }
    }
