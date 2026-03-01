from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from src.models import ExecuteResult, Expectation, TestCase, ValidationResult

_TRAILING_ZERO_RE = re.compile(r"^(-?\d+)\.0+$")
_SCI_PLUS_RE = re.compile(r"([eE])\+(\d+)$")
_JSON_TRAILING_ZERO_RE = re.compile(r"(?<=[\s:,\[])(-?\d+)\.0(?=[,\s}\]\)])")


class Validator:
    """Validates SQL execution results against expected outcomes."""

    def validate(self, actual: ExecuteResult, case: TestCase) -> ValidationResult:
        expect = case.expect
        dispatch = {
            "rows": self._validate_rows,
            "count": self._validate_count,
            "error": self._validate_error,
            "affected_rows": self._validate_affected_rows,
            "regex": self._validate_regex,
            "no_error": self._validate_no_error,
            "result_text": self._validate_result_text,
        }
        handler = dispatch.get(expect.type)
        if handler is None:
            return ValidationResult(
                passed=False, message=f"Unknown expect type: {expect.type}"
            )
        return handler(actual, expect, case)

    def _validate_rows(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if actual.is_error:
            return ValidationResult(
                passed=False,
                message=f"Expected rows but got error: {actual.error}",
                expected_repr=repr(expect.value),
                actual_repr=str(actual.error),
            )
        if actual.rows is None:
            return ValidationResult(
                passed=False,
                message="Expected rows but got no result set (DML/DDL?)",
                expected_repr=repr(expect.value),
                actual_repr="no result set",
            )

        expected_rows = expect.value
        actual_rows = actual.rows

        if case.ignore_order:
            expected_rows = sorted(expected_rows, key=_row_sort_key)
            actual_rows = sorted(actual_rows, key=_row_sort_key)

        if len(actual_rows) != len(expected_rows):
            return ValidationResult(
                passed=False,
                message=f"Row count mismatch: expected {len(expected_rows)}, got {len(actual_rows)}",
                expected_repr=repr(expected_rows),
                actual_repr=repr(actual_rows),
            )

        for i, (act_row, exp_row) in enumerate(zip(actual_rows, expected_rows)):
            if len(act_row) != len(exp_row):
                return ValidationResult(
                    passed=False,
                    message=f"Row {i}: column count mismatch ({len(act_row)} vs {len(exp_row)})",
                    expected_repr=repr(exp_row),
                    actual_repr=repr(act_row),
                )
            for j, (a, e) in enumerate(zip(act_row, exp_row)):
                if not _compare_value(a, e, case.tolerance):
                    return ValidationResult(
                        passed=False,
                        message=f"Row {i}, Col {j}: expected {e!r}, got {a!r}",
                        expected_repr=repr(exp_row),
                        actual_repr=repr(act_row),
                    )

        return ValidationResult(passed=True)

    def _validate_count(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if actual.is_error:
            return ValidationResult(
                passed=False,
                message=f"Expected row count but got error: {actual.error}",
                expected_repr=str(expect.value),
                actual_repr=str(actual.error),
            )
        if actual.rows is None:
            return ValidationResult(
                passed=False,
                message="Expected rows but got no result set",
                expected_repr=str(expect.value),
                actual_repr="no result set",
            )
        actual_count = len(actual.rows)
        expected_count = int(expect.value)
        if actual_count != expected_count:
            return ValidationResult(
                passed=False,
                message=f"Row count: expected {expected_count}, got {actual_count}",
                expected_repr=str(expected_count),
                actual_repr=str(actual_count),
            )
        return ValidationResult(passed=True)

    def _validate_error(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if not actual.is_error:
            return ValidationResult(
                passed=False,
                message=f"Expected an error but query succeeded. Rows={actual.rows}",
                expected_repr=repr(expect.value),
                actual_repr="no error",
            )
        err = actual.error
        err_spec = expect.value if isinstance(expect.value, dict) else {}

        if "code" in err_spec:
            actual_code = getattr(err, "errno", None)
            if actual_code != err_spec["code"]:
                return ValidationResult(
                    passed=False,
                    message=f"Error code mismatch: expected {err_spec['code']}, got {actual_code}",
                    expected_repr=str(err_spec["code"]),
                    actual_repr=str(actual_code),
                )

        if "message_contains" in err_spec:
            err_msg = str(err)
            if err_spec["message_contains"] not in err_msg:
                return ValidationResult(
                    passed=False,
                    message=f"Error message does not contain '{err_spec['message_contains']}': {err_msg}",
                    expected_repr=err_spec["message_contains"],
                    actual_repr=err_msg,
                )

        return ValidationResult(passed=True)

    def _validate_affected_rows(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if actual.is_error:
            return ValidationResult(
                passed=False,
                message=f"Expected affected_rows but got error: {actual.error}",
                expected_repr=str(expect.value),
                actual_repr=str(actual.error),
            )
        expected_count = int(expect.value)
        if actual.affected_rows != expected_count:
            return ValidationResult(
                passed=False,
                message=f"Affected rows: expected {expected_count}, got {actual.affected_rows}",
                expected_repr=str(expected_count),
                actual_repr=str(actual.affected_rows),
            )
        return ValidationResult(passed=True)

    def _validate_regex(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if actual.is_error:
            target = str(actual.error)
        elif actual.rows:
            target = repr(actual.rows)
        else:
            target = ""

        pattern = str(expect.value)
        if re.search(pattern, target):
            return ValidationResult(passed=True)
        return ValidationResult(
            passed=False,
            message=f"Regex '{pattern}' did not match output",
            expected_repr=pattern,
            actual_repr=target[:500],
        )

    def _validate_no_error(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if actual.is_error:
            return ValidationResult(
                passed=False,
                message=f"Expected no error but got: {actual.error}",
                expected_repr="no error",
                actual_repr=str(actual.error),
            )
        return ValidationResult(passed=True)

    def _validate_result_text(
        self, actual: ExecuteResult, expect: Expectation, case: TestCase
    ) -> ValidationResult:
        if actual.is_error:
            return ValidationResult(
                passed=False,
                message=f"Expected result but got error: {actual.error}",
                expected_repr=str(expect.value)[:200],
                actual_repr=str(actual.error),
            )
        if actual.rows is None:
            actual_text = ""
        else:
            header = "\t".join(actual.column_names or [])
            data_lines = [
                "\t".join(_format_cell(v) for v in row) for row in actual.rows
            ]
            parts = ([header] if header else []) + data_lines
            actual_text = "\n".join(parts)

        expected_text = str(expect.value).strip()
        actual_text = actual_text.strip()

        expected_text = _normalize_text(expected_text)
        actual_text = _normalize_text(actual_text)

        if case.ignore_order:
            expected_lines = sorted(expected_text.splitlines())
            actual_lines = sorted(actual_text.splitlines())
        else:
            expected_lines = expected_text.splitlines()
            actual_lines = actual_text.splitlines()

        if expected_lines == actual_lines:
            return ValidationResult(passed=True)

        return ValidationResult(
            passed=False,
            message="Result text mismatch",
            expected_repr=expected_text[:500],
            actual_repr=actual_text[:500],
        )


def _is_numeric(val) -> bool:
    try:
        Decimal(str(val))
        return True
    except (InvalidOperation, ValueError, TypeError):
        return False


def _compare_value(actual, expected, tolerance: float | None = None) -> bool:
    if tolerance is not None and _is_numeric(actual) and _is_numeric(expected):
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal(str(tolerance))
    return str(actual) == str(expected)


def _row_sort_key(row: list) -> list:
    return [str(v) for v in row]


def _format_cell(value) -> str:
    """Convert a single cell value to MySQL-compatible text representation."""
    if value is None:
        return "NULL"
    if isinstance(value, set):
        return ",".join(sorted(value)) if value else ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e16:
            return str(int(value))
        s = str(value)
        return _SCI_PLUS_RE.sub(r"\1\2", s)
    return str(value)


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: unify NULL, number formats, etc."""
    lines = text.splitlines()
    normalized = []
    for line in lines:
        cells = line.split("\t")
        out = []
        for cell in cells:
            c = cell.strip()
            m = _TRAILING_ZERO_RE.match(c)
            if m:
                c = m.group(1)
            c = _SCI_PLUS_RE.sub(r"\1\2", c)
            c = _JSON_TRAILING_ZERO_RE.sub(r"\1", c)
            out.append(c)
        normalized.append("\t".join(out))
    return "\n".join(normalized)
