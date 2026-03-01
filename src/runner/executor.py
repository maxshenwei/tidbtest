from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from src.db.connector import DatabaseConnector
from src.models import CaseResult, SuiteResult, TestCase, TestRunResult, TestSuite
from src.runner.isolator import Isolator
from src.runner.retrier import Retrier
from src.runner.validator import Validator

logger = logging.getLogger(__name__)


class TestExecutor:
    """Orchestrates test suite execution with isolation, retry, and validation."""

    def __init__(
        self,
        connector: DatabaseConnector,
        config: dict | None = None,
        analyzer=None,
    ):
        config = config or {}
        runner_cfg = config.get("runner", {})
        self.connector = connector
        self.validator = Validator()
        self.retrier = Retrier(runner_cfg.get("retry"))
        self.isolator = Isolator(connector)
        self.analyzer = analyzer
        self.ai_analyze_on_failure = runner_cfg.get("ai_analyze_on_failure", False)

    def run(
        self,
        suites: list[TestSuite],
        ai_analyze: bool | None = None,
    ) -> TestRunResult:
        if ai_analyze is None:
            ai_analyze = self.ai_analyze_on_failure

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        tidb_version = self._detect_version()

        logger.info(
            "Starting test run %s against TiDB %s (%d suites)",
            run_id, tidb_version, len(suites),
        )

        start = time.time()
        suite_results = []
        for suite in suites:
            result = self._run_suite(suite, tidb_version, ai_analyze)
            suite_results.append(result)

        duration = time.time() - start
        run_result = TestRunResult(
            run_id=run_id,
            tidb_version=tidb_version,
            suites=suite_results,
            duration_sec=duration,
        )
        summary = run_result.summary
        logger.info(
            "Run %s completed in %.2fs — %d passed, %d failed, %d skipped",
            run_id, duration,
            summary["passed"], summary["failed"], summary["skipped"],
        )
        return run_result

    def _detect_version(self) -> str:
        try:
            conn = self.connector.get_connection()
            version = self.connector.get_version(conn)
            conn.close()
            return version
        except Exception as e:
            logger.warning("Could not detect TiDB version: %s", e)
            return "unknown"

    def _run_suite(
        self, suite: TestSuite, tidb_version: str, ai_analyze: bool
    ) -> SuiteResult:
        logger.info("Running suite '%s' (%d cases)", suite.suite, len(suite.cases))
        start = time.time()

        conn = self.connector.get_connection()
        db_name: str | None = None
        case_results: list[CaseResult] = []

        try:
            db_name = self.isolator.create_isolated_db(conn, suite.suite)
            self._run_phase(conn, suite.setup, "setup", suite.suite)

            for case in suite.cases:
                if case.min_version and not _version_ok(tidb_version, case.min_version):
                    case_results.append(CaseResult(
                        case_id=case.id, suite_name=suite.suite,
                        status="skipped", duration_ms=0, sql=case.sql,
                    ))
                    logger.info("  [SKIP] %s (requires %s)", case.id, case.min_version)
                    continue
                result = self._run_case(conn, case, suite.suite, ai_analyze)
                case_results.append(result)

            self._run_phase(conn, suite.teardown, "teardown", suite.suite)
        except Exception as e:
            logger.error("Suite '%s' aborted: %s", suite.suite, e)
        finally:
            if db_name:
                self.isolator.drop_isolated_db(conn, db_name)
            try:
                conn.close()
            except Exception:
                pass

        duration_ms = (time.time() - start) * 1000
        return SuiteResult(name=suite.suite, cases=case_results, duration_ms=duration_ms)

    def _run_phase(self, conn, sqls: list[str], phase: str, suite_name: str) -> None:
        for sql in sqls:
            result = self.connector.execute(conn, sql)
            if result.is_error:
                logger.error("Suite '%s' %s failed: %s -> %s", suite_name, phase, sql, result.error)
                raise RuntimeError(f"{phase} SQL failed: {result.error}")

    def _run_case(
        self, conn, case: TestCase, suite_name: str, ai_analyze: bool
    ) -> CaseResult:
        start = time.time()
        retries = 0
        last_validation = None

        for attempt in range(1 + self.retrier.max_retries):
            exec_result = self.connector.execute(conn, case.sql)
            validation = self.validator.validate(exec_result, case)

            if validation.passed:
                duration = (time.time() - start) * 1000
                logger.info("  [PASS] %s (%.1fms, %d retries)", case.id, duration, retries)
                return CaseResult(
                    case_id=case.id, suite_name=suite_name,
                    status="passed", duration_ms=duration,
                    sql=case.sql, retries=retries,
                )

            if exec_result.is_error and self.retrier.is_retryable(exec_result.error):
                retries += 1
                if attempt < self.retrier.max_retries:
                    logger.info("  [RETRY] %s attempt %d: %s", case.id, retries, exec_result.error)
                    self.retrier.wait(retries)
                    try:
                        conn.ping(reconnect=True)
                    except Exception:
                        pass
                    continue

            last_validation = validation
            break

        duration = (time.time() - start) * 1000
        error_detail = {
            "expected": last_validation.expected_repr if last_validation else "",
            "actual": last_validation.actual_repr if last_validation else "",
            "message": last_validation.message if last_validation else str(exec_result.error),
        }
        logger.info("  [FAIL] %s: %s", case.id, error_detail["message"])

        ai_result = None
        if ai_analyze and self.analyzer:
            try:
                ai_result = self.analyzer.analyze_failure(
                    case=case,
                    exec_result=exec_result,
                    error_detail=error_detail,
                )
            except Exception as e:
                logger.warning("AI analysis failed for %s: %s", case.id, e)

        return CaseResult(
            case_id=case.id, suite_name=suite_name,
            status="failed", duration_ms=duration,
            sql=case.sql, retries=retries,
            error_detail=error_detail, ai_analysis=ai_result,
        )


def _version_ok(current: str, minimum: str) -> bool:
    """Check if current version >= minimum version (best-effort semver)."""
    try:
        def parse(v: str) -> tuple[int, ...]:
            v = v.lstrip("v").split("-")[0]
            return tuple(int(x) for x in v.split(".")[:3])
        return parse(current) >= parse(minimum)
    except (ValueError, IndexError):
        return True
