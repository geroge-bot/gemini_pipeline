import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_LOG_DIR = r"D:\Token使用统计"


def _log_dir() -> Path:
    return Path(os.environ.get("TOKEN_USAGE_LOG_DIR", DEFAULT_LOG_DIR))


def _now() -> datetime:
    return datetime.now().astimezone()


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return _to_jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _to_jsonable(
            {key: item for key, item in vars(value).items() if not key.startswith("_")}
        )
    return str(value)


def _openai_token_usage(raw_usage: Any) -> Dict[str, Optional[int]]:
    usage = _to_jsonable(raw_usage) or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def _gemini_token_usage(raw_usage: Any) -> Dict[str, Optional[int]]:
    usage = _to_jsonable(raw_usage) or {}
    return {
        "prompt_tokens": usage.get("promptTokenCount"),
        "completion_tokens": usage.get("candidatesTokenCount"),
        "total_tokens": usage.get("totalTokenCount"),
    }


def extract_openai_usage(raw_usage: Any) -> tuple[Dict[str, Optional[int]], Any]:
    return _openai_token_usage(raw_usage), _to_jsonable(raw_usage)


def extract_gemini_usage(raw_usage: Any) -> tuple[Dict[str, Optional[int]], Any]:
    return _gemini_token_usage(raw_usage), _to_jsonable(raw_usage)


def append_record(record: Dict[str, Any]) -> None:
    timestamp = datetime.fromisoformat(record["timestamp"])
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{timestamp.date().isoformat()}.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def log_api_call(
    *,
    service_format: str,
    service_name: Optional[str] = None,
    operation: str,
    model: Optional[str],
    token_usage: Dict[str, Optional[int]],
    raw_usage: Any,
    timestamp: Optional[datetime] = None,
    result_preview: Optional[str] = None,
    status: str = "success",
    error: Optional[str] = None,
    call_id: Optional[str] = None,
) -> str:
    timestamp = timestamp or _now()
    call_id = call_id or str(uuid.uuid4())
    append_record(
        {
            "event": "api_call",
            "call_id": call_id,
            "timestamp": timestamp.isoformat(),
            "service_format": service_format,
            "service_name": service_name,
            "operation": operation,
            "model": model,
            "token_usage": token_usage,
            "raw_usage": _to_jsonable(raw_usage),
            "result_path": None,
            "result_preview": result_preview[:500] if result_preview else None,
            "status": status,
            "error": error,
        }
    )
    return call_id


def log_result_saved(
    *,
    call_id: Optional[str],
    result_path: str,
    result_kind: str,
    timestamp: Optional[datetime] = None,
) -> Optional[str]:
    if not call_id:
        return None
    timestamp = timestamp or _now()
    append_record(
        {
            "event": "result_saved",
            "call_id": call_id,
            "timestamp": timestamp.isoformat(),
            "result_path": result_path,
            "result_kind": result_kind,
        }
    )
    return call_id


def _iter_records(start: datetime, end: datetime) -> Iterable[Dict[str, Any]]:
    day = start.date()
    while day <= end.date():
        log_path = _log_dir() / f"{day.isoformat()}.jsonl"
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        timestamp = datetime.fromisoformat(record["timestamp"])
                        if start <= timestamp < end:
                            yield record
        day = day + timedelta(days=1)


def _group_key(timestamp: datetime, group_by: str) -> str:
    if group_by == "hour":
        bucket = timestamp.replace(minute=0, second=0, microsecond=0)
    elif group_by == "day":
        bucket = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elif group_by == "month":
        bucket = timestamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("group_by must be one of: hour, day, month")
    return bucket.isoformat()


def summarize_usage(start: datetime, end: datetime, group_by: str = "day") -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for record in _iter_records(start, end):
        if record.get("event") != "api_call":
            continue
        key = _group_key(datetime.fromisoformat(record["timestamp"]), group_by)
        bucket = summary.setdefault(
            key,
            {
                "call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "by_operation": {},
                "by_model": {},
                "by_service": {},
            },
        )
        usage = record.get("token_usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens") or 0

        bucket["call_count"] += 1
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["total_tokens"] += total_tokens

        for group_name, field in (
            ("by_operation", record.get("operation")),
            ("by_model", record.get("model")),
            ("by_service", record.get("service_name")),
        ):
            group = bucket[group_name].setdefault(
                field or "unknown",
                {"call_count": 0, "total_tokens": 0},
            )
            group["call_count"] += 1
            group["total_tokens"] += total_tokens
    return summary
