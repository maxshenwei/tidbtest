from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Expectation:
    type: str  # rows | count | error | affected_rows | regex
    value: Any


@dataclass
class TestCase:
    id: str
    description: str
    sql: str
    expect: Expectation
    tolerance: float | None = None
    ignore_order: bool = False
    min_version: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class TestSuite:
    suite: str
    description: str
    tags: list[str]
    setup: list[str]
    teardown: list[str]
    cases: list[TestCase]


@dataclass
class ExecuteResult:
    """Raw result from executing a SQL statement."""

    rows: list[list[Any]] | None = None
    column_names: list[str] | None = None
    affected_rows: int = 0
    error: Exception | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


@dataclass
class ValidationResult:
    passed: bool
    message: str = ""
    expected_repr: str = ""
    actual_repr: str = ""


@dataclass
class CaseResult:
    case_id: str
    suite_name: str
    status: str  # passed | failed | skipped | error
    duration_ms: float
    sql: str = ""
    retries: int = 0
    error_detail: dict[str, Any] | None = None
    ai_analysis: dict[str, Any] | None = None


@dataclass
class SuiteResult:
    name: str
    cases: list[CaseResult] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class TestRunResult:
    run_id: str
    tidb_version: str
    suites: list[SuiteResult] = field(default_factory=list)
    duration_sec: float = 0.0

    @property
    def summary(self) -> dict[str, Any]:
        total = passed = failed = skipped = retried = 0
        for s in self.suites:
            for c in s.cases:
                total += 1
                if c.status == "passed":
                    passed += 1
                elif c.status == "failed":
                    failed += 1
                elif c.status == "skipped":
                    skipped += 1
                if c.retries > 0:
                    retried += 1
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_sec": round(self.duration_sec, 3),
            "flaky_retried": retried,
        }
