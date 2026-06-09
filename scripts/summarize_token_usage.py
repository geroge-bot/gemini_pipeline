import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.utils.api_usage_logger import summarize_usage


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Summarize API token usage logs by hour, day, or month."
    )
    parser.add_argument("--start", required=True, help="Inclusive ISO datetime, e.g. 2026-05-12T00:00:00+08:00")
    parser.add_argument("--end", required=True, help="Exclusive ISO datetime, e.g. 2026-05-13T00:00:00+08:00")
    parser.add_argument(
        "--group_by",
        choices=["hour", "day", "month"],
        default="day",
        help="Time bucket for aggregation.",
    )
    args = parser.parse_args(argv)

    summary = summarize_usage(
        start=_parse_datetime(args.start),
        end=_parse_datetime(args.end),
        group_by=args.group_by,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
