# AI-Assisted 数据库测试框架 MVP 设计文档

## 1. 项目概述

### 1.1 目标

构建一个面向 TiDB 的最小可行测试框架，具备以下核心能力：

- **基础能力**：读取、执行 SQL 测试用例，校验结果，支持隔离与重试，输出报告
- **AI 能力**：引入 LLM 实现测试用例自动生成 + 失败原因智能分析（双方向）
- **可扩展**：架构上为未来的 Agent 化测试平台留出演进空间

### 1.2 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | 生态成熟，AI 集成便利 |
| 数据库驱动 | mysql-connector-python | TiDB 兼容 MySQL 协议 |
| AI 后端 | OpenAI API (GPT-4) | 能力强，支持 function calling |
| 测试运行器 | 自研（非 pytest） | 需要精细控制隔离、重试、报告 |
| 配置格式 | YAML | 可读性好，适合测试用例定义 |
| 报告 | JSON + HTML | 机器可读 + 人类可读 |

### 1.3 非目标（MVP 阶段不做）

- 分布式测试执行
- CI/CD 深度集成
- Web UI 控制台
- 多数据库方言支持（仅 TiDB/MySQL）

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Entry                            │
│                  (tidbtest run / generate)                   │
└──────────────┬──────────────────────┬───────────────────────┘
               │                      │
       ┌───────▼───────┐     ┌────────▼────────┐
       │  Test Runner   │     │  AI Engine      │
       │  ─────────     │     │  ──────────     │
       │  • Loader      │     │  • Generator    │
       │  • Executor    │     │  • Analyzer     │
       │  • Validator   │◄───►│  • Prompt Mgr   │
       │  • Retrier     │     │  • Quality Gate  │
       │  • Reporter    │     │                  │
       └───────┬───────┘     └────────┬─────────┘
               │                      │
       ┌───────▼──────────────────────▼─────────┐
       │          Database Connector             │
       │  • Per-suite Connection                 │
       │  • Database-level Isolation             │
       │  • Version Detection                    │
       └────────────────────────────────────────┘
               │
       ┌───────▼───────────────────────────────┐
       │       TiDB (Docker / Remote)           │
       │  • 单实例模式（MVP）                    │
       │  • 多版本通过切换容器镜像实现            │
       └───────────────────────────────────────┘
```

---

## 3. 核心模块设计

### 3.1 测试用例格式

#### 3.1.1 YAML 格式（框架原生）

采用 YAML 定义测试用例，兼顾人类可读与机器解析：

```yaml
# tests/cases/basic_crud.yaml
suite: basic_crud
description: "基础 CRUD 操作测试"
tags: [crud, smoke]
setup:
  - "CREATE TABLE IF NOT EXISTS t_test (id INT PRIMARY KEY, name VARCHAR(64), score DECIMAL(10,2))"
  - "INSERT INTO t_test VALUES (1, 'alice', 95.5), (2, 'bob', 87.0)"

cases:
  - id: select_all
    description: "全表查询"
    sql: "SELECT * FROM t_test ORDER BY id"
    expect:
      type: rows        # rows | count | error | affected_rows | regex
      value:
        - [1, "alice", "95.50"]
        - [2, "bob", "87.00"]

  - id: insert_duplicate
    description: "主键冲突"
    sql: "INSERT INTO t_test VALUES (1, 'charlie', 60.0)"
    expect:
      type: error
      value:
        code: 1062      # Duplicate entry
        message_contains: "Duplicate"

  - id: update_score
    description: "更新操作"
    sql: "UPDATE t_test SET score = 99.0 WHERE id = 1"
    expect:
      type: affected_rows
      value: 1

  - id: complex_query
    description: "聚合查询"
    sql: "SELECT COUNT(*), AVG(score) FROM t_test"
    expect:
      type: rows
      value:
        - [2, "91.2500"]   # AVG 精度
      tolerance: 0.01       # 浮点容差

teardown:
  - "DROP TABLE IF EXISTS t_test"
```

**设计要点**：
- `expect.type` 支持多种验证模式，覆盖常见断言场景
- `tolerance` 支持浮点数比较容差，减少 flaky test
- `setup/teardown` 在 suite 级别执行，保证测试前后状态干净

#### 3.1.2 MySQL Test Framework 格式（.test / .result）

框架同时兼容 TiDB / mysql-tester 的原生测试格式，可以直接加载 [pingcap/tidb](https://github.com/pingcap/tidb) 仓库中的 `.test` 用例，无需转换。

```sql
# tests/cases/real_tidb_case_suites/select_basic.test
drop table if exists t;
create table t (c1 int, c2 int, c3 int);
insert into t values (1, 2, 3);
select * from t;
--error 1054
select non_exist_col from t;
drop table t;
```

配套的 `.result` 文件（同名、同目录）提供预期输出：

```
select * from t;
c1	c2	c3
1	2	3
select non_exist_col from t;
Error 1054 (42S22): Unknown column 'non_exist_col' in 'field list'
```

**加载器设计**（`test_file_loader.py`）：

| 特性 | 实现方式 |
|------|---------|
| SQL 解析 | 按 `;` 结尾切分，支持多行 SQL |
| `--error` 指令 | 转为 `expect.type = "error"` |
| `--sorted_result` | 转为 `ignore_order = True` |
| 预期输出匹配 | 顺序匹配 `.result` 文件，同一 SQL 多次出现时按出现顺序依次消费 |
| Warning 过滤 | 自动跳过 `Level/Code/Message` 格式的 MySQL Warning 输出 |
| 无 `.result` 文件 | 降级为 smoke test 模式（仅验证 SQL 不报错） |

**统一调度**：`loader.py` 根据文件后缀自动分发，`.yaml` / `.yml` 走 YAML 加载器，`.test` 走 `.test` 加载器，两种格式可混合放在同一目录中。

项目中已包含从 TiDB 仓库提取的真实用例，位于 `tests/cases/real_tidb_case_suites/`：

| 文件 | 内容 | Cases |
|------|------|-------|
| `select_basic.test` / `.result` | SELECT 基础查询、类型转换、变量 | 64 |
| `cte_basic.test` / `.result` | CTE 递归查询、嵌套、异常 | 30 |
| `expression_issues.test` / `.result` | 各类表达式边界 case、类型转换 bug | 85 |

### 3.2 测试执行引擎

#### 3.2.1 执行流程

```
Load YAML/.test ──► Validate/Parse ──► Create Isolated Database
                                          │
                                   Execute Setup SQLs
                                          │
                              ┌───────────▼───────────┐
                              │  For each test case:   │
                              │  ┌──────────────────┐  │
                              │  │ Execute SQL       │  │
                              │  │ Capture Result    │  │
                              │  │ Validate Expect   │──┼──► Pass
                              │  │ If fail:          │  │
                              │  │   retry? ─► retry │  │
                              │  │   else ─► record  │──┼──► Fail (+ trigger AI analysis)
                              │  └──────────────────┘  │
                              └───────────┬───────────┘
                                   Execute Teardown SQLs
                                          │
                                   Close Session
                                          │
                                   Generate Report
```

#### 3.2.2 测试隔离策略

MVP 阶段采用 **数据库级隔离**：

```python
# 每个 test suite 创建独立数据库
db_name = f"tidbtest_{suite_name}_{uuid4().hex[:8]}"
conn.execute(f"CREATE DATABASE {db_name}")
conn.execute(f"USE {db_name}")
# ... 执行测试 ...
conn.execute(f"DROP DATABASE {db_name}")
```

**为什么不用事务隔离（BEGIN/ROLLBACK）？**
- DDL 在 TiDB 中会隐式提交，事务隔离无法覆盖 DDL 测试
- 某些测试需要验证事务行为本身
- 数据库级隔离更彻底，代价是稍慢（可接受）

#### 3.2.3 失败重试机制

```python
class RetryPolicy:
    max_retries: int = 2
    retry_on: list[str] = ["connection_lost", "lock_timeout"]
    backoff_base: float = 1.0  # 指数退避基数（秒）

    # 不重试的情况：
    # - 断言失败（逻辑错误，重试无意义）
    # - 语法错误
    # 仅重试基础设施层面的瞬时故障
```

**防 flaky 关键设计**：
- 区分 **infra flaky**（网络抖动、锁超时）和 **logic flaky**（时间依赖、并发竞争）
- Infra flaky → 自动重试
- Logic flaky → 不重试，标记为 flaky 待人工审查
- 所有重试行为记录到报告中，可追溯

### 3.3 结果校验器

支持 7 种断言类型，覆盖 YAML 和 `.test` 两种格式的需求：

```python
dispatch = {
    "rows":          self._validate_rows,          # 精确行比对
    "count":         self._validate_count,          # 行数断言
    "error":         self._validate_error,          # 期望错误码/消息
    "affected_rows": self._validate_affected_rows,  # DML 影响行数
    "regex":         self._validate_regex,          # 正则匹配
    "no_error":      self._validate_no_error,       # 仅验证不报错（.test smoke 模式）
    "result_text":   self._validate_result_text,    # 原始文本比对（.test/.result 模式）
}
```

**`result_text` 校验的归一化处理**：

Python 数据库驱动返回的值与 MySQL 原生文本格式有差异，校验器通过 `_format_cell` 和 `_normalize_text` 进行归一化：

| 差异 | Python 驱动返回 | MySQL 原生 | 归一化策略 |
|------|----------------|-----------|-----------|
| NULL | `None` | `NULL` | `_format_cell`: None → "NULL" |
| 整数浮点 | `246.0` | `246` | `_format_cell`: 整数值的 float → int 表示 |
| 科学计数法 | `1.23e+19` | `1.23e19` | 正则去除 `e` 后的 `+` |
| SET 类型 | `{'a'}` | `a` | `_format_cell`: set → 逗号分隔排序字符串 |
| JSON 内浮点 | `{"v": 0}` | `{"v": 0.0}` | `_normalize_text`: JSON 内 `X.0` → `X` |

**浮点数比较**使用容差模式避免精度 flaky：

```python
def _compare_value(actual, expected, tolerance=None):
    if tolerance and _is_numeric(actual) and _is_numeric(expected):
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal(str(tolerance))
    return str(actual) == str(expected)
```

### 3.4 测试报告

输出双格式报告（Schema v2.0）：

**JSON 格式**（AI / 机器消费）：

```json
{
  "schema_version": "2.0",
  "run_id": "20260301-011649",
  "timestamp": "2026-03-01T01:16:49.123456+00:00",
  "tidb_version": "8.0.11-TiDB-v8.1.0",
  "environment": {
    "os": "Darwin 24.6.0",
    "python": "3.14.2",
    "framework": "tidbtest-mvp"
  },
  "summary": {
    "total": 194, "passed": 194, "failed": 0, "skipped": 0,
    "duration_sec": 0.84,
    "flaky_retried": 0,
    "pass_rate": 100.0
  },
  "failure_summary": [
    {
      "suite": "cte_basic", "case_id": "line_48",
      "sql": "WITH RECURSIVE ...",
      "error": { "expected": "...", "actual": "...", "message": "Result text mismatch" },
      "ai_analysis": { "root_cause": "...", "category": "version_regression", "confidence": 0.52 }
    }
  ],
  "suites": [ ... ]
}
```

v2 设计要点：
- `failure_summary` 顶层数组 —— AI 可直接获取所有失败上下文，无需遍历所有 suite/case
- `environment` —— 为 AI 根因分析提供 OS / Python / 框架版本信息
- `pass_rate` —— 直接给出通过率百分比
- `timestamp` —— ISO 8601 UTC，便于关联 CI/CD 时间线

**HTML 格式**（人类消费）：

| 功能 | 说明 |
|------|------|
| 通过率进度条 | 页面顶部红绿比例条，一眼看到健康度 |
| Summary 卡片 | Total / Passed / Failed / Skipped 四色卡片 |
| Quick Jump 导航 | 列出所有失败 case 的锚点链接，点击直达 |
| 状态筛选按钮 | All / Passed / Failed / Skipped 一键过滤 |
| 可折叠 Suite | 点击 suite header 收起/展开，减少信息过载 |
| Diff 视图 | Expected（绿色）vs Actual（红色）分区显示 |
| AI 分析面板 | 蓝色面板展示 root cause / category / suggestion |
| 置信度标签 | HIGH / MED / LOW 彩色标签标注 AI 分析可信度 |
| 失败行高亮 | failed 行红色背景，skipped 行半透明 |

---

## 4. AI 能力设计

### 4.1 方向一：测试用例自动生成

#### 4.1.1 工作流

```
用户输入 Feature 描述          Schema 信息（DDL）
     │                              │
     └──────────┬───────────────────┘
                │
        ┌───────▼───────┐
        │  Prompt 组装   │  ← 包含：feature 描述 + schema + 测试模板 + 约束规则
        └───────┬───────┘
                │
        ┌───────▼───────┐
        │  LLM 生成      │  ← GPT-4 / Claude
        └───────┬───────┘
                │
        ┌───────▼───────┐
        │  Quality Gate  │  ← 语法检查 + Schema 一致性 + 试运行
        └───────┬───────┘
                │
         Pass ──┼── Fail
          │          │
     写入 YAML    反馈给 LLM 重新生成（最多 3 轮）
```

#### 4.1.2 Prompt 设计策略

采用 **结构化 Prompt + Few-shot + 约束注入** 模式：

```
System: 你是一个 TiDB 数据库测试专家。根据给定的 feature 描述和数据库 schema，
生成全面的 SQL 测试用例。

约束：
1. 必须覆盖正常路径和异常路径
2. 必须包含边界值测试
3. 使用给定的 YAML 格式输出
4. SQL 必须兼容 TiDB {version}
5. 不要生成依赖执行顺序的用例

以下是一个参考示例：
{few_shot_example}

---
Feature 描述：{feature_description}
当前 Schema：{schema_ddl}

请生成测试用例：
```

#### 4.1.3 Quality Gate（质量关卡）

这是防止 AI "幻觉"进入测试集的核心环节。LLM 输出必须逐层通过 5 级检查：

```
LLM 生成 YAML
     │
     ▼
 ① YAML 格式 ─── 失败 → error，终止，反馈 LLM 修复
     │
     ▼
 ② 结构校验  ─── 失败 → error，终止，反馈 LLM 修复
     │
     ▼
 ③ SQL 语法  ─── 失败 → error（累积）
     │
     ▼
 ④ Schema 一致性 ─ 问题 → warning（不阻断）
     │
     ▼
 ⑤ 最佳实践  ─── 问题 → warning（不阻断）
     │
     ▼
 有 error → 整体失败，把错误列表反馈 LLM 要求修复（最多 3 轮）
 只有 warning → 通过，提示用户注意
 全部清洁 → 通过，保存 YAML 文件
```

| 层级 | 检查项 | 类型 | 实现 |
|------|--------|------|------|
| ① YAML 格式 | `yaml.safe_load` 解析成功，根节点为 dict | error | 拦截 LLM 输出的 markdown 围栏、缩进错误等 |
| ② 结构校验 | 必须有 `suite`、`cases` 列表；每个 case 有 `id`、`sql`、`expect`；`expect.type` 合法 | error | 通过 `loader._parse_case` 尝试构建 `TestSuite` |
| ③ SQL 语法 | 所有 SQL（setup + cases + teardown）经 `sqlparse` 解析 | error | 拦截 LLM 生成的伪代码或自然语言混入 |
| ④ Schema 一致性 | case SQL 中引用的表名需在 DDL 或 setup 中存在 | warning | 正则提取 `FROM/JOIN/INTO/UPDATE` 后的表名做交叉检查 |
| ⑤ 最佳实践 | SELECT + rows 断言但无 `ORDER BY` 且 `ignore_order=false` | warning | 典型 flaky 来源，提醒而不阻断 |

**设计决策**：前 3 层产生 **error**（阻断），后 2 层产生 **warning**（提醒）。Schema 一致性和最佳实践属于建议级别，不应因此拒绝整个 suite。

#### 4.1.4 生成模式

通过 CLI 的 `generate` 子命令触发，输入 feature 描述 + 可选的 schema DDL：

```bash
python -m src.cli generate \
  --feature "TiDB 支持 Common Table Expression (CTE) 递归查询" \
  --schema ./schema.sql \
  --output tests/cases/ai_generated/cte.yaml
```

### 4.2 方向二：失败原因智能分析

#### 4.2.1 触发时机

测试用例失败且重试后仍失败时，自动触发 AI 分析。

#### 4.2.2 分析 Context 组装

每个失败 case 发送给 LLM 的上下文包含：

```
## Failed Test Case
Suite: {suite_name}
Case ID: {case_id}
SQL: {sql}

Expected: {expected}
Actual: {actual}
Error Message: {message}

## Environment
TiDB Version: {tidb_version}
Retries attempted: {retries}

## Additional Context
{extra_context}
```

上下文中的 retries 数量帮助 AI 区分 infra 问题（有重试）和逻辑问题（无重试），TiDB 版本帮助 AI 判断是否为版本回归。

#### 4.2.3 分析输出

```json
{
  "root_cause": "v8.1.0 引入了新的悲观锁实现，在 Region Split 期间锁等待超时阈值行为发生变化",
  "category": "version_regression",       // bug | version_regression | flaky | test_issue | env_issue
  "confidence": 0.82,
  "suggestion": "建议检查 tikv_pessimistic_txn_lock_wait_timeout 配置，或在 setup 中增加 SET innodb_lock_wait_timeout = 30",
  "related_issues": ["https://github.com/pingcap/tidb/issues/xxxxx"]
}
```

#### 4.2.4 分析质量控制

- **confidence 阈值**：低于 0.6 的分析标记为 "仅供参考"，不作为主结论
- **分类约束**：要求 LLM 必须从预定义 category 中选择，避免自由发挥
- **人类反馈回路**：分析结果附带 "有用/无用" 标记，用于后续 prompt 优化

### 4.3 AI 角色定位

```
┌──────────────────────────────────────────────────────┐
│                  人类工程师                            │
│  • 定义 feature 需求                                  │
│  • 审核 AI 生成的用例                                  │
│  • 判断 AI 分析是否准确                                │
│  • 维护高价值手写用例                                  │
└──────────────────┬───────────────────────────────────┘
                   │ 指导 + 审核
┌──────────────────▼───────────────────────────────────┐
│                  AI 助手                              │
│  • 批量生成初始测试用例（提效 10x）                     │
│  • 快速定位失败根因（缩短排查时间）                     │
│  • 不替代人类判断，只提供辅助                          │
└──────────────────────────────────────────────────────┘
```

**核心原则：AI 是加速器，不是决策者。** 所有 AI 输出都经过 Quality Gate 或人工审核才能进入正式测试集。

---

## 5. 多版本回归设计

### 5.1 已实现：版本感知

**版本自动探测**：每次运行自动检测 TiDB 版本，记录在报告的 `tidb_version` 字段中。

**Case 级版本门控**：测试用例可声明最低版本要求，低版本自动 skip：

```yaml
cases:
  - id: cte_recursive
    min_version: "v7.1.0"    # 低于此版本自动 skip，不会误报 fail
    sql: "WITH RECURSIVE cte AS (...) SELECT * FROM cte"
    expect:
      type: rows
      value: [...]
```

**版本切换**：通过修改 `docker-compose.yaml` 的镜像版本即可切换目标版本：

```yaml
services:
  tidb:
    image: pingcap/tidb:v7.5.0   # 改为需要测试的版本
```

### 5.2 规划中：多版本同时执行与对比（Phase 3）

```yaml
# 未来 config.yaml 扩展
targets:
  - name: stable
    version: v7.5.0
  - name: latest
    version: v8.1.0
```

```
┌──────────────┬─────────┬─────────┐
│ Test Case    │ v7.5.0  │ v8.1.0  │
├──────────────┼─────────┼─────────┤
│ select_all   │ ✅ 12ms │ ✅ 10ms │
│ cte_query    │ ⏭ skip  │ ✅ 45ms │
│ json_extract │ ✅ 18ms │ ❌ err  │  ← 回归！
└──────────────┴─────────┴─────────┘
```

当某个用例在新版本失败但旧版本通过时，自动标记为 **version regression** 并触发 AI 分析。

---

## 6. 减少 Flaky Test 策略

| 策略 | 实现方式 | 状态 |
|------|---------|------|
| 结果容差 | 浮点数比较支持 `tolerance`（Decimal 精度） | ✅ 已实现 |
| 确定性排序 | `SELECT` 建议加 `ORDER BY`，验证时可配置 `ignore_order: true` | ✅ 已实现 |
| 隔离执行 | 独立 database per suite，避免数据污染 | ✅ 已实现 |
| 重试分类 | 仅重试 infra 瞬时错误（连接断开、锁超时、死锁），不重试逻辑错误 | ✅ 已实现 |
| 版本门控 | `min_version` 避免在不支持的版本上误报 | ✅ 已实现 |
| 格式归一化 | `_format_cell` / `_normalize_text` 统一 NULL、浮点、SET/JSON 表示差异 | ✅ 已实现 |
| AI 辅助检测 | Quality Gate 第 ⑤ 层检查 SELECT + rows 无 ORDER BY 的 flaky 模式 | ✅ 已实现 |
| 等待而非 sleep | 对 DDL 异步操作使用 polling 等待 | ⬜ 规划中 |
| Flaky 标记 | 连续 N 次结果不一致的用例自动标记为 flaky，进入观察队列 | ⬜ 规划中 |

---

## 7. 项目结构

```
tidbtest/
├── config.yaml                  # 全局配置（数据库连接、AI 配置、运行参数）
├── docker-compose.yaml          # TiDB 单节点容器（一键启动测试环境）
├── tests/
│   └── cases/                   # 测试用例目录（YAML + .test 混合）
│       ├── basic_crud.yaml
│       ├── transaction.yaml
│       ├── ai_generated/        # AI 生成的用例（隔离存放，待审核）
│       │   └── generated.yaml
│       └── real_tidb_case_suites/   # 从 TiDB 仓库提取的真实 .test 用例
│           ├── select_basic.test / .result
│           ├── cte_basic.test / .result
│           └── expression_issues.test / .result
├── src/
│   ├── __init__.py
│   ├── cli.py                   # CLI 入口（run / generate / analyze / report）
│   ├── models.py                # 核心数据模型（TestCase, TestSuite, Results...）
│   ├── runner/
│   │   ├── __init__.py
│   │   ├── loader.py            # YAML 用例加载 + 格式分发
│   │   ├── test_file_loader.py  # .test/.result 格式加载器
│   │   ├── executor.py          # 测试执行引擎
│   │   ├── validator.py         # 结果校验器（7 种断言 + 归一化）
│   │   ├── retrier.py           # 重试策略（infra-only）
│   │   └── isolator.py          # 数据库级测试隔离
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── client.py            # LLM API 客户端抽象（OpenAI / Mock）
│   │   ├── generator.py         # AI 测试用例生成
│   │   ├── analyzer.py          # AI 失败原因分析
│   │   ├── quality_gate.py      # AI 输出 5 层质量关卡
│   │   └── prompts/             # Prompt 模板
│   │       ├── generate.py
│   │       └── analyze.py
│   ├── report/
│   │   ├── __init__.py
│   │   ├── json_report.py       # JSON 报告（v2 schema）
│   │   └── html_report.py       # HTML 交互式报告
│   └── db/
│       ├── __init__.py
│       └── connector.py         # 数据库连接管理
├── reports/                     # 测试报告输出目录
├── requirements.txt
├── README.md
└── DESIGN.md
```

---

## 8. CLI 使用设计

```bash
# 运行所有测试（-v 为全局 verbose 参数，放在子命令前）
python -m src.cli -v run

# 运行特定 suite
python -m src.cli run --suite basic_crud

# 运行时开启 AI 失败分析
python -m src.cli run --ai-analyze

# AI 生成测试用例
python -m src.cli generate \
  --feature "TiDB 新增 FLASHBACK TABLE 功能" \
  --schema ./schema.sql \
  --output tests/cases/ai_generated/flashback.yaml

# 对已有报告批量 AI 分析
python -m src.cli analyze --report reports/latest.json

# JSON 报告转 HTML
python -m src.cli report --input reports/latest.json --output reports/latest.html
```

---

## 9. Agentic 测试平台重构思路

### 9.1 从工具到 Agent 的演进路径

```
Phase 1 (MVP - 当前)          Phase 2 (工具链)           Phase 3 (Agent)
───────────────────          ──────────────────         ─────────────────
• 人工触发 AI 生成            • PR 触发自动生成          • Agent 自主决定测试策略
• 人工审核用例                • 自动化质量门禁           • Agent 判断何时需要新用例
• 人工触发分析                • 失败自动分析             • Agent 自动修复 flaky test
                             • 报告自动推送             • Agent 跨版本智能对比
```

### 9.2 Agent 化架构愿景

```
┌─────────────────────────────────────────────────────────┐
│                    Test Orchestrator Agent               │
│  • 接收 PR/commit 事件                                   │
│  • 分析代码变更 → 决定测试范围                             │
│  • 调度子 Agent                                          │
└──────────┬──────────┬──────────┬────────────────────────┘
           │          │          │
   ┌───────▼──┐ ┌─────▼────┐ ┌──▼──────────┐
   │ Generator │ │ Executor │ │  Analyzer   │
   │  Agent    │ │  Agent   │ │   Agent     │
   │           │ │          │ │             │
   │ 生成/补充  │ │ 编排执行  │ │ 失败分析    │
   │ 测试用例   │ │ 管理隔离  │ │ 回归检测    │
   │ 质量把关   │ │ 结果收集  │ │ 根因定位    │
   └──────────┘ └──────────┘ └─────────────┘
                                    │
                            ┌───────▼────────┐
                            │  Fixer Agent   │
                            │  • 修复 flaky  │
                            │  • 提交 PR     │
                            └────────────────┘
```

### 9.3 关键设计原则

1. **渐进式自治**：Agent 的自治程度可配置（`auto_approve: false` → 所有 AI 操作需人类确认）
2. **可观测性优先**：Agent 的每一步决策都有 trace log，可以回溯 "为什么这么做"
3. **兜底机制**：AI 失败不阻塞流水线，降级为传统模式继续执行
4. **反馈闭环**：人类对 AI 结果的 accept/reject 持续反馈，优化 prompt 和策略

---

## 10. AI 引入效果评估体系

### 10.1 量化指标

| 维度 | 指标 | 计算方式 | 目标 |
|------|------|---------|------|
| **效率** | 用例生成速度 | AI 生成 N 条用例的时间 vs 人工 | 提效 5x+ |
| **效率** | 故障分析耗时 | AI 给出根因的时间 vs 人工排查 | 缩短 50%+ |
| **质量** | 生成用例有效率 | 通过 Quality Gate 的比例 | > 70% |
| **质量** | 生成用例存活率 | 6 个月后仍在用的 AI 生成用例比例 | > 50% |
| **质量** | 分析准确率 | 人工确认 AI 根因正确的比例 | > 60% |
| **覆盖** | 增量覆盖率 | AI 用例新增覆盖的代码路径 | +15%+ |
| **可靠性** | Flaky Rate | AI 生成用例的 flaky 率 vs 人工 | ≤ 人工水平 |

### 10.2 评估方法

#### A/B 对比实验

```
              ┌─ Group A：纯人工编写测试 ─── 记录效率、覆盖率、flaky 率
同一个 Feature ─┤
              └─ Group B：AI 辅助编写测试 ─── 记录效率、覆盖率、flaky 率
                                              ↓
                                         对比分析
```

#### 回溯验证

- 用 AI 分析器回溯分析过去 6 个月的已知失败
- 检查 AI 是否能正确识别已知 root cause
- 计算准确率作为 baseline

#### 持续监控仪表盘

```
┌───────────────────── AI 测试效果仪表盘 ─────────────────────┐
│                                                             │
│  本周生成: 127 用例  │  有效率: 78%  │  已合入: 89 条       │
│  分析触发: 23 次     │  准确率: 65%  │  采纳率: 71%         │
│                                                             │
│  趋势: 有效率 ↑3%   │  Flaky: ↓ 2 条                       │
│                                                             │
│  [最近失败分析]  [生成用例审核队列]  [效果周报]              │
└─────────────────────────────────────────────────────────────┘
```

### 10.3 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| AI 生成低质量用例浪费审核时间 | 效率反降 | Quality Gate 前置过滤 + 置信度阈值 |
| AI 分析给出错误根因误导工程师 | 信任下降 | 明确标注 confidence，低于阈值提示 "仅供参考" |
| LLM API 不稳定/延迟高 | 阻塞测试 | 降级机制：AI 超时则跳过，不阻塞核心流程 |
| Prompt 注入/异常输出 | 安全风险 | 输出经 schema validation，不直接执行未校验 SQL |
| API 成本增长 | 预算压力 | 缓存相似查询结果 + 按需调用（仅失败触发分析） |

---

## 11. 实现优先级（MVP Roadmap）

```
Sprint 1（核心框架）                    Sprint 2（AI 能力）
─────────────────                     ─────────────────
✅ YAML 用例加载器                     ✅ AI 测试用例生成（Prompt + Few-shot）
✅ .test/.result 格式兼容              ✅ Quality Gate 5 层检查
✅ SQL 执行引擎                        ✅ AI 失败分析（结构化 JSON 输出）
✅ 结果校验器（7 种断言 + 归一化）     ✅ 分析结果集成到报告
✅ 测试隔离（database 级）             ✅ CLI generate/analyze 命令
✅ 失败重试（infra-only + 指数退避）
✅ JSON v2 / HTML 交互式报告
✅ CLI run 命令
✅ Docker Compose 一键启动
✅ 真实 TiDB 测试用例集成

Sprint 3（增强）
─────────────────
⬜ 多版本同时执行 + 跨版本对比报告
⬜ Suite 级并发执行
⬜ Flaky test 自动检测与标记
⬜ 效果评估仪表盘
⬜ AI 生成后自动试运行验证
```

---

## 12. 总结

本 MVP 的核心设计哲学：

1. **基础先行**：先做好一个可靠的测试执行框架，再叠加 AI 能力
2. **AI 增强而非 AI 依赖**：AI 是 "copilot"，不是 "autopilot"；所有 AI 输出经过质量关卡
3. **可度量**：每项 AI 能力都有明确的评估指标，用数据说话
4. **渐进演进**：MVP → 工具链 → Agent，逐步提升自治程度，每一步都可验证价值
