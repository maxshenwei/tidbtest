from __future__ import annotations

import json
import logging
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.models import TestRunResult

logger = logging.getLogger(__name__)


def generate_json_report(result: TestRunResult, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = result.summary
    total = summary["total"]
    pass_rate = round(summary["passed"] / total * 100, 2) if total else 0.0

    failures: list[dict] = []
    for suite in result.suites:
        for case in suite.cases:
            if case.status in ("failed", "error"):
                entry = {
                    "suite": suite.name,
                    "case_id": case.case_id,
                    "sql": case.sql,
                    "status": case.status,
                    "retries": case.retries,
                }
                if case.error_detail:
                    entry["error"] = case.error_detail
                if case.ai_analysis:
                    entry["ai_analysis"] = case.ai_analysis
                failures.append(entry)

    report = {
        "schema_version": "2.0",
        "run_id": result.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tidb_version": result.tidb_version,
        "environment": {
            "os": f"{platform.system()} {platform.release()}",
            "python": sys.version.split()[0],
            "framework": "tidbtest-mvp",
        },
        "summary": {
            **summary,
            "pass_rate": pass_rate,
        },
        "failure_summary": failures,
        "suites": [],
    }

    for suite in result.suites:
        suite_data = {
            "name": suite.name,
            "duration_ms": round(suite.duration_ms, 1),
            "cases": [],
        }
        for case in suite.cases:
            case_data = {
                "id": case.case_id,
                "status": case.status,
                "duration_ms": round(case.duration_ms, 1),
                "sql": case.sql,
                "retries": case.retries,
            }
            if case.error_detail:
                case_data["error"] = case.error_detail
            if case.ai_analysis:
                case_data["ai_analysis"] = case.ai_analysis
            suite_data["cases"].append(case_data)
        report["suites"].append(suite_data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("JSON report written to %s", output_path)
    return output_path


def load_json_report(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
