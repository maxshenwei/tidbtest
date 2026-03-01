"""AI-powered test case generator.

Uses an LLM to generate YAML test suites from feature descriptions
and/or schema DDL, with quality gate validation and iterative refinement.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from src.ai.client import get_ai_client
from src.ai.prompts.generate import build_fix_prompt, build_generate_prompt
from src.ai.quality_gate import run_quality_gate
from src.models import TestSuite

logger = logging.getLogger(__name__)


class GenerationResult:
    def __init__(self):
        self.suite: TestSuite | None = None
        self.yaml_text: str = ""
        self.quality_errors: list[str] = []
        self.quality_warnings: list[str] = []
        self.rounds: int = 0
        self.success: bool = False


def generate_test_suite(
    feature: str,
    schema_ddl: str = "",
    tidb_version: str = "",
    ai_config: dict | None = None,
    max_rounds: int = 3,
) -> GenerationResult:
    """Generate a test suite from a feature description.

    Calls LLM → quality gate → fix loop (up to max_rounds).
    """
    ai_config = ai_config or {}
    client = get_ai_client(ai_config)
    result = GenerationResult()

    messages = build_generate_prompt(feature, schema_ddl, tidb_version)

    for round_num in range(1, max_rounds + 1):
        result.rounds = round_num
        logger.info("Generation round %d/%d", round_num, max_rounds)

        raw = client.chat(messages)
        yaml_text = _extract_yaml(raw)
        result.yaml_text = yaml_text

        gate_result, suite = run_quality_gate(yaml_text, schema_ddl)
        result.quality_errors = gate_result.errors
        result.quality_warnings = gate_result.warnings

        if gate_result.passed and suite is not None:
            result.suite = suite
            result.success = True
            logger.info(
                "Generation succeeded in round %d (%d cases, %d warnings)",
                round_num, len(suite.cases), len(gate_result.warnings),
            )
            if gate_result.warnings:
                for w in gate_result.warnings:
                    logger.warning("  Quality warning: %s", w)
            return result

        logger.warning(
            "Quality gate failed in round %d: %d errors",
            round_num, len(gate_result.errors),
        )
        for err in gate_result.errors:
            logger.warning("  %s", err)

        if round_num < max_rounds:
            messages = build_fix_prompt(yaml_text, gate_result.errors)

    logger.error("Generation failed after %d rounds", max_rounds)
    return result


def save_generated_suite(yaml_text: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(yaml_text)
    logger.info("Generated suite saved to %s", output_path)
    return output_path


def _extract_yaml(raw: str) -> str:
    """Extract YAML content from LLM response, stripping markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end])
    return raw.strip()
