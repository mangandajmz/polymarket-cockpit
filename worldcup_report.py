from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from worldcup_recommendations import WorldCupRecommendationStore
from worldcup_snapshot import DEFAULT_DB_PATH


DEFAULT_REPORT_PATH = Path("worldcup_report.html")


def _fmt_num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def _fmt_signed(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.{digits}f}"


def _cell(value: Any) -> str:
    return escape("" if value is None else str(value))


def _metric(label: str, value: str, hint: str = "") -> str:
    return (
        '<section class="metric">'
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        f"<small>{escape(hint)}</small>"
        "</section>"
    )


def render_worldcup_report_html(
    recommendations: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    generated_at_utc: str | None = None,
) -> str:
    generated_at_utc = generated_at_utc or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    open_count = sum(1 for row in recommendations if not row.get("resolution_result"))
    resolved_count = int(summary.get("resolved_count") or 0)
    positive_edges = sum(1 for row in recommendations if float(row.get("edge") or 0) > 0)

    metric_html = "\n".join(
        [
            _metric("Saved Recommendations", str(len(recommendations)), "tracked rows"),
            _metric("Open Recommendations", str(open_count), "awaiting result"),
            _metric("Resolved Recommendations", str(resolved_count), "WON/LOST only"),
            _metric(
                "Avg Brier Edge",
                _fmt_signed(summary.get("average_brier_edge")),
                "positive beats midpoint",
            ),
            _metric("Positive Entry Edges", str(positive_edges), "operator P minus midpoint"),
        ]
    )

    if recommendations:
        row_html = "\n".join(_recommendation_row(row) for row in recommendations)
    else:
        row_html = (
            '<tr><td colspan="11" class="empty">'
            "No World Cup paper recommendations have been saved yet."
            "</td></tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>World Cup Paper Recommendation Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --surface: #ffffff;
      --ink: #17202a;
      --muted: #64707d;
      --line: #d9dee5;
      --accent: #256c5b;
      --warn: #a45a16;
      --bad: #9d2f3f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 32px auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-end;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
      margin-bottom: 22px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      font-weight: 700;
    }}
    .subtle, .stamp {{
      color: var(--muted);
      font-size: 14px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0 24px;
    }}
    .metric {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 104px;
    }}
    .metric span, .metric small {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}
    .metric strong {{
      display: block;
      margin: 8px 0;
      font-size: 25px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    th {{
      background: #eef2f5;
      color: #33404c;
      font-size: 12px;
      text-transform: uppercase;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .badge {{
      display: inline-block;
      min-width: 58px;
      padding: 2px 8px;
      border-radius: 999px;
      text-align: center;
      background: #e9f3ef;
      color: var(--accent);
      font-weight: 700;
      font-size: 12px;
    }}
    .badge.lost {{ background: #f7e9eb; color: var(--bad); }}
    .badge.open {{ background: #f7efe5; color: var(--warn); }}
    .thesis {{ max-width: 280px; }}
    .empty {{ color: var(--muted); text-align: center; padding: 28px; }}
    @media (max-width: 900px) {{
      header {{ display: block; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>World Cup Paper Recommendation Report</h1>
        <div class="subtle">Paper-only local report for saved Polymarket World Cup recommendations.</div>
      </div>
      <div class="stamp">Generated UTC: {escape(generated_at_utc)}</div>
    </header>

    <section class="metrics">
      {metric_html}
    </section>

    <table>
      <thead>
        <tr>
          <th>Created UTC</th>
          <th>Status</th>
          <th>Question</th>
          <th>Outcome</th>
          <th class="num">User P</th>
          <th class="num">Mid</th>
          <th class="num">Edge</th>
          <th>Result</th>
          <th class="num">Brier</th>
          <th class="num">Brier Edge</th>
          <th>Thesis</th>
        </tr>
      </thead>
      <tbody>
        {row_html}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def _recommendation_row(row: dict[str, Any]) -> str:
    result = str(row.get("resolution_result") or "OPEN")
    badge_class = "open"
    if result == "WON":
        badge_class = "won"
    elif result == "LOST":
        badge_class = "lost"
    return (
        "<tr>"
        f"<td>{_cell(row.get('created_at_utc'))}</td>"
        f"<td>{_cell(row.get('status'))}</td>"
        f"<td>{_cell(row.get('question'))}<br><small>{_cell(row.get('recommendation_id'))}</small></td>"
        f"<td>{_cell(row.get('outcome'))}</td>"
        f"<td class=\"num\">{_fmt_num(row.get('user_probability'))}</td>"
        f"<td class=\"num\">{_fmt_num(row.get('midpoint'))}</td>"
        f"<td class=\"num\">{_fmt_signed(row.get('edge'))}</td>"
        f"<td><span class=\"badge {badge_class}\">{escape(result)}</span></td>"
        f"<td class=\"num\">{_fmt_num(row.get('brier_score'))}</td>"
        f"<td class=\"num\">{_fmt_signed(row.get('brier_edge'))}</td>"
        f"<td class=\"thesis\">{_cell(row.get('thesis'))}</td>"
        "</tr>"
    )


def write_worldcup_report(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    output_path: str | Path = DEFAULT_REPORT_PATH,
    limit: int = 100,
    generated_at_utc: str | None = None,
) -> Path:
    output_path = Path(output_path)
    store = WorldCupRecommendationStore(db_path)
    html = render_worldcup_report_html(
        store.load_recommendations(limit=limit),
        store.load_evaluation_summary(),
        generated_at_utc=generated_at_utc,
    )
    if output_path.parent != Path("."):
        output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a local static HTML report for World Cup paper recommendations."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    output_path = write_worldcup_report(
        db_path=args.db,
        output_path=args.output,
        limit=args.limit,
    )
    print(f"Wrote World Cup paper recommendation report: {output_path}")


if __name__ == "__main__":
    main()
