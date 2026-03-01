from __future__ import annotations

import html
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATUS_ICON = {
    "passed": "&#x2705;",
    "failed": "&#x274C;",
    "skipped": "&#x23ED;",
    "error": "&#x26A0;",
}

STATUS_CLASS = {
    "passed": "pass",
    "failed": "fail",
    "skipped": "skip",
    "error": "fail",
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TiDB Test Report &mdash; {run_id}</title>
<style>
  :root {{
    --bg: #0f172a; --surface: #1e293b; --surface2: #253348; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308; --blue: #3b82f6;
    --green-bg: rgba(34,197,94,.12); --red-bg: rgba(239,68,68,.12);
    --yellow-bg: rgba(234,179,8,.12); --blue-bg: rgba(59,130,246,.12);
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif;
         background: var(--bg); color: var(--text); padding: 2rem; line-height: 1.6; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .25rem; }}
  .meta {{ color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }}
  .meta strong {{ color: var(--text); }}

  /* ---- Summary Cards ---- */
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
              gap: .75rem; margin-bottom: .75rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border);
           border-radius: .75rem; padding: 1.1rem 1rem; text-align: center; }}
  .card .num {{ font-size: 2rem; font-weight: 700; }}
  .card .label {{ font-size: .7rem; color: var(--muted); text-transform: uppercase;
                  letter-spacing: .05em; margin-top: .15rem; }}
  .card.green .num {{ color: var(--green); }}
  .card.red   .num {{ color: var(--red); }}
  .card.yellow .num {{ color: var(--yellow); }}
  .card.blue  .num {{ color: var(--blue); }}

  /* ---- Pass Rate Bar ---- */
  .rate-bar-container {{ margin-bottom: 2rem; }}
  .rate-bar {{ height: 10px; border-radius: 999px; overflow: hidden; display: flex;
               background: var(--surface); }}
  .rate-bar .seg-pass {{ background: var(--green); }}
  .rate-bar .seg-fail {{ background: var(--red); }}
  .rate-bar .seg-skip {{ background: var(--yellow); }}
  .rate-label {{ display: flex; justify-content: space-between; font-size: .75rem;
                 color: var(--muted); margin-top: .35rem; }}

  /* ---- Quick Failures ---- */
  .failures-nav {{ background: var(--surface); border: 1px solid var(--border);
                   border-radius: .75rem; padding: 1rem 1.25rem; margin-bottom: 1.5rem; }}
  .failures-nav h2 {{ font-size: 1rem; margin-bottom: .6rem; color: var(--red); }}
  .failures-nav ul {{ list-style: none; }}
  .failures-nav li {{ padding: .3rem 0; font-size: .85rem; }}
  .failures-nav a {{ color: var(--blue); text-decoration: none; }}
  .failures-nav a:hover {{ text-decoration: underline; }}
  .failures-nav .f-sql {{ color: var(--muted); font-size: .78rem; margin-left: .4rem;
                          font-family: 'Fira Code', monospace; }}

  /* ---- Filters ---- */
  .filters {{ display: flex; gap: .5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .filter-btn {{ background: var(--surface); border: 1px solid var(--border); color: var(--text);
                 padding: .4rem 1rem; border-radius: 999px; cursor: pointer; font-size: .8rem;
                 transition: all .15s; }}
  .filter-btn:hover {{ background: var(--surface2); }}
  .filter-btn.active {{ background: var(--blue); border-color: var(--blue); color: #fff; }}

  /* ---- Suite ---- */
  .suite {{ background: var(--surface); border: 1px solid var(--border);
            border-radius: .75rem; margin-bottom: 1.25rem; overflow: hidden; }}
  .suite-header {{ padding: .85rem 1.25rem; font-weight: 600; font-size: 1.05rem;
                   border-bottom: 1px solid var(--border); cursor: pointer;
                   display: flex; justify-content: space-between; align-items: center;
                   user-select: none; }}
  .suite-header:hover {{ background: var(--surface2); }}
  .suite-header .toggle {{ font-size: .8rem; color: var(--muted); transition: transform .2s; }}
  .suite-header.collapsed .toggle {{ transform: rotate(-90deg); }}
  .suite-body {{ overflow: hidden; transition: max-height .3s ease; }}
  .suite-body.collapsed {{ max-height: 0 !important; }}
  .suite-stats {{ font-size: .75rem; color: var(--muted); display: flex; gap: .75rem; }}
  .suite-stats .ss-pass {{ color: var(--green); }}
  .suite-stats .ss-fail {{ color: var(--red); }}

  /* ---- Table ---- */
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: .6rem 1.25rem; font-size: .7rem;
        text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--border);
        position: sticky; top: 0; background: var(--surface); }}
  td {{ padding: .65rem 1.25rem; border-bottom: 1px solid var(--border);
        font-size: .85rem; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr[data-status="passed"] {{ }}
  tr[data-status="failed"], tr[data-status="error"] {{ background: var(--red-bg); }}
  tr[data-status="skipped"] {{ opacity: .6; }}

  /* ---- Badges ---- */
  .badge {{ display: inline-block; padding: .15rem .6rem; border-radius: 999px;
            font-size: .75rem; font-weight: 600; }}
  .badge.pass {{ background: var(--green-bg); color: var(--green); }}
  .badge.fail {{ background: var(--red-bg); color: var(--red); }}
  .badge.skip {{ background: var(--yellow-bg); color: var(--yellow); }}

  /* ---- SQL & Details ---- */
  .sql {{ font-family: 'Fira Code', monospace; font-size: .78rem; color: var(--muted);
           word-break: break-all; }}
  .error-box {{ background: var(--red-bg); border: 1px solid rgba(239,68,68,.2);
                border-radius: .5rem; padding: .75rem; margin-top: .5rem; font-size: .8rem; }}
  .diff-block {{ font-family: 'Fira Code', monospace; font-size: .75rem; margin-top: .5rem;
                 border-radius: .4rem; overflow: hidden; border: 1px solid var(--border); }}
  .diff-header {{ background: var(--surface2); padding: .35rem .75rem; font-size: .7rem;
                  color: var(--muted); font-weight: 600; }}
  .diff-content {{ padding: .5rem .75rem; white-space: pre-wrap; word-break: break-all;
                   max-height: 200px; overflow-y: auto; }}
  .diff-content.expected {{ background: rgba(34,197,94,.06); color: var(--green); }}
  .diff-content.actual   {{ background: rgba(239,68,68,.06); color: var(--red); }}
  .ai-box {{ background: var(--blue-bg); border: 1px solid rgba(59,130,246,.2);
             border-radius: .5rem; padding: .75rem; margin-top: .5rem; font-size: .8rem; }}
  .ai-box strong {{ color: var(--blue); }}
  .confidence {{ opacity: .7; font-size: .75rem; }}
  .ai-label {{ display: inline-block; padding: .1rem .5rem; border-radius: 999px; font-size: .65rem;
               font-weight: 600; margin-left: .4rem; }}
  .ai-label.high {{ background: var(--green-bg); color: var(--green); }}
  .ai-label.medium {{ background: var(--yellow-bg); color: var(--yellow); }}
  .ai-label.low {{ background: var(--red-bg); color: var(--red); }}

  /* ---- Footer ---- */
  .footer {{ text-align: center; color: var(--muted); font-size: .75rem; margin-top: 2rem;
             padding-top: 1.5rem; border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<div class="container">
  <h1>TiDB Test Report</h1>
  <div class="meta">
    Run <strong>{run_id}</strong> &middot; TiDB <strong>{version}</strong> &middot;
    {duration} &middot; {timestamp}
    {env_html}
  </div>

  <div class="summary">
    <div class="card blue"><div class="num">{total}</div><div class="label">Total</div></div>
    <div class="card green"><div class="num">{passed}</div><div class="label">Passed</div></div>
    <div class="card red"><div class="num">{failed}</div><div class="label">Failed</div></div>
    <div class="card yellow"><div class="num">{skipped}</div><div class="label">Skipped</div></div>
  </div>

  <div class="rate-bar-container">
    <div class="rate-bar">
      <div class="seg-pass" style="width:{pass_pct}%"></div>
      <div class="seg-fail" style="width:{fail_pct}%"></div>
      <div class="seg-skip" style="width:{skip_pct}%"></div>
    </div>
    <div class="rate-label">
      <span>Pass rate: <strong style="color:var(--green)">{pass_rate}%</strong></span>
      <span>{flaky_retried} flaky retried</span>
    </div>
  </div>

  {failures_nav_html}

  <div class="filters">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="passed">Passed</button>
    <button class="filter-btn" data-filter="failed">Failed</button>
    <button class="filter-btn" data-filter="skipped">Skipped</button>
  </div>

  {suites_html}

  <div class="footer">Generated by <strong>tidbtest-mvp</strong> &middot; Schema v2.0</div>
</div>

<script>
(function() {{
  // Filter buttons
  document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const f = btn.dataset.filter;
      document.querySelectorAll('tr[data-status]').forEach(row => {{
        row.style.display = (f === 'all' || row.dataset.status === f) ? '' : 'none';
      }});
    }});
  }});
  // Collapsible suites
  document.querySelectorAll('.suite-header').forEach(header => {{
    header.addEventListener('click', () => {{
      header.classList.toggle('collapsed');
      header.nextElementSibling.classList.toggle('collapsed');
    }});
  }});
}})();
</script>
</body>
</html>
"""


def _format_diff(expected: str, actual: str) -> str:
    """Render expected vs actual in a side-by-side-ish diff block."""
    return (
        '<div class="diff-block">'
        '<div class="diff-header">&#x2212; Expected</div>'
        f'<div class="diff-content expected">{html.escape(str(expected))}</div>'
        '<div class="diff-header">&#x2b; Actual</div>'
        f'<div class="diff-content actual">{html.escape(str(actual))}</div>'
        '</div>'
    )


def _confidence_label(conf) -> str:
    if not isinstance(conf, (int, float)):
        return ""
    if conf >= 0.75:
        return '<span class="ai-label high">HIGH</span>'
    elif conf >= 0.5:
        return '<span class="ai-label medium">MED</span>'
    return '<span class="ai-label low">LOW</span>'


def generate_html_report(report_data: dict, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = report_data["summary"]
    total = summary.get("total", 0) or 1
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    pass_rate = summary.get("pass_rate", round(passed / total * 100, 2) if total else 0)
    pass_pct = round(passed / total * 100, 1)
    fail_pct = round(failed / total * 100, 1)
    skip_pct = round(skipped / total * 100, 1)

    # Environment line
    env = report_data.get("environment", {})
    env_parts = [f"{k}: {v}" for k, v in env.items()] if env else []
    env_html = f'<br><span style="font-size:.78rem">{" &middot; ".join(html.escape(p) for p in env_parts)}</span>' if env_parts else ""

    # Failures quick-nav
    failures = report_data.get("failure_summary", [])
    if not failures:
        for suite in report_data.get("suites", []):
            for case in suite.get("cases", []):
                if case["status"] in ("failed", "error"):
                    failures.append({
                        "suite": suite["name"],
                        "case_id": case["id"],
                        "sql": case.get("sql", ""),
                    })

    failures_nav_html = ""
    if failures:
        items = []
        for f in failures:
            anchor = f'{f["suite"]}__{f["case_id"]}'
            short_sql = f["sql"][:80] + ("..." if len(f.get("sql", "")) > 80 else "")
            items.append(
                f'<li><a href="#{html.escape(anchor)}">{html.escape(f["suite"])} / {html.escape(f["case_id"])}</a>'
                f'<span class="f-sql">{html.escape(short_sql)}</span></li>'
            )
        failures_nav_html = (
            '<div class="failures-nav">'
            f'<h2>&#x274C; {len(failures)} Failure(s) &mdash; Quick Jump</h2>'
            f'<ul>{"".join(items)}</ul></div>'
        )

    # Build suites
    suites_html = []
    for suite in report_data.get("suites", []):
        s_total = len(suite.get("cases", []))
        s_pass = sum(1 for c in suite.get("cases", []) if c["status"] == "passed")
        s_fail = sum(1 for c in suite.get("cases", []) if c["status"] in ("failed", "error"))

        rows = []
        for case in suite.get("cases", []):
            status = case["status"]
            icon = STATUS_ICON.get(status, "")
            cls = STATUS_CLASS.get(status, "")
            anchor_id = f'{suite["name"]}__{case["id"]}'

            extra = ""
            err = case.get("error")
            if err:
                msg = str(err.get("message", ""))
                exp = str(err.get("expected", ""))
                act = str(err.get("actual", ""))
                extra += f'<div class="error-box"><strong>Error:</strong> {html.escape(msg)}</div>'
                if exp or act:
                    extra += _format_diff(exp, act)

            ai = case.get("ai_analysis")
            if ai:
                conf = ai.get("confidence", "?")
                conf_lbl = _confidence_label(conf)
                related = ai.get("related_issues", [])
                related_html = ""
                if related:
                    links = ", ".join(
                        f'<a href="{html.escape(u)}" style="color:var(--blue)">{html.escape(u.split("/")[-1])}</a>'
                        for u in related
                    )
                    related_html = f"<br><strong>Related:</strong> {links}"
                extra += (
                    f'<div class="ai-box">'
                    f'<strong>AI Analysis</strong>{conf_lbl}<br>'
                    f'<strong>Root Cause:</strong> {html.escape(str(ai.get("root_cause", "")))}<br>'
                    f'<strong>Category:</strong> {html.escape(str(ai.get("category", "")))}<br>'
                    f'<strong>Suggestion:</strong> {html.escape(str(ai.get("suggestion", "")))}<br>'
                    f'<strong>Confidence:</strong> {conf}'
                    f'{related_html}'
                    f'</div>'
                )

            retries_note = (
                f' <span style="color:var(--yellow)">(+{case["retries"]} retries)</span>'
                if case.get("retries") else ""
            )
            rows.append(
                f'<tr data-status="{status}" id="{html.escape(anchor_id)}">'
                f'<td>{html.escape(case["id"])}</td>'
                f'<td><span class="badge {cls}">{icon} {status}</span>{retries_note}</td>'
                f'<td>{case.get("duration_ms", 0):.1f}ms</td>'
                f'<td><span class="sql">{html.escape(case.get("sql", ""))}</span>{extra}</td>'
                f'</tr>'
            )

        rows_html = "\n".join(rows)
        suites_html.append(
            f'<div class="suite">'
            f'<div class="suite-header">'
            f'{html.escape(suite["name"])}'
            f'<div style="display:flex;align-items:center;gap:1rem;">'
            f'<div class="suite-stats">'
            f'<span class="ss-pass">{s_pass}/{s_total} passed</span>'
            f'<span class="ss-fail">{s_fail} failed</span>'
            f'</div>'
            f'<span class="toggle">&#x25BC;</span>'
            f'</div>'
            f'</div>'
            f'<div class="suite-body">'
            f'<table><tr><th>Case</th><th>Status</th><th>Duration</th><th>SQL / Details</th></tr>'
            f'{rows_html}</table></div></div>'
        )

    timestamp = report_data.get("timestamp", "")
    if timestamp:
        timestamp = timestamp.replace("T", " ").split(".")[0] + " UTC"

    page = HTML_TEMPLATE.format(
        run_id=html.escape(report_data.get("run_id", "")),
        version=html.escape(report_data.get("tidb_version", "")),
        duration=f'{summary.get("duration_sec", 0):.2f}s',
        timestamp=html.escape(timestamp),
        env_html=env_html,
        total=summary.get("total", 0),
        passed=passed,
        failed=failed,
        skipped=skipped,
        pass_rate=pass_rate,
        pass_pct=pass_pct,
        fail_pct=fail_pct,
        skip_pct=skip_pct,
        flaky_retried=summary.get("flaky_retried", 0),
        failures_nav_html=failures_nav_html,
        suites_html="\n".join(suites_html),
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)

    logger.info("HTML report written to %s", output_path)
    return output_path
