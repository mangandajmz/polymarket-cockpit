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
    model = OnlineLogisticModel() if policy_name == "model" else None

    for row in ordered:
        observed = _parse_ts(row.get("observed_at_utc"))
        resolved = _parse_ts(row.get("resolved_at_utc"))
        payoff = normalized_pnl_per_dollar(row)
        if observed is None or resolved is None or payoff is None:
            continue

        releasable = [pos for pos in open_positions if pos["resolved_at"] <= observed]
        if releasable:
            for pos in releasable:
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
            bankroll -= unit_stake
            open_positions.append({
                "resolved_at": resolved,
                "stake": unit_stake,
                "payoff": payoff,
            })
            trades_taken += 1

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
        bankroll += pos["stake"] + pos["stake"] * pos["payoff"]

    return {
        "policy_name": policy_name,
        "start_bankroll": start_bankroll,
        "final_bankroll": bankroll,
        "trades_taken": trades_taken,
        "max_locked_capital": max_locked,
        "avg_score": (sum(scores) / len(scores)) if scores else 0.0,
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
