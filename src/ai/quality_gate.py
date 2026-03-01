"""Quality Gate for AI-generated test cases.

Validates AI output through multiple stages:
1. YAML format validation
2. Schema structural check
3. SQL syntax check (basic, via sqlparse)
4. Schema consistency check (tables/columns referenced exist in DDL)
"""

from __future__ import annotations

import logging
import re

import sqlparse
import yaml

from src.models import TestSuite
from src.runner.loader import VALID_EXPECT_TYPES, LoadError, load_suite

logger = logging.getLogger(__name__)


class QualityGateResult:
    def __init__(self):
        self.passed = True
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)


def run_quality_gate(
    yaml_text: str,
    schema_ddl: str = "",
) -> tuple[QualityGateResult, TestSuite | None]:
    """Run all quality gate checks on generated YAML text.

    Returns (result, parsed_suite_or_None).
    """
    result = QualityGateResult()

    # Stage 1: YAML parse
    parsed = _check_yaml_format(yaml_text, result)
    if parsed is None:
        return result, None

    # Stage 2: structural validation
    suite = _check_structure(parsed, result)
    if suite is None:
        return result, None

    # Stage 3: SQL syntax
    _check_sql_syntax(suite, result)

    # Stage 4: schema consistency
    if schema_ddl:
        _check_schema_consistency(suite, schema_ddl, result)

    # Stage 5: best-practice warnings
    _check_best_practices(suite, result)

    return result, suite if result.passed else None


def _check_yaml_format(yaml_text: str, result: QualityGateResult) -> dict | None:
    try:
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            result.add_error("YAML root must be a mapping, got " + type(data).__name__)
            return None
        return data
    except yaml.YAMLError as e:
        result.add_error(f"YAML parse error: {e}")
        return None


def _check_structure(data: dict, result: QualityGateResult) -> TestSuite | None:
    if "suite" not in data:
        result.add_error("Missing required key: 'suite'")
    if "cases" not in data or not isinstance(data.get("cases"), list):
        result.add_error("Missing or invalid 'cases' (must be a list)")
    if result.errors:
        return None

    for i, case in enumerate(data["cases"]):
        prefix = f"Case #{i}"
        if "id" not in case:
            result.add_error(f"{prefix}: missing 'id'")
        else:
            prefix = f"Case '{case['id']}'"
        if "sql" not in case:
            result.add_error(f"{prefix}: missing 'sql'")
        if "expect" not in case:
            result.add_error(f"{prefix}: missing 'expect'")
        elif isinstance(case["expect"], dict):
            etype = case["expect"].get("type", "")
            if etype not in VALID_EXPECT_TYPES:
                result.add_error(
                    f"{prefix}: invalid expect.type '{etype}', "
                    f"must be one of {VALID_EXPECT_TYPES}"
                )

    if result.errors:
        return None

    try:
        from src.runner.loader import _parse_case
        from pathlib import Path

        cases = [_parse_case(c, Path("<ai-generated>"), i) for i, c in enumerate(data["cases"])]
        return TestSuite(
            suite=data["suite"],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            setup=data.get("setup", []),
            teardown=data.get("teardown", []),
            cases=cases,
        )
    except LoadError as e:
        result.add_error(f"Structure validation failed: {e}")
        return None


def _check_sql_syntax(suite: TestSuite, result: QualityGateResult) -> None:
    all_sqls = list(suite.setup) + [c.sql for c in suite.cases] + list(suite.teardown)
    for sql in all_sqls:
        try:
            parsed = sqlparse.parse(sql)
            if not parsed:
                result.add_error(f"SQL produced no statements: {sql[:80]}")
        except Exception as e:
            result.add_error(f"SQL parse error in '{sql[:80]}': {e}")


def _check_schema_consistency(
    suite: TestSuite, schema_ddl: str, result: QualityGateResult
) -> None:
    """Best-effort check: tables referenced in test SQL should exist in schema DDL."""
    schema_tables = set()
    for match in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?",
        schema_ddl,
        re.IGNORECASE,
    ):
        schema_tables.add(match.group(1).lower())

    # Also include tables created in setup
    for sql in suite.setup:
        for match in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?",
            sql,
            re.IGNORECASE,
        ):
            schema_tables.add(match.group(1).lower())

    if not schema_tables:
        return

    for case in suite.cases:
        referenced = _extract_table_refs(case.sql)
        for table in referenced:
            if table.lower() not in schema_tables:
                result.add_warning(
                    f"Case '{case.id}': references table '{table}' not found in schema or setup"
                )


def _extract_table_refs(sql: str) -> set[str]:
    """Extract table names from FROM / JOIN / INTO / UPDATE clauses (best-effort)."""
    tables = set()
    patterns = [
        r"(?:FROM|JOIN|INTO|UPDATE)\s+`?(\w+)`?",
    ]
    for pat in patterns:
        for m in re.finditer(pat, sql, re.IGNORECASE):
            name = m.group(1).lower()
            if name not in {"select", "set", "where", "values", "into", "from"}:
                tables.add(name)
    return tables


def _check_best_practices(suite: TestSuite, result: QualityGateResult) -> None:
    for case in suite.cases:
        sql_upper = case.sql.strip().upper()
        if sql_upper.startswith("SELECT") and case.expect.type == "rows":
            if "ORDER BY" not in sql_upper and not case.ignore_order:
                result.add_warning(
                    f"Case '{case.id}': SELECT with rows expectation but no ORDER BY "
                    f"and ignore_order=false — may cause flaky results"
                )
