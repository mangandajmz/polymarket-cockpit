from __future__ import annotations

import argparse
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from bayesian_stats import rank_trader_posteriors
from opportunity_replay import load_opportunities, simulate_event_driven_policy


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def filter_rows(rows: list[dict], *, lookback_days: float, now: datetime | None = None) -> tuple[list[dict], datetime]:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=lookback_days)
    kept = []
    for row in rows:
        observed = parse_ts(row.get("observed_at_utc"))
        if observed and observed >= cutoff:
            kept.append(row)
    return kept, cutoff


def resolved_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("resolution_status") in ("WIN", "LOSS")]


def eligible_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in resolved_rows(rows):
        if row.get("whale_side") != "BUY":
            continue
        if row.get("is_crypto") or row.get("is_spread") or row.get("is_futures") or row.get("price_capped"):
            continue
        if float(row.get("whale_size_usdc") or 0.0) <= 0:
            continue
        out.append(row)
    return out


def win_rate(rows: list[dict], take_fn) -> tuple[int, float]:
    taken = [row for row in rows if take_fn(row)]
    if not taken:
        return 0, 0.0
    wins = sum(1 for row in taken if row.get("resolution_status") == "WIN")
    return len(taken), wins / len(taken) * 100.0


def disagreement_rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    disagreements = 0
    for row in rows:
        heuristic = row.get("decision") == "COPIED"
        model = row.get("shadow_model_decision") == "TAKE"
        if heuristic != model:
            disagreements += 1
    return disagreements / len(rows) * 100.0


def brier_score(rows: list[dict], score_key: str, transform=lambda x: x) -> float | None:
    scored = []
    for row in resolved_rows(rows):
        value = row.get(score_key)
        if value is None:
            continue
        try:
            p = float(transform(value))
        except (TypeError, ValueError):
            continue
        y = 1.0 if row.get("resolution_status") == "WIN" else 0.0
        scored.append((p - y) ** 2)
    if not scored:
        return None
    return sum(scored) / len(scored)


def bucket_table(rows: list[dict], score_key: str, buckets: list[tuple[float, float]]) -> list[dict]:
    table = []
    for low, high in buckets:
        members = []
        for row in resolved_rows(rows):
            value = row.get(score_key)
            if value is None:
                continue
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            if low <= score < high or (high == buckets[-1][1] and low <= score <= high):
                members.append(row)
        if not members:
            continue
        wins = sum(1 for row in members if row.get("resolution_status") == "WIN")
        avg_score = sum(float(row[score_key]) for row in members) / len(members)
        table.append({
            "bucket": f"{low*100:.0f}-{high*100:.0f}%",
            "count": len(members),
            "avg_score": avg_score * 100.0,
            "actual_wr": wins / len(members) * 100.0,
        })
    return table


def trader_summary(rows: list[dict]) -> list[dict]:
    stats: dict[str, dict[str, int]] = {}
    for row in resolved_rows(rows):
        trader = row.get("trader", "unknown")
        bucket = stats.setdefault(trader, {"wins": 0, "losses": 0})
        if row.get("resolution_status") == "WIN":
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    return [
        {
            "trader": row.trader,
            "observed_wr": row.observed_win_rate * 100.0,
            "posterior_mean": row.posterior_mean * 100.0,
            "lower_bound": row.lower_bound * 100.0,
            "n": row.total,
        }
        for row in rank_trader_posteriors(stats)
    ]


def top_skip_reasons(rows: list[dict], limit: int = 8) -> list[tuple[str, int]]:
    counts = Counter(
        row.get("decision_reason") or "unknown"
        for row in rows
        if row.get("decision") == "SKIP"
    )
    return counts.most_common(limit)


def build_report(rows: list[dict], *, lookback_days: float) -> dict:
    scoped_rows, cutoff = filter_rows(rows, lookback_days=lookback_days)
    scoped_resolved = resolved_rows(scoped_rows)
    scoped_eligible = eligible_rows(scoped_rows)

    coverage = {
        "lookback_days": lookback_days,
        "cutoff": cutoff,
        "opportunities": len(scoped_rows),
        "resolved": len(scoped_resolved),
        "unresolved": len(scoped_rows) - len(scoped_resolved),
        "copied": sum(1 for row in scoped_rows if row.get("decision") == "COPIED"),
        "skipped": sum(1 for row in scoped_rows if row.get("decision") == "SKIP"),
        "unique_traders": len({row.get("trader") for row in scoped_rows}),
        "top_skip_reasons": top_skip_reasons(scoped_rows),
    }

    selection = {
        "heuristic": win_rate(scoped_eligible, lambda row: row.get("decision") == "COPIED"),
        "bayes": win_rate(scoped_eligible, lambda row: (float(row.get("bayes_lower_bound") or 0.0) * 100.0) >= 60.0),
        "model": win_rate(scoped_eligible, lambda row: row.get("shadow_model_decision") == "TAKE"),
        "disagreement_rate": disagreement_rate(scoped_rows),
    }

    calibration = {
        "model_brier": brier_score(scoped_rows, "shadow_model_score"),
        "bayes_brier": brier_score(scoped_rows, "bayes_posterior_mean"),
        "model_buckets": bucket_table(scoped_rows, "shadow_model_score", [(0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 1.0)]),
        "bayes_buckets": bucket_table(scoped_rows, "bayes_lower_bound", [(0.0, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 1.0)]),
    }

    replay = {
        "current": simulate_event_driven_policy(scoped_eligible, policy_name="current"),
        "bayes": simulate_event_driven_policy(scoped_eligible, policy_name="bayes"),
        "model": simulate_event_driven_policy(scoped_eligible, policy_name="model"),
    }

    return {
        "coverage": coverage,
        "selection": selection,
        "calibration": calibration,
        "replay": replay,
        "traders": trader_summary(scoped_rows)[:8],
    }


def compact_snapshot(report: dict) -> dict:
    coverage = report["coverage"]
    selection = report["selection"]
    calibration = report["calibration"]
    replay = report["replay"]
    return {
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "lookback_days": coverage["lookback_days"],
        "cutoff_utc": coverage["cutoff"].strftime("%Y-%m-%d %H:%M:%S"),
        "opportunities": coverage["opportunities"],
        "resolved": coverage["resolved"],
        "copied": coverage["copied"],
        "skipped": coverage["skipped"],
        "heuristic_trades": selection["heuristic"][0],
        "heuristic_wr": selection["heuristic"][1],
        "bayes_trades": selection["bayes"][0],
        "bayes_wr": selection["bayes"][1],
        "model_trades": selection["model"][0],
        "model_wr": selection["model"][1],
        "disagreement_rate": selection["disagreement_rate"],
        "model_brier": calibration["model_brier"],
        "bayes_brier": calibration["bayes_brier"],
        "current_bankroll": replay["current"]["final_bankroll"],
        "bayes_bankroll": replay["bayes"]["final_bankroll"],
        "model_bankroll": replay["model"]["final_bankroll"],
        "top_skip_reasons": coverage["top_skip_reasons"][:5],
        "top_traders": report["traders"][:5],
    }


def fmt_pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:.1f}%"


def fmt_brier(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def print_report(report: dict):
    coverage = report["coverage"]
    selection = report["selection"]
    calibration = report["calibration"]
    replay = report["replay"]

    print("=" * 72)
    print(f"Daily Evaluation Report  |  lookback {coverage['lookback_days']} day(s)")
    print(f"Cutoff: {coverage['cutoff'].strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 72)
    print("")
    print("Coverage")
    print(f"  Opportunities : {coverage['opportunities']}")
    print(f"  Resolved      : {coverage['resolved']}")
    print(f"  Unresolved    : {coverage['unresolved']}")
    print(f"  Copied        : {coverage['copied']}")
    print(f"  Skipped       : {coverage['skipped']}")
    print(f"  Unique traders: {coverage['unique_traders']}")
    if coverage["top_skip_reasons"]:
        print("  Top skip reasons:")
        for reason, count in coverage["top_skip_reasons"]:
            print(f"    {reason:<30} {count:>5}")

    print("")
    print("Selection Quality")
    for name, label in (("heuristic", "Heuristic"), ("bayes", "Bayesian gate"), ("model", "Predictive model")):
        trades, wr = selection[name]
        print(f"  {label:<16} trades {trades:>4} | win rate {fmt_pct(wr)}")
    print(f"  Disagreement rate: {fmt_pct(selection['disagreement_rate'])}")

    print("")
    print("Calibration")
    print(f"  Model Brier: {fmt_brier(calibration['model_brier'])}")
    print(f"  Bayes Brier: {fmt_brier(calibration['bayes_brier'])}")
    if calibration["model_buckets"]:
        print("  Model score buckets:")
        for row in calibration["model_buckets"]:
            print(f"    {row['bucket']:<9} n={row['count']:<4} pred={row['avg_score']:>5.1f}% actual={row['actual_wr']:>5.1f}%")
    if calibration["bayes_buckets"]:
        print("  Bayes LCB buckets:")
        for row in calibration["bayes_buckets"]:
            print(f"    {row['bucket']:<9} n={row['count']:<4} pred={row['avg_score']:>5.1f}% actual={row['actual_wr']:>5.1f}%")

    print("")
    print("Replay")
    for key, label in (("current", "Current"), ("bayes", "Bayes"), ("model", "Model")):
        row = replay[key]
        print(
            f"  {label:<8} bankroll ${row['start_bankroll']:.2f} -> ${row['final_bankroll']:.2f} | "
            f"trades {row['trades_taken']:>4} | max locked ${row['max_locked_capital']:.2f}"
        )

    if report["traders"]:
        print("")
        print("Top Traders (posterior lower bound)")
        for row in report["traders"]:
            print(
                f"  {row['trader']:<18} n={row['n']:<3} "
                f"obs={row['observed_wr']:>5.1f}% post={row['posterior_mean']:>5.1f}% "
                f"lcb={row['lower_bound']:>5.1f}%"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Generate a robust daily evaluation report from bot_state.db."
    )
    parser.add_argument("--db", default="bot_state.db", help="Path to the SQLite state database")
    parser.add_argument("--days", type=float, default=1.0, help="Lookback window in days (default: 1.0)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    rows = load_opportunities(db_path)
    if not rows:
        raise SystemExit("No opportunities found in database.")
    print_report(build_report(rows, lookback_days=args.days))


if __name__ == "__main__":
    main()
