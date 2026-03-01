"""Prompt templates for AI-powered test case generation."""

SYSTEM_PROMPT = """\
You are a senior TiDB/MySQL database test engineer. Your job is to generate \
comprehensive SQL test cases for a given feature or schema.

Rules:
1. Cover normal paths, error paths, and boundary values.
2. Each test case must be independent — do NOT rely on execution order.
3. For SELECT queries, always include ORDER BY to ensure deterministic results.
4. Output ONLY valid YAML following the exact schema below — no markdown fences, \
   no explanation text outside the YAML.
5. SQL must be compatible with TiDB (MySQL-compatible syntax).
6. Use realistic but simple test data.
7. For floating-point results, note that TiDB may return different precision \
   than MySQL — use the tolerance field when appropriate.
8. Include at least one error/edge-case test (e.g., NULL handling, duplicate key, \
   type overflow).

Output YAML schema:
```
suite: <snake_case_name>
description: "<description>"
tags: [<tag1>, <tag2>]
setup:
  - "<SQL to create tables and seed data>"
cases:
  - id: <snake_case_id>
    description: "<what this tests>"
    sql: "<SQL statement>"
    expect:
      type: rows | count | error | affected_rows | regex
      value: <expected value>
    tolerance: <optional float, for numeric comparison>
    ignore_order: <optional bool>
teardown:
  - "<cleanup SQL>"
```

Expect type semantics:
- rows: value is a list of lists, each inner list is a row
- count: value is an integer (expected row count)
- error: value is {code: <mysql_errno>, message_contains: "<substring>"}
- affected_rows: value is an integer
- regex: value is a regex pattern to match against the result
"""

FEW_SHOT_EXAMPLE = """\
suite: cte_basic
description: "Common Table Expression 基础功能测试"
tags: [cte, sql]
setup:
  - "CREATE TABLE departments (id INT PRIMARY KEY, name VARCHAR(64), parent_id INT)"
  - >-
    INSERT INTO departments VALUES
    (1, 'CEO', NULL), (2, 'Engineering', 1), (3, 'Backend', 2),
    (4, 'Frontend', 2), (5, 'Sales', 1)
cases:
  - id: simple_cte
    description: "简单 CTE 查询"
    sql: >-
      WITH eng AS (SELECT * FROM departments WHERE parent_id = 2)
      SELECT id, name FROM eng ORDER BY id
    expect:
      type: rows
      value:
        - [3, "Backend"]
        - [4, "Frontend"]

  - id: recursive_cte
    description: "递归 CTE - 查找所有下级部门"
    sql: >-
      WITH RECURSIVE sub AS (
        SELECT id, name, parent_id FROM departments WHERE id = 1
        UNION ALL
        SELECT d.id, d.name, d.parent_id
        FROM departments d INNER JOIN sub s ON d.parent_id = s.id
      )
      SELECT id, name FROM sub ORDER BY id
    expect:
      type: rows
      value:
        - [1, "CEO"]
        - [2, "Engineering"]
        - [3, "Backend"]
        - [4, "Frontend"]
        - [5, "Sales"]

  - id: cte_not_exist_table
    description: "CTE 中引用不存在的表"
    sql: "WITH t AS (SELECT * FROM no_such_table) SELECT * FROM t"
    expect:
      type: error
      value:
        code: 1146
        message_contains: "doesn't exist"
teardown:
  - "DROP TABLE IF EXISTS departments"
"""


def build_generate_prompt(
    feature_description: str,
    schema_ddl: str = "",
    tidb_version: str = "",
) -> list[dict]:
    user_content = f"Feature description:\n{feature_description}\n"
    if schema_ddl:
        user_content += f"\nCurrent schema (DDL):\n{schema_ddl}\n"
    if tidb_version:
        user_content += f"\nTarget TiDB version: {tidb_version}\n"
    user_content += "\nPlease generate a complete test suite in YAML format."

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Here is a reference example:\n\n{FEW_SHOT_EXAMPLE}"},
        {"role": "assistant", "content": "Understood. I'll follow this format exactly."},
        {"role": "user", "content": user_content},
    ]


def build_fix_prompt(
    original_yaml: str,
    errors: list[str],
) -> list[dict]:
    """Build a prompt asking the LLM to fix issues in generated YAML."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"The following generated test YAML has issues:\n\n{original_yaml}\n\n"
                f"Issues found:\n" + "\n".join(f"- {e}" for e in errors) + "\n\n"
                "Please fix these issues and output the corrected YAML only."
            ),
        },
    ]
