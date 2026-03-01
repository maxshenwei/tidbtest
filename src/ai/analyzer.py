"""AI-powered failure analysis.

Analyzes test failures by sending context to an LLM and parsing
structured root-cause analysis results.
"""

from __future__ import annotations

import json
import logging

from src.ai.client import get_ai_client
from src.ai.prompts.analyze import build_analysis_prompt
from src.models import ExecuteResult, TestCase

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"bug", "version_regression", "flaky", "test_issue", "env_issue"}


class FailureAnalyzer:
    """Analyzes test failures using LLM."""

    def __init__(self, ai_config: dict | None = None, tidb_version: str = "unknown"):
        self._config = ai_config or {}
        self._client = get_ai_client(self._config)
        self._tidb_version = tidb_version

    def analyze_failure(
        self,
        case: TestCase,
        exec_result: ExecuteResult,
        error_detail: dict,
        extra_context: str = "",
    ) -> dict:
        messages = build_analysis_prompt(
            case_id=case.id,
            suite_name="",
            sql=case.sql,
            expected=error_detail.get("expected", ""),
            actual=error_detail.get("actual", ""),
            message=error_detail.get("message", ""),
            tidb_version=self._tidb_version,
            retries=0,
            extra_context=extra_context or "None",
        )

        try:
            raw = self._client.chat(messages)
            analysis = _parse_analysis(raw)
            confidence = analysis.get("confidence", 0)
            if isinstance(confidence, (int, float)) and confidence < 0.6:
                logger.info(
                    "AI analysis for '%s' has low confidence (%.2f) — marked as advisory",
                    case.id, confidence,
                )
            return analysis
        except Exception as e:
            logger.warning("AI analysis failed for case '%s': %s", case.id, e)
            return {
                "root_cause": f"AI analysis error: {e}",
                "category": "env_issue",
                "confidence": 0.0,
                "suggestion": "AI analysis could not complete. Please investigate manually.",
                "related_issues": [],
            }


def analyze_report_failures(
    report_data: dict,
    ai_config: dict | None = None,
) -> dict:
    """Analyze all failures in a JSON report, returning enriched report."""
    ai_config = ai_config or {}
    tidb_version = report_data.get("tidb_version", "unknown")
    analyzer = FailureAnalyzer(ai_config, tidb_version)

    analyzed_count = 0
    for suite in report_data.get("suites", []):
        for case in suite.get("cases", []):
            if case.get("status") != "failed":
                continue
            if case.get("ai_analysis"):
                continue

            dummy_case = TestCase(
                id=case["id"],
                description="",
                sql=case.get("sql", ""),
                expect=None,
            )
            error_detail = case.get("error", {})
            dummy_exec = ExecuteResult(error=Exception(error_detail.get("message", "")))

            analysis = analyzer.analyze_failure(dummy_case, dummy_exec, error_detail)
            case["ai_analysis"] = analysis
            analyzed_count += 1

    logger.info("AI analysis completed for %d failed cases", analyzed_count)
    return report_data


def _parse_analysis(raw: str) -> dict:
    """Parse JSON analysis from LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end]).strip()

    analysis = json.loads(raw)

    required_keys = {"root_cause", "category", "confidence", "suggestion"}
    missing = required_keys - set(analysis.keys())
    if missing:
        raise ValueError(f"Analysis missing required fields: {missing}")

    if analysis["category"] not in VALID_CATEGORIES:
        logger.warning(
            "AI returned invalid category '%s', defaulting to 'test_issue'",
            analysis["category"],
        )
        analysis["category"] = "test_issue"

    conf = analysis.get("confidence", 0)
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        analysis["confidence"] = 0.5

    analysis.setdefault("related_issues", [])
    return analysis
