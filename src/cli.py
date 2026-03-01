"""CLI entry point for tidbtest.

Commands:
  run       — Execute test suites against TiDB
  generate  — AI-generate test cases from feature descriptions
  analyze   — AI-analyze failures from a previous report
  report    — Convert JSON report to HTML
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="tidbtest",
        description="AI-assisted TiDB test framework",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    p_run = sub.add_parser("run", help="Run test suites")
    p_run.add_argument("--config", default="config.yaml", help="Config file path")
    p_run.add_argument("--suite", help="Run only this suite (name or file path)")
    p_run.add_argument("--test-dir", help="Override test directory")
    p_run.add_argument(
        "--ai-analyze", action="store_true",
        help="Enable AI failure analysis on failed cases",
    )

    # --- generate ---
    p_gen = sub.add_parser("generate", help="AI-generate test cases")
    p_gen.add_argument("--config", default="config.yaml", help="Config file path")
    p_gen.add_argument("--feature", required=True, help="Feature description")
    p_gen.add_argument("--schema", help="Path to schema DDL file")
    p_gen.add_argument(
        "--output",
        default="tests/cases/ai_generated/generated.yaml",
        help="Output YAML path",
    )

    # --- analyze ---
    p_ana = sub.add_parser("analyze", help="AI-analyze failures from a report")
    p_ana.add_argument("--config", default="config.yaml", help="Config file path")
    p_ana.add_argument("--report", required=True, help="JSON report file to analyze")
    p_ana.add_argument("--output", help="Write enriched report (default: overwrite input)")

    # --- report ---
    p_rep = sub.add_parser("report", help="Convert JSON report to HTML")
    p_rep.add_argument("--input", required=True, help="JSON report file")
    p_rep.add_argument("--output", help="HTML output path")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    config = _load_config(args.config if hasattr(args, "config") else "config.yaml")

    if args.command == "run":
        _cmd_run(args, config)
    elif args.command == "generate":
        _cmd_generate(args, config)
    elif args.command == "analyze":
        _cmd_analyze(args, config)
    elif args.command == "report":
        _cmd_report(args, config)


def _cmd_run(args, config: dict):
    from src.db.connector import load_connector_from_config
    from src.runner.loader import load_suite, load_suites_from_dir
    from src.runner.executor import TestExecutor
    from src.report.json_report import generate_json_report
    from src.report.html_report import generate_html_report

    connector = load_connector_from_config(config)

    analyzer = None
    if args.ai_analyze:
        from src.ai.analyzer import FailureAnalyzer
        analyzer = FailureAnalyzer(config.get("ai"), "unknown")

    executor = TestExecutor(connector, config, analyzer=analyzer)

    test_dir = args.test_dir or config.get("runner", {}).get("test_dir", "tests/cases")
    if args.suite:
        p = Path(args.suite)
        if p.exists() and p.suffix in (".yaml", ".yml"):
            suites = [load_suite(p)]
        else:
            all_suites = load_suites_from_dir(test_dir)
            suites = [s for s in all_suites if s.suite == args.suite]
            if not suites:
                print(f"Suite '{args.suite}' not found in {test_dir}")
                sys.exit(1)
    else:
        suites = load_suites_from_dir(test_dir)

    if not suites:
        print("No test suites found.")
        sys.exit(1)

    result = executor.run(suites, ai_analyze=args.ai_analyze)

    report_dir = Path(config.get("runner", {}).get("report_dir", "reports"))
    json_path = report_dir / f"{result.run_id}.json"
    generate_json_report(result, json_path)

    json_data = json.loads(json_path.read_text())
    html_path = report_dir / f"{result.run_id}.html"
    generate_html_report(json_data, html_path)

    (report_dir / "latest.json").write_text(json_path.read_text())

    summary = result.summary
    print(f"\n{'='*60}")
    print(f"  Test Run: {result.run_id}")
    print(f"  TiDB:    {result.tidb_version}")
    print(f"  Total:   {summary['total']}  |  "
          f"Pass: {summary['passed']}  |  "
          f"Fail: {summary['failed']}  |  "
          f"Skip: {summary['skipped']}")
    print(f"  Duration: {summary['duration_sec']:.2f}s")
    if summary["flaky_retried"]:
        print(f"  Retried: {summary['flaky_retried']} cases")
    print(f"  Report:  {json_path}  /  {html_path}")
    print(f"{'='*60}\n")

    if summary["failed"] > 0:
        sys.exit(1)


def _cmd_generate(args, config: dict):
    from src.ai.generator import generate_test_suite, save_generated_suite

    schema_ddl = ""
    if args.schema:
        schema_ddl = Path(args.schema).read_text(encoding="utf-8")

    ai_config = config.get("ai", {})
    result = generate_test_suite(
        feature=args.feature,
        schema_ddl=schema_ddl,
        ai_config=ai_config,
        max_rounds=ai_config.get("max_retries", 3),
    )

    if result.success:
        output_path = save_generated_suite(result.yaml_text, args.output)
        print(f"Generated {len(result.suite.cases)} test cases -> {output_path}")
        if result.quality_warnings:
            print(f"  Warnings ({len(result.quality_warnings)}):")
            for w in result.quality_warnings:
                print(f"    - {w}")
    else:
        print(f"Generation failed after {result.rounds} rounds.")
        print(f"  Errors:")
        for e in result.quality_errors:
            print(f"    - {e}")
        sys.exit(1)


def _cmd_analyze(args, config: dict):
    from src.ai.analyzer import analyze_report_failures

    report_path = Path(args.report)
    report_data = json.loads(report_path.read_text(encoding="utf-8"))

    enriched = analyze_report_failures(report_data, config.get("ai"))

    output_path = Path(args.output) if args.output else report_path
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    failed_cases = [
        c for s in enriched.get("suites", [])
        for c in s.get("cases", [])
        if c.get("status") == "failed"
    ]
    print(f"Analyzed {len(failed_cases)} failed cases. Output: {output_path}")
    for c in failed_cases:
        ai = c.get("ai_analysis", {})
        conf = ai.get("confidence", 0)
        marker = " (low confidence)" if isinstance(conf, (int, float)) and conf < 0.6 else ""
        print(f"  [{c['id']}] {ai.get('category', '?')}{marker}: {ai.get('root_cause', '?')[:100]}")


def _cmd_report(args, config: dict):
    from src.report.html_report import generate_html_report

    input_path = Path(args.input)
    report_data = json.loads(input_path.read_text(encoding="utf-8"))

    output = args.output or str(input_path.with_suffix(".html"))
    generate_html_report(report_data, output)
    print(f"HTML report generated: {output}")


def _load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        logging.warning("Config file '%s' not found, using defaults", path)
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


if __name__ == "__main__":
    main()
