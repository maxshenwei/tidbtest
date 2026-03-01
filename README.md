# tidbtest — AI-Assisted TiDB Test Framework

A minimal viable test framework for TiDB that combines reliable SQL test execution with AI-powered test generation and failure analysis.

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (optional, for quick TiDB setup)
- (Optional) OpenAI API key for AI features

### Start TiDB (Optional)

No existing TiDB or MySQL instance for testing? Use the included `docker-compose.yaml` to start one:

```bash
docker compose up -d
```

This launches a TiDB single-node container on `127.0.0.1:4000`, matching the default `config.yaml`. No PD or TiKV required.

### Install

```bash
pip install -r requirements.txt
```

### Configure

Edit `config.yaml` to configure your targeting testing entry point:

```yaml
database:
  host: "127.0.0.1"
  port: 4000
  user: "root"
  password: ""

ai: # for ai feature
  api_key: "${OPENAI_API_KEY}"    # or hardcode your key
  model: "gpt-4"
```

### Run Tests

```bash
# Run all test suites (YAML + .test files)
python -m src.cli run

# Run a specific suite by name
python -m src.cli run --suite basic_crud

# Run only the real TiDB test cases (.test format)
python -m src.cli run --test-dir tests/cases/real_tidb_case_suites

# Run with AI failure analysis
python -m src.cli run --ai-analyze

# Verbose mode
python -m src.cli -v run
```

### View Reports

After each test run, reports are generated automatically in the `reports/` directory:

```
reports/
├── 20260301-011649.json   # Machine-readable (for AI analysis)
├── 20260301-011649.html   # Human-readable (open in browser)
└── latest.json            # Symlink to the most recent run
```

Open the HTML report in your browser to see:
- Pass rate progress bar and summary cards
- Quick-jump navigation to failed cases
- Expected vs Actual diff view for failures
- AI analysis panels (if `--ai-analyze` was used)
- Filter buttons (All / Passed / Failed / Skipped)
- Collapsible test suites

```bash
# macOS
open reports/20260301-011649.html

# Or manually convert an existing JSON report to HTML
python -m src.cli report --input reports/latest.json --output reports/latest.html
```

### AI: Generate Test Cases

```bash
# From a feature description
python -m src.cli generate \
  --feature "TiDB supports Common Table Expression (CTE) with recursive queries" \
  --schema schema.sql \
  --output tests/cases/ai_generated/cte.yaml

# Schema-only (AI infers what to test)
python -m src.cli generate \
  --feature "CRUD operations for user management" \
  --output tests/cases/ai_generated/user_crud.yaml
```

### AI: Analyze Failures

```bash
# Analyze all failures in a report
python -m src.cli analyze --report reports/latest.json

# Write to a separate file
python -m src.cli analyze --report reports/latest.json --output reports/analyzed.json
```

## Test Case Formats

### YAML Format

框架原生格式，支持丰富的断言类型和反 flaky 选项：

```yaml
suite: my_feature
description: "Feature X tests"
tags: [feature-x]

setup:
  - "CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(64))"
  - "INSERT INTO t VALUES (1, 'alice')"

cases:
  - id: basic_select
    description: "Simple query"
    sql: "SELECT * FROM t ORDER BY id"
    expect:
      type: rows
      value:
        - [1, "alice"]

  - id: duplicate_key
    description: "PK violation"
    sql: "INSERT INTO t VALUES (1, 'bob')"
    expect:
      type: error
      value:
        code: 1062
        message_contains: "Duplicate"

teardown:
  - "DROP TABLE IF EXISTS t"
```

**Expect types:** `rows`, `count`, `error`, `affected_rows`, `regex`

**Anti-flaky options:** `tolerance` (float comparison), `ignore_order` (set comparison), `min_version` (version gating)

### MySQL Test Framework Format (.test / .result)

兼容 TiDB / mysql-tester 原有的 `.test` 测试用例，可以直接将 [pingcap/tidb](https://github.com/pingcap/tidb) 仓库中的测试文件放入 `tests/cases/` 目录运行，无需转换。

```sql
# select_basic.test
drop table if exists t;
create table t (c1 int, c2 int, c3 int);
insert into t values (1, 2, 3);
select * from t;
--error 1054
select non_exist_col from t;
drop table t;
```

配套的 `.result` 文件提供预期输出（与 `.test` 同名、同目录）：

```
drop table if exists t;
create table t (c1 int, c2 int, c3 int);
insert into t values (1, 2, 3);
select * from t;
c1	c2	c3
1	2	3
select non_exist_col from t;
Error 1054 (42S22): Unknown column 'non_exist_col' in 'field list'
drop table t;
```

支持的指令：`--error`（期望错误码）、`--sorted_result`（忽略行序）。项目中已包含从 TiDB 仓库提取的真实测试用例，位于 `tests/cases/real_tidb_case_suites/`。

## Project Structure

```
src/
├── cli.py              # CLI entry point
├── models.py           # Data models
├── db/connector.py     # TiDB/MySQL connector
├── runner/
│   ├── loader.py       # YAML test case loader
│   ├── executor.py     # Test execution engine
│   ├── validator.py    # Result validation
│   ├── retrier.py      # Retry policy (infra-only)
│   └── isolator.py     # Database-level isolation
├── report/
│   ├── json_report.py  # JSON report generator
│   └── html_report.py  # HTML report generator
└── ai/
    ├── client.py       # LLM API client abstraction
    ├── generator.py    # AI test case generation
    ├── analyzer.py     # AI failure analysis
    ├── quality_gate.py # Generated code validation
    └── prompts/        # Prompt templates
```

## Architecture

See [DESIGN.md](DESIGN.md) for the full design document.
