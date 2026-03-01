"""Loader for MySQL Test Framework .test files (used by TiDB/mysql-tester).

Parses .test files into our TestSuite model, supporting:
- Plain SQL execution
- --error directive (expected error codes)
- --sorted_result directive
- # comments
- Multi-line SQL (continues until ;)
- Optional .result file for text-based output validation

When no .result file is present, cases validate only:
  - --error: correct error code
  - Everything else: no execution error (smoke test mode)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.models import Expectation, TestCase, TestSuite

logger = logging.getLogger(__name__)

DIRECTIVE_RE = re.compile(r"^--\s*(\w+)\s*(.*)?$")
ERROR_NAME_MAP = {
    "ER_NO_SUCH_TABLE": 1146,
    "ER_PARSE_ERROR": 1064,
    "ER_DUP_ENTRY": 1062,
    "ER_BAD_FIELD_ERROR": 1054,
    "ER_TABLE_EXISTS_ERROR": 1050,
    "ER_BAD_NULL_ERROR": 1048,
    "ER_DUP_FIELDNAME": 1060,
    "ER_WRONG_VALUE_COUNT_ON_ROW": 1136,
    "ER_TRUNCATED_WRONG_VALUE_FOR_FIELD": 1366,
    "ER_CANT_AGGREGATE_2COLLATIONS": 1267,
    "ER_WRONG_USAGE": 1221,
    "ER_NON_UNIQ_ERROR": 1052,
    "ER_CANT_AGGREGATE_3COLLATIONS": 1270,
    "ER_OPERAND_COLUMNS": 1241,
    "ER_SUBQUERY_NO_1_ROW": 1242,
    "ER_CTE_RECURSIVE_REQUIRES_UNION": 1353,
    "ER_NOT_SUPPORTED_YET": 1235,
    "ER_DATA_OUT_OF_RANGE": 1690,
    "ER_WRONG_FIELD_WITH_GROUP": 1055,
    "ER_SP_DOES_NOT_EXIST": 1305,
    "ER_ILLEGAL_REFERENCE": 1247,
    "ER_COLLATION_CHARSET_MISMATCH": 1253,
    "ER_UNKNOWN_ERROR": 1105,
    "ER_UNSUPPORTED_COLLATION": 1115,
}


def load_test_file(path: str | Path) -> TestSuite:
    """Load a .test file into a TestSuite.

    Each SQL statement becomes a sequential test case. --error directives
    produce error-expectation cases; all others expect no error.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Test file not found: {path}")

    raw_steps = _parse_test_file(path)

    result_path = _find_result_file(path)
    expected_outputs: dict[int, str] = {}
    if result_path and result_path.exists():
        expected_outputs = _parse_result_file(result_path, raw_steps)
        logger.info("Loaded .result file: %s (%d outputs)", result_path, len(expected_outputs))

    suite_name = path.stem
    cases: list[TestCase] = []

    for i, step in enumerate(raw_steps):
        sql = step["sql"]
        directives = step["directives"]

        expect_error = directives.get("error")
        sorted_result = "sorted_result" in directives

        if expect_error is not None:
            codes = _parse_error_codes(expect_error)
            primary_code = next((c for c in codes if c != 0), codes[0]) if codes else 0
            case = TestCase(
                id=f"line_{step['line']}",
                description=f"[--error {expect_error}] {sql[:60]}",
                sql=sql,
                expect=Expectation(type="error", value={"code": primary_code}),
            )
        elif i in expected_outputs:
            case = TestCase(
                id=f"line_{step['line']}",
                description=sql[:80],
                sql=sql,
                expect=Expectation(type="result_text", value=expected_outputs[i]),
                ignore_order=sorted_result,
            )
        else:
            case = TestCase(
                id=f"line_{step['line']}",
                description=sql[:80],
                sql=sql,
                expect=Expectation(type="no_error", value=None),
            )

        cases.append(case)

    logger.info(
        "Loaded .test file '%s': %d SQL statements (%d with --error, %d with expected output)",
        suite_name, len(cases),
        sum(1 for c in cases if c.expect.type == "error"),
        sum(1 for c in cases if c.expect.type == "result_text"),
    )

    return TestSuite(
        suite=suite_name,
        description=f"Imported from {path.name}",
        tags=["imported", "mysql-test-format"],
        setup=[],
        teardown=[],
        cases=cases,
    )


def _parse_test_file(path: Path) -> list[dict]:
    """Parse .test file into a list of {sql, line, directives}."""
    lines = path.read_text(encoding="utf-8").splitlines()
    steps: list[dict] = []
    pending_directives: dict[str, str] = {}
    sql_buffer: list[str] = []
    sql_start_line = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        m = DIRECTIVE_RE.match(stripped)
        if m:
            directive_name = m.group(1).lower()
            directive_arg = (m.group(2) or "").strip()

            skip_directives = {
                "enable_warnings", "disable_warnings",
                "enable_info", "disable_info",
                "replace_regex", "replace_column",
                "let", "echo", "sleep",
                "connect", "disconnect", "source",
            }
            if directive_name in skip_directives:
                i += 1
                continue
            if directive_name == "error":
                pending_directives["error"] = directive_arg
            elif directive_name == "sorted_result":
                pending_directives["sorted_result"] = ""
            i += 1
            continue

        if not sql_buffer:
            sql_start_line = i + 1

        sql_buffer.append(line)

        if stripped.endswith(";"):
            full_sql = " ".join(sql_buffer).strip()
            full_sql = re.sub(r"\s+", " ", full_sql)
            steps.append({
                "sql": full_sql,
                "line": sql_start_line,
                "directives": pending_directives,
            })
            pending_directives = {}
            sql_buffer = []
        i += 1

    if sql_buffer:
        full_sql = " ".join(sql_buffer).strip()
        steps.append({
            "sql": full_sql,
            "line": sql_start_line,
            "directives": pending_directives,
        })

    return steps


_WARNING_HEADER_RE = re.compile(r"^Level\tCode\tMessage$")
_WARNING_ROW_RE = re.compile(r"^(Warning|Note|Error)\t\d+\t")


def _parse_result_file(result_path: Path, steps: list[dict]) -> dict[int, str]:
    """Parse .result file, mapping step index -> expected text output.

    Uses sequential matching: each SQL in the result file is matched to the
    next unconsumed occurrence of that SQL in the steps list. This correctly
    handles duplicate SQLs (e.g., multiple `select * from t;`).

    MySQL warning output (Level/Code/Message tables) is filtered out since
    the framework does not capture SHOW WARNINGS output.
    """
    from collections import defaultdict

    content = result_path.read_text(encoding="utf-8")
    result_lines = content.splitlines()

    sql_queues: dict[str, list[int]] = defaultdict(list)
    for i, step in enumerate(steps):
        normalized = re.sub(r"\s+", " ", step["sql"].strip().rstrip(";")).lower()
        sql_queues[normalized].append(i)

    sql_consumed: dict[str, int] = defaultdict(int)

    expected: dict[int, str] = {}
    current_idx: int | None = None
    output_lines: list[str] = []
    skip_warnings = False

    for rline in result_lines:
        normalized = re.sub(r"\s+", " ", rline.strip().rstrip(";")).lower()

        if normalized in sql_queues:
            if current_idx is not None and output_lines:
                expected[current_idx] = "\n".join(output_lines)

            queue = sql_queues[normalized]
            consumed = sql_consumed[normalized]
            if consumed < len(queue):
                current_idx = queue[consumed]
                sql_consumed[normalized] = consumed + 1
            else:
                current_idx = None
            output_lines = []
            skip_warnings = False
            continue

        if rline.startswith("ERROR "):
            current_idx = None
            output_lines = []
            skip_warnings = False
            continue

        if _WARNING_HEADER_RE.match(rline):
            skip_warnings = True
            continue
        if skip_warnings:
            if _WARNING_ROW_RE.match(rline):
                continue
            skip_warnings = False

        if current_idx is not None:
            output_lines.append(rline)

    if current_idx is not None and output_lines:
        expected[current_idx] = "\n".join(output_lines)

    return expected


def _find_result_file(test_path: Path) -> Path | None:
    """Find the .result file for a .test file.

    Checks: sibling .result, ../r/<name>.result, r/<name>.result
    """
    stem = test_path.stem
    candidates = [
        test_path.with_suffix(".result"),
        test_path.parent / "r" / f"{stem}.result",
        test_path.parent.parent / "r" / f"{stem}.result",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _parse_error_codes(spec: str) -> list[int]:
    """Parse error spec like '1062' or 'ER_NO_SUCH_TABLE' or '0,ER_PARSE_ERROR'."""
    codes = []
    for part in spec.replace(",", " ").split():
        part = part.strip()
        if part.isdigit():
            codes.append(int(part))
        elif part in ERROR_NAME_MAP:
            codes.append(ERROR_NAME_MAP[part])
        else:
            logger.debug("Unknown error name: %s", part)
    return codes
