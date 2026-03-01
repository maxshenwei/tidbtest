from __future__ import annotations

import logging
from pathlib import Path

import yaml

from src.models import Expectation, TestCase, TestSuite

logger = logging.getLogger(__name__)

REQUIRED_SUITE_KEYS = {"suite", "cases"}
REQUIRED_CASE_KEYS = {"id", "sql", "expect"}
VALID_EXPECT_TYPES = {"rows", "count", "error", "affected_rows", "regex", "no_error", "result_text"}


class LoadError(Exception):
    pass


def load_suite(path: str | Path) -> TestSuite:
    path = Path(path)
    if not path.exists():
        raise LoadError(f"Test file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise LoadError(f"Invalid YAML structure in {path}: expected a mapping")

    missing = REQUIRED_SUITE_KEYS - set(data.keys())
    if missing:
        raise LoadError(f"Missing required keys in {path}: {missing}")

    cases = []
    for i, raw_case in enumerate(data["cases"]):
        case = _parse_case(raw_case, path, i)
        cases.append(case)

    return TestSuite(
        suite=data["suite"],
        description=data.get("description", ""),
        tags=data.get("tags", []),
        setup=data.get("setup", []),
        teardown=data.get("teardown", []),
        cases=cases,
    )


def _parse_case(raw: dict, path: Path, index: int) -> TestCase:
    missing = REQUIRED_CASE_KEYS - set(raw.keys())
    if missing:
        raise LoadError(f"Case #{index} in {path} missing keys: {missing}")

    expect_raw = raw["expect"]
    etype = expect_raw.get("type", "rows")
    if etype not in VALID_EXPECT_TYPES:
        raise LoadError(
            f"Case '{raw['id']}' in {path}: invalid expect type '{etype}'. "
            f"Must be one of {VALID_EXPECT_TYPES}"
        )

    return TestCase(
        id=raw["id"],
        description=raw.get("description", ""),
        sql=raw["sql"],
        expect=Expectation(type=etype, value=expect_raw.get("value")),
        tolerance=raw.get("tolerance"),
        ignore_order=raw.get("ignore_order", False),
        min_version=raw.get("min_version"),
        tags=raw.get("tags", []),
    )


def load_suites_from_dir(directory: str | Path) -> list[TestSuite]:
    directory = Path(directory)
    if not directory.is_dir():
        raise LoadError(f"Test directory not found: {directory}")

    from src.runner.test_file_loader import load_test_file

    suites = []
    all_files = sorted(
        list(directory.rglob("*.yaml")) + list(directory.rglob("*.test"))
    )
    if not all_files:
        logger.warning("No test files (.yaml / .test) found in %s", directory)
        return suites

    for f in all_files:
        try:
            if f.suffix == ".test":
                suite = load_test_file(f)
            else:
                suite = load_suite(f)
            suites.append(suite)
            logger.info("Loaded suite '%s' (%d cases) from %s", suite.suite, len(suite.cases), f)
        except Exception as e:
            logger.error("Failed to load %s: %s", f, e)
            raise

    return suites
