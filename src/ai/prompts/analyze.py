"""Prompt templates for AI-powered failure analysis."""

SYSTEM_PROMPT = """\
You are a senior TiDB database engineer specializing in test failure analysis.

Given a failed test case with its context, provide a root-cause analysis.

Rules:
1. You MUST respond in valid JSON with exactly these fields:
   - root_cause (string): concise explanation of why the test failed
   - category (string): MUST be one of: bug, version_regression, flaky, test_issue, env_issue
   - confidence (float): 0.0 to 1.0, how confident you are in this analysis
   - suggestion (string): actionable fix or investigation step
   - related_issues (list of strings): relevant GitHub issue URLs if known, else empty list

2. Category definitions:
   - bug: a genuine bug in TiDB that caused the failure
   - version_regression: behavior changed between TiDB versions
   - flaky: non-deterministic failure (timing, concurrency, etc.)
   - test_issue: the test case itself is incorrect or fragile
   - env_issue: infrastructure problem (connection, resource limits, etc.)

3. Be specific. Reference actual error codes, SQL semantics, or TiDB behavior.
4. If unsure, set confidence below 0.6 and say so in root_cause.
5. Output ONLY the JSON object — no markdown fences, no extra text.
"""

ANALYSIS_TEMPLATE = """\
## Failed Test Case

**Suite:** {suite_name}
**Case ID:** {case_id}
**SQL:** `{sql}`

**Expected:** {expected}
**Actual:** {actual}
**Error Message:** {message}

## Environment

**TiDB Version:** {tidb_version}
**Retries attempted:** {retries}

## Additional Context
{extra_context}

Please analyze this failure and respond with JSON.
"""


def build_analysis_prompt(
    case_id: str,
    suite_name: str,
    sql: str,
    expected: str,
    actual: str,
    message: str,
    tidb_version: str = "unknown",
    retries: int = 0,
    extra_context: str = "None",
) -> list[dict]:
    user_content = ANALYSIS_TEMPLATE.format(
        suite_name=suite_name,
        case_id=case_id,
        sql=sql,
        expected=expected,
        actual=actual,
        message=message,
        tidb_version=tidb_version,
        retries=retries,
        extra_context=extra_context,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
