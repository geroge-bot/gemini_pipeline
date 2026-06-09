import json
from datetime import datetime, timezone

from pipeline.utils.api_usage_logger import log_api_call
from scripts.summarize_token_usage import main


def test_summarize_token_usage_cli_outputs_grouped_json(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("TOKEN_USAGE_LOG_DIR", str(tmp_path))
    log_api_call(
        timestamp=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        service_format="openai",
        operation="vl_dialogue",
        model="model-a",
        token_usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        raw_usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    )

    main(
        [
            "--start",
            "2026-05-12T00:00:00+00:00",
            "--end",
            "2026-05-13T00:00:00+00:00",
            "--group_by",
            "day",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["2026-05-12T00:00:00+00:00"]["total_tokens"] == 5
