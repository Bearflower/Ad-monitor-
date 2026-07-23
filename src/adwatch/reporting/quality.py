import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from adwatch.pipeline.runner import PipelineSummary


def write_quality_report(
    report_dir: Path,
    data_date: date,
    source: str,
    summaries: list[PipelineSummary],
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    destination = report_dir / f"quality-{data_date.isoformat()}.json"
    temporary = destination.with_suffix(".json.tmp")
    payload = {
        "data_date": data_date.isoformat(),
        "source": source,
        "simulated": source == "mock",
        "runs": [asdict(summary) for summary in summaries],
        "totals": {
            "received": sum(summary.received for summary in summaries),
            "accepted": sum(summary.accepted for summary in summaries),
            "quarantined": sum(summary.quarantined for summary in summaries),
        },
    }
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination
