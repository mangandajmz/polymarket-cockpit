from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from api_client import JsonApiClient


GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

WORLD_CUP_TERMS = ("world cup", "fifa", "2026 world cup", "fifa world cup")
COMBO_TERMS = ("combo", "parlay", "same-game", "same game", "sgp")


@dataclass(frozen=True)
class MarketClassification:
    is_world_cup_related: bool
    is_combo_candidate: bool


@dataclass(frozen=True)
class EventSummary:
    id: str
    title: str
    slug: str


@dataclass(frozen=True)
class MarketSummary:
    id: str
    question: str
    condition_id: str
    outcomes: list[str]
    clob_token_ids: list[str]
    active: bool | None
    closed: bool | None
    enable_order_book: bool | None
    is_combo_candidate: bool


@dataclass(frozen=True)
class BookSample:
    token_id: str
    best_bid: float | None
    best_ask: float | None
    midpoint: float | None


@dataclass(frozen=True)
class WorldCupSpikeResult:
    query: str
    events: list[EventSummary]
    markets: list[MarketSummary]
    token_count: int
    sample_books: list[BookSample]
    combo_candidate_count: int
    wallet_auth_used: bool
    unsigned_rfq_quote_supported: bool | None


def parse_jsonish_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def classify_market(market: dict[str, Any]) -> MarketClassification:
    text = " ".join(
        str(market.get(key, ""))
        for key in ("question", "title", "description", "slug", "eventSlug")
    ).lower()
    return MarketClassification(
        is_world_cup_related=any(term in text for term in WORLD_CUP_TERMS),
        is_combo_candidate=any(term in text for term in COMBO_TERMS),
    )


def _rows_from_payload(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _event_from_row(row: dict[str, Any]) -> EventSummary:
    title = row.get("title") or row.get("name") or row.get("question") or ""
    return EventSummary(
        id=str(row.get("id") or ""),
        title=str(title),
        slug=str(row.get("slug") or ""),
    )


def _market_from_row(row: dict[str, Any]) -> MarketSummary:
    classification = classify_market(row)
    return MarketSummary(
        id=str(row.get("id") or row.get("conditionId") or ""),
        question=str(row.get("question") or row.get("title") or ""),
        condition_id=str(row.get("conditionId") or ""),
        outcomes=parse_jsonish_list(row.get("outcomes")),
        clob_token_ids=parse_jsonish_list(row.get("clobTokenIds")),
        active=_maybe_bool(row.get("active")),
        closed=_maybe_bool(row.get("closed")),
        enable_order_book=_maybe_bool(row.get("enableOrderBook")),
        is_combo_candidate=classification.is_combo_candidate,
    )


def _maybe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _dedupe_events(events: list[EventSummary]) -> list[EventSummary]:
    seen: set[tuple[str, str]] = set()
    deduped: list[EventSummary] = []
    for event in events:
        key = (event.id, event.slug or event.title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _dedupe_markets(markets: list[MarketSummary]) -> list[MarketSummary]:
    seen: set[tuple[str, str]] = set()
    deduped: list[MarketSummary] = []
    for market in markets:
        key = (market.id, market.question)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(market)
    return deduped


def _best_price(rows: Any, *, highest: bool) -> float | None:
    if not isinstance(rows, list):
        return None
    prices = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            prices.append(float(row.get("price")))
        except (TypeError, ValueError):
            continue
    if not prices:
        return None
    return max(prices) if highest else min(prices)


def _book_sample_from_payload(token_id: str, payload: Any) -> BookSample:
    if not isinstance(payload, dict):
        return BookSample(token_id=token_id, best_bid=None, best_ask=None, midpoint=None)
    best_bid = _best_price(payload.get("bids"), highest=True)
    best_ask = _best_price(payload.get("asks"), highest=False)
    midpoint = None
    if best_bid is not None and best_ask is not None:
        midpoint = round((best_bid + best_ask) / 2, 6)
    return BookSample(
        token_id=token_id,
        best_bid=best_bid,
        best_ask=best_ask,
        midpoint=midpoint,
    )


class PolymarketWorldCupSpike:
    def __init__(
        self,
        *,
        client: JsonApiClient | None = None,
        sample_price_count: int = 5,
    ) -> None:
        self.client = client or JsonApiClient(
            default_timeout=10.0,
            default_retries=2,
            backoff_base=0.75,
            jitter_max=0.15,
        )
        if client is None:
            self.client.session.headers.update({
                "User-Agent": "polymarket-cockpit-worldcup-spike/0.1"
            })
        self.sample_price_count = sample_price_count

    def run(self, *, query: str = "2026 FIFA World Cup", limit: int = 100) -> WorldCupSpikeResult:
        search_payload = self.client.get_json(
            f"{GAMMA_API}/public-search",
            params={"q": query, "limit": limit},
            retries=2,
        )
        events = [_event_from_row(row) for row in _rows_from_payload(search_payload, "events")]
        markets = [
            _market_from_row(row)
            for row in _rows_from_payload(search_payload, "markets")
            if classify_market(row).is_world_cup_related
        ]

        market_payload = self.client.get_json(
            f"{GAMMA_API}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
            retries=2,
        )
        markets.extend(
            _market_from_row(row)
            for row in _rows_from_payload(market_payload, "markets")
            if classify_market(row).is_world_cup_related
        )

        events = _dedupe_events(events)
        markets = _dedupe_markets(markets)
        sample_token_ids = self._sample_token_ids(markets)
        sample_books = [self.fetch_book_sample(token_id) for token_id in sample_token_ids]

        return WorldCupSpikeResult(
            query=query,
            events=events,
            markets=markets,
            token_count=sum(len(market.clob_token_ids) for market in markets),
            sample_books=sample_books,
            combo_candidate_count=sum(1 for market in markets if market.is_combo_candidate),
            wallet_auth_used=False,
            unsigned_rfq_quote_supported=None,
        )

    def fetch_book_sample(self, token_id: str) -> BookSample:
        payload = self.client.get_json(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            retries=2,
        )
        return _book_sample_from_payload(token_id, payload)

    def _sample_token_ids(self, markets: list[MarketSummary]) -> list[str]:
        token_ids: list[str] = []
        for market in markets:
            if market.enable_order_book is False:
                continue
            for token_id in market.clob_token_ids:
                if token_id and token_id not in token_ids:
                    token_ids.append(token_id)
                if len(token_ids) >= self.sample_price_count:
                    return token_ids
        return token_ids


def summarize_spike(result: WorldCupSpikeResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "event_count": len(result.events),
        "market_count": len(result.markets),
        "token_count": result.token_count,
        "sample_book_count": len(result.sample_books),
        "sample_midpoint_count": sum(1 for book in result.sample_books if book.midpoint is not None),
        "combo_candidate_count": result.combo_candidate_count,
        "wallet_auth_used": result.wallet_auth_used,
        "unsigned_rfq_quote_supported": (
            result.unsigned_rfq_quote_supported
            if result.unsigned_rfq_quote_supported is not None
            else "unknown"
        ),
    }


def _to_jsonable(result: WorldCupSpikeResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["summary"] = summarize_spike(result)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a read-only API spike for Polymarket World Cup market discovery."
    )
    parser.add_argument("--query", default="2026 FIFA World Cup")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sample-price-count", type=int, default=5)
    args = parser.parse_args()

    spike = PolymarketWorldCupSpike(sample_price_count=args.sample_price_count)
    result = spike.run(query=args.query, limit=args.limit)
    print(json.dumps(_to_jsonable(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
