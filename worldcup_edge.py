from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from worldcup_snapshot import DEFAULT_DB_PATH, WorldCupSnapshotStore


DEFAULT_PROBABILITY_PATH = Path("worldcup_probabilities.csv")


@dataclass(frozen=True)
class ProbabilityInput:
    token_id: str
    user_probability: float
    note: str = ""


@dataclass(frozen=True)
class EdgeRow:
    rank: int
    token_id: str
    question: str
    outcome: str
    best_bid: float | None
    best_ask: float | None
    midpoint: float
    spread: float | None
    user_probability: float
    edge: float
    note: str
    captured_at_utc: str


def normalize_probability(value: str | float | int) -> float:
    probability = float(value)
    if probability > 1:
        probability = probability / 100
    if probability < 0 or probability > 1:
        raise ValueError(f"user_probability must be between 0 and 1 or 0 and 100: {value}")
    return probability


def load_probability_file(path: str | Path) -> dict[str, ProbabilityInput]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"token_id", "user_probability"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("probability CSV must include token_id and user_probability columns")

        probabilities: dict[str, ProbabilityInput] = {}
        for row in reader:
            token_id = str(row.get("token_id") or "").strip()
            if not token_id:
                continue
            probabilities[token_id] = ProbabilityInput(
                token_id=token_id,
                user_probability=normalize_probability(row.get("user_probability") or ""),
                note=str(row.get("note") or "").strip(),
            )
    return probabilities


def _coerce_probability_map(
    probabilities: Mapping[str, ProbabilityInput | float | int],
) -> dict[str, ProbabilityInput]:
    coerced: dict[str, ProbabilityInput] = {}
    for token_id, value in probabilities.items():
        if isinstance(value, ProbabilityInput):
            coerced[token_id] = value
        else:
            coerced[token_id] = ProbabilityInput(
                token_id=token_id,
                user_probability=normalize_probability(value),
            )
    return coerced


def build_edge_board(
    store: WorldCupSnapshotStore,
    probabilities: Mapping[str, ProbabilityInput | float | int],
    *,
    max_spread: float | None = None,
    min_edge: float | None = None,
    limit: int = 50,
) -> list[EdgeRow]:
    probability_map = _coerce_probability_map(probabilities)
    odds_rows = store.load_latest_odds(limit=10_000)
    edge_rows: list[EdgeRow] = []

    for row in odds_rows:
        token_id = str(row.get("token_id") or "")
        probability = probability_map.get(token_id)
        midpoint = row.get("midpoint")
        if probability is None or midpoint is None:
            continue
        spread = row.get("spread")
        if max_spread is not None and spread is not None and float(spread) > max_spread:
            continue

        edge = round(probability.user_probability - float(midpoint), 6)
        if min_edge is not None and edge < min_edge:
            continue

        edge_rows.append(
            EdgeRow(
                rank=0,
                token_id=token_id,
                question=str(row.get("question") or ""),
                outcome=str(row.get("outcome") or ""),
                best_bid=_maybe_float(row.get("best_bid")),
                best_ask=_maybe_float(row.get("best_ask")),
                midpoint=float(midpoint),
                spread=_maybe_float(spread),
                user_probability=probability.user_probability,
                edge=edge,
                note=probability.note,
                captured_at_utc=str(row.get("captured_at_utc") or ""),
            )
        )

    edge_rows.sort(key=lambda item: item.edge, reverse=True)
    return [
        EdgeRow(
            rank=idx,
            token_id=row.token_id,
            question=row.question,
            outcome=row.outcome,
            best_bid=row.best_bid,
            best_ask=row.best_ask,
            midpoint=row.midpoint,
            spread=row.spread,
            user_probability=row.user_probability,
            edge=row.edge,
            note=row.note,
            captured_at_utc=row.captured_at_utc,
        )
        for idx, row in enumerate(edge_rows[:limit], start=1)
    ]


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def _fmt_edge(value: float) -> str:
    return f"{value:+.3f}"


def _clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3] + "..."


def format_edge_table(rows: list[EdgeRow]) -> str:
    if not rows:
        return "No edge rows matched the current probabilities and filters."

    headers = ["Rank", "Question", "Outcome", "User P", "Mid", "Edge", "Spread", "Note"]
    widths = [4, 42, 8, 7, 7, 7, 8, 24]
    lines = [
        " | ".join(header.ljust(width) for header, width in zip(headers, widths)),
        "-+-".join("-" * width for width in widths),
    ]
    for row in rows:
        values = [
            str(row.rank).rjust(widths[0]),
            _clip(row.question, widths[1]).ljust(widths[1]),
            _clip(row.outcome, widths[2]).ljust(widths[2]),
            _fmt_price(row.user_probability).rjust(widths[3]),
            _fmt_price(row.midpoint).rjust(widths[4]),
            _fmt_edge(row.edge).rjust(widths[5]),
            _fmt_price(row.spread).rjust(widths[6]),
            _clip(row.note, widths[7]).ljust(widths[7]),
        ]
        lines.append(" | ".join(values))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a read-only World Cup edge board from stored odds and user probabilities."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--probabilities", default=str(DEFAULT_PROBABILITY_PATH))
    parser.add_argument("--max-spread", type=float, default=None)
    parser.add_argument("--min-edge", type=float, default=None)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    probabilities = load_probability_file(args.probabilities)
    store = WorldCupSnapshotStore(args.db)
    rows = build_edge_board(
        store,
        probabilities,
        max_spread=args.max_spread,
        min_edge=args.min_edge,
        limit=args.limit,
    )
    print(format_edge_table(rows))


if __name__ == "__main__":
    main()
