from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bayesian_stats import compute_posterior, estimate_beta_prior, rank_trader_posteriors
from shadow_model import OnlineLogisticModel


def load_opportunities(db_path: Path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row) for row in conn.execute(
                """
                SELECT *
                FROM opportunities
                ORDER BY observed_at_utc, event_id
                """
            ).fetchall()
        ]


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _normalize_utc(value)
    if hasattr(value, "to_pydatetime"):
        try:
            return _normalize_utc(value.to_pydatetime())
        except Exception:
            pass
    try:
        return _normalize_utc(datetime.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        try:
            return _normalize_utc(datetime.fromisoformat(str(value)))
        except (TypeError, ValueError):
            return None


def normalized_pnl_per_dollar(row: dict) -> float | None:
    status = row.get("resolution_status")
    price = float(row.get("price") or 0.0)
    if status not in ("WIN", "LOSS") or price <= 0.0:
        return None
    if status == "LOSS":
        return -1.0
    return (1.0 / price) - 1.0


def hold_hours(row: dict) -> float | None:
    observed = _parse_ts(row.get("observed_at_utc"))
    resolved = _parse_ts(row.get("resolved_at_utc"))
    if not observed or not resolved:
        return None
    return max(0.0, (resolved - observed).total_seconds() / 3600.0)


def _replay_metrics(
    *,
    policy_name: str,
    start_bankroll: float,
    bankroll: float,
    trades_taken: int,
    max_locked: float,
    total_locked_dollar_hours: float,
    total_hold_hours: float,
    avg_score: float,
    model_threshold: float | None = None,
) -> dict:
    bankroll_delta = bankroll - start_bankroll
    return {
        "policy_name": policy_name,
        "start_bankroll": start_bankroll,
        "final_bankroll": bankroll,
        "bankroll_delta": bankroll_delta,
        "trades_taken": trades_taken,
        "max_locked_capital": max_locked,
        "total_locked_dollar_hours": total_locked_dollar_hours,
        "return_per_locked_dollar_hour": (
            bankroll_delta / total_locked_dollar_hours if total_locked_dollar_hours > 0 else 0.0
        ),
        "avg_hold_hours": (total_hold_hours / trades_taken) if trades_taken else 0.0,
        "roi_per_trade": (bankroll_delta / trades_taken) if trades_taken else 0.0,
        "avg_score": avg_score,
        "model_threshold": model_threshold,
    }


def summarize(rows: list[dict]):
    total = len(rows)
    copied = [row for row in rows if row["decision"] == "COPIED"]
    skipped = [row for row in rows if row["decision"] == "SKIP"]
    resolved = [row for row in copied if row.get("resolution_status") in ("WIN", "LOSS")]
    copied_pnl = sum(float(row.get("resolved_pnl") or 0.0) for row in resolved)

    by_reason: dict[str, int] = {}
    for row in skipped:
        reason = row.get("decision_reason") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1

    print(f"Opportunities: {total}")
    print(f"Copied       : {len(copied)}")
    print(f"Skipped      : {len(skipped)}")
    print(f"Resolved copy: {len(resolved)}")
    print(f"Copy PnL     : ${copied_pnl:+.2f}")
    print("")
    print("Top skip reasons:")
    for reason, count in sorted(by_reason.items(), key=lambda item: (-item[1], item[0]))[:10]:
        print(f"  {reason:<32} {count:>6}")

    trader_stats: dict[str, dict[str, int]] = {}
    for row in resolved:
        trader = row["trader"]
        stats = trader_stats.setdefault(trader, {"wins": 0, "losses": 0})
        if row.get("resolution_status") == "WIN":
            stats["wins"] += 1
        elif row.get("resolution_status") == "LOSS":
            stats["losses"] += 1

    if trader_stats:
        print("")
        print("Bayesian trader ranking (90% lower bound):")
        for posterior in rank_trader_posteriors(trader_stats)[:10]:
            print(
                f"  {posterior.trader:<18} "
                f"obs {posterior.observed_win_rate * 100:>5.1f}%  "
                f"post {posterior.posterior_mean * 100:>5.1f}%  "
                f"lcb {posterior.lower_bound * 100:>5.1f}%  "
                f"n={posterior.total}"
            )

    resolved_all = [
        row for row in rows
        if row.get("resolution_status") in ("WIN", "LOSS")
        and row.get("whale_side") == "BUY"
        and not row.get("is_crypto")
        and not row.get("is_spread")
        and not row.get("is_futures")
        and not row.get("price_capped")
        and float(row.get("whale_size_usdc") or 0) > 0
    ]
    if resolved_all:
        print("")
        print("Shadow replay (Bayesian gate on resolved opportunity stream):")
        run_shadow_replay(resolved_all)


def run_shadow_replay(rows: list[dict], threshold_pct: float = 60.0):
    trainer: dict[str, dict[str, int]] = {}
    alpha_prior, beta_prior = estimate_beta_prior(trainer)
    current_total = current_wins = 0
    shadow_total = shadow_wins = 0

    for row in rows:
        trader = row["trader"]
        stats = trainer.get(trader, {"wins": 0, "losses": 0})
        posterior = compute_posterior(
            stats["wins"],
            stats["losses"],
            alpha_prior=alpha_prior,
            beta_prior=beta_prior,
            trader=trader,
        )
        label = row["resolution_status"]
        current_taken = row.get("decision") == "COPIED"
        shadow_taken = posterior.lower_bound * 100 >= threshold_pct

        if current_taken:
            current_total += 1
            if label == "WIN":
                current_wins += 1

        if shadow_taken:
            shadow_total += 1
            if label == "WIN":
                shadow_wins += 1

        bucket = trainer.setdefault(trader, {"wins": 0, "losses": 0})
        if label == "WIN":
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        alpha_prior, beta_prior = estimate_beta_prior(trainer)

    current_wr = (current_wins / current_total * 100) if current_total else 0.0
    shadow_wr = (shadow_wins / shadow_total * 100) if shadow_total else 0.0
    print(f"  Current policy copied : {current_total:>5} | win rate {current_wr:>5.1f}%")
    print(f"  Shadow Bayesian gate  : {shadow_total:>5} | win rate {shadow_wr:>5.1f}% | threshold {threshold_pct:.0f}% LCB")
    print("")
    print("Walk-forward bankroll replay ($10 stake, event-driven capital release):")
    current_metrics = simulate_event_driven_policy(rows, policy_name="current")
    shadow_metrics = simulate_event_driven_policy(
        rows,
        policy_name="bayes",
        threshold_pct=threshold_pct,
    )
    print(
        f"  Current policy bankroll: ${current_metrics['start_bankroll']:.2f} -> "
        f"${current_metrics['final_bankroll']:.2f} | trades {current_metrics['trades_taken']:>4} | "
        f"max locked ${current_metrics['max_locked_capital']:.2f}"
    )
    print(
        f"  Bayesian shadow bankrl: ${shadow_metrics['start_bankroll']:.2f} -> "
        f"${shadow_metrics['final_bankroll']:.2f} | trades {shadow_metrics['trades_taken']:>4} | "
        f"max locked ${shadow_metrics['max_locked_capital']:.2f}"
    )
    model_metrics = simulate_event_driven_policy(
        rows,
        policy_name="model",
        model_threshold=0.57,
    )
    print(
        f"  Predictive shadow bank: ${model_metrics['start_bankroll']:.2f} -> "
        f"${model_metrics['final_bankroll']:.2f} | trades {model_metrics['trades_taken']:>4} | "
        f"max locked ${model_metrics['max_locked_capital']:.2f} | avg p {model_metrics['avg_score']:.3f}"
    )


def _policy_take_row(
    row: dict,
    policy_name: str,
    trainer: dict[str, dict[str, int]],
    alpha_prior: float,
    beta_prior: float,
    threshold_pct: float,
    model: OnlineLogisticModel | None = None,
    model_threshold: float = 0.57,
) -> bool:
    if policy_name == "current":
        return row.get("decision") == "COPIED"
    if policy_name == "hybrid":
        if row.get("decision") != "COPIED":
            return False
        if model is None or model.examples_seen < 10:
            return False
        return model.predict_proba(row) >= model_threshold
    if policy_name == "model":
        if model is None or model.examples_seen < 10:
            return False
        return model.predict_proba(row) >= model_threshold
    if policy_name != "bayes":
        raise ValueError(f"Unsupported policy: {policy_name}")
    trader = row["trader"]
    stats = trainer.get(trader, {"wins": 0, "losses": 0})
    posterior = compute_posterior(
        stats["wins"],
        stats["losses"],
        alpha_prior=alpha_prior,
        beta_prior=beta_prior,
        trader=trader,
    )
    return posterior.lower_bound * 100 >= threshold_pct


def simulate_event_driven_policy(
    rows: list[dict],
    *,
    policy_name: str,
    threshold_pct: float = 60.0,
    start_bankroll: float = 300.0,
    unit_stake: float = 10.0,
    model_threshold: float = 0.57,
) -> dict:
    ordered = sorted(
        rows,
        key=lambda row: (
            _parse_ts(row.get("observed_at_utc")) or datetime.min,
            row.get("event_id") or "",
        ),
    )
    trainer: dict[str, dict[str, int]] = {}
    alpha_prior, beta_prior = estimate_beta_prior(trainer)
    bankroll = start_bankroll
    open_positions: list[dict] = []
    trades_taken = 0
    max_locked = 0.0
    scores: list[float] = []
    total_locked_dollar_hours = 0.0
    total_hold_hours = 0.0
    model = OnlineLogisticModel() if policy_name in ("model", "hybrid") else None

    for row in ordered:
        observed = _parse_ts(row.get("observed_at_utc"))
        resolved = _parse_ts(row.get("resolved_at_utc"))
        payoff = normalized_pnl_per_dollar(row)
        if observed is None or resolved is None or payoff is None:
            continue

        releasable = [pos for pos in open_positions if pos["resolved_at"] <= observed]
        if releasable:
            for pos in releasable:
                total_locked_dollar_hours += pos["stake"] * pos.get("hold_hours", 0.0)
                bankroll += pos["stake"] + pos["stake"] * pos["payoff"]
            open_positions = [pos for pos in open_positions if pos["resolved_at"] > observed]

        take_trade = _policy_take_row(
            row,
            policy_name,
            trainer,
            alpha_prior,
            beta_prior,
            threshold_pct,
            model,
            model_threshold,
        )
        if model is not None:
            scores.append(model.predict_proba(row))
        if take_trade and bankroll >= unit_stake:
            hold = max(0.0, (resolved - observed).total_seconds() / 3600.0)
            bankroll -= unit_stake
            open_positions.append({
                "resolved_at": resolved,
                "stake": unit_stake,
                "payoff": payoff,
                "hold_hours": hold,
            })
            trades_taken += 1
            total_hold_hours += hold

        locked_capital = sum(pos["stake"] for pos in open_positions)
        max_locked = max(max_locked, locked_capital)

        bucket = trainer.setdefault(row["trader"], {"wins": 0, "losses": 0})
        if row["resolution_status"] == "WIN":
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        alpha_prior, beta_prior = estimate_beta_prior(trainer)
        if model is not None:
            model.update(row, 1 if row["resolution_status"] == "WIN" else 0)

    for pos in sorted(open_positions, key=lambda item: item["resolved_at"]):
        total_locked_dollar_hours += pos["stake"] * pos.get("hold_hours", 0.0)
        bankroll += pos["stake"] + pos["stake"] * pos["payoff"]

    return _replay_metrics(
        policy_name=policy_name,
        start_bankroll=start_bankroll,
        bankroll=bankroll,
        trades_taken=trades_taken,
        max_locked=max_locked,
        total_locked_dollar_hours=total_locked_dollar_hours,
        total_hold_hours=total_hold_hours,
        avg_score=(sum(scores) / len(scores)) if scores else 0.0,
        model_threshold=model_threshold if policy_name in ("model", "hybrid") else None,
    )


def simulate_model_threshold_sweep(
    rows: list[dict],
    *,
    thresholds: list[float] | None = None,
    start_bankroll: float = 300.0,
    unit_stake: float = 10.0,
) -> list[dict]:
    thresholds = thresholds or [0.55, 0.60, 0.65, 0.70]
    out = []
    for threshold in thresholds:
        metrics = simulate_event_driven_policy(
            rows,
            policy_name="model",
            model_threshold=threshold,
            start_bankroll=start_bankroll,
            unit_stake=unit_stake,
        )
        out.append(metrics)
    return out


def simulate_hybrid_threshold_sweep(
    rows: list[dict],
    *,
    thresholds: list[float] | None = None,
    start_bankroll: float = 300.0,
    unit_stake: float = 10.0,
) -> list[dict]:
    thresholds = thresholds or [0.55, 0.60, 0.65, 0.70]
    out = []
    for threshold in thresholds:
        metrics = simulate_event_driven_policy(
            rows,
            policy_name="hybrid",
            model_threshold=threshold,
            start_bankroll=start_bankroll,
            unit_stake=unit_stake,
        )
        out.append(metrics)
    return out


def analyze_model_replay(
    rows: list[dict],
    *,
    model_threshold: float = 0.57,
    warmup_examples: int = 10,
) -> dict:
    ordered = sorted(
        rows,
        key=lambda row: (
            _parse_ts(row.get("observed_at_utc")) or datetime.min,
            row.get("event_id") or "",
        ),
    )
    model = OnlineLogisticModel()
    parsed_rows = 0
    skipped_rows = 0
    warm_rows = 0
    replay_take_count = 0
    logged_take_count = 0
    replay_logged_agree = 0
    replay_logged_disagree = 0
    first_warm_event_id = None
    first_warm_observed_at = None
    scores_all: list[float] = []
    scores_warm: list[float] = []
    score_buckets = {"lt_50": 0, "50_55": 0, "55_60": 0, "60_70": 0, "ge_70": 0}

    for row in ordered:
        observed = _parse_ts(row.get("observed_at_utc"))
        resolved = _parse_ts(row.get("resolved_at_utc"))
        payoff = normalized_pnl_per_dollar(row)
        if observed is None or resolved is None or payoff is None:
            skipped_rows += 1
            continue
        parsed_rows += 1

        score = model.predict_proba(row)
        scores_all.append(score)
        if model.examples_seen >= warmup_examples:
            if first_warm_event_id is None:
                first_warm_event_id = row.get("event_id")
                first_warm_observed_at = row.get("observed_at_utc")
            warm_rows += 1
            scores_warm.append(score)
            replay_take = score >= model_threshold
            logged_take = row.get("shadow_model_decision") == "TAKE"
            replay_take_count += int(replay_take)
            logged_take_count += int(logged_take)
            if replay_take == logged_take:
                replay_logged_agree += 1
            else:
                replay_logged_disagree += 1

            if score < 0.50:
                score_buckets["lt_50"] += 1
            elif score < 0.55:
                score_buckets["50_55"] += 1
            elif score < 0.60:
                score_buckets["55_60"] += 1
            elif score < 0.70:
                score_buckets["60_70"] += 1
            else:
                score_buckets["ge_70"] += 1

        model.update(row, 1 if row["resolution_status"] == "WIN" else 0)

    total_cmp = replay_logged_agree + replay_logged_disagree
    avg_all = (sum(scores_all) / len(scores_all)) if scores_all else 0.0
    avg_warm = (sum(scores_warm) / len(scores_warm)) if scores_warm else 0.0
    return {
        "threshold": model_threshold,
        "warmup_examples": warmup_examples,
        "parsed_rows": parsed_rows,
        "skipped_rows": skipped_rows,
        "warm_rows": warm_rows,
        "first_warm_event_id": first_warm_event_id,
        "first_warm_observed_at": first_warm_observed_at,
        "avg_score_all": avg_all,
        "avg_score_warm": avg_warm,
        "max_score_warm": max(scores_warm) if scores_warm else 0.0,
        "min_score_warm": min(scores_warm) if scores_warm else 0.0,
        "replay_take_count": replay_take_count,
        "logged_take_count": logged_take_count,
        "replay_take_rate_warm": (replay_take_count / warm_rows * 100.0) if warm_rows else 0.0,
        "logged_take_rate_warm": (logged_take_count / warm_rows * 100.0) if warm_rows else 0.0,
        "replay_logged_agreement": (replay_logged_agree / total_cmp * 100.0) if total_cmp else 0.0,
        "replay_logged_disagreements": replay_logged_disagree,
        "score_buckets": score_buckets,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Summarize logged whale opportunities from bot_state.db."
    )
    parser.add_argument(
        "--db",
        default="bot_state.db",
        help="Path to the SQLite state database (default: bot_state.db)",
    )
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    rows = load_opportunities(db_path)
    if not rows:
        raise SystemExit("No opportunities found. Run the bot after the logging upgrade first.")
    summarize(rows)


if __name__ == "__main__":
    main()
